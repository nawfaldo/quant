"""Continuously stream Binance spot 1-minute klines into QuestDB.

The companion to ``binance_fetch&import_1m.py``: that script backfills a closed
date range, this one keeps the table live from now on. Both write the same nine
columns to the same table and both store timestamps the same way, so a table can
be backfilled once and then handed to this process indefinitely.

TIMESTAMPS. Binance emits real UTC instants. This repository stores exchange-local
wall-clock labelled as UTC (see AGENT.md), and ``btc_1m`` specifically holds *New
York* wall-clock — verified by minute-return correlation against ``nq_1m``: 0.520
at zero shift and ~0.003 at +/-1h. ``stored_timestamp_ms`` below reproduces the
backfill script's conversion exactly. Writing raw UTC would silently shift every
bar four or five hours (DST-dependent) against the rest of the database, and no
error would ever be raised.

ONLY CLOSED CANDLES ARE WRITTEN. The kline stream republishes the in-progress
minute several times a second with a moving close. Those carry ``k.x == false``
and are dropped: a strategy that saw them would be reading the future's open as
this minute's close.

GAPS. Every disconnect, and every startup, is followed by a REST backfill from
the last row already in the table up to the current minute -- however far back
that is, because a bounded catch-up leaves a hole the live path never revisits
(see ``catch_up``). ``--max-backfill-days`` bounds only the bootstrap of an
empty table. So a dropped connection self-heals rather than leaving a hole. The stream and the backfill
both go through the same idempotent write path, and QuestDB deduplicates on the
designated timestamp when the table is configured for it — overlap is harmless.

NETWORK. Binance endpoints are blocked on this connection unless Warp is on; the
symptom is a silent timeout at connect rather than an HTTP error. The preflight
below fails loudly with that hint instead of retrying forever.

    python tools/binance_stream_1m.py --symbol BTCUSDT --table btc_1m

Multiple matching symbols and tables can share one process. Values are paired
by position:

    python tools/binance_stream_1m.py \
        --symbol ETHUSDT ETHBTC BTCUSDT \
        --table ethusd_1m ethbtc_1m btc_1m
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import websockets

from parquet_writer import Sender, TimestampNanos, max_timestamp_ns


UTC = timezone.utc
MINUTE_MS = 60_000
REST_URL = "https://data-api.binance.vision/api/v3/klines"
STREAM_URL = "wss://stream.binance.com:9443/ws"
#: Binance caps a klines request at 1000 rows.
REST_LIMIT = 1000


#: Kline array positions, per the Binance REST and websocket payloads.
COLUMN_ORDER = (
    "open", "high", "low", "close", "volume",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
)


def log(message: str) -> None:
    stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def stored_timestamp_ms(actual_ms: int, wall_clock: ZoneInfo) -> int:
    """Real UTC instant -> wall-clock fields reinterpreted as UTC.

    Byte-for-byte the same transform as `binance_fetch&import_1m.py`, so a
    streamed row and a backfilled row for the same minute land on one timestamp.
    """
    local = datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock)
    naive = local.replace(tzinfo=UTC)
    return int(naive.timestamp() * 1000)


def parse_kline(values: list) -> tuple[int, dict[str, float]]:
    """`(open time ms, columns)` from a REST kline row."""
    return int(values[0]), {
        "open": float(values[1]),
        "high": float(values[2]),
        "low": float(values[3]),
        "close": float(values[4]),
        "volume": float(values[5]),
        "quote_volume": float(values[7]),
        "trades": int(values[8]),
        "taker_buy_base": float(values[9]),
        "taker_buy_quote": float(values[10]),
    }


def parse_stream_kline(k: dict) -> tuple[int, dict[str, float]]:
    """`(open time ms, columns)` from a websocket kline payload."""
    return int(k["t"]), {
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "quote_volume": float(k["q"]),
        "trades": int(k["n"]),
        "taker_buy_base": float(k["V"]),
        "taker_buy_quote": float(k["Q"]),
    }


def last_stored_ms(table: str, wall_clock: ZoneInfo) -> int | None:
    """Newest stored minute, converted back to a real UTC instant.

    Returned as the *actual* instant so it can be handed straight to the REST
    endpoint, which only speaks real UTC.
    """
    try:
        newest = max_timestamp_ns(table)
    except Exception as error:  # noqa: BLE001 - table may not exist yet
        log(f"could not read {table}: {error}")
        return None
    if newest is None:
        return None
    stored_ms = newest // 1_000_000
    # Invert stored_timestamp_ms: read the stored fields as local wall clock.
    naive = datetime.fromtimestamp(stored_ms / 1000, tz=UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=wall_clock).timestamp() * 1000)


def preflight(symbol: str) -> None:
    """Fail loudly, with the Warp hint, rather than retrying a blocked host."""
    try:
        params = urlencode({"symbol": symbol, "interval": "1m", "limit": 1})
        with urlopen(f"{REST_URL}?{params}", timeout=15) as response:
            json.load(response)
    except Exception as error:  # noqa: BLE001
        raise SystemExit(
            f"cannot reach Binance ({error}).\n"
            "This connection blocks exchange endpoints unless Warp is on — turn "
            "it on and retry before debugging this script."
        ) from error


def rest_backfill(symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, dict]]:
    """Closed klines in `[start_ms, end_ms)`, oldest first."""
    out = []
    cursor = start_ms
    while cursor < end_ms:
        params = urlencode({
            "symbol": symbol, "interval": "1m",
            "startTime": cursor, "endTime": end_ms - 1, "limit": REST_LIMIT,
        })
        with urlopen(f"{REST_URL}?{params}", timeout=30) as response:
            batch = json.load(response)
        if not batch:
            break
        for values in batch:
            open_ms, columns = parse_kline(values)
            if open_ms + MINUTE_MS <= end_ms:
                out.append((open_ms, columns))
        cursor = int(batch[-1][0]) + MINUTE_MS
        if len(batch) < REST_LIMIT:
            break
        time.sleep(0.1)          # stay well inside the public rate limit
    return out


class Writer:
    """Buffered ILP writer that reconnects on failure."""

    def __init__(self, table: str, wall_clock: ZoneInfo):
        self.table = table
        self.wall_clock = wall_clock
        self.sender: Sender | None = None
        self.written = 0

    def _connect(self) -> Sender:
        if self.sender is None:
            self.sender = Sender()
            self.sender.establish()
            log(f"Parquet writer ready for {self.table}")
        return self.sender

    def write(self, rows: list[tuple[int, dict]]) -> None:
        if not rows:
            return
        try:
            sender = self._connect()
            for open_ms, columns in rows:
                stored_ms = stored_timestamp_ms(open_ms, self.wall_clock)
                sender.row(
                    self.table,
                    columns={name: columns[name] for name in COLUMN_ORDER},
                    at=TimestampNanos(stored_ms * 1_000_000),
                )
            sender.flush()
            self.written += len(rows)
        except Exception as error:  # noqa: BLE001 - drop the sender and retry next time
            log(f"Parquet write to {self.table} failed ({error}); retrying on next batch")
            try:
                if self.sender is not None:
                    self.sender.close()
            except Exception:  # noqa: BLE001
                pass
            self.sender = None
            raise

    def close(self) -> None:
        if self.sender is not None:
            try:
                self.sender.flush()
                self.sender.close()
            except Exception:  # noqa: BLE001
                pass
            self.sender = None


def catch_up(symbol: str, table: str, writer: Writer, wall_clock: ZoneInfo,
             max_days: int) -> None:
    """Fill everything between the table's last row and the current minute.

    `max_days` BOUNDS A BOOTSTRAP, NOT A CATCH-UP. It used to floor the start of
    every backfill, which is right for an EMPTY table -- walking years of
    archive over this connection is `binance_fetch&import_1m.py`'s job -- and
    silently wrong for a populated one. A table more than `max_days` stale
    resumed at the floor instead of at its own last row, so the minutes in
    between were never requested; the stream then wrote past them, and every
    later catch-up measures its gap from the NEW maximum, so the hole is
    permanent and nothing in the live path ever revisits it. The floor now
    applies only when there is no last row to resume from.
    """
    now_ms = int(time.time() * 1000) // MINUTE_MS * MINUTE_MS
    last = last_stored_ms(table, wall_clock)
    start = last + MINUTE_MS if last is not None else now_ms - max_days * 86_400_000
    if start >= now_ms:
        log(f"{symbol} -> {table}: no gap to backfill")
        return now_ms
    missing = (now_ms - start) // MINUTE_MS
    log(f"{symbol} -> {table}: backfilling {missing} minute(s) from "
        f"{datetime.fromtimestamp(start / 1000, tz=UTC):%Y-%m-%d %H:%M} UTC")
    rows = rest_backfill(symbol, start, now_ms)
    writer.write(rows)
    log(f"{symbol} -> {table}: backfilled {len(rows)} row(s)")
    # THE MINUTE STILL IN FLIGHT IS NOT COVERED AND CANNOT BE. `now_ms` is the
    # start of the current minute and REST only serves closed klines, so
    # everything from here on is the websocket's job -- see `stream`, which
    # comes back for this boundary once it knows which minute it caught first.
    return now_ms


async def stream(symbol: str, table: str, writer: Writer, wall_clock: ZoneInfo,
                 max_days: int, stop: asyncio.Event) -> None:
    endpoint = f"{STREAM_URL}/{symbol.lower()}@kline_1m"
    backoff = 1
    while not stop.is_set():
        try:
            covered_to = catch_up(symbol, table, writer, wall_clock, max_days)
            async with websockets.connect(endpoint, ping_interval=20,
                                          ping_timeout=20) as socket:
                log(f"streaming {symbol} 1m -> {table}")
                backoff = 1
                bridged = False
                while not stop.is_set():
                    raw = await asyncio.wait_for(socket.recv(), timeout=120)
                    payload = json.loads(raw)
                    kline = payload.get("k")
                    # `x` marks the candle closed; in-progress republishes carry a
                    # moving close and must never reach the table.
                    if not kline or not kline.get("x"):
                        continue
                    open_ms, columns = parse_stream_kline(kline)
                    # BRIDGE THE CONNECT ITSELF, ONCE. `catch_up` runs before the
                    # socket is open and can only cover closed minutes, so every
                    # minute between its cutoff and the first kline this
                    # connection actually catches belongs to nobody. Neither pass
                    # can find it afterwards: both resume from the table's MAXIMUM,
                    # so an interior hole is invisible to them and permanent.
                    #
                    # ONE MINUTE IS ENOUGH TO COST A TRADE. On 2026-09-09 a feed
                    # restart at 14:24:20 left ETHUSD missing 14:24 alone; the
                    # server's cursor then sat on that hole for the five minutes
                    # `LATE_BAR_GRACE_SECONDS` allows, and refused
                    # `ethusd_pullback`'s 14:31 entry as a stale catch-up bar.
                    if not bridged:
                        bridged = True
                        if covered_to is not None and open_ms > covered_to:
                            missed = rest_backfill(symbol, covered_to, open_ms)
                            if missed:
                                writer.write(missed)
                                log(f"{symbol} -> {table}: bridged "
                                    f"{len(missed)} minute(s) lost while connecting")
                    writer.write([(open_ms, columns)])
                    stamp = datetime.fromtimestamp(open_ms / 1000, tz=UTC)
                    log(f"{stamp:%Y-%m-%d %H:%M} UTC  close={columns['close']:.2f}  "
                        f"vol={columns['volume']:.3f}  (total {writer.written})")
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - any drop reconnects and re-heals
            if stop.is_set():
                break
            log(f"{symbol} -> {table}: stream error "
                f"({type(error).__name__}: {error}); "
                f"reconnecting in {backoff}s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--symbol", nargs="+", default=["BTCUSDT"],
        help="one or more Binance spot symbols, paired by position with --table",
    )
    parser.add_argument(
        "--table", nargs="+", default=["btc_1m"],
        help="one or more target QuestDB tables, paired by position with --symbol",
    )
    parser.add_argument(
        "--timezone", default="America/New_York",
        help="wall-clock timezone stored as fake UTC (default: America/New_York, "
             "matching btc_1m and the backfill script)",
    )
    parser.add_argument(
        "--max-backfill-days", type=int, default=7,
        help="how far back to BOOTSTRAP an empty table; a table that already "
             "has rows is always caught up from its own last row, however "
             "stale (default: 7)",
    )
    args = parser.parse_args()

    if len(args.symbol) != len(args.table):
        parser.error(
            "--symbol and --table must contain the same number of values "
            f"(received {len(args.symbol)} symbol(s) and {len(args.table)} table(s))"
        )
    pairs = list(zip(args.symbol, args.table))

    try:
        wall_clock = ZoneInfo(args.timezone)
    except Exception as error:  # noqa: BLE001
        raise SystemExit(f"invalid IANA timezone {args.timezone!r}: {error}") from error

    for symbol, _ in pairs:
        preflight(symbol)
    log(f"Binance reachable; storing {args.timezone} wall-clock as fake UTC")

    writers = [Writer(table, wall_clock) for _, table in pairs]
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def request_stop(*_):
        log("shutting down")
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), request_stop)
            except (ValueError, OSError):
                pass

    try:
        loop.run_until_complete(
            asyncio.gather(*(
                stream(symbol, table, writer, wall_clock,
                       args.max_backfill_days, stop)
                for (symbol, table), writer in zip(pairs, writers)
            ))
        )
    except KeyboardInterrupt:
        pass
    finally:
        for writer in writers:
            writer.close()
        loop.close()
        summary = ", ".join(
            f"{symbol}->{table}: {writer.written} row(s)"
            for (symbol, table), writer in zip(pairs, writers)
        )
        log(f"stopped ({summary})")


if __name__ == "__main__":
    main()
