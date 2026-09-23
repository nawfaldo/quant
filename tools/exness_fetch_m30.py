"""Export all available Exness M30 bars for selected symbols to Parquet.

MetaTrader stores one-minute history and derives larger timeframes from it. The
broker decides how far its server history reaches, so the exporter asks for all
locally available M30 bars and then applies the requested date bounds itself.
"""

from __future__ import annotations

# Must precede pandas, which hangs on this machine's broken WMI at import.
import _prelude  # noqa: F401  -- see tools/_prelude.py

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as _mt5
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5

import parquet_writer as pw

mt5 = synchronized_mt5(_mt5)


DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
DEFAULT_SYMBOLS = ("XALUSD", "XNIUSD", "XZNUSD")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--from-date", default="2018-01-01")
    parser.add_argument(
        "--to-date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="inclusive UTC date (default: today)",
    )
    parser.add_argument("--terminal", type=Path, default=DEFAULT_TERMINAL)
    parser.add_argument("--output", type=Path, default=pw.VENDOR_DIR / "exness")
    return parser.parse_args()


def export_symbol(symbol: str, start: pd.Timestamp, end: pd.Timestamp, output: Path) -> Path:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"cannot select {symbol}: {mt5.last_error()}")

    terminal = mt5.terminal_info()
    limit = min(terminal.maxbars if terminal else 100_000, 200_000)
    rates = None
    for attempt in range(10):
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, limit)
        if rates is not None and len(rates) > 0:
            break
        time.sleep(1.0)
    if rates is None:
        raise RuntimeError(f"cannot fetch {symbol}: {mt5.last_error()}")

    frame = pd.DataFrame(rates)
    if frame.empty:
        raise RuntimeError(f"Exness returned no M30 bars for {symbol}")

    frame.insert(0, "symbol", symbol)
    frame["timestamp"] = pd.to_datetime(frame.pop("time"), unit="s", utc=True)
    frame = frame[
        (frame["timestamp"] >= start)
        & (frame["timestamp"] < end)
        & (frame["timestamp"] + pd.Timedelta(minutes=30) <= pd.Timestamp.now(tz="UTC"))
    ].copy()
    if frame.empty:
        raise RuntimeError(f"Exness has no completed {symbol} M30 bars in the requested range")

    columns = [
        "timestamp", "symbol", "open", "high", "low", "close",
        "tick_volume", "spread", "real_volume",
    ]
    frame = frame[columns].sort_values("timestamp").drop_duplicates("timestamp")
    destination = output / f"exness_{symbol.lower()}_m30.parquet"
    output.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(destination, index=False, compression="zstd")
    print(
        f"{symbol}: {len(frame):,} bars, "
        f"{frame.iloc[0]['timestamp'].isoformat()} through "
        f"{frame.iloc[-1]['timestamp'].isoformat()} -> {destination}"
    )
    return destination


def main() -> int:
    args = arguments()
    start = pd.Timestamp(args.from_date, tz="UTC")
    end = pd.Timestamp(args.to_date, tz="UTC") + pd.Timedelta(days=1)
    if end <= start:
        raise SystemExit("--to-date must not precede --from-date")

    if not mt5.initialize(path=str(args.terminal)):
        raise SystemExit(f"MetaTrader initialization failed: {mt5.last_error()}")
    try:
        for symbol in args.symbols:
            export_symbol(symbol.upper(), start, end, args.output)
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
