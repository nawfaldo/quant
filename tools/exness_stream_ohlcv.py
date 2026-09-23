#!/usr/bin/env python3
"""Continuously copy completed Exness MT5 candles into the Parquet store.

Multiple symbols, tables, and timeframes are paired by position. The defaults
feed the two non-crypto markets used by the live IDK portfolio:

    py tools/exness_stream_ohlcv.py

Completed bars are appended as `data/parquet/<table>/<date>.parquet` shards; the
bulk history exported out of QuestDB stays in `<table>.parquet` and is never
rewritten. See `parquet_writer` for the layout and the dedup rule.

This collector reads market data only. It never sends, changes, or closes an
MT5 order.
"""

from __future__ import annotations

import argparse
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import MetaTrader5 as _mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)

from parquet_writer import Sender, TimestampNanos, max_timestamp_ns


UTC = timezone.utc
DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
TIMEFRAMES = {
    "M1": (mt5.TIMEFRAME_M1, 60),
    "M30": (mt5.TIMEFRAME_M30, 1_800),
}


def log(message: str) -> None:
    stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def safe_table(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table name: {value!r}")
    return value


def actual_to_stored_ns(actual_seconds: int, wall_clock: ZoneInfo) -> int:
    """Real UTC instant -> New York wall clock reinterpreted as UTC."""
    local = datetime.fromtimestamp(actual_seconds, tz=UTC).astimezone(wall_clock)
    stored = local.replace(tzinfo=UTC)
    return int(stored.timestamp()) * 1_000_000_000


def stored_to_actual_seconds(stored_nanos: int, wall_clock: ZoneInfo) -> int:
    """Invert the repository's fake-UTC wall-clock timestamp convention."""
    stored = datetime.fromtimestamp(stored_nanos / 1_000_000_000, tz=UTC)
    local = stored.replace(tzinfo=None, microsecond=0).replace(tzinfo=wall_clock)
    return int(local.timestamp())


def latest_actual_seconds(table: str, wall_clock: ZoneInfo) -> int | None:
    newest = max_timestamp_ns(table)
    if newest is None:
        return None
    return stored_to_actual_seconds(newest, wall_clock)


class Writer:
    """One Parquet writer shared by both markets."""

    def __init__(self, wall_clock: ZoneInfo):
        self.wall_clock = wall_clock
        self.sender: Sender | None = None
        self.written = 0

    def connect(self) -> Sender:
        if self.sender is None:
            self.sender = Sender()
            self.sender.establish()
            log("Parquet writer ready")
        return self.sender

    def write(self, symbol: str, table: str, rows: list[object]) -> None:
        if not rows:
            return
        sender = self.connect()
        try:
            for row in rows:
                common = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
                if table == "xalusd_30m":
                    columns = {
                        **common,
                        "tick_volume": int(row["tick_volume"]),
                        "spread": int(row["spread"]),
                        "real_volume": int(row["real_volume"]),
                    }
                    tags = {"symbol": symbol}
                else:
                    columns = {**common, "volume": float(row["tick_volume"])}
                    tags = None
                sender.row(
                    table,
                    symbols=tags,
                    columns=columns,
                    at=TimestampNanos(
                        actual_to_stored_ns(int(row["time"]), self.wall_clock)
                    ),
                )
            sender.flush()
            self.written += len(rows)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.sender is None:
            return
        try:
            self.sender.flush()
            self.sender.close()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
        self.sender = None


def completed_rates(
    symbol: str,
    timeframe: str,
    last_actual: int | None,
    max_backfill_days: int,
) -> list[object]:
    mt5_timeframe, step = TIMEFRAMES[timeframe]
    now = datetime.now(tz=UTC)
    floor = int((now - timedelta(days=max_backfill_days)).timestamp())
    start_seconds = max(
        (last_actual + step) if last_actual is not None else floor,
        floor,
    )
    rates = mt5.copy_rates_range(
        symbol,
        mt5_timeframe,
        datetime.fromtimestamp(start_seconds, tz=UTC),
        now,
    )
    if rates is None:
        raise RuntimeError(f"MT5 rates failed for {symbol}: {mt5.last_error()}")
    now_seconds = int(now.timestamp())
    return [
        row
        for row in rates
        if int(row["time"]) >= start_seconds
        and int(row["time"]) + step <= now_seconds
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", nargs="+", default=["XALUSD", "XNGUSD"])
    parser.add_argument("--table", nargs="+", default=["xalusd_30m", "xngusd_1m"])
    parser.add_argument(
        "--timeframe", nargs="+", choices=sorted(TIMEFRAMES), default=["M30", "M1"]
    )
    parser.add_argument("--terminal", type=Path, default=DEFAULT_TERMINAL)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-backfill-days", type=int, default=7)
    parser.add_argument("--once", action="store_true", help="catch up once, then exit")
    args = parser.parse_args()
    lengths = (len(args.symbol), len(args.table), len(args.timeframe))
    if len(set(lengths)) != 1:
        parser.error(
            "--symbol, --table, and --timeframe must contain the same number of values"
        )
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    if args.max_backfill_days < 1:
        parser.error("--max-backfill-days must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    try:
        wall_clock = ZoneInfo(args.timezone)
        feeds = [
            (symbol.upper(), safe_table(table), timeframe)
            for symbol, table, timeframe in zip(
                args.symbol, args.table, args.timeframe
            )
        ]
    except Exception as error:  # noqa: BLE001 - report invalid CLI configuration
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    if not args.terminal.is_file():
        print(f"MT5 terminal not found: {args.terminal}", file=sys.stderr)
        return 1
    if not mt5.initialize(path=str(args.terminal)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    stop = Event()

    def request_stop(*_: object) -> None:
        log("shutting down")
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), request_stop)
            except (ValueError, OSError):
                pass

    writer = Writer(wall_clock)
    last: dict[str, int | None] = {}
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account is unavailable: {mt5.last_error()}")
        log(f"connected to MT5 account {account.login} on {account.server} (read-only feed)")
        for symbol, table, timeframe in feeds:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(f"MT5 symbol is unavailable: {symbol}")
            last[table] = latest_actual_seconds(table, wall_clock)
            log(f"configured {symbol} {timeframe} -> {table}")

        while not stop.is_set():
            for symbol, table, timeframe in feeds:
                try:
                    rows = completed_rates(
                        symbol, timeframe, last[table], args.max_backfill_days
                    )
                    if not rows:
                        continue
                    writer.write(symbol, table, rows)
                    last[table] = int(rows[-1]["time"])
                    stamp = datetime.fromtimestamp(last[table], tz=UTC)
                    log(
                        f"{symbol} {timeframe} -> {table}: wrote {len(rows)} bar(s), "
                        f"latest {stamp:%Y-%m-%d %H:%M} UTC"
                    )
                except Exception as error:  # noqa: BLE001 - retry the feed next poll
                    log(f"{symbol} -> {table}: {type(error).__name__}: {error}")
            if args.once:
                break
            stop.wait(args.poll_seconds)
    except Exception as error:  # noqa: BLE001
        print(f"Exness stream failed: {error}", file=sys.stderr)
        return 1
    finally:
        writer.close()
        mt5.shutdown()
    log(f"stopped after writing {writer.written} bar(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
