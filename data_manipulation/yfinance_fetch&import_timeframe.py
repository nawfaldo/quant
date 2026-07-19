#!/usr/bin/env python3
"""Download, replace, and verify Yahoo Finance candles in QuestDB.

The requested inclusive local date range is fetched at the selected Yahoo
Finance timeframe. Reruns and overlapping ranges are idempotent: the affected
DAY partitions in the single ``<table-prefix>_<timeframe>`` destination are
dropped before the replacement rows are imported.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import yfinance as yf


REQUIRED_COLUMNS = {"ts", "underlying", "osi", "open", "high", "low", "close", "volume"}
TIMEFRAMES = ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo")
INTRADAY_LOOKBACK_DAYS = {
    "1m": 7,
    "2m": 59,
    "5m": 59,
    "15m": 59,
    "30m": 59,
    "60m": 729,
    "90m": 59,
    "1h": 729,
}


def parse_date_range(value: str) -> tuple[date, date]:
    """Parse an inclusive DD/MM/YY-DD/MM/YY local date range."""
    parts = value.split("-", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("date must use DD/MM/YY-DD/MM/YY")
    try:
        start = datetime.strptime(parts[0], "%d/%m/%y").date()
        end = datetime.strptime(parts[1], "%d/%m/%y").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "date must use DD/MM/YY-DD/MM/YY with valid calendar dates"
        ) from exc
    if end < start:
        raise argparse.ArgumentTypeError("date range end must not precede start")
    return start, end


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid IANA timezone: {value!r}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Yahoo Finance candles, replace the requested QuestDB "
            "range, and verify the result."
        )
    )
    parser.add_argument("--symbol", required=True, help="Yahoo Finance ticker to fetch")
    parser.add_argument(
        "--timeframe",
        choices=TIMEFRAMES,
        default="1d",
        help="Yahoo Finance candle interval and destination table suffix (default: 1d)",
    )
    parser.add_argument(
        "--date",
        dest="date_range",
        type=parse_date_range,
        required=False,
        default=None,
        metavar="DD/MM/YY-DD/MM/YY",
        help="inclusive date range in --timezone",
    )
    parser.add_argument(
        "--table-prefix",
        required=True,
        help="QuestDB table prefix; candles are written to <prefix>_<timeframe>",
    )
    parser.add_argument(
        "--timezone",
        type=parse_timezone,
        required=True,
        metavar="IANA_TIMEZONE",
        help="IANA timezone used for date windows and stored wall-clock timestamps",
    )
    parser.add_argument("--questdb-host", default=os.getenv("QUESTDB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--questdb-http-port", type=int, default=int(os.getenv("QUESTDB_HTTP_PORT", "9000"))
    )
    parser.add_argument(
        "--questdb-ilp-port", type=int, default=int(os.getenv("QUESTDB_ILP_PORT", "9009"))
    )
    return parser.parse_args()


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe QuestDB identifier: {value!r}")
    return value


def default_output(symbol: str, timeframe: str, start: date, end: date) -> Path:
    return Path(f"yfinance_{symbol.upper()}_{timeframe}_{start.isoformat()}_{end.isoformat()}.parquet")


def download(
    start: date,
    end: date,
    output: Path,
    symbol: str,
    timeframe: str,
    timezone: ZoneInfo,
) -> None:
    """Fetch OHLCV bars and persist an importer-compatible Parquet file."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone)
    print(
        f"Downloading Yahoo {timeframe} candles {start.isoformat()} through {end.isoformat()}...",
        flush=True,
    )
    history = yf.Ticker(symbol).history(
        start=start_dt,
        end=end_dt,
        interval=timeframe,
        auto_adjust=False,
        actions=False,
        prepost=False,
        raise_errors=True,
    )
    if history.empty:
        raise RuntimeError(f"Yahoo Finance returned no {timeframe} candles for this range")

    # A Ticker history index is timezone-aware in normal yfinance responses.
    # Interpret a naive index in the selected exchange timezone defensively.
    index = history.index
    if index.tz is None:
        index = index.tz_localize(timezone)
    else:
        index = index.tz_convert(timezone)
    history = history.loc[(index.date >= start) & (index.date <= end)].copy()
    index = index[(index.date >= start) & (index.date <= end)]
    history.index = index
    history = history[~history.index.duplicated(keep="first")].sort_index()
    if history.empty:
        raise RuntimeError(
            f"Yahoo Finance returned no {timeframe} candles inside the requested local range"
        )

    required = ("Open", "High", "Low", "Close", "Volume")
    missing = [column for column in required if column not in history.columns]
    if missing:
        raise RuntimeError(f"Yahoo Finance response is missing: {', '.join(missing)}")

    canonical_symbol = symbol.upper()
    rows = {
        "ts": history.index.tz_convert("UTC").to_pydatetime(),
        "underlying": [canonical_symbol] * len(history),
        # Retaining a synthetic contract key allows the duplicate check to use
        # the same timestamp/key invariant as the intraday Yahoo importer.
        "osi": [canonical_symbol] * len(history),
        "open": history["Open"].astype(float).tolist(),
        "high": history["High"].astype(float).tolist(),
        "low": history["Low"].astype(float).tolist(),
        "close": history["Close"].astype(float).tolist(),
        "volume": history["Volume"].fillna(0).astype("int64").tolist(),
    }
    table = pa.table(rows, schema=pa.schema([
        pa.field("ts", pa.timestamp("us", tz="UTC")),
        pa.field("underlying", pa.string()),
        pa.field("osi", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.int64()),
    ])).sort_by("ts")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(output)


def validate_parquet(path: Path, start: date, end: date, timezone: ZoneInfo) -> int:
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows <= 0:
        raise RuntimeError("Downloaded Parquet contains no candles")
    schema = parquet.schema_arrow
    missing = sorted(REQUIRED_COLUMNS.difference(schema.names))
    if missing:
        raise RuntimeError(f"Downloaded Parquet is missing: {', '.join(missing)}")
    ts_type = schema.field("ts").type
    if not pa.types.is_timestamp(ts_type) or ts_type.tz != "UTC":
        raise RuntimeError(f"Expected ts to be a UTC timestamp, received {ts_type}")
    ts_column = pq.read_table(path, columns=["ts"])["ts"]
    first, last = pc.min(ts_column).as_py(), pc.max(ts_column).as_py()
    if first is None or last is None:
        raise RuntimeError("Downloaded Parquet has no valid timestamps")
    first_local, last_local = first.astimezone(timezone), last.astimezone(timezone)
    if first_local.date() < start or last_local.date() > end:
        raise RuntimeError(
            "Downloaded timestamps fall outside the requested local range: "
            f"{first_local.isoformat()} through {last_local.isoformat()}"
        )
    duplicate_count = len(ts_column) - len(pc.unique(ts_column))
    if duplicate_count:
        raise RuntimeError(f"Downloaded Parquet contains {duplicate_count:,} duplicate timestamp(s)")
    print(
        f"Validated {parquet.metadata.num_rows:,} rows; {timezone.key} timestamps "
        f"{first_local.isoformat()} through {last_local.isoformat()}.",
        flush=True,
    )
    return parquet.metadata.num_rows


def questdb_exec(host: str, port: int, query: str) -> dict[str, Any]:
    url = f"http://{host}:{port}/exec?{urlencode({'query': query})}"
    try:
        with urlopen(url, timeout=300) as response:
            result = json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"QuestDB HTTP {exc.code}: {details[:2000]}\nSQL: {query}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("QuestDB returned a non-object response")
    if "error" in result:
        raise RuntimeError(f"QuestDB query failed: {result['error']}\nSQL: {query}")
    return result


def scalar_count(result: dict[str, Any]) -> int:
    dataset = result.get("dataset")
    if not isinstance(dataset, list) or not dataset or not dataset[0]:
        raise RuntimeError("QuestDB count query returned no value")
    return int(dataset[0][0])


def table_exists(host: str, port: int, table: str) -> bool:
    safe_identifier(table)
    return scalar_count(
        questdb_exec(host, port, f"select count() from tables() where table_name='{table}'")
    ) == 1


def get_latest_date(host: str, port: int, table: str, timezone: ZoneInfo) -> date | None:
    """Query QuestDB for the maximum timestamp in a table and return its local date."""
    if not table_exists(host, port, table):
        return None
    try:
        result = questdb_exec(host, port, f"select max(timestamp) from {table}")
        dataset = result.get("dataset")
        if dataset and dataset[0] and dataset[0][0] is not None:
            ts_str = dataset[0][0]
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            dt_utc = datetime.fromisoformat(ts_str)
            dt_naive = dt_utc.replace(tzinfo=None)
            return dt_naive.date()
    except Exception as exc:
        print(f"Warning: failed to query latest timestamp for {table}: {exc}", file=sys.stderr)
    return None


def range_bounds(start: date, end: date) -> tuple[str, str]:
    return (
        f"{start.isoformat()}T00:00:00.000000Z",
        f"{(end + timedelta(days=1)).isoformat()}T00:00:00.000000Z",
    )


def count_range(host: str, port: int, table: str, start: date, end: date) -> int:
    safe_identifier(table)
    lower, upper = range_bounds(start, end)
    return scalar_count(questdb_exec(
        host, port,
        f"select count() from {table} where timestamp >= '{lower}' and timestamp < '{upper}'",
    ))


def clear_destination_range(host: str, port: int, table: str, start: date, end: date) -> None:
    if not table_exists(host, port, table):
        return
    before = count_range(host, port, table, start, end)
    if before == 0:
        return
    metadata = questdb_exec(host, port, f"select partitionBy from tables() where table_name='{table}'")
    if metadata.get("dataset") != [["DAY"]]:
        raise RuntimeError(f"Refusing replacement: {table} is not DAY-partitioned")
    partition_result = questdb_exec(
        host, port,
        f"select name from table_partitions('{table}') where name >= '{start.isoformat()}' and name <= '{end.isoformat()}'",
    )
    dataset = partition_result.get("dataset")
    partitions = [str(row[0]) for row in dataset] if isinstance(dataset, list) else []
    if not partitions:
        raise RuntimeError(f"{table} has {before:,} range rows but no matching DAY partitions")
    partition_sql_list = []
    chunk_size = 100
    for i in range(0, len(partitions), chunk_size):
        chunk = partitions[i:i + chunk_size]
        partition_sql_list.append(",".join(f"'{p}'" for p in chunk))
    print(f"Replacing {before:,} existing rows in {table}...", flush=True)
    for partition_sql in partition_sql_list:
        questdb_exec(host, port, f"alter table {table} drop partition list {partition_sql}")
    deadline = time.monotonic() + 120
    while count_range(host, port, table, start, end) != 0:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {table} range deletion")
        time.sleep(0.25)


def run_importer(parquet_path: Path, table: str, host: str, ilp_port: int, timezone: ZoneInfo) -> None:
    importer = Path(__file__).with_name("questdb_parquet_importer.py")
    if not importer.is_file():
        raise RuntimeError(f"Missing companion importer: {importer}")
    subprocess.run([
        sys.executable, str(importer), str(parquet_path), "--table", table,
        "--ts-col", "ts", "--tag-cols", "underlying", "--timezone", timezone.key,
        "--host", host, "--port", str(ilp_port),
    ], check=True)


def find_duplicate(host: str, port: int, table: str, start: date, end: date) -> list[Any] | None:
    safe_identifier(table)
    lower, upper = range_bounds(start, end)
    result = questdb_exec(host, port, (
        "select * from (select timestamp,osi,count() occurrences "
        f"from {table} where timestamp >= '{lower}' and timestamp < '{upper}' "
        "group by timestamp,osi) where occurrences > 1 limit 1"
    ))
    dataset = result.get("dataset")
    return dataset[0] if isinstance(dataset, list) and dataset else None


def verify_import(host: str, http_port: int, table: str, start: date, end: date, source_rows: int) -> None:
    deadline = time.monotonic() + 120
    # Wait for table to exist
    while time.monotonic() < deadline:
        try:
            if table_exists(host, http_port, table):
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not table_exists(host, http_port, table):
        raise RuntimeError(f"Expected QuestDB table was not created: {table}")

    # Wait for expected row count
    rows = -1
    while time.monotonic() < deadline:
        try:
            rows = count_range(host, http_port, table, start, end)
            if rows == source_rows:
                break
        except Exception:
            pass
        time.sleep(0.5)

    if rows != source_rows:
        raise RuntimeError(f"{table} has {rows:,} rows in range; expected {source_rows:,}")
    duplicate = find_duplicate(host, http_port, table, start, end)
    if duplicate is not None:
        raise RuntimeError(f"Duplicate timestamp/osi in {table}: {duplicate}")
    print(f"Verified {table}: {rows:,} rows, no duplicate timestamp/osi.")


def main() -> int:
    args = parse_args()
    symbol = args.symbol.upper()
    try:
        base_table = safe_identifier(args.table_prefix)
        table = safe_identifier(f"{base_table}_{args.timeframe}")
        if args.date_range is not None:
            start, end = args.date_range
        else:
            end = datetime.now(args.timezone).date() - timedelta(days=1)
            start = get_latest_date(args.questdb_host, args.questdb_http_port, table, args.timezone)
            if start is None:
                lookback_days = INTRADAY_LOOKBACK_DAYS.get(args.timeframe)
                start = (
                    end - timedelta(days=lookback_days - 1)
                    if lookback_days is not None
                    else date(1990, 1, 2)
                )
            elif start > end:
                start = end
        with tempfile.TemporaryDirectory(prefix="yfinance-import-") as temporary_dir:
            output = Path(temporary_dir) / default_output(symbol, args.timeframe, start, end)
            print(
                f"Downloading {symbol} {args.timeframe} candles for the inclusive {args.timezone.key} "
                f"range {start.isoformat()} through {end.isoformat()}...", flush=True,
            )
            download(start, end, output, symbol, args.timeframe, args.timezone)
            print(f"Saved {output} ({output.stat().st_size:,} bytes).", flush=True)
            source_rows = validate_parquet(output, start, end, args.timezone)
            clear_destination_range(args.questdb_host, args.questdb_http_port, table, start, end)
            run_importer(output, table, args.questdb_host, args.questdb_ilp_port, args.timezone)
            verify_import(args.questdb_host, args.questdb_http_port, table, start, end, source_rows)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Yahoo Finance {args.timeframe} pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(f"Complete: {table} replaced for {start.isoformat()} through {end.isoformat()}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
