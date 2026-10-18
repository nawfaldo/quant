#!/usr/bin/env python3
"""Publish the forming Exness MT5 candle for the web chart.

    py tools/exness_live_candle.py                     # whatever the web chart shows
    py tools/exness_live_candle.py --symbol US500      # ...plus always US500

Follows the web app: while a chart is open on a symbol, the web app lists it
in `data/live/watch.json` (with a heartbeat), and this script publishes only
that symbol, switching when the chart does. `--symbol` adds one that is
published even with no chart open. Exness MT5 symbol names are the store's table names in upper case, so
`EURUSD` is published as `exness_eurusd_1m`.

For each published symbol it writes two small JSON files under `data/live/`,
each replaced atomically:

- `<name>.history.json` - the last `--bars` completed candles, rewritten once
  per new minute, so the chart has no gap after the Parquet history ends.
- `<name>.live.json` - the previous candle and the forming one, rewritten
  whenever the forming candle changes (polled every `--poll-seconds`).

Times use the store's convention: New York wall clock written as if it were
UTC, so the chart lines up with the Parquet bars.

This reads market data only. It never sends, changes, or closes an MT5 order.
It shares the terminal with the execution bridge through the IPC mutex, and
each poll is a single extension call.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import MetaTrader5 as _mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)

DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
LIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "live"
WATCH_PATH = LIVE_DIR / "watch.json"
WATCH_STALE_SECONDS = 30  # the web app refreshes the file every few seconds


def log(message: str) -> None:
    stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def stored_seconds(actual_seconds: int, wall_clock: ZoneInfo) -> int:
    """Real UTC instant -> New York wall clock reinterpreted as UTC."""
    local = datetime.fromtimestamp(actual_seconds, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp())


def to_bars(rates: object, wall_clock: ZoneInfo) -> list[dict[str, float | int]]:
    return [
        {
            "time": stored_seconds(int(r["time"]), wall_clock),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
        }
        for r in rates
    ]


def write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def watched_symbols() -> set[str]:
    """Symbols the web app currently shows, if its heartbeat is recent."""
    try:
        watch = json.loads(WATCH_PATH.read_text(encoding="utf-8"))
        if time.time() - float(watch.get("updated", 0)) > WATCH_STALE_SECONDS:
            return set()
        return {str(s).upper() for s in watch.get("symbols", []) if str(s).isalnum()}
    except (OSError, ValueError, TypeError):
        return set()


class Feed:
    """One symbol's live and history files."""

    def __init__(self, symbol: str, name: str):
        self.symbol = symbol
        self.history_path = LIVE_DIR / f"{name}.history.json"
        self.live_path = LIVE_DIR / f"{name}.live.json"
        self.history_minute: int | None = None
        self.last_live = ""

    def poll(self, bars: int, wall_clock: ZoneInfo) -> None:
        # Position 0 is the forming candle, 1 the one that just closed.
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 2)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 rates failed for {self.symbol}: {mt5.last_error()}")
        forming = int(rates[-1]["time"])

        if forming != self.history_minute:
            closed = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 1, bars)
            if closed is None:
                raise RuntimeError(f"MT5 history failed for {self.symbol}: {mt5.last_error()}")
            write_json(self.history_path, {"bars": to_bars(closed, wall_clock)})
            self.history_minute = forming

        live = json.dumps(to_bars(rates, wall_clock), separators=(",", ":"))
        if live != self.last_live:
            write_json(self.live_path, {"bars": json.loads(live)})
            self.last_live = live


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default=None, help="also publish this symbol at all times")
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--terminal", type=Path, default=DEFAULT_TERMINAL)
    parser.add_argument("--timezone", default="America/New_York")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    wall_clock = ZoneInfo(args.timezone)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

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

    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account is unavailable: {mt5.last_error()}")
        always = {args.symbol.upper()} if args.symbol else set()
        log(f"publishing the web chart's symbol{' and ' + args.symbol.upper() if always else ''} from {account.server} (read-only) to {LIVE_DIR}")

        feeds: dict[str, Feed] = {}
        unavailable: dict[str, float] = {}  # symbol -> when MT5 refused it
        while not stop.is_set():
            started = time.monotonic()
            wanted = always | watched_symbols()
            for symbol in wanted - feeds.keys():
                if started - unavailable.get(symbol, -1e9) < 60:
                    continue  # retry a refused symbol once a minute, not every poll
                if mt5.symbol_select(symbol, True):
                    unavailable.pop(symbol, None)
                    feeds[symbol] = Feed(symbol, f"exness_{symbol.lower()}_1m")
                    log(f"now publishing {symbol}")
                else:
                    unavailable[symbol] = started
                    log(f"MT5 symbol is unavailable: {symbol}")
            for symbol in feeds.keys() - wanted:
                del feeds[symbol]
                log(f"stopped publishing {symbol}")
            for feed in feeds.values():
                try:
                    feed.poll(args.bars, wall_clock)
                except Exception as error:  # noqa: BLE001 - retry on the next poll
                    log(f"poll failed: {error}")
            stop.wait(max(0.0, args.poll_seconds - (time.monotonic() - started)))
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
