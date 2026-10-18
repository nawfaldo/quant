#!/usr/bin/env python3
"""Keep every FundedNext book market's `fundednext_<broker>_1m` table live.

    py tools/fundednext_stream_1m.py

WHY. The 2026-10-18 FundedNext book (seal 9e0e0b402357) reads the broker's own
minute bars live, for two reasons:

  * NINE MARKETS HAVE NO VENDOR TABLE (xauusd, us2000, us30, cadjpy, nzdusd,
    euraud, usdchf, chfjpy, gbpaud), so their cells were SIGNALLED off
    FundedNext's minutes (`cfd_families.table_for`) and their live twins must
    be too -- `backtest::prepare::live_ohlcv_source` reads these tables.
  * EVERY CELL WAS SEALED UNDER `CFD_BROKER_HOURS=1`: a vendor candle the
    broker did not quote is dropped. Live asks this table whether the broker
    printed inside each candle before the candle is stepped.

The daemon `idk_market_live_data_feeds.py` imports these tables once a day for
the backtest; that is a day too late for both of the above, so this streams
them every few seconds instead.

THE MARKETS COME FROM THE BUILT REGISTRY (`fundednext_live_book`), never from a
list typed here, so a re-seated book cannot leave a market unfed.

FUNDEDNEXT'S MT5 EPOCH IS SERVER TIME, New York + 7h all year since
2024-07-08 (`exness_import_1m.FUNDEDNEXT_SERVER_AHEAD_OF_NY_HOURS`), so the
stored New York wall clock is the raw stamp minus seven hours.

Read-only against MT5, through the FundedNext terminal's own IPC mutex, so it
shares the terminal with the bridge. It never sends, changes or closes an order.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import MetaTrader5 as _mt5

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
sys.path.insert(0, str(WORKSPACE / "mt5"))
from ipc_lock import _NamedMutex, synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)
mt5._mutex = _NamedMutex(r"Local\QuantMetaTrader5IPC_fundednext")

from parquet_writer import Sender, TimestampNanos, max_timestamp_ns  # noqa: E402

TERMINAL = Path(r"C:\Program Files\FundedNext MT5 Terminal\terminal64.exe")
SERVER_AHEAD_OF_NY = 7 * 3600
POLL_SECONDS = 2.0
#: Bars asked for per poll: enough to bridge a restart of a few hours; a
#: longer gap is the daily importer's job.
BACKFILL_BARS = 600


def log(message: str) -> None:
    print(f"[{datetime.now(tz=timezone.utc):%H:%M:%S}] {message}", flush=True)


def book_symbols() -> dict[str, str]:
    """`{broker symbol: table}` for every market the built FundedNext book trades."""
    for name in ("fundednext_live_book.exe", "fundednext_live_book"):
        binary = WORKSPACE / "live_trade" / "target" / "release" / name
        if binary.is_file():
            break
    else:
        sys.exit("no live_trade/target/release/fundednext_live_book -- build it first")
    done = subprocess.run([str(binary)], capture_output=True, text=True, check=True,
                          cwd=WORKSPACE, timeout=60)
    rows = json.loads(done.stdout)["strategies"]
    return {row["symbol"]: f"fundednext_{row['symbol'].lower()}_1m" for row in rows}


def completed(symbol: str, after_server: int | None) -> list:
    """Closed M1 bars newer than `after_server` (server seconds)."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, BACKFILL_BARS)
    if rates is None or not len(rates):
        return []
    tick = mt5.symbol_info_tick(symbol)
    now = int(tick.time) if tick is not None else int(rates[-1]["time"]) + 60
    return [row for row in rates
            if (after_server is None or int(row["time"]) > after_server)
            and int(row["time"]) + 60 <= now]


def main() -> int:
    symbols = book_symbols()
    if not TERMINAL.is_file():
        print(f"FundedNext terminal not found: {TERMINAL}", file=sys.stderr)
        return 1
    if not mt5.initialize(path=str(TERMINAL)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1
    account = mt5.account_info()
    log(f"connected to {account.login} on {account.server} (read-only feed), "
        f"{len(symbols)} markets")
    stop = Event()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), lambda *_: stop.set())
            except (ValueError, OSError):
                pass
    last: dict[str, int | None] = {}
    for symbol, table in symbols.items():
        if not mt5.symbol_select(symbol, True):
            log(f"{symbol}: not available on this account")
            continue
        newest = max_timestamp_ns(table)
        last[symbol] = None if newest is None else newest // 1_000_000_000 + SERVER_AHEAD_OF_NY
    sender = Sender()
    sender.establish()
    try:
        while not stop.is_set():
            for symbol in list(last):
                table = symbols[symbol]
                try:
                    rows = completed(symbol, last[symbol])
                    for row in rows:
                        sender.row(table, columns={
                            "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"]),
                            "volume": float(row["tick_volume"]),
                            "spread": int(row["spread"]),
                            "real_volume": int(row["real_volume"])},
                            at=TimestampNanos((int(row["time"]) - SERVER_AHEAD_OF_NY)
                                              * 1_000_000_000))
                    if rows:
                        sender.flush()
                        last[symbol] = int(rows[-1]["time"])
                except Exception as error:  # noqa: BLE001 - retried next poll
                    log(f"{symbol}: {type(error).__name__}: {error}")
            stop.wait(POLL_SECONDS)
    finally:
        try:
            sender.flush()
            sender.close()
        except Exception:  # noqa: BLE001
            pass
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
