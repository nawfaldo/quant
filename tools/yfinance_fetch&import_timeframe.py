#!/usr/bin/env python3
"""Download, replace, and verify Yahoo Finance candles in the store.

The requested inclusive local date range is fetched at the selected Yahoo
Finance timeframe. Reruns and overlapping ranges are idempotent: the affected
DAY partitions in the single ``<table-prefix>_<timeframe>`` destination are
dropped before the replacement rows are imported.
"""


from __future__ import annotations

# Must precede pandas, which hangs on this machine's broken WMI at import.
import _prelude  # noqa: F401  -- see tools/_prelude.py

import parquet_writer as pw


import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
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


UTC = timezone.utc
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
            "Download Yahoo Finance candles, replace the requested stored "
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
        help="table prefix; candles are written to <prefix>_<timeframe>",
    )
    parser.add_argument(
        "--timezone",
        type=parse_timezone,
        required=True,
        metavar="IANA_TIMEZONE",
        help="IANA timezone used for date windows and stored wall-clock timestamps",
    )
    return parser.parse_args()


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table identifier: {value!r}")
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


def table_exists(table: str) -> bool:
    safe_identifier(table)
    return bool(pw.table_files(table))


def get_latest_date(table: str, timezone: ZoneInfo) -> date | None:
    """The local date of the table's newest stored timestamp."""
    newest = pw.max_timestamp_ns(table)
    if newest is None:
        return None
    return datetime.fromtimestamp(newest / 1e9, tz=UTC).replace(tzinfo=None).date()


def range_nanoseconds(start: date, end: date) -> tuple[int, int]:
    """`[start, end]` inclusive of `end`, as half-open epoch nanoseconds."""
    lower = datetime.combine(start, dtime.min, tzinfo=UTC)
    upper = datetime.combine(end + timedelta(days=1), dtime.min, tzinfo=UTC)
    return (
        int(lower.timestamp()) * 1_000_000_000,
        int(upper.timestamp()) * 1_000_000_000,
    )


def count_range(table: str, start: date, end: date) -> int:
    safe_identifier(table)
    lower, upper = range_nanoseconds(start, end)
    return len(pw.read_columns(table, [], lower, upper)[1])


def clear_destination_range(table: str, start: date, end: date) -> None:
    """Drop the rows about to be replaced.

    `ALTER TABLE ... DROP PARTITION`, its DAY-partitioning precondition and the
    120-second poll that followed it are all gone: a shard is a file, deleting
    rows is synchronous, and there is no partitioning scheme to check.
    """
    if not table_exists(table):
        return
    before = count_range(table, start, end)
    if before == 0:
        return
    lower, upper = range_nanoseconds(start, end)
    print(f"Replacing {before:,} existing rows in {table}...", flush=True)
    pw.delete_range(table, lower, upper)
    remaining = count_range(table, start, end)
    if remaining:
        raise RuntimeError(f"{remaining:,} rows survived the range delete in {table}")


def run_importer(parquet_path: Path, table: str, timezone: ZoneInfo) -> None:
    """Load the staged real-UTC candles, converting each to `timezone`.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py` as a subprocess, which did the timezone
    conversion while streaming ILP; that importer existed only for QuestDB and
    is gone. `underlying` was a `--tag-cols` SYMBOL and is now a text column.

    `dedup=False`: the DST fall-back hour legitimately produces two candles
    with the same stored timestamp ([[dst-fakes-hourly-bar-gaps]]).
    """
    handle = pq.ParquetFile(str(parquet_path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()

    names = [name for name in staged.schema.names if name != "ts"]
    columns = {name: staged[name].to_pylist() for name in names}
    sender = pw.Sender(dedup=False)
    for index, moment in enumerate(staged["ts"].to_pylist()):
        local = moment.replace(tzinfo=UTC).astimezone(timezone)
        stored = int(local.replace(tzinfo=UTC).timestamp())
        sender.row(
            table,
            columns={name: columns[name][index] for name in names},
            at=pw.TimestampNanos(stored * 1_000_000_000),
        )
    sender.flush()


def find_duplicate(table: str, start: date, end: date) -> list[Any] | None:
    """A (timestamp, osi) pair stored more than once, or None."""
    safe_identifier(table)
    lower, upper = range_nanoseconds(start, end)
    _, rows = pw.read_columns(table, ["osi"], lower, upper)
    seen: set[tuple[int, Any]] = set()
    for stamp, osi in rows:
        key = (stamp, osi)
        if key in seen:
            return [pw.format_timestamp(stamp), osi, 2]
        seen.add(key)
    return None


def verify_import(table: str, start: date, end: date, source_rows: int) -> None:
    """The range holds exactly `source_rows` rows and no duplicate key.

    No polling. The two 120-second waits this replaces existed because a
    QuestDB table appeared and filled asynchronously after an ILP flush; a
    shard is renamed into place complete.
    """
    if not table_exists(table):
        raise RuntimeError(f"Expected table was not created: {table}")
    rows = count_range(table, start, end)
    if rows != source_rows:
        raise RuntimeError(f"{table} has {rows:,} rows in range; expected {source_rows:,}")
    duplicate = find_duplicate(table, start, end)
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
            start = get_latest_date(table, args.timezone)
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
            clear_destination_range(table, start, end)
            run_importer(output, table, args.timezone)
            verify_import(table, start, end, source_rows)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Yahoo Finance {args.timeframe} pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(f"Complete: {table} replaced for {start.isoformat()} through {end.isoformat()}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
