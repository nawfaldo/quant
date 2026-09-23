#!/usr/bin/env python3
"""Download, replace, aggregate, and verify Yahoo Finance candles in the store.

Yahoo Finance supplies underlying OHLCV bars, not historical option candles.
The seven complete calendar days ending two days ago of 1-minute bars are
written to Parquet, imported into the seven standard timeframe tables, and
that range is replaced on reruns.  Keeping a full-day publication buffer
prevents a still-partial prior date from being imported.

With --latest-minute, only the newest completed 1-minute candle is fetched and
upserted into the 1-minute table.  This mode never replaces a daily partition.
"""


from __future__ import annotations

# Must precede pandas, which hangs on this machine's broken WMI at import.
import _prelude  # noqa: F401  -- see tools/_prelude.py

import parquet_writer as pw


import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone as dt_timezone
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
YAHOO_AVAILABILITY_LAG_DAYS = 2


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid IANA timezone: {value!r}") from exc


def latest_fully_available_date(timezone: ZoneInfo, now: datetime | None = None) -> date:
    """Return the newest date safe to request from Yahoo's intraday feed."""
    current = datetime.now(timezone) if now is None else now.astimezone(timezone)
    return current.date() - timedelta(days=YAHOO_AVAILABILITY_LAG_DAYS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download seven fully available days ending two days ago of Yahoo Finance "
            "1-minute candles, replace that QuestDB range, and aggregate all timeframes."
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
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help=(
            "Newest date to request, default two days before today. Only useful "
            "for backfilling: Yahoo serves roughly 30 days of 1-minute history, "
            "so an older date returns nothing rather than failing loudly."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=LOOKBACK_DAYS,
        help=f"Inclusive window length ending at --end-date (default {LOOKBACK_DAYS}). "
             "Yahoo caps a single 1-minute request at seven days; longer windows "
             "are already chunked by the downloader.",
    )
    parser.add_argument(
        "--latest-minute",
        action="store_true",
        help=(
            "Fetch and upsert only Yahoo's newest completed 1-minute candle "
            "into <table-prefix>_1m instead of replacing historical days"
        ),
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
    # `aggregate_rows` groups on `osi`. A single synthetic key correctly
    # groups all bars for this one underlying.
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


def download_latest_minute(output: Path, symbol: str, timezone: ZoneInfo) -> datetime:
    """Persist Yahoo's newest completed 1-minute candle and return its UTC time."""
    now = datetime.now(timezone)
    current_minute = now.replace(second=0, microsecond=0)
    history = yf.Ticker(symbol).history(
        period="1d",
        interval="1m",
        auto_adjust=False,
        actions=False,
        prepost=True,
    )
    if history.empty:
        raise RuntimeError("Yahoo Finance returned no 1-minute candles")

    index = history.index
    if index.tz is None:
        index = index.tz_localize(timezone)
    else:
        index = index.tz_convert(timezone)
    history = history.copy()
    history.index = index
    history = history.loc[history.index < current_minute]
    if history.empty:
        raise RuntimeError("Yahoo Finance returned no completed 1-minute candle")

    history = history.iloc[[-1]]
    candle_time = history.index[0]
    required = ("Open", "High", "Low", "Close", "Volume")
    missing = [column for column in required if column not in history.columns]
    if missing:
        raise RuntimeError(f"Yahoo Finance response is missing: {', '.join(missing)}")

    canonical_symbol = symbol.upper()
    table = pa.table({
        "ts": [candle_time.tz_convert("UTC").to_pydatetime()],
        "underlying": [canonical_symbol],
        "osi": [canonical_symbol],
        "open": [float(history.iloc[0]["Open"])],
        "high": [float(history.iloc[0]["High"])],
        "low": [float(history.iloc[0]["Low"])],
        "close": [float(history.iloc[0]["Close"])],
        "volume": [int(history.iloc[0]["Volume"]) if pd.notna(history.iloc[0]["Volume"]) else 0],
    }, schema=pa.schema([
        pa.field("ts", pa.timestamp("us", tz="UTC")),
        pa.field("underlying", pa.string()),
        pa.field("osi", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.int64()),
    ]))
    pq.write_table(table, output, compression="zstd")
    age = int((current_minute - candle_time).total_seconds() // 60)
    print(
        f"Yahoo latest completed candle: {candle_time.isoformat()} "
        f"({age} minute{'s' if age != 1 else ''} behind the current minute).",
        flush=True,
    )
    return candle_time.tz_convert("UTC").to_pydatetime()


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


#: Seconds in each aggregated timeframe, matching TIMEFRAME_SUFFIXES.
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def table_exists(table: str) -> bool:
    safe_identifier(table)
    return bool(pw.table_files(table))


def range_nanoseconds(start: date, end: date) -> tuple[int, int]:
    """`[start, end]` inclusive of `end`, as half-open epoch nanoseconds."""
    lower = datetime.combine(start, dtime.min, tzinfo=dt_timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), dtime.min, tzinfo=dt_timezone.utc)
    return (
        int(lower.timestamp()) * 1_000_000_000,
        int(upper.timestamp()) * 1_000_000_000,
    )


def count_range(table: str, start: date, end: date) -> int:
    safe_identifier(table)
    lower, upper = range_nanoseconds(start, end)
    return len(pw.read_columns(table, [], lower, upper)[1])


def clear_destination_range(base_table: str, start: date, end: date) -> None:
    """Drop the rows about to be replaced, in every aggregated timeframe.

    `ALTER TABLE ... DROP PARTITION`, its DAY-partitioning precondition, the
    distinct-date probe that fed it and the 120-second poll after it are all
    gone: a shard is a file and deleting rows is synchronous.
    """
    lower, upper = range_nanoseconds(start, end)
    for suffix in TIMEFRAME_SUFFIXES:
        table = safe_identifier(f"{base_table}_{suffix}")
        if not table_exists(table):
            continue
        before = count_range(table, start, end)
        if before == 0:
            continue
        print(f"Replacing {before:,} existing rows in {table}...", flush=True)
        pw.delete_range(table, lower, upper)
        remaining = count_range(table, start, end)
        if remaining:
            raise RuntimeError(f"{remaining:,} rows survived the delete in {table}")


def aggregate_rows(rows: list[dict], seconds: int) -> list[tuple[int, dict]]:
    """Roll one-minute rows up into `seconds` buckets, per contract.

    This is `questdb_parquet_importer.py --aggregate --contract-col osi`,
    written out. Each contract is bucketed independently -- an option chain
    holds many contracts on the same timestamps, and mixing them would produce
    one meaningless candle per bucket instead of one per contract.
    """
    buckets: dict[tuple[str, int], dict] = {}
    for row in rows:
        bucket = row["stored_seconds"] // seconds * seconds
        key = (row["osi"], bucket)
        current = buckets.get(key)
        if current is None:
            buckets[key] = {
                "underlying": row["underlying"],
                "osi": row["osi"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "_bucket": bucket,
            }
        else:
            current["high"] = max(current["high"], row["high"])
            current["low"] = min(current["low"], row["low"])
            current["close"] = row["close"]
            current["volume"] += row["volume"]
    ordered = sorted(buckets.values(), key=lambda value: (value["_bucket"], value["osi"]))
    return [
        (value.pop("_bucket") * 1_000_000_000, value) for value in ordered
    ]


def staged_rows(parquet_path: Path, timezone: ZoneInfo) -> list[dict]:
    """The staged real-UTC candles, each carrying its stored wall-clock second."""
    handle = pq.ParquetFile(str(parquet_path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()
    rows = []
    for record in staged.to_pylist():
        moment = record["ts"]
        local = moment.replace(tzinfo=dt_timezone.utc).astimezone(timezone)
        rows.append({
            "stored_seconds": int(local.replace(tzinfo=dt_timezone.utc).timestamp()),
            "underlying": record["underlying"],
            "osi": record["osi"],
            "open": float(record["open"]),
            "high": float(record["high"]),
            "low": float(record["low"]),
            "close": float(record["close"]),
            "volume": int(record["volume"]),
        })
    return rows


def run_importer(
    parquet_path: Path,
    base_table: str,
    timezone: ZoneInfo,
    *,
    aggregate: bool = True,
) -> None:
    """Load the staged candles, writing every timeframe when `aggregate`.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py` as a subprocess, whose `--aggregate` mode
    fanned one download out to `_1m` through `_1d`; that importer existed only
    for QuestDB and is gone, so the fan-out is `aggregate_rows` above.

    `dedup=True` here, unlike the vendor bar importers: the aggregation emits
    one row per (contract, bucket) already, so a repeat is a correction rather
    than a DST duplicate.
    """
    rows = staged_rows(parquet_path, timezone)
    if not rows:
        return
    suffixes = TIMEFRAME_SUFFIXES if aggregate else (None,)
    sender = pw.Sender()
    for suffix in suffixes:
        if suffix is None:
            table = safe_identifier(base_table)
            emitted = [
                (row["stored_seconds"] * 1_000_000_000, {
                    name: row[name] for name in
                    ("underlying", "osi", "open", "high", "low", "close", "volume")
                })
                for row in rows
            ]
        else:
            table = safe_identifier(f"{base_table}_{suffix}")
            emitted = aggregate_rows(rows, TIMEFRAME_SECONDS[suffix])
        for moment, columns in emitted:
            sender.row(table, columns=columns, at=pw.TimestampNanos(moment))
    sender.flush()


def replace_latest_minute(
    parquet_path: Path,
    base_table: str,
    candle_time_utc: datetime,
    timezone: ZoneInfo,
) -> None:
    """Upsert one candle.

    THE UPSERT IS NOW THE DEFAULT. QuestDB needed a count, then either an ILP
    insert or a hand-built `UPDATE ... SET` and a 30-second poll, because it
    could not replace a row. A write at a timestamp the shard already holds
    replaces it, so all three paths collapse into one.
    """
    table = safe_identifier(f"{base_table}_1m")
    stored_time = candle_time_utc.astimezone(timezone).replace(tzinfo=dt_timezone.utc)
    timestamp_text = stored_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    row = pq.read_table(parquet_path).to_pylist()[0]
    numeric_fields = ("open", "high", "low", "close")
    if any(not math.isfinite(float(row[field])) for field in numeric_fields):
        raise RuntimeError("Yahoo Finance returned a non-finite candle value")

    run_importer(parquet_path, table, timezone, aggregate=False)

    moment = int(stored_time.timestamp()) * 1_000_000_000
    _, stored = pw.read_columns(table, [], moment, moment + 1)
    if len(stored) != 1:
        raise RuntimeError(
            f"Expected one {table} row at {timestamp_text}; found {len(stored)}"
        )
    print(f"Verified {table}: one candle at {timestamp_text}.", flush=True)


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


def verify_import(base_table: str, start: date, end: date, source_rows: int) -> None:
    """Every timeframe holds rows, `_1m` holds exactly the source count.

    No polling: the three 120-second waits this replaces existed because a
    QuestDB table appeared and filled asynchronously after an ILP flush.
    """
    for suffix in TIMEFRAME_SUFFIXES:
        table = safe_identifier(f"{base_table}_{suffix}")
        if not table_exists(table):
            raise RuntimeError(f"Expected table was not created: {table}")
        rows = count_range(table, start, end)
        if suffix == "1m" and rows != source_rows:
            raise RuntimeError(f"{table} has {rows:,} rows in range; expected {source_rows:,}")
        if rows == 0:
            raise RuntimeError(f"{table} has no rows in the imported range")
        duplicate = find_duplicate(table, start, end)
        if duplicate is not None:
            raise RuntimeError(f"Duplicate timestamp/osi in {table}: {duplicate}")
    print(f"Verified {base_table}: every timeframe populated, no duplicate key.")


def main() -> int:
    args = parse_args()
    if args.latest_minute:
        symbol = args.symbol.upper()
        try:
            base_table = safe_identifier(args.table_prefix)
            with tempfile.TemporaryDirectory(prefix="yfinance-latest-") as temporary_dir:
                output = Path(temporary_dir) / f"yfinance_{symbol}_latest_1m.parquet"
                candle_time = download_latest_minute(output, symbol, args.timezone)
                replace_latest_minute(
                    output, base_table, candle_time, args.timezone,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Yahoo Finance latest-minute pipeline failed: {exc}", file=sys.stderr)
            return 1
        print(f"Complete: latest completed {symbol} candle upserted into {base_table}_1m.")
        return 0

    # Yahoo can expose a partial previous date before all of its intraday bars
    # have settled.  Leave one full calendar day between today and the newest
    # requested date so a partial day can never replace a complete partition.
    # An explicit --end-date is never allowed past the safe date: a partial
    # trailing day would replace a complete partition with an incomplete one.
    safe_end = latest_fully_available_date(args.timezone)
    end = min(args.end_date, safe_end) if args.end_date else safe_end
    start = end - timedelta(days=max(args.days, 1) - 1)
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
            clear_destination_range(base_table, start, end)
            run_importer(output, base_table, args.timezone)
            verify_import(base_table, start, end, source_rows)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Yahoo Finance pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Complete: {base_table}_1m through {base_table}_1d replaced for "
        f"{start.isoformat()} through {end.isoformat()}.", flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
