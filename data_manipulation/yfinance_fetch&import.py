#!/usr/bin/env python3
"""Download, replace, aggregate, and verify Yahoo Finance candles in QuestDB.

Yahoo Finance supplies underlying OHLCV bars, not historical option candles.
The seven complete calendar days ending yesterday of 1-minute bars are written to Parquet,
imported into the seven standard timeframe tables, and that range is replaced
on reruns.
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

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pandas as pd
import yfinance as yf


TIMEFRAME_SUFFIXES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
REQUIRED_COLUMNS = {"ts", "underlying", "osi", "open", "high", "low", "close", "volume"}
LOOKBACK_DAYS = 7
YAHOO_1M_REQUEST_DAYS = 7


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid IANA timezone: {value!r}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download the seven complete days ending yesterday of Yahoo Finance 1-minute "
            "candles, replace that QuestDB range, and aggregate all timeframes."
        )
    )
    parser.add_argument("--symbol", required=True, help="Yahoo Finance ticker to fetch")
    parser.add_argument(
        "--table-prefix",
        required=True,
        help="QuestDB table prefix; timeframe suffixes are appended",
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


def default_output(symbol: str, start: date, end: date) -> Path:
    return Path(f"yfinance_{symbol.upper()}_1m_{start.isoformat()}_{end.isoformat()}.parquet")


def download(start: date, end: date, output: Path, symbol: str, timezone: ZoneInfo) -> None:
    """Fetch 1-minute OHLCV bars and persist the importer-compatible Parquet file."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone)
    ticker = yf.Ticker(symbol)
    chunks = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + timedelta(days=YAHOO_1M_REQUEST_DAYS), end_dt)
        print(
            f"Downloading Yahoo 1m chunk {cursor.date().isoformat()} through "
            f"{(chunk_end - timedelta(microseconds=1)).date().isoformat()}...",
            flush=True,
        )
        chunk = ticker.history(
            start=cursor,
            end=chunk_end,
            interval="1m",
            auto_adjust=False,
            actions=False,
            prepost=False,
            raise_errors=True,
        )
        if not chunk.empty:
            chunks.append(chunk)
        cursor = chunk_end
    if not chunks:
        raise RuntimeError("Yahoo Finance returned no 1-minute candles for this range")
    history = pd.concat(chunks).sort_index()
    history = history[~history.index.duplicated(keep="first")]
    if history.empty:
        raise RuntimeError("Yahoo Finance returned no 1-minute candles for this range")

    # A Ticker history index is timezone-aware in normal yfinance responses.
    # Interpret a naive index in the selected exchange timezone defensively.
    index = history.index
    if index.tz is None:
        index = index.tz_localize(timezone)
    else:
        index = index.tz_convert(timezone)
    history = history.loc[(index.date >= start) & (index.date <= end)].copy()
    index = index[(index.date >= start) & (index.date <= end)]
    if history.empty:
        raise RuntimeError("Yahoo Finance returned no candles inside the requested local range")

    required = ("Open", "High", "Low", "Close", "Volume")
    missing = [column for column in required if column not in history.columns]
    if missing:
        raise RuntimeError(f"Yahoo Finance response is missing: {', '.join(missing)}")

    canonical_symbol = symbol.upper()
    # questdb_parquet_importer's aggregate mode groups on --contract-col.  A
    # single synthetic key correctly groups all bars for this one underlying.
    rows = {
        "ts": index.tz_convert("UTC").to_pydatetime(),
        "underlying": [canonical_symbol] * len(history),
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
    ]))
    table = table.sort_by("ts")
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
    return scalar_count(questdb_exec(host, port, f"select count() from tables() where table_name='{table}'")) == 1


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


def clear_destination_range(host: str, port: int, base_table: str, start: date, end: date) -> None:
    for suffix in TIMEFRAME_SUFFIXES:
        table = safe_identifier(f"{base_table}_{suffix}")
        if not table_exists(host, port, table):
            continue
        before = count_range(host, port, table, start, end)
        if before == 0:
            continue
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
        partition_sql = ",".join(f"'{partition}'" for partition in partitions)
        print(f"Replacing {before:,} existing rows in {table}...", flush=True)
        questdb_exec(host, port, f"alter table {table} drop partition list {partition_sql}")
        deadline = time.monotonic() + 120
        while count_range(host, port, table, start, end) != 0:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for {table} range deletion")
            time.sleep(0.25)


def run_importer(parquet_path: Path, base_table: str, host: str, ilp_port: int, timezone: ZoneInfo) -> None:
    importer = Path(__file__).with_name("questdb_parquet_importer.py")
    if not importer.is_file():
        raise RuntimeError(f"Missing companion importer: {importer}")
    subprocess.run([
        sys.executable, str(importer), str(parquet_path), "--table", base_table,
        "--ts-col", "ts", "--tag-cols", "underlying", "--contract-col", "osi",
        "--timezone", timezone.key, "--aggregate", "--host", host, "--port", str(ilp_port),
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


def verify_import(host: str, http_port: int, base_table: str, start: date, end: date, source_rows: int) -> None:
    for suffix in TIMEFRAME_SUFFIXES:
        table = safe_identifier(f"{base_table}_{suffix}")
        if not table_exists(host, http_port, table):
            raise RuntimeError(f"Expected QuestDB table was not created: {table}")
        rows = count_range(host, http_port, table, start, end)
        if suffix == "1m" and rows != source_rows:
            deadline = time.monotonic() + 120
            while rows != source_rows and time.monotonic() < deadline:
                time.sleep(0.25)
                rows = count_range(host, http_port, table, start, end)
            if rows != source_rows:
                raise RuntimeError(f"{table} has {rows:,} rows in range; expected {source_rows:,}")
        if rows == 0:
            deadline = time.monotonic() + 120
            while rows == 0 and time.monotonic() < deadline:
                time.sleep(0.25)
                rows = count_range(host, http_port, table, start, end)
        if rows == 0:
            raise RuntimeError(f"{table} has no rows in the imported range")
        duplicate = find_duplicate(host, http_port, table, start, end)
        if duplicate is not None:
            raise RuntimeError(f"Duplicate timestamp/osi in {table}: {duplicate}")
        print(f"Verified {table}: {rows:,} rows, no duplicate timestamp/osi.")


def main() -> int:
    args = parse_args()
    end = datetime.now(args.timezone).date() - timedelta(days=1)
    start = end - timedelta(days=LOOKBACK_DAYS - 1)
    symbol = args.symbol.upper()
    try:
        base_table = safe_identifier(args.table_prefix)
        with tempfile.TemporaryDirectory(prefix="yfinance-import-") as temporary_dir:
            output = Path(temporary_dir) / default_output(symbol, start, end)
            print(
                f"Downloading {symbol} 1m candles for the inclusive {args.timezone.key} "
                f"range {start.isoformat()} through {end.isoformat()}...", flush=True,
            )
            download(start, end, output, symbol, args.timezone)
            print(f"Saved {output} ({output.stat().st_size:,} bytes).", flush=True)
            source_rows = validate_parquet(output, start, end, args.timezone)
            clear_destination_range(args.questdb_host, args.questdb_http_port, base_table, start, end)
            run_importer(output, base_table, args.questdb_host, args.questdb_ilp_port, args.timezone)
            verify_import(args.questdb_host, args.questdb_http_port, base_table, start, end, source_rows)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Yahoo Finance pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Complete: {base_table}_1m through {base_table}_1d replaced for "
        f"{start.isoformat()} through {end.isoformat()}.", flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
