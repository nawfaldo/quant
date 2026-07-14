#!/usr/bin/env python3
"""Stream CSV data to QuestDB over ILP/TCP.

Raw mode imports arbitrary CSV columns. Aggregate mode consumes sorted 1-minute
OHLCV rows and writes 1m, 5m, 15m, 30m, 1h, 4h, and 1d tables in one pass.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import math
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TIMEFRAMES = ((1, "1m"), (5, "5m"), (15, "15m"), (30, "30m"), (60, "1h"), (240, "4h"), (1440, "1d"))


@dataclass
class Candle:
    timestamp_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="questdb_csv_importer.py",
        description="Stream a CSV file to QuestDB, optionally aggregating OHLCV bars.",
    )
    parser.add_argument("input", type=Path, help="CSV file")
    parser.add_argument("--table", help="table/base name (default: filename stem)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9009)
    parser.add_argument("--ts-col", default="0", help="timestamp column name or index")
    parser.add_argument("--ts-col2", help="optional time column name or index to merge")
    parser.add_argument("--delim", default=",", help="single-character delimiter")
    parser.add_argument("--tz-hours", type=int, default=0, help="fixed hours added to timestamps")
    parser.add_argument(
        "--tz-et",
        action="store_true",
        help="convert true UTC to New York wall-clock stored as UTC (DST-aware)",
    )
    parser.add_argument("--tag-cols", default="", help="comma-separated string columns stored as SYMBOL tags")
    parser.add_argument("--aggregate", "-a", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if len(args.delim) != 1:
        parser.error("--delim must be one character")
    if args.tz_et and args.tz_hours:
        parser.error("use either --tz-et or --tz-hours, not both")
    return args


def escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def escape_name(value: str) -> str:
    return escape_measurement(value).replace("=", "\\=")


def quote_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def scale_unix_to_ns(value: int) -> int:
    magnitude = abs(value)
    if magnitude >= 100_000_000_000_000_000:
        return value
    if magnitude >= 100_000_000_000_000:
        return value * 1_000
    if magnitude >= 100_000_000_000:
        return value * 1_000_000
    return value * 1_000_000_000


def datetime_to_ns(value: datetime) -> int:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    seconds = calendar.timegm(value.timetuple())
    return seconds * 1_000_000_000 + value.microsecond * 1_000


def parse_timestamp(value: str) -> int:
    text = value.strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return scale_unix_to_ns(int(text))

    slash = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?",
        text,
    )
    if slash:
        day, month, year, hour, minute, second, fraction = slash.groups()
        value_dt = datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0),
            int(second or 0), int((fraction or "0").ljust(6, "0")),
        )
        return datetime_to_ns(value_dt)

    dotted = re.fullmatch(
        r"(\d{4})\.(\d{2})\.(\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?",
        text,
    )
    if dotted:
        year, month, day, hour, minute, second, fraction = dotted.groups()
        value_dt = datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0),
            int(second or 0), int((fraction or "0").ljust(6, "0")),
        )
        return datetime_to_ns(value_dt)

    try:
        value_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unsupported timestamp: {text!r}") from exc
    return datetime_to_ns(value_dt)


def utc_ns_to_fake_et(timestamp_ns: int) -> int:
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    utc_dt = EPOCH + timedelta(seconds=seconds, microseconds=nanos // 1_000)
    et = utc_dt.astimezone(NEW_YORK)
    return calendar.timegm(et.timetuple()) * 1_000_000_000 + et.microsecond * 1_000 + nanos % 1_000


def adjusted_timestamp(raw: str, raw2: str | None, args: argparse.Namespace) -> int:
    timestamp_ns = parse_timestamp(f"{raw.strip()} {raw2.strip()}" if raw2 is not None else raw)
    if args.tz_et:
        return utc_ns_to_fake_et(timestamp_ns)
    return timestamp_ns + args.tz_hours * 3_600_000_000_000


def resolve_column(value: str | None, header: list[str], *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    assert value is not None
    try:
        index = int(value)
    except ValueError:
        try:
            return header.index(value)
        except ValueError as exc:
            raise ValueError(f"column {value!r} not found in header") from exc
    if index < 0 or index >= len(header):
        raise ValueError(f"column index {index} out of range")
    return index


def infer_type(value: str) -> str:
    text = value.strip()
    try:
        float(text)
        return "float"
    except ValueError:
        return "string"


def encoded_field(value: str, value_type: str, name: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if value_type == "integer":
        return f"{int(text)}i"
    if value_type == "float":
        number = float(text)
        if not math.isfinite(number):
            return None
        if name.lower() == "volume" or name.endswith(("_count", "_cnt", "_qty")):
            return f"{round(number)}i"
        return repr(number)
    return quote_string(text)


def raw_line(
    row: list[str], header: list[str], types: list[str], table: str,
    ts_index: int, ts2_index: int | None, tag_cols: set[str], args: argparse.Namespace,
) -> bytes:
    if len(row) < len(header):
        raise ValueError("row has fewer columns than header")
    timestamp_ns = adjusted_timestamp(row[ts_index], row[ts2_index] if ts2_index is not None else None, args)
    tags: list[str] = []
    fields: list[str] = []
    for index, (name, value, value_type) in enumerate(zip(header, row, types)):
        if index == ts_index or index == ts2_index:
            continue
        if name in tag_cols:
            if value.strip():
                tags.append(f"{escape_name(name)}={escape_name(value.strip())}")
            continue
        encoded = encoded_field(value, value_type, name)
        if encoded is not None:
            fields.append(f"{escape_name(name)}={encoded}")
    if not fields:
        raise ValueError("row has no fields")
    tag_text = "," + ",".join(tags) if tags else ""
    return f"{escape_measurement(table)}{tag_text} {','.join(fields)} {timestamp_ns}\n".encode()


def detect_ohlcv(header: list[str]) -> tuple[int, int, int, int, int | None]:
    lower = [name.lower() for name in header]
    try:
        open_i, high_i, low_i, close_i = (lower.index(name) for name in ("open", "high", "low", "close"))
    except ValueError as exc:
        raise ValueError("--aggregate requires open/high/low/close columns") from exc
    volume_i = lower.index("volume") if "volume" in lower else lower.index("vol") if "vol" in lower else None
    return open_i, high_i, low_i, close_i, volume_i


def candle_line(table: str, candle: Candle) -> bytes:
    volume = round(candle.volume)
    return (
        f"{escape_measurement(table)} open={candle.open!r},high={candle.high!r},"
        f"low={candle.low!r},close={candle.close!r},volume={volume}i {candle.timestamp_ns}\n"
    ).encode()


def aggregate_rows(
    rows: Iterable[list[str]], header: list[str], base: str, ts_index: int,
    ts2_index: int | None, args: argparse.Namespace,
) -> Iterable[bytes]:
    open_i, high_i, low_i, close_i, volume_i = detect_ohlcv(header)
    states: dict[int, Candle] = {}
    tables = {minutes: f"{base}_{suffix}" for minutes, suffix in TIMEFRAMES}
    for row in rows:
        timestamp_ns = adjusted_timestamp(row[ts_index], row[ts2_index] if ts2_index is not None else None, args)
        values = float(row[open_i]), float(row[high_i]), float(row[low_i]), float(row[close_i])
        volume = float(row[volume_i]) if volume_i is not None and row[volume_i].strip() else 0.0
        for minutes, _ in TIMEFRAMES:
            period_ns = minutes * 60 * 1_000_000_000
            bar_ns = timestamp_ns // period_ns * period_ns
            state = states.get(minutes)
            if state is not None and state.timestamp_ns != bar_ns:
                yield candle_line(tables[minutes], state)
                state = None
            if state is None:
                states[minutes] = Candle(bar_ns, *values, volume)
            else:
                state.high = max(state.high, values[1])
                state.low = min(state.low, values[2])
                state.close = values[3]
                state.volume += volume
    for minutes, _ in TIMEFRAMES:
        if minutes in states:
            yield candle_line(tables[minutes], states[minutes])


def strip_timeframe_suffix(table: str) -> str:
    return re.sub(r"_(?:1m|5m|15m|30m|1h|4h|1d)$", "", table)


def send_lines(lines: Iterable[bytes], args: argparse.Namespace) -> tuple[int, int]:
    sock = None if args.dry_run else socket.create_connection((args.host, args.port), timeout=30)
    sent = skipped = 0
    pending: list[bytes] = []
    try:
        for line in lines:
            if args.limit is not None and sent >= args.limit:
                break
            if args.dry_run and args.limit is None and sent >= 3:
                break
            if args.dry_run and sent < 3:
                print(line.decode().rstrip())
            elif sock is not None:
                pending.append(line)
                if len(pending) >= args.batch_size:
                    sock.sendall(b"".join(pending))
                    pending.clear()
            sent += 1
            if not args.dry_run and sent % 50_000 == 0:
                print(f"\rSent {sent:,} ILP rows...", end="", file=sys.stderr, flush=True)
        if sock is not None and pending:
            sock.sendall(b"".join(pending))
    finally:
        if sock is not None:
            sock.close()
    return sent, skipped


def main() -> int:
    args = parse_args()
    path = args.input.expanduser()
    if not path.is_file():
        raise ValueError(f"CSV file not found: {path}")
    table = args.table or re.sub(r"[^A-Za-z0-9_]+", "_", path.stem).strip("_").lower()
    tag_cols = {name.strip() for name in args.tag_cols.split(",") if name.strip()}

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, delimiter=args.delim)
        try:
            header_row = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV file is empty") from exc
        header = [name.strip().strip('"').strip("<>") for name in header_row]
        ts_index = resolve_column(args.ts_col, header)
        ts2_index = resolve_column(args.ts_col2, header, optional=True)
        assert ts_index is not None
        try:
            first_row = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV has a header but no data rows") from exc
        rows = chain([first_row], reader)

        if args.aggregate:
            base = strip_timeframe_suffix(table)
            lines = aggregate_rows(rows, header, base, ts_index, ts2_index, args)
        else:
            types = [infer_type(value) for value in first_row]
            lines = (
                raw_line(row, header, types, table, ts_index, ts2_index, tag_cols, args)
                for row in rows
            )
        sent, skipped = send_lines(lines, args)

    mode = "Dry run" if args.dry_run else "Done"
    print(f"\n{mode}. Sent {sent:,} ILP rows; skipped {skipped:,}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
