"""Yahoo Finance data ingestion and aggregation script.

Fetches historical data for multiple timeframes directly from yfinance,
clears the database (yf only) to start fresh, and stores the data in QuestDB.
"""

import socket
import logging
import argparse
from datetime import datetime
import pandas as pd
import yfinance as yf
import httpx

import config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("fetch_yfinance")


def drop_yfinance_tables() -> None:
    """Drop all yfinance tables in QuestDB to start fresh."""
    timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    url = f"http://{config.QUESTDB_HOST}:{config.QUESTDB_HTTP_PORT}/exec"
    
    for tf in timeframes:
        table_name = f"yf_{config.BASE_TABLE_NAME}_{tf}"
        query = f"DROP TABLE {table_name}"
        logger.info(f"Dropping table {table_name} if it exists...")
        try:
            response = httpx.get(url, params={"query": query}, timeout=10.0)
            if response.status_code == 200:
                logger.info(f"Successfully dropped table {table_name}.")
            elif response.status_code == 400:
                err_json = response.json()
                if "table does not exist" in err_json.get("error", ""):
                    logger.info(f"Table {table_name} does not exist. Skipping.")
                else:
                    logger.warning(f"Error response dropping table {table_name}: {err_json}")
            else:
                logger.warning(f"Unexpected status code {response.status_code} dropping table {table_name}: {response.text}")
        except Exception as e:
            logger.warning(f"Failed to drop table {table_name}: {e}")


def get_max_timestamp(table_name: str) -> pd.Timestamp | None:
    """Query QuestDB REST API for the maximum timestamp in a table.

    Returns a timezone-aware Timestamp (America/New_York) or None if the table is empty/doesn't exist.
    """
    query = f"SELECT max(timestamp) FROM {table_name}"
    url = f"http://{config.QUESTDB_HOST}:{config.QUESTDB_HTTP_PORT}/exec"

    try:
        response = httpx.get(url, params={"query": query}, timeout=10.0)
        if response.status_code == 400:
            err_json = response.json()
            if "table does not exist" in err_json.get("error", ""):
                logger.info(f"Table {table_name} does not exist. Treating as empty.")
                return None
            response.raise_for_status()

        response.raise_for_status()
        res_json = response.json()

        dataset = res_json.get("dataset", [])
        if dataset and dataset[0] and dataset[0][0] is not None:
            ts_str = dataset[0][0]
            # Parse ISO timestamp and strip timezone to get naive wall-clock time
            naive_ts = pd.to_datetime(ts_str).tz_localize(None)
            # Localize to America/New_York (representing the exchange time stored as fake-UTC)
            tz_ts = naive_ts.tz_localize("America/New_York")
            logger.info(f"Max timestamp for {table_name} found: {tz_ts}")
            return tz_ts
    except Exception as e:
        logger.warning(f"Could not query max timestamp for {table_name}: {e}. Treating as empty.")

    return None


def fetch_yfinance_data(interval: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    """Fetch historical data for config.TICKER with the given interval and date range from yfinance."""
    logger.info(f"Fetching {interval} data for {config.TICKER} from {start_dt} to {end_dt}")
    ticker = yf.Ticker(config.TICKER)

    # 1-minute data must be fetched in chunks due to yfinance constraints
    if interval == "1m":
        chunk_size = pd.Timedelta(days=7)
    else:
        chunk_size = end_dt - start_dt

    current_start = start_dt
    dfs = []

    while current_start < end_dt:
        current_end = min(current_start + chunk_size, end_dt)
        if current_start >= current_end:
            break
        logger.info(f"Downloading chunk: {current_start} to {current_end}")

        try:
            df_chunk = ticker.history(start=current_start, end=current_end, interval=interval)
            if not df_chunk.empty:
                logger.info(f"Downloaded {len(df_chunk)} rows.")
                dfs.append(df_chunk)
            else:
                logger.info("Chunk was empty.")
        except Exception as e:
            logger.error(f"Error downloading chunk {current_start} to {current_end}: {e}")

        current_start = current_end

    if not dfs:
        return pd.DataFrame()

    # Combine and drop duplicates
    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    # Normalize index to America/New_York timezone
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York")
    else:
        df.index = df.index.tz_localize("America/New_York")

    return df


def write_to_questdb(table_name: str, df: pd.DataFrame) -> int:
    """Stream a pandas DataFrame into QuestDB using the InfluxDB Line Protocol (ILP) over TCP."""
    if df.empty:
        logger.info(f"No new rows to write to {table_name}.")
        return 0

    logger.info(f"Writing {len(df)} rows to {table_name} via ILP...")

    # Strip timezone to get naive wall-clock time
    naive_index = df.index.tz_localize(None)
    # Convert naive timestamps to nanoseconds epoch
    nanos = naive_index.to_numpy().astype("datetime64[ns]").view("int64")

    # Connect via TCP socket to QuestDB ILP port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((config.QUESTDB_HOST, config.QUESTDB_ILP_PORT))

        lines = []
        for i, (idx, row) in enumerate(df.iterrows()):
            # Format row values (all values match questdb_csv_importer row names)
            line = (
                f"{table_name} "
                f"open={row['Open']},high={row['High']},"
                f"low={row['Low']},close={row['Close']},"
                f"volume={int(row['Volume'])}i "
                f"{nanos[i]}\n"
            )
            lines.append(line)

            # Send in batches of 1000 lines
            if len(lines) >= 1000:
                s.sendall("".join(lines).encode("utf-8"))
                lines = []

        if lines:
            s.sendall("".join(lines).encode("utf-8"))

        logger.info(f"Successfully wrote {len(df)} rows to {table_name}.")
        return len(df)
    except Exception as e:
        logger.error(f"Failed to write to QuestDB table {table_name}: {e}")
        raise
    finally:
        s.close()


def query_table_data(table_name: str, start_dt: pd.Timestamp | None) -> pd.DataFrame:
    """Fetch records from a QuestDB table starting from start_dt."""
    query = f"SELECT timestamp, open, high, low, close, volume FROM {table_name}"
    if start_dt is not None:
        formatted_ts = start_dt.tz_localize(None).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        query += f" WHERE timestamp >= '{formatted_ts}'"

    url = f"http://{config.QUESTDB_HOST}:{config.QUESTDB_HTTP_PORT}/exec"

    try:
        response = httpx.get(url, params={"query": query}, timeout=30.0)
        response.raise_for_status()
        res_json = response.json()

        columns = [col["name"] for col in res_json["columns"]]
        dataset = res_json.get("dataset", [])

        if not dataset:
            return pd.DataFrame()

        df = pd.DataFrame(dataset, columns=columns)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)

        # Rename to match standard uppercase Open, High, Low, Close, Volume
        df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        }, inplace=True)

        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York")
        else:
            df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
        return df
    except Exception as e:
        logger.error(f"Error querying data from {table_name}: {e}")
        return pd.DataFrame()


def is_yfinance_candle_complete(start_time: pd.Timestamp, interval: str, now: pd.Timestamp) -> bool:
    """Determine if a candle is fully completed based on current exchange time."""
    if interval == "1d":
        # 1d candle starting at 00:00 is complete after 17:00 (5 PM) Exchange Time
        return now >= start_time + pd.Timedelta(hours=17)

    # Intraday candles: complete if now is at or after start_time + duration
    duration_map = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
    }
    minutes = duration_map.get(interval)
    if minutes is not None:
        return now >= start_time + pd.Timedelta(minutes=minutes)
    return True


def main() -> None:
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Yahoo Finance data collection and storage.")
    parser.add_argument("--skip-clear", action="store_true", help="Skip clearing database tables before fetching.")
    args = parser.parse_args()

    # 1. Clear database tables first if not requested to skip
    if not args.skip_clear:
        drop_yfinance_tables()

    now = pd.Timestamp.now(tz="America/New_York")

    # Timeframe limits in days
    lookback_days = {
        "1m": 29,
        "5m": 59,
        "15m": 59,
        "30m": 59,
        "1h": 365,
        "1d": 365,
    }

    # 2. Process direct yfinance timeframes
    timeframes = ["1m", "5m", "15m", "30m", "1h", "1d"]

    for tf in timeframes:
        target_table = f"yf_{config.BASE_TABLE_NAME}_{tf}"
        logger.info(f"--- Processing timeframe {tf} ---")

        # Determine start date
        limit_dt = now - pd.Timedelta(days=lookback_days[tf])
        T_max_tf = get_max_timestamp(target_table)

        if T_max_tf is None:
            start_dt = limit_dt
            logger.info(f"Table empty. Fetching full history starting: {start_dt}")
        else:
            start_dt = max(T_max_tf, limit_dt)
            logger.info(f"Incremental fetch starting at: {start_dt}")

        df = fetch_yfinance_data(tf, start_dt, now)

        if df.empty:
            logger.info(f"No new data fetched for {tf}.")
            continue

        # Filter out existing data to prevent duplicates
        if T_max_tf is not None:
            df = df[df.index > T_max_tf]

        if df.empty:
            logger.info(f"All fetched rows were already in database for {tf}.")
            continue

        # Filter out incomplete candles
        valid_rows = []
        for ts, row in df.iterrows():
            if is_yfinance_candle_complete(ts, tf, now):
                row.name = ts
                valid_rows.append(row)

        if not valid_rows:
            logger.info(f"No complete candles to write for {tf}.")
            continue

        df_to_write = pd.DataFrame(valid_rows)
        write_to_questdb(target_table, df_to_write)

    # 3. Process resampled 4h timeframe from 1h data
    tf_4h = "4h"
    target_table_4h = f"yf_{config.BASE_TABLE_NAME}_{tf_4h}"
    logger.info(f"--- Processing resampled timeframe {tf_4h} ---")

    limit_dt_4h = now - pd.Timedelta(days=365)
    T_max_4h = get_max_timestamp(target_table_4h)

    # If incremental, we start querying 1h data from slightly before the last 4h timestamp
    if T_max_4h is None:
        query_start = limit_dt_4h
        logger.info("Table empty. Querying full 1h history to resample.")
    else:
        query_start = max(T_max_4h - pd.Timedelta(hours=4), limit_dt_4h)
        logger.info(f"Incremental aggregation starting from: {query_start}")

    # Query 1h data from QuestDB
    df_1h = query_table_data(f"yf_{config.BASE_TABLE_NAME}_1h", query_start)

    if not df_1h.empty:
        # Resample to 4h
        df_resampled = df_1h.resample("4h", label="left", closed="left").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }).dropna()

        # Filter out rows before or equal to T_max_4h
        if T_max_4h is not None:
            df_resampled = df_resampled[df_resampled.index > T_max_4h]

        # Filter for complete candles (now >= start_time + 4 hours)
        valid_candles_4h = []
        for start_time, row in df_resampled.iterrows():
            if now >= start_time + pd.Timedelta(hours=4):
                row.name = start_time
                valid_candles_4h.append(row)

        if valid_candles_4h:
            df_to_write_4h = pd.DataFrame(valid_candles_4h)
            write_to_questdb(target_table_4h, df_to_write_4h)
        else:
            logger.info("No complete 4h candles to write.")
    else:
        logger.warning("No 1h data found in database. Skipping 4h resampling.")

    logger.info("Data collection and aggregation completed successfully.")


if __name__ == "__main__":
    main()
