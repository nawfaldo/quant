"""Backfill Binance spot 1-minute klines into an existing stored table.

By default the script replaces the table's latest (possibly partial) day and
appends through yesterday. Binance timestamps are real UTC instants; this
repository stores exchange-local wall-clock timestamps labelled as UTC, so the
default ``America/New_York`` conversion deliberately preserves the convention
already used by ``btc_1m``.

The public Binance market-data endpoint needs no API key. Downloads and overlap
validation complete before any stored row is removed.
"""

from __future__ import annotations


import argparse
import concurrent.futures
import math
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

import parquet_writer as pw
import requests


UTC = timezone.utc
MINUTE_MS = 60_000
API_URL = "https://data-api.binance.vision/api/v3/klines"
COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
)
SCHEMA = pa.schema([
    pa.field("ts", pa.timestamp("ms", tz="UTC")),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()),
    pa.field("quote_volume", pa.float64()),
    pa.field("trades", pa.int64()),
    pa.field("taker_buy_base", pa.float64()),
    pa.field("taker_buy_quote", pa.float64()),
])


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid IANA timezone: {value!r}") from exc


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table identifier: {value!r}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace the latest BTC minute day and append free Binance spot klines."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Binance spot symbol")
    parser.add_argument("--table", default="btc_1m", help="existing stored table")
    parser.add_argument(
        "--timezone",
        type=parse_timezone,
        default=ZoneInfo("America/New_York"),
        help="wall-clock timezone stored as fake UTC (default: America/New_York)",
    )
    parser.add_argument("--start", type=iso_date, help="inclusive local start date")
    parser.add_argument("--end", type=iso_date, help="inclusive local end date")
    return parser.parse_args()


def latest_timestamp(table: str) -> datetime:
    newest = pw.max_timestamp_ns(table)
    if newest is None:
        raise RuntimeError(f"{table} is empty")
    return datetime.fromtimestamp(newest / 1e9, tz=UTC)


def partition_start(day: date) -> date:
    """The first day this run re-downloads.

    THE DAY ITSELF, NOT THE MONTH. QuestDB partitioned these tables by MONTH,
    so replacing the tail meant dropping and re-downloading the whole month --
    up to thirty days of klines to correct one. The store shards by day, so the
    replacement is exactly the days that are being replaced.
    """
    return day


def range_nanoseconds(start: date, end: date) -> tuple[int, int]:
    """`[start, end]` inclusive of `end`, as half-open epoch nanoseconds."""
    lower = int(datetime.combine(start, datetime_time.min, tzinfo=UTC).timestamp())
    upper = int(
        datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=UTC).timestamp()
    )
    return lower * 1_000_000_000, upper * 1_000_000_000


def range_count(table: str, start: date, end: date) -> int:
    lower, upper = range_nanoseconds(start, end)
    return len(pw.read_columns(table, [], lower, upper)[1])


def actual_bounds(start: date, end: date, wall_clock: ZoneInfo) -> tuple[int, int]:
    start_local = datetime.combine(start, datetime_time.min, tzinfo=wall_clock)
    end_local = datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=wall_clock)
    return int(start_local.timestamp() * 1000), int(end_local.timestamp() * 1000)


def stored_timestamp_ms(actual_ms: int, wall_clock: ZoneInfo) -> int:
    local = datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock)
    fake_utc = local.replace(tzinfo=UTC)
    return int(fake_utc.timestamp() * 1000)


def values(row: list[Any]) -> tuple[float | int, ...]:
    return (
        float(row[1]),
        float(row[2]),
        float(row[3]),
        float(row[4]),
        float(row[5]),
        float(row[7]),
        int(row[8]),
        float(row[9]),
        float(row[10]),
    )


def request_page(params: dict[str, Any]) -> list[list[Any]]:
    for attempt in range(8):
        try:
            response = requests.get(API_URL, params=params, timeout=30)
            if response.status_code in (418, 429):
                delay = float(response.headers.get("Retry-After", min(60, 2 ** attempt)))
                print(f"Binance rate limit; retrying in {delay:.1f}s...", flush=True)
                time.sleep(delay)
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected Binance response: {payload!r}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            if attempt == 7:
                raise RuntimeError(f"Binance download failed after retries: {exc}") from exc
            time.sleep(min(30.0, 2.0 ** attempt))
    raise RuntimeError("Binance download exhausted retries")


def page_table(rows: list[list[Any]]) -> pa.Table:
    return pa.Table.from_arrays([
        pa.array([int(row[0]) for row in rows], type=SCHEMA.field("ts").type),
        pa.array([float(row[1]) for row in rows], type=pa.float64()),
        pa.array([float(row[2]) for row in rows], type=pa.float64()),
        pa.array([float(row[3]) for row in rows], type=pa.float64()),
        pa.array([float(row[4]) for row in rows], type=pa.float64()),
        pa.array([float(row[5]) for row in rows], type=pa.float64()),
        pa.array([float(row[7]) for row in rows], type=pa.float64()),
        pa.array([int(row[8]) for row in rows], type=pa.int64()),
        pa.array([float(row[9]) for row in rows], type=pa.float64()),
        pa.array([float(row[10]) for row in rows], type=pa.float64()),
    ], schema=SCHEMA)


def download(
    output: Path,
    symbol: str,
    start: date,
    end: date,
    wall_clock: ZoneInfo,
    overlap_until_ms: int,
) -> tuple[int, int, dict[int, list[tuple[float | int, ...]]]]:
    start_ms, end_exclusive_ms = actual_bounds(start, end, wall_clock)
    expected_rows = (end_exclusive_ms - start_ms) // MINUTE_MS
    previous_ms: int | None = None
    row_count = 0
    stored_minutes: set[int] = set()
    overlap: dict[int, list[tuple[float | int, ...]]] = defaultdict(list)
    writer = pq.ParquetWriter(output, SCHEMA, compression="zstd")
    try:
        page_span = 1000 * MINUTE_MS
        page_starts = list(range(start_ms, end_exclusive_ms, page_span))

        def fetch(page_start: int) -> list[list[Any]]:
            return request_page({
                "symbol": symbol,
                "interval": "1m",
                "startTime": page_start,
                "endTime": min(end_exclusive_ms, page_start + page_span) - 1,
                "limit": 1000,
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            for offset in range(0, len(page_starts), 48):
                starts = page_starts[offset:offset + 48]
                pages = executor.map(fetch, starts)
                for page_start, page in zip(starts, pages):
                    rows = [row for row in page if page_start <= int(row[0]) < end_exclusive_ms]
                    expected_page_rows = min(1000, (end_exclusive_ms - page_start) // MINUTE_MS)
                    if len(rows) != expected_page_rows or int(rows[0][0]) != page_start:
                        raise RuntimeError(
                            f"Binance page at {page_start} has {len(rows)} rows; "
                            f"expected {expected_page_rows}"
                        )
                    for row in rows:
                        current_ms = int(row[0])
                        if previous_ms is not None and current_ms != previous_ms + MINUTE_MS:
                            raise RuntimeError(
                                f"non-contiguous Binance data: {previous_ms} followed by {current_ms}"
                            )
                        previous_ms = current_ms
                        stored_ms = stored_timestamp_ms(current_ms, wall_clock)
                        stored_minutes.add(stored_ms)
                        if stored_ms <= overlap_until_ms:
                            overlap[stored_ms].append(values(row))
                    writer.write_table(page_table(rows))
                    row_count += len(rows)
                    if row_count % 100_000 < len(rows):
                        print(f"  downloaded {row_count:,}/{expected_rows:,} rows", flush=True)
    finally:
        writer.close()

    if row_count != expected_rows:
        raise RuntimeError(f"downloaded {row_count:,} rows; expected {expected_rows:,}")
    if previous_ms != end_exclusive_ms - MINUTE_MS:
        raise RuntimeError("Binance download did not reach the requested final minute")
    return row_count, len(stored_minutes), overlap


def existing_overlap(
    table: str,
    start: date,
    latest: datetime,
) -> dict[int, list[tuple[float | int, ...]]]:
    lower = int(
        datetime.combine(start, datetime_time.min, tzinfo=UTC).timestamp()
    ) * 1_000_000_000
    # `<= latest` in the SQL, so the half-open upper bound is one nanosecond past.
    upper = int(latest.timestamp() * 1_000_000_000) + 1
    _, stored = pw.read_columns(table, list(COLUMNS), lower, upper)
    output: dict[int, list[tuple[float | int, ...]]] = defaultdict(list)
    # `read_columns` puts the timestamp FIRST; the loop below expects it last,
    # which is the order the SQL selected.
    for row in ([*values[1:], values[0]] for values in stored):
        ts_ms = int(row[9]) // 1_000_000
        output[ts_ms].append((
            float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), int(row[6]), float(row[7]), float(row[8]),
        ))
    return output


def assert_values_equal(expected: tuple[Any, ...], actual: tuple[Any, ...], ts_ms: int) -> None:
    for column, left, right in zip(COLUMNS, expected, actual):
        if column == "trades":
            equal = int(left) == int(right)
        else:
            equal = math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-9)
        if not equal:
            label = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat()
            raise RuntimeError(
                f"source mismatch at stored {label}, {column}: table={left}, Binance={right}"
            )


def assert_partial_candle_compatible(
    existing: tuple[Any, ...],
    downloaded: tuple[Any, ...],
    ts_ms: int,
) -> None:
    old = dict(zip(COLUMNS, existing))
    new = dict(zip(COLUMNS, downloaded))
    compatible = (
        math.isclose(float(old["open"]), float(new["open"]), rel_tol=1e-12, abs_tol=1e-9)
        and float(new["high"]) >= float(old["high"])
        and float(new["low"]) <= float(old["low"])
        and float(new["volume"]) >= float(old["volume"])
        and float(new["quote_volume"]) >= float(old["quote_volume"])
        and int(new["trades"]) >= int(old["trades"])
        and float(new["taker_buy_base"]) >= float(old["taker_buy_base"])
        and float(new["taker_buy_quote"]) >= float(old["taker_buy_quote"])
    )
    if not compatible:
        label = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat()
        raise RuntimeError(
            f"latest partial candle is incompatible at stored {label}: "
            f"table={existing}, Binance={downloaded}"
        )


def verify_overlap(
    existing: dict[int, list[tuple[float | int, ...]]],
    downloaded: dict[int, list[tuple[float | int, ...]]],
) -> int:
    compared = 0
    latest_ms = max(existing)
    for ts_ms, existing_rows in existing.items():
        downloaded_rows = downloaded.get(ts_ms, [])
        if len(existing_rows) != len(downloaded_rows):
            raise RuntimeError(
                f"overlap multiplicity mismatch at {ts_ms}: "
                f"table={len(existing_rows)}, Binance={len(downloaded_rows)}"
            )
        left_rows = sorted(existing_rows)
        right_rows = sorted(downloaded_rows)
        for left, right in zip(left_rows, right_rows):
            if ts_ms == latest_ms:
                assert_partial_candle_compatible(left, right, ts_ms)
            else:
                assert_values_equal(left, right, ts_ms)
            compared += 1
    if compared == 0:
        raise RuntimeError("no existing rows were available for Binance overlap validation")
    return compared


def clear_range(table: str, start: date, end: date) -> int:
    """Drop every stored row in `[start, end]`. Returns how many went.

    This was `ALTER TABLE ... DROP PARTITION`, guarded by a re-read of
    `partitionBy` in case the table's partitioning changed mid-download, and
    then a 120-second poll waiting for QuestDB to actually apply the drop.
    None of that survives: a shard is a file, deleting it is synchronous, and
    there is no partitioning scheme to change underneath the run.
    """
    lower, upper = range_nanoseconds(start, end)
    before = range_count(table, start, end)
    pw.delete_range(table, lower, upper)
    remaining = range_count(table, start, end)
    if remaining:
        raise RuntimeError(f"{remaining:,} rows survived the range delete")
    return before


def import_parquet(path: Path, table: str, wall_clock: ZoneInfo) -> None:
    """Load the staged download into the store.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This used to run
    `questdb_parquet_importer.py` as a subprocess to stream ILP; that importer
    existed only for QuestDB and is gone. The staged file already holds the
    validated, contiguous klines, so this reads it and appends.

    `dedup=False` IS LOAD-BEARING. The wall-clock conversion below makes the
    DST fall-back hour produce two bars with the SAME stored timestamp, and
    they are both real -- `main` counts them and fails if the number changes.
    A deduplicating write would silently delete an hour of history a year.
    """
    handle = pq.ParquetFile(str(path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()

    sender = pw.Sender(dedup=False)
    stamps = staged["ts"].to_pylist()
    columns = {name: staged[name].to_pylist() for name in COLUMNS}
    for index, moment in enumerate(stamps):
        stored_ms = stored_timestamp_ms(int(moment.timestamp() * 1000), wall_clock)
        sender.row(
            table,
            columns={name: columns[name][index] for name in COLUMNS},
            at=pw.TimestampNanos(stored_ms * 1_000_000),
        )
    sender.flush()


def verify_count(table: str, start: date, end: date, expected: int) -> None:
    """The range holds exactly `expected` rows.

    No polling. QuestDB needed a 180-second retry loop because a count taken
    right after an ILP flush understated the table while the WAL was still
    applying ([[questdb-update-read-lag]]); a shard is renamed into place
    complete, so the first read is the settled one.
    """
    observed = range_count(table, start, end)
    if observed != expected:
        raise RuntimeError(f"stored range has {observed:,} rows; expected {expected:,}")


def duplicate_timestamp_count(table: str, start: date, end: date) -> int:
    """Rows sharing a stored timestamp -- the DST fall-back hour, and nothing else."""
    lower, upper = range_nanoseconds(start, end)
    _, rows = pw.read_columns(table, [], lower, upper)
    seen: set[int] = set()
    extra = 0
    for row in rows:
        if row[0] in seen:
            extra += 1
        else:
            seen.add(row[0])
    return extra


def main() -> int:
    args = parse_args()
    try:
        table = safe_identifier(args.table)
        symbol = args.symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]+", symbol):
            raise ValueError(f"invalid Binance symbol: {symbol!r}")
        latest = latest_timestamp(table)
        requested_start = args.start or latest.date()
        start = partition_start(requested_start)
        end = args.end or (datetime.now(args.timezone).date() - timedelta(days=1))
        if end < start:
            print(f"Nothing to import: {table} latest date {start} is after {end}.")
            return 0
        print(
            f"Downloading free Binance {symbol} 1m data for local {args.timezone.key} "
            f"dates {start} through {end}...",
            flush=True,
        )
        with tempfile.TemporaryDirectory(prefix="binance-1m-") as temporary:
            parquet = Path(temporary) / f"{symbol}_1m_{start}_{end}.parquet"
            rows, unique_stored, downloaded_overlap = download(
                parquet,
                symbol,
                start,
                end,
                args.timezone,
                int(latest.timestamp() * 1000),
            )
            existing = existing_overlap(table, start, latest)
            compared = verify_overlap(existing, downloaded_overlap)
            print(
                f"Validated {compared:,} existing overlap rows against finalized Binance candles.",
                flush=True,
            )
            removed = clear_range(table, start, end)
            print(f"Replacing {removed:,} existing rows and importing {rows:,} rows...", flush=True)
            import_parquet(parquet, table, args.timezone)
            verify_count(table, start, end, rows)
            duplicates = duplicate_timestamp_count(table, start, end)
            expected_duplicates = rows - unique_stored
            if duplicates != expected_duplicates:
                raise RuntimeError(
                    f"the store has {duplicates} duplicate wall-clock timestamps; "
                    f"expected {expected_duplicates} from DST fallback"
                )
        final = latest_timestamp(table)
        expected_final = datetime.combine(end, datetime_time(23, 59), tzinfo=UTC)
        if final != expected_final:
            raise RuntimeError(f"final timestamp is {final.isoformat()}, expected {expected_final.isoformat()}")
        print(
            f"Complete: {table} now ends {final.isoformat()} with {rows:,} replacement rows; "
            f"{duplicates} duplicate wall-clock minutes are expected from DST fallback.",
            flush=True,
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Binance 1m pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
