"""Sync all source tables into their canonical nq_* OHLCV counterparts in QuestDB.

Sources integrated:
  - yf_nq_{tf}  → nq_{tf}   (OHLCV rows fetched and written via ILP)
  - bm_nq_ticks → nq_{tf}   (tick data aggregated via SAMPLE BY, then written via ILP)

After a successful integration each source table is dropped.
Timestamps in all sources use ET stored as fake-UTC — no conversion needed.
"""

import socket
import logging
import httpx
import pandas as pd

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_nq_tables")

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

# QuestDB SAMPLE BY unit per timeframe
TF_SAMPLE_BY = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
}


# ── QuestDB helpers ────────────────────────────────────────────────────────────

def exec_query(query: str) -> dict:
    url = f"http://{config.QUESTDB_HOST}:{config.QUESTDB_HTTP_PORT}/exec"
    response = httpx.get(url, params={"query": query}, timeout=120.0)
    response.raise_for_status()
    return response.json()


def table_exists(table: str) -> bool:
    try:
        result = exec_query(f"SELECT count() FROM {table}")
        return bool(result.get("dataset"))
    except Exception:
        return False


def drop_table(table: str) -> None:
    exec_query(f"DROP TABLE IF EXISTS '{table}'")
    logger.info(f"Dropped {table}.")


# ── shared OHLCV write via ILP ─────────────────────────────────────────────────

def write_ohlcv_rows(table_name: str, df: pd.DataFrame) -> int:
    """Write DataFrame (index = tz-aware UTC, cols = Open/High/Low/Close/Volume) via ILP."""
    if df.empty:
        return 0

    naive_index = df.index.tz_localize(None) if df.index.tz is None else df.index.tz_convert("UTC").tz_localize(None)
    nanos = naive_index.to_numpy().astype("datetime64[ns]").view("int64")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((config.QUESTDB_HOST, config.QUESTDB_ILP_PORT))
        lines = []
        for i, (_, row) in enumerate(df.iterrows()):
            lines.append(
                f"{table_name} "
                f"open={row['Open']},high={row['High']},"
                f"low={row['Low']},close={row['Close']},"
                f"volume={int(row['Volume'])}i "
                f"{nanos[i]}\n"
            )
            if len(lines) >= 1000:
                s.sendall("".join(lines).encode("utf-8"))
                lines = []
        if lines:
            s.sendall("".join(lines).encode("utf-8"))
        return len(df)
    finally:
        s.close()


def fetch_existing_timestamps(dst_table: str, start: pd.Timestamp, end: pd.Timestamp) -> set:
    """Set of timestamps already in dst_table within [start, end]."""
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = exec_query(
        f"SELECT timestamp FROM {dst_table}"
        f" WHERE timestamp >= '{start_str}' AND timestamp <= '{end_str}'"
    )
    return {pd.Timestamp(r[0], tz="UTC") for r in result.get("dataset", [])}


def rows_to_ohlcv_df(rows: list, cols: list) -> pd.DataFrame:
    """Convert QuestDB dataset rows to an OHLCV DataFrame with a UTC-aware index."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                        "close": "Close", "volume": "Volume"}, inplace=True)
    return df


def merge_into_dst(df: pd.DataFrame, dst: str) -> None:
    """Insert all df rows not already present in dst."""
    if df.empty:
        return
    src_start, src_end = df.index.min(), df.index.max()
    logger.info(f"  source: {len(df)} rows  [{src_start} → {src_end}]")

    if not table_exists(dst):
        written = write_ohlcv_rows(dst, df)
        logger.info(f"  {dst} created — wrote {written} rows.")
        return

    existing = fetch_existing_timestamps(dst, src_start, src_end)
    logger.info(f"  {dst}: {len(existing)} rows already in overlap range.")
    new_df = df[~df.index.isin(existing)]
    if new_df.empty:
        logger.info(f"  No new rows for {dst}.")
        return
    written = write_ohlcv_rows(dst, new_df)
    logger.info(f"  Wrote {written} new rows to {dst}.")


# ── yf_nq_* → nq_* ────────────────────────────────────────────────────────────

def integrate_yf_timeframe(tf: str) -> bool:
    src = f"yf_nq_{tf}"
    dst = f"nq_{tf}"

    if not table_exists(src):
        logger.info(f"{src} does not exist — skipping.")
        return False

    logger.info(f"--- {src} → {dst} ---")
    result = exec_query(
        f"SELECT timestamp, open, high, low, close, volume FROM {src} ORDER BY timestamp ASC"
    )
    df = rows_to_ohlcv_df(result.get("dataset", []),
                          ["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        logger.info(f"{src} is empty.")
        return True

    merge_into_dst(df, dst)
    return True


# ── bm_nq_ticks → nq_* (aggregate) ───────────────────────────────────────────

def aggregate_ticks_for_tf(tf: str) -> pd.DataFrame:
    """Aggregate bm_nq_ticks into OHLCV bars for the given timeframe using SAMPLE BY."""
    sample = TF_SAMPLE_BY[tf]
    result = exec_query(
        f"SELECT timestamp,"
        f" first(price) AS open,"
        f" max(price)   AS high,"
        f" min(price)   AS low,"
        f" last(price)  AS close,"
        f" sum(size)    AS volume"
        f" FROM bm_nq_ticks"
        f" SAMPLE BY {sample} FILL(NONE) ALIGN TO CALENDAR"
        f" ORDER BY timestamp ASC"
    )
    return rows_to_ohlcv_df(result.get("dataset", []),
                            ["timestamp", "open", "high", "low", "close", "volume"])


def integrate_ticks() -> bool:
    if not table_exists("bm_nq_ticks"):
        logger.info("bm_nq_ticks does not exist — skipping.")
        return False

    src_info = exec_query(
        "SELECT min(timestamp), max(timestamp), count() FROM bm_nq_ticks"
    )["dataset"][0]
    logger.info(f"bm_nq_ticks: {src_info[2]} rows  [{src_info[0]} → {src_info[1]}]")

    for tf in TIMEFRAMES:
        dst = f"nq_{tf}"
        logger.info(f"--- bm_nq_ticks → {dst} ({tf}) ---")
        df = aggregate_ticks_for_tf(tf)
        if df.empty:
            logger.info(f"  No bars produced for {tf}.")
            continue
        merge_into_dst(df, dst)

    return True


# ── drop source tables ─────────────────────────────────────────────────────────

def drop_sources(yf_integrated: list[str], ticks_integrated: bool) -> None:
    logger.info("--- Dropping source tables ---")
    for tf in yf_integrated:
        drop_table(f"yf_nq_{tf}")
    if ticks_integrated:
        drop_table("bm_nq_ticks")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # Drop incorrectly-created nq_ticks if present
    if table_exists("nq_ticks"):
        logger.info("Dropping stale nq_ticks table (superseded by OHLCV aggregation).")
        drop_table("nq_ticks")

    yf_integrated = []
    for tf in TIMEFRAMES:
        if integrate_yf_timeframe(tf):
            yf_integrated.append(tf)

    ticks_integrated = integrate_ticks()

    drop_sources(yf_integrated, ticks_integrated)
    logger.info("Sync complete.")


if __name__ == "__main__":
    main()
