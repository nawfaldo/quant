"""Yahoo Finance data ingestion and aggregation script.

Fetches 1-minute intraday data for NQ futures, streams it to QuestDB,
and aggregates it to multiple higher timeframes without duplication.
"""

import socket
import logging
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
                logger.info(f"Table {table_name} does not exist. Starting fresh.")
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


def fetch_yfinance_1m(start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    """Fetch 1-minute historical data for TICKER from start_dt to end_dt.

    Downloads in 7-day chunks to respect Yahoo Finance's constraints.
    """
    logger.info(f"Fetching 1m data for {config.TICKER} from {start_dt} to {end_dt}")
    ticker = yf.Ticker(config.TICKER)

    # 7-day chunk size limit
    chunk_size = pd.Timedelta(days=7)
    current_start = start_dt
    dfs = []

    while current_start < end_dt:
        # yfinance end is exclusive, so add 1 minute to avoid missing the boundary minute
        current_end = min(current_start + chunk_size, end_dt)
        logger.info(f"Downloading chunk: {current_start} to {current_end}")

        try:
            df_chunk = ticker.history(start=current_start, end=current_end, interval="1m")
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


def query_1m_data(start_dt: pd.Timestamp | None) -> pd.DataFrame:
    """Fetch 1-minute records from QuestDB's base 1m table for aggregation."""
    table_name = f"yf_{config.BASE_TABLE_NAME}_1m"

    query = f"SELECT timestamp, open, high, low, close, volume FROM {table_name}"
    if start_dt is not None:
        # Convert start_dt to naive timestamp for UTC string representation in QuestDB
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

        # Convert index from naive (stored as UTC) to America/New_York timezone
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York")
        else:
            df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
        return df
    except Exception as e:
        logger.error(f"Error querying 1m data: {e}")
        return pd.DataFrame()


def is_candle_complete(start_time: pd.Timestamp, duration_minutes: int, max_1m_time: pd.Timestamp) -> bool:
    """Determine if a candle is fully completed based on the maximum 1m bar timestamp available."""
    # A candle of length duration_minutes starting at start_time is complete
    # when we have the 1-minute bar that is at or after start_time + (duration_minutes - 1)
    return max_1m_time >= start_time + pd.Timedelta(minutes=duration_minutes - 1)


def aggregate_and_write() -> None:
    """Aggregate 1-minute data into higher timeframes and write them to QuestDB."""
    base_1m_table = f"yf_{config.BASE_TABLE_NAME}_1m"
    max_1m_time = get_max_timestamp(base_1m_table)

    if max_1m_time is None:
        logger.info("No 1-minute data found in database. Skipping aggregation.")
        return

    # Timeframes: (suffix, pandas resample rule, duration in minutes)
    timeframes = [
        ("5m", "5min", 5),
        ("15m", "15min", 15),
        ("30m", "30min", 30),
        ("1h", "h", 60),
        ("4h", "4h", 240),
        ("1d", "D", 1440),
    ]

    for suffix, rule, duration in timeframes:
        target_table = f"yf_{config.BASE_TABLE_NAME}_{suffix}"
        logger.info(f"Processing aggregation for {target_table}...")

        # Query max timestamp of the target table
        T_max_tf = get_max_timestamp(target_table)

        # Query 1m data starting from T_max_tf (or None if empty)
        df_1m = query_1m_data(T_max_tf)
        if df_1m.empty:
            logger.info(f"No new 1-minute data to aggregate for {target_table}.")
            continue

        # Perform OHLCV resampling
        df_resampled = df_1m.resample(rule, label="left", closed="left").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }).dropna()

        if df_resampled.empty:
            logger.info(f"Resampling resulted in empty DataFrame for {target_table}.")
            continue

        # Filter rules:
        # 1. Start time must be strictly greater than T_max_tf (future proof / no duplicate inserts)
        # 2. Candle must be fully completed
        valid_candles = []
        for start_time, row in df_resampled.iterrows():
            # T_max_tf check
            if T_max_tf is not None and start_time <= T_max_tf:
                continue

            # Candle completion check
            if not is_candle_complete(start_time, duration, max_1m_time):
                continue

            # In order to construct the series properly, we must assign name to start_time
            row.name = start_time
            valid_candles.append(row)

        if not valid_candles:
            logger.info(f"No new completed candles to write for {target_table}.")
            continue

        df_to_write = pd.DataFrame(valid_candles)
        write_to_questdb(target_table, df_to_write)


def main() -> None:
    """Main execution function."""
    base_1m_table = f"yf_{config.BASE_TABLE_NAME}_1m"

    # 1. Determine where to start fetching from
    T_max_1m = get_max_timestamp(base_1m_table)

    now = pd.Timestamp.now(tz="America/New_York")
    if T_max_1m is None:
        # Empty database: Fetch full history (maximum yfinance lookback of 29 days)
        start_dt = now - pd.Timedelta(days=29)
        logger.info(f"Starting fresh. Historical limit start: {start_dt}")
    else:
        # Fetch starting from the last known timestamp (to fetch any updates or new bars)
        # We start exactly at T_max_1m to ensure we catch any overlapping/forming bar,
        # but we filter out duplicates afterwards.
        start_dt = T_max_1m
        logger.info(f"Incremental fetch starting at: {start_dt}")

    # 2. Fetch the 1-minute data from yfinance
    df_1m = fetch_yfinance_1m(start_dt, now)

    if df_1m.empty:
        logger.info("No new data fetched from yfinance.")
    else:
        # 3. Filter out any rows at or before T_max_1m to avoid duplicates
        if T_max_1m is not None:
            df_1m = df_1m[df_1m.index > T_max_1m]

        if df_1m.empty:
            logger.info("All fetched rows were already in the database. No new 1m data to write.")
        else:
            # 4. Stream 1-minute data into QuestDB
            write_to_questdb(base_1m_table, df_1m)

    # 5. Run aggregations for all higher timeframes
    aggregate_and_write()

    logger.info("Data collection and aggregation completed successfully.")


if __name__ == "__main__":
    main()
