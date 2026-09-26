#!/usr/bin/env python3
"""Import TradingView candles for IDX stocks that sit in BOTH LQ45 and ISSI.

The universe comes from the two IDX constituent announcements (the .xlsx
"Lampiran" files). A code counts only if it is a current constituent of both:
rows under "Konstituen yang keluar dari penghitungan indeks" are exits and are
skipped.

SOURCE IS TRADINGVIEW, NOT YAHOO. Yahoo has no 4h interval and its 1h data
reaches back only ~730 days. TradingView without a login (via the unofficial
`tvdatafeed` scraper) serves at most ~5,000-6,000 bars per timeframe: 1h
reaches back to about 2023-01, 4h (two bars a day, 09:00 and 13:00) to 2016.
Bars are stamped at their start, Jakarta time.

Rows go to `idx_<code>_<timeframe>`, Asia/Jakarta wall-clock encoded as fake UTC, same
columns as `yfinance_fetch&import_timeframe.py`. Rows
at an existing timestamp are replaced, so rerunning keeps extending the
history and refreshes a partial last bar. Cash dividends (from Yahoo) go to
`idx_<code>_dividends` on every run.

    py tradingview_idx.py --timeframe 1h [--start 2018-01-01] --lq45 <LQ45.xlsx> --issi <ISSI.xlsx>
    py tradingview_idx.py --timeframe 1m --codes TLKM,ADRO
"""

from __future__ import annotations

import _prelude  # noqa: F401  -- must precede pandas; see tools/_prelude.py

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pandas as pd
import yfinance as yf
from tvDatafeed import Interval, TvDatafeed

import parquet_writer as pw

JAKARTA = "Asia/Jakarta"
EXIT_HEADER = "Konstituen yang keluar"
ATTEMPTS = 3
N_BARS = 20_000  # more than TradingView serves; it returns what it has
TIMEFRAMES = {
    "1m": Interval.in_1_minute, "3m": Interval.in_3_minute,
    "5m": Interval.in_5_minute, "15m": Interval.in_15_minute,
    "30m": Interval.in_30_minute, "45m": Interval.in_45_minute,
    "1h": Interval.in_1_hour, "2h": Interval.in_2_hour,
    "3h": Interval.in_3_hour, "4h": Interval.in_4_hour,
    "1d": Interval.in_daily, "1w": Interval.in_weekly, "1mo": Interval.in_monthly,
}


def constituents(path: Path) -> set[str]:
    """Codes in the announcement's constituent table, excluding the exit list."""
    sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    codes: set[str] = set()
    for row in sheet.iter_rows(values_only=True):
        label = row[1] if len(row) > 1 else None
        if isinstance(label, str) and label.strip().startswith(EXIT_HEADER):
            break
        if isinstance(label, int) and isinstance(row[2], str):
            codes.add(row[2].strip().upper())
    if not codes:
        raise RuntimeError(f"no constituents found in {path}")
    return codes


def fetch(tv: TvDatafeed, code: str, timeframe: str) -> pd.DataFrame:
    bars = tv.get_hist(code, "IDX", interval=TIMEFRAMES[timeframe], n_bars=N_BARS)
    if bars is None or bars.empty:
        raise RuntimeError(f"TradingView returned no {timeframe} candles")
    # tvdatafeed stamps bars in this machine's local zone; pin them to Jakarta.
    local = datetime.now().astimezone().tzinfo
    bars.index = bars.index.tz_localize(local).tz_convert(JAKARTA)
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars.rename(columns=str.capitalize)


def store(code: str, table: str, bars: pd.DataFrame) -> None:
    sender = pw.Sender(dedup=True)
    for moment, bar in bars.iterrows():
        wall = moment.tz_localize(None)  # Jakarta wall-clock, stored as fake UTC
        sender.row(
            table,
            columns={
                "underlying": code, "osi": code,
                "open": float(bar.Open), "high": float(bar.High),
                "low": float(bar.Low), "close": float(bar.Close),
                "volume": int(bar.Volume),
            },
            at=pw.TimestampNanos(wall.value),
        )
    sender.flush()


def store_dividends(code: str) -> int:
    """Cash dividends from Yahoo into `idx_<code>_dividends`, keyed on ex-date.

    The price tables are unadjusted, so a backtest holding through an ex-date
    sees the price drop and must be credited the dividend to be honest.
    TradingView does not serve dividends; Yahoo does.
    """
    series = yf.Ticker(f"{code}.JK").dividends
    if series.empty:
        return 0
    sender = pw.Sender(dedup=True)
    for stamp, amount in series.items():
        ex_date = pd.Timestamp(stamp.date())  # midnight, Jakarta date as fake UTC
        sender.row(f"idx_{code.lower()}_dividends",
                   columns={"underlying": code, "amount": float(amount)},
                   at=pw.TimestampNanos(ex_date.value))
    sender.flush()
    return len(series)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lq45", type=Path)
    parser.add_argument("--issi", type=Path)
    parser.add_argument("--codes", help="comma-separated codes; skips the constituent files")
    parser.add_argument("--timeframe", choices=TIMEFRAMES, default="1h")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="drop bars before this YYYY-MM-DD (Jakarta date)")
    args = parser.parse_args()

    if args.codes:
        codes = sorted({c.strip().upper() for c in args.codes.split(",") if c.strip()})
        print(f"{len(codes)} codes given: {' '.join(codes)}", flush=True)
    elif args.lq45 and args.issi:
        codes = sorted(constituents(args.lq45) & constituents(args.issi))
        print(f"{len(codes)} codes in both LQ45 and ISSI: {' '.join(codes)}", flush=True)
    else:
        parser.error("give --codes, or both --lq45 and --issi")

    tv = TvDatafeed()
    failed = []
    for code in codes:
        table = f"idx_{code.lower()}_{args.timeframe}"
        try:
            for attempt in range(ATTEMPTS):
                try:
                    bars = fetch(tv, code, args.timeframe)
                    if args.start is not None:
                        bars = bars[bars.index.date >= args.start]
                    break
                except Exception:
                    # A dropped socket shows up as "no data" or a timeout; reconnect.
                    if attempt == ATTEMPTS - 1:
                        raise
                    time.sleep(5)
                    tv = TvDatafeed()
            store(code, table, bars)
            span = pw.table_span(table)
            dividends = store_dividends(code)
            print(f"{code:5} {len(bars):5} bars fetched -> {table} {span}, {dividends} dividends", flush=True)
        except Exception as exc:  # one bad ticker must not stop the batch
            failed.append(code)
            print(f"{code:5} FAILED: {exc}", file=sys.stderr, flush=True)
    if failed:
        print(f"Failed: {' '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
