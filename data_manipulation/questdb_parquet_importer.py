#!/usr/bin/env python3
"""Stream Parquet rows to QuestDB over ILP/TCP.

QuestDB can store source timestamps as configurable timezone wall-clock values
labelled as UTC.  --timezone converts real UTC instants to that convention,
including daylight-saving transitions.
"""

from __future__ import annotations

import argparse
import calendar
import math
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TIMEFRAMES = ((1, "1m"), (5, "5m"), (15, "15m"), (30, "30m"), (60, "1h"), (240, "4h"), (1440, "1d"))
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass
class AggregateRow:
    timestamp_ns: int
    values: dict[str, Any]


class OptionAggregator:
    """Aggregate chronologically sorted option rows independently by contract."""

    def __init__(
        self,
        base_table: str,
        schema: pa.Schema,
        ts_col: str,
        contract_col: str,
        tag_cols: set[str],
    ) -> None:
        self.schema = schema
        self.ts_col = ts_col
        self.contract_col = contract_col
        self.tag_cols = tag_cols
        self.tables = {minutes: f"{base_table}_{suffix}" for minutes, suffix in TIMEFRAMES}
        self.current_buckets: dict[int, int | None] = {minutes: None for minutes, _ in TIMEFRAMES[1:]}
        self.states: dict[int, dict[Any, AggregateRow]] = {minutes: {} for minutes, _ in TIMEFRAMES[1:]}
        self.last_timestamp_ns: int | None = None

    def process_batch(
        self,
        columns: dict[str, list[Any]],
        timestamps: list[int | None],
    ) -> tuple[list[bytes], dict[str, int]]:
        output: list[bytes] = []
        counts: dict[str, int] = {}
        for row_index, timestamp_ns in enumerate(timestamps):
            if timestamp_ns is None:
                continue
            if self.last_timestamp_ns is not None and timestamp_ns < self.last_timestamp_ns:
                raise ValueError("Parquet rows must be globally sorted by timestamp for --aggregate")
            self.last_timestamp_ns = timestamp_ns
            contract = columns[self.contract_col][row_index]
            if contract is None:
                continue

            for minutes, _ in TIMEFRAMES[1:]:
                period_ns = minutes * 60 * 1_000_000_000
                bucket = timestamp_ns // period_ns * period_ns
                current_bucket = self.current_buckets[minutes]
                if current_bucket is not None and bucket != current_bucket:
                    table = self.tables[minutes]
                    flushed = self._flush_timeframe(minutes)
                    output.extend(flushed)
                    counts[table] = counts.get(table, 0) + len(flushed)
                self.current_buckets[minutes] = bucket

                state = self.states[minutes].get(contract)
                if state is None:
                    values = {
                        name: columns[name][row_index]
                        for name in self.schema.names
                        if name != self.ts_col
                    }
                    self.states[minutes][contract] = AggregateRow(bucket, values)
                else:
                    state.values["high"] = max(float(state.values["high"]), float(columns["high"][row_index]))
                    state.values["low"] = min(float(state.values["low"]), float(columns["low"][row_index]))
                    state.values["close"] = columns["close"][row_index]
                    state.values["volume"] = int(state.values["volume"]) + int(columns["volume"][row_index])
        return output, counts

    def finish(self) -> tuple[list[bytes], dict[str, int]]:
        output: list[bytes] = []
        counts: dict[str, int] = {}
        for minutes, _ in TIMEFRAMES[1:]:
            table = self.tables[minutes]
            flushed = self._flush_timeframe(minutes)
            output.extend(flushed)
            counts[table] = len(flushed)
        return output, counts

    def _flush_timeframe(self, minutes: int) -> list[bytes]:
        table = self.tables[minutes]
        lines = [
            encoded_row(table, self.schema, row.values, row.timestamp_ns, self.ts_col, self.tag_cols)
            for row in self.states[minutes].values()
        ]
        self.states[minutes].clear()
        return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="questdb_parquet_importer.py",
        description="Stream a Parquet file or directory of Parquet files to QuestDB."
    )
    parser.add_argument("input", type=Path, help="Parquet file or directory")
    parser.add_argument("--table", help="QuestDB table (default: normalized file stem)")
    parser.add_argument("--host", default="127.0.0.1", help="QuestDB ILP host")
    parser.add_argument("--port", type=int, default=9009, help="QuestDB ILP TCP port")
    parser.add_argument("--ts-col", default="ts", help="designated timestamp column")
    parser.add_argument(
        "--tag-cols",
        default="",
        help="comma-separated string columns to store as SYMBOL tags",
    )
    parser.add_argument(
        "--timezone",
        metavar="IANA_TIMEZONE",
        help="convert true UTC timestamps to timezone wall-clock values stored as UTC",
    )
    parser.add_argument(
        "--aggregate",
        "-a",
        action="store_true",
        help="write contract-level 1m/5m/15m/30m/1h/4h/1d tables",
    )
    parser.add_argument(
        "--contract-col",
        default="osi",
        help="unique option contract column used by --aggregate (default: osi)",
    )
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--limit", type=int, help="stop after this many total rows")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print sample ILP without connecting to QuestDB",
    )
    return parser.parse_args()


def parquet_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".parquet":
            raise ValueError(f"not a Parquet file: {input_path}")
        return [input_path]
    if not input_path.is_dir():
        raise ValueError(f"input does not exist: {input_path}")

    def natural_key(path: Path) -> tuple[str, int]:
        match = re.fullmatch(r"(.*)\((\d+)\)", path.stem)
        return (match.group(1), int(match.group(2))) if match else (path.stem, 0)

    files = sorted(input_path.glob("*.parquet"), key=natural_key)
    if not files:
        raise ValueError(f"no .parquet files found in: {input_path}")
    return files


def default_table(path: Path, files: list[Path]) -> str:
    stem = files[0].stem if path.is_dir() else path.stem
    stem = re.sub(r"\(\d+\)$", "", stem)
    return re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()


def escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def escape_name(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
        .replace("=", "\\=")
    )


def escape_tag(value: str) -> str:
    return escape_name(value)


def quote_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def fake_timezone_ns(utc_us: int, timezone: ZoneInfo, cache: dict[int, int]) -> int:
    cached = cache.get(utc_us)
    if cached is not None:
        return cached
    seconds, micros = divmod(utc_us, 1_000_000)
    utc_dt = EPOCH + timedelta(seconds=seconds, microseconds=micros)
    local = utc_dt.astimezone(timezone)
    # Reinterpret local wall-clock fields as UTC, matching this repository's convention.
    result = calendar.timegm(local.timetuple()) * 1_000_000_000 + local.microsecond * 1_000
    cache[utc_us] = result
    return result


def timestamp_ns(
    array: pa.Array,
    timezone: ZoneInfo | None,
    cache: dict[int, int],
) -> list[int | None]:
    if not pa.types.is_timestamp(array.type):
        raise TypeError(f"timestamp column must be a Parquet timestamp, got {array.type}")
    unit_scale = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}
    raw = pc.cast(array, pa.int64()).to_pylist()
    scale = unit_scale[array.type.unit]
    if timezone is None:
        return [None if value is None else value * scale for value in raw]

    if array.type.tz not in (None, "UTC"):
        raise ValueError(f"--timezone expects UTC timestamps, got timezone {array.type.tz!r}")
    if array.type.unit == "us":
        return [
            None if value is None else fake_timezone_ns(value, timezone, cache)
            for value in raw
        ]
    # Normalize less common timestamp units to microseconds without floats.
    values_us = [
        None if value is None else (value * scale) // 1_000
        for value in raw
    ]
    return [
        None if value is None else fake_timezone_ns(value, timezone, cache)
        for value in values_us
    ]


def field_value(value: Any, data_type: pa.DataType) -> str | None:
    if value is None:
        return None
    if pa.types.is_boolean(data_type):
        return "t" if value else "f"
    if pa.types.is_integer(data_type):
        return f"{int(value)}i"
    if pa.types.is_floating(data_type):
        number = float(value)
        return None if not math.isfinite(number) else repr(number)
    if pa.types.is_date(data_type):
        return quote_string(value.isoformat())
    if pa.types.is_timestamp(data_type):
        return quote_string(value.isoformat())
    if pa.types.is_binary(data_type) or pa.types.is_large_binary(data_type):
        return quote_string(bytes(value).hex())
    return quote_string(str(value))


def validate_schema(schema: pa.Schema, ts_col: str, tag_cols: set[str]) -> None:
    if ts_col not in schema.names:
        raise ValueError(f"timestamp column {ts_col!r} not found; columns: {schema.names}")
    if not pa.types.is_timestamp(schema.field(ts_col).type):
        raise TypeError(f"{ts_col!r} is {schema.field(ts_col).type}, not a timestamp")
    for name in tag_cols:
        if name not in schema.names:
            raise ValueError(f"tag column {name!r} not found")
        if not (pa.types.is_string(schema.field(name).type) or pa.types.is_large_string(schema.field(name).type)):
            raise TypeError(f"tag column {name!r} must be a string")


def validate_aggregate_schema(schema: pa.Schema, contract_col: str) -> None:
    required = {contract_col, *OHLCV_COLUMNS}
    missing = sorted(required.difference(schema.names))
    if missing:
        raise ValueError(f"--aggregate requires columns: {', '.join(missing)}")


def check_matching_schemas(files: Iterable[Path]) -> pa.Schema:
    iterator = iter(files)
    first_file = next(iterator)
    expected = pq.ParquetFile(first_file).schema_arrow
    for path in iterator:
        actual = pq.ParquetFile(path).schema_arrow
        if not actual.equals(expected):
            raise ValueError(f"schema mismatch between {first_file.name} and {path.name}")
    return expected


def decode_batch(
    batch: pa.RecordBatch,
    ts_col: str,
    timezone: ZoneInfo | None,
    tz_cache: dict[int, int],
) -> tuple[dict[str, list[Any]], list[int | None]]:
    schema = batch.schema
    columns = {name: batch.column(i).to_pylist() for i, name in enumerate(schema.names) if name != ts_col}
    timestamps = timestamp_ns(batch.column(schema.get_field_index(ts_col)), timezone, tz_cache)
    return columns, timestamps


def encoded_row(
    table: str,
    schema: pa.Schema,
    values: dict[str, Any],
    timestamp_ns: int,
    ts_col: str,
    tag_cols: set[str],
) -> bytes:
    measurement = escape_measurement(table)
    tags: list[str] = []
    fields: list[str] = []
    for field in schema:
        name = field.name
        if name == ts_col:
            continue
        value = values[name]
        if name in tag_cols:
            if value is not None:
                tags.append(f"{escape_name(name)}={escape_tag(str(value))}")
            continue
        encoded = field_value(value, field.type)
        if encoded is not None:
            fields.append(f"{escape_name(name)}={encoded}")
    if not fields:
        raise ValueError("row has no fields")
    tag_text = "," + ",".join(tags) if tags else ""
    return f"{measurement}{tag_text} {','.join(fields)} {timestamp_ns}\n".encode()


def batch_lines(
    table: str,
    schema: pa.Schema,
    columns: dict[str, list[Any]],
    timestamps: list[int | None],
    ts_col: str,
    tag_cols: set[str],
) -> list[bytes]:
    output: list[bytes] = []
    for row_index, timestamp_ns in enumerate(timestamps):
        if timestamp_ns is None:
            continue
        values = {name: columns[name][row_index] for name in schema.names if name != ts_col}
        output.append(encoded_row(table, schema, values, timestamp_ns, ts_col, tag_cols))
    return output


def strip_timeframe_suffix(table: str) -> str:
    return re.sub(r"_(?:1m|5m|15m|30m|1h|4h|1d)$", "", table)


def merge_counts(target: dict[str, int], additions: dict[str, int]) -> None:
    for table, count in additions.items():
        target[table] = target.get(table, 0) + count


def sample_timestamp(value_ns: int) -> str:
    seconds, nanos = divmod(value_ns, 1_000_000_000)
    return (EPOCH + timedelta(seconds=seconds, microseconds=nanos // 1_000)).isoformat()


def main() -> int:
    args = parse_args()
    try:
        import_timezone = ZoneInfo(args.timezone) if args.timezone else None
    except Exception as exc:
        print(f"Invalid IANA timezone: {args.timezone!r}: {exc}", file=sys.stderr)
        return 2
    files = parquet_files(args.input.expanduser())
    table = args.table or default_table(args.input, files)
    tag_cols = {name.strip() for name in args.tag_cols.split(",") if name.strip()}
    schema = check_matching_schemas(files)
    validate_schema(schema, args.ts_col, tag_cols)
    if args.aggregate:
        validate_aggregate_schema(schema, args.contract_col)
        table = strip_timeframe_suffix(table)

    print(f"Input: {len(files)} Parquet file(s)", file=sys.stderr)
    if args.aggregate:
        print("Tables: " + ", ".join(f"{table}_{suffix}" for _, suffix in TIMEFRAMES), file=sys.stderr)
    else:
        print(f"Table: {table}", file=sys.stderr)
    print(f"Rows: {sum(pq.ParquetFile(path).metadata.num_rows for path in files):,}", file=sys.stderr)
    print(
        f"Timestamp mode: UTC -> {import_timezone.key} wall-clock (fake UTC)"
        if import_timezone is not None
        else "Timestamp mode: preserve true UTC",
        file=sys.stderr,
    )

    sock: socket.socket | None = None
    if not args.dry_run:
        sock = socket.create_connection((args.host, args.port), timeout=30)
        sock.settimeout(None)

    imported = 0
    tz_cache: dict[int, int] = {}
    sent_counts: dict[str, int] = {}
    aggregator = OptionAggregator(table, schema, args.ts_col, args.contract_col, tag_cols) if args.aggregate else None
    dry_run_samples: dict[str, bytes] = {}
    try:
        for path in files:
            parquet = pq.ParquetFile(path)
            file_rows = 0
            for batch in parquet.iter_batches(batch_size=args.batch_size):
                if args.limit is not None:
                    remaining = args.limit - imported
                    if remaining <= 0:
                        break
                    if batch.num_rows > remaining:
                        batch = batch.slice(0, remaining)
                columns, timestamps = decode_batch(
                    batch,
                    args.ts_col,
                    import_timezone,
                    tz_cache,
                )
                base_table = f"{table}_1m" if aggregator is not None else table
                lines = batch_lines(base_table, schema, columns, timestamps, args.ts_col, tag_cols)
                counts = {base_table: len(lines)}
                if aggregator is not None:
                    aggregate_lines, aggregate_counts = aggregator.process_batch(columns, timestamps)
                    lines.extend(aggregate_lines)
                    merge_counts(counts, aggregate_counts)

                imported += batch.num_rows
                file_rows += batch.num_rows
                if args.dry_run:
                    for line in lines:
                        line_table = line.split(b",", 1)[0].split(b" ", 1)[0].decode().replace("\\ ", " ")
                        dry_run_samples.setdefault(line_table, line)
                    if aggregator is not None:
                        final_lines, _ = aggregator.finish()
                        for line in final_lines:
                            line_table = line.split(b",", 1)[0].split(b" ", 1)[0].decode().replace("\\ ", " ")
                            dry_run_samples.setdefault(line_table, line)
                    expected_samples = 7 if aggregator is not None else 1
                    if len(dry_run_samples) >= expected_samples:
                        break
                else:
                    assert sock is not None
                    sock.sendall(b"".join(lines))
                    merge_counts(sent_counts, counts)
                if imported % 100_000 < batch.num_rows:
                    print(f"\rImported {imported:,} rows...", end="", file=sys.stderr, flush=True)
            if args.dry_run and len(dry_run_samples) >= (7 if aggregator is not None else 1):
                break
            print(f"\rImported {imported:,} rows ({path.name}: {file_rows:,})", file=sys.stderr)
            if args.limit is not None and imported >= args.limit:
                break
        if aggregator is not None and not args.dry_run:
            final_lines, final_counts = aggregator.finish()
            assert sock is not None
            sock.sendall(b"".join(final_lines))
            merge_counts(sent_counts, final_counts)
    finally:
        if sock is not None:
            sock.close()

    if args.dry_run:
        for sample_table in sorted(dry_run_samples):
            print(dry_run_samples[sample_table].decode().rstrip())
        if dry_run_samples:
            first_line = next(iter(dry_run_samples.values()))
            ts_text = first_line.decode().rsplit(" ", 1)[1].strip()
            print(f"First stored timestamp: {sample_timestamp(int(ts_text))}", file=sys.stderr)
        return 0

    print(f"Done. Read {imported:,} source rows.", file=sys.stderr)
    for sent_table in sorted(sent_counts):
        print(f"  {sent_table}: {sent_counts[sent_table]:,} rows sent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, pa.ArrowException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
