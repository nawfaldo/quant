"""CBOE VIX daily data ingestion script.

Fetches ^VIX daily bars from yfinance and stores them in the QuestDB table
`vix_1d`, following the same fake-UTC ET wall-clock convention as the rest of
the pipeline (naive New York timestamps written as if they were UTC).

Incremental: re-running only appends bars newer than the table's max timestamp.
"""

import logging
import argparse
import pandas as pd
import yfinance as yf

from fetch_yfinance import (
    get_max_timestamp,
    write_to_questdb,
    is_yfinance_candle_complete,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("fetch_vix")

TICKER = "^VIX"
TABLE = "vix_1d"
HISTORY_START = "1990-01-02"  # start of CBOE VIX history on Yahoo


def main() -> None:
    parser = argparse.ArgumentParser(description="VIX daily data collection.")
    parser.add_argument("--start", default=HISTORY_START, help="Full-history start date (YYYY-MM-DD).")
    args = parser.parse_args()

    now = pd.Timestamp.now(tz="America/New_York")
    t_max = get_max_timestamp(TABLE)

    if t_max is None:
        start_dt = pd.Timestamp(args.start, tz="America/New_York")
        logger.info(f"Table {TABLE} empty. Fetching full history from {start_dt.date()}.")
    else:
        start_dt = t_max
        logger.info(f"Incremental fetch starting at: {start_dt}")

    ticker = yf.Ticker(TICKER)
    df = ticker.history(start=start_dt, end=now, interval="1d")
    if df.empty:
        logger.info("No new data fetched.")
        return

    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York")
    else:
        df.index = df.index.tz_localize("America/New_York")

    if t_max is not None:
        df = df[df.index > t_max]

    # Drop today's still-forming candle (same 17:00 ET rule as the NQ fetcher).
    df = df[[is_yfinance_candle_complete(ts, "1d", now) for ts in df.index]]

    if df.empty:
        logger.info("All fetched rows were already in the database.")
        return

    # ^VIX has no volume; keep the column so the table schema matches the
    # other OHLCV tables the importer creates.
    df["Volume"] = df["Volume"].fillna(0).astype(int)

    write_to_questdb(TABLE, df)
    logger.info("VIX data collection completed successfully.")


if __name__ == "__main__":
    main()
