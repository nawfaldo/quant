#!/usr/bin/env python3
"""Download, replace, aggregate, and verify LSE option candles in the store.

The source timestamps are real UTC.  The companion Parquet importer converts
them to configurable timezone wall-clock values stored as UTC, matching this repository's
NQ convention.  Before importing, the requested date window is deleted from
all seven destination tables so reruns and overlapping ranges are idempotent.
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
from datetime import date, datetime, time as dtime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import parquet_writer as pw

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


VAULT_URL = "https://api.londonstrategicedge.com/vault"
EXPORT_URL = f"{VAULT_URL}/export"
POLL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 30 * 60
TIMEFRAME_SUFFIXES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
REQUIRED_COLUMNS = {
    "ts",
    "underlying",
    "expiry",
    "opt_type",
    "strike",
    "osi",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def load_local_env() -> None:
    """Load simple KEY=value entries from the .env beside this script."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        os.environ.setdefault(name, value)


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
            "Download LSE 1-minute option candles, replace the requested "
            "stored range, and aggregate all timeframes."
        )
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="underlying symbol to fetch",
    )
    parser.add_argument(
        "--date",
        dest="date_range",
        type=parse_date_range,
        required=True,
        metavar="DD/MM/YY-DD/MM/YY",
        help="inclusive date range in --timezone",
    )
    parser.add_argument(
        "--table-prefix",
        required=True,
        help="table prefix; timeframe suffixes are appended "
        "(e.g. qqq_options)",
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


def default_output(symbol: str, start: date, end: date) -> Path:
    return Path(f"options_{symbol.upper()}_1m_{start.isoformat()}_{end.isoformat()}.parquet")


def api_request(
    url: str,
    api_key: str,
    *,
    payload: dict[str, str] | None = None,
) -> Request:
    headers = {
        "x-api-key": api_key,
        "Accept": "application/octet-stream, application/json",
        "User-Agent": "lse-options-importer/2.0",
    }
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
    return Request(url, data=data, headers=headers, method=method)


def read_response(request: Request) -> tuple[bytes, str]:
    with urlopen(request, timeout=300) as response:
        return response.read(), response.headers.get_content_type()


def read_json(request: Request) -> dict[str, Any]:
    body, _ = read_response(request)
    result = json.loads(body.decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError(f"Expected a JSON object, received {type(result).__name__}")
    return result


def download(api_key: str, start: date, end: date, output: Path, symbol: str) -> None:
    payload = {
        "dataset": "options",
        "symbol": symbol.upper(),
        "timeframe": "1m",
        "start": start.isoformat(),
        "end": (end + timedelta(days=1)).isoformat(),
        "format": "parquet",
    }
    result = read_json(api_request(EXPORT_URL, api_key, payload=payload))
    job_id = result.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(
            "Export did not return a job_id:\n" + json.dumps(result, indent=2)[:4000]
        )

    poll_value = result.get("poll")
    poll_url = (
        f"{VAULT_URL}{poll_value}"
        if isinstance(poll_value, str) and poll_value.startswith("/")
        else f"{EXPORT_URL}/{job_id}"
    )
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        status_result = read_json(api_request(poll_url, api_key))
        status = status_result.get("status")
        if status == "ready":
            break
        if status in {"failed", "expired"}:
            raise RuntimeError(
                f"Export job {status}: " + json.dumps(status_result, indent=2)[:4000]
            )
        if status not in {"queued", "running"}:
            raise RuntimeError(f"Unknown export status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Export did not finish within {POLL_TIMEOUT_SECONDS // 60} minutes"
            )
        print(f"Export job {status}; checking again in {POLL_SECONDS}s...", flush=True)
        time.sleep(POLL_SECONDS)

    body, content_type = read_response(
        api_request(f"{EXPORT_URL}/{job_id}/download", api_key)
    )
    if content_type == "application/json":
        raise RuntimeError(
            "Download returned JSON instead of Parquet:\n"
            + body.decode("utf-8", errors="replace")[:4000]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    temporary.write_bytes(body)
    temporary.replace(output)


def validate_parquet(path: Path, start: date, end: date, timezone: ZoneInfo) -> int:
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows <= 0:
        raise RuntimeError("Downloaded Parquet contains no option rows")
    schema = parquet.schema_arrow
    missing = sorted(REQUIRED_COLUMNS.difference(schema.names))
    if missing:
        raise RuntimeError(f"Downloaded Parquet is missing: {', '.join(missing)}")
    ts_type = schema.field("ts").type
    if not pa.types.is_timestamp(ts_type) or ts_type.tz != "UTC":
        raise RuntimeError(f"Expected ts to be a UTC timestamp, received {ts_type}")

    ts_column = pq.read_table(path, columns=["ts"])["ts"]
    first = pc.min(ts_column).as_py()
    last = pc.max(ts_column).as_py()
    if first is None or last is None:
        raise RuntimeError("Downloaded Parquet has no valid timestamps")
    first_local = first.astimezone(timezone)
    last_local = last.astimezone(timezone)
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

    `ALTER TABLE ... DROP PARTITION` and the poll that waited for it are gone:
    a shard is a file and deleting rows is synchronous.
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


def run_importer(parquet_path: Path, base_table: str, timezone: ZoneInfo) -> None:
    """Load the staged candles and write every timeframe.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py --aggregate` as a subprocess; that importer
    existed only for QuestDB and is gone. Each option contract is bucketed
    INDEPENDENTLY, keyed on `osi` -- a chain holds many contracts on the same
    timestamps, and mixing them would give one meaningless candle per bucket
    instead of one per contract.
    """
    handle = pq.ParquetFile(str(parquet_path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()

    carried = [
        name for name in staged.schema.names
        if name not in ("ts", "open", "high", "low", "close", "volume")
    ]
    rows = []
    for record in staged.to_pylist():
        local = record["ts"].replace(tzinfo=dt_timezone.utc).astimezone(timezone)
        rows.append((
            int(local.replace(tzinfo=dt_timezone.utc).timestamp()),
            record,
        ))
    if not rows:
        return

    sender = pw.Sender()
    for suffix in TIMEFRAME_SUFFIXES:
        table = safe_identifier(f"{base_table}_{suffix}")
        width = TIMEFRAME_SECONDS[suffix]
        buckets: dict[tuple[Any, int], dict] = {}
        for seconds, record in rows:
            bucket = seconds // width * width
            key = (record.get("osi"), bucket)
            current = buckets.get(key)
            if current is None:
                candle = {name: record[name] for name in carried}
                candle.update({
                    "open": float(record["open"]),
                    "high": float(record["high"]),
                    "low": float(record["low"]),
                    "close": float(record["close"]),
                    "volume": record["volume"],
                })
                buckets[key] = {"_bucket": bucket, **candle}
            else:
                current["high"] = max(current["high"], float(record["high"]))
                current["low"] = min(current["low"], float(record["low"]))
                current["close"] = float(record["close"])
                current["volume"] += record["volume"]
        for candle in sorted(buckets.values(), key=lambda value: value["_bucket"]):
            moment = candle.pop("_bucket")
            sender.row(table, columns=candle,
                       at=pw.TimestampNanos(moment * 1_000_000_000))
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


def verify_import(base_table: str, start: date, end: date, source_rows: int) -> None:
    """Every timeframe holds rows, `_1m` holds exactly the source count.

    No polling: the waits this replaces existed because a QuestDB table
    appeared and filled asynchronously after an ILP flush.
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
    load_local_env()
    api_key = os.getenv("LSE_API_KEY") or os.getenv("KEY")
    if not api_key:
        print("Missing API key in LSE_API_KEY (or KEY).", file=sys.stderr)
        return 2

    start, end = args.date_range
    symbol = args.symbol.upper()
    base_table = safe_identifier(args.table_prefix)
    try:
        with tempfile.TemporaryDirectory(prefix="lse-import-") as temporary_dir:
            output = Path(temporary_dir) / default_output(symbol, start, end)
            print(
                f"Downloading {symbol} option 1m candles for the inclusive {args.timezone.key} "
                f"range {start.isoformat()} through {end.isoformat()}...",
                flush=True,
            )
            download(api_key, start, end, output, symbol)
            print(f"Saved {output} ({output.stat().st_size:,} bytes).", flush=True)
            source_rows = validate_parquet(output, start, end, args.timezone)
            clear_destination_range(base_table, start, end)
            run_importer(output, base_table, args.timezone)
            verify_import(base_table, start, end, source_rows)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"LSE API returned HTTP {exc.code}: {details[:2000]}", file=sys.stderr)
        return 1
    except (URLError, OSError, ValueError, RuntimeError) as exc:
        print(f"LSE option pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Complete: {base_table}_1m through {base_table}_1d replaced for "
        f"{start.isoformat()} through {end.isoformat()}.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
