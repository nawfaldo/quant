"""Create and backfill a stored 1-minute table from Binance spot klines.

Unlike ``binance_fetch&import_1m.py`` -- which extends an already-populated
table and requires perfectly contiguous data -- this script bootstraps a new
table from the symbol's first listed candle. Binance omits minutes with no
trades, so gaps are recorded and reported rather than treated as errors.

Timestamps follow the repository convention: exchange-local wall-clock values
labelled as UTC, so the default ``America/New_York`` conversion matches
``btc_1m``.
"""

from __future__ import annotations


import argparse
import concurrent.futures
import re
import sys
import tempfile
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

import parquet_writer as pw
import requests


UTC = timezone.utc
MINUTE_MS = 60_000
API_ROOT = "https://data-api.binance.vision/api/v3"
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
#: The columns a kline row carries, in the order the staged file holds them.
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Binance spot symbol, e.g. ETHUSDT")
    parser.add_argument("--table", required=True, help="table to create and fill")
    parser.add_argument(
        "--timezone",
        type=parse_timezone,
        default=ZoneInfo("America/New_York"),
        help="wall-clock timezone stored as fake UTC (default: America/New_York)",
    )
    parser.add_argument("--start", type=iso_date, help="inclusive local start date (default: listing)")
    parser.add_argument("--end", type=iso_date, help="inclusive local end date (default: yesterday)")
    return parser.parse_args()


def request_json(path: str, params: dict[str, Any]) -> Any:
    for attempt in range(8):
        try:
            response = requests.get(f"{API_ROOT}/{path}", params=params, timeout=30)
            if response.status_code in (418, 429):
                delay = float(response.headers.get("Retry-After", min(60, 2 ** attempt)))
                print(f"  rate limited; retrying in {delay:.1f}s...", flush=True)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 7:
                raise RuntimeError(f"Binance request failed after retries: {exc}") from exc
            time.sleep(min(30.0, 2.0 ** attempt))
    raise RuntimeError("Binance request exhausted retries")


def assert_trading(symbol: str) -> None:
    payload = request_json("exchangeInfo", {"symbol": symbol})
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not symbols:
        raise RuntimeError(f"{symbol} is not listed on Binance spot")
    status = symbols[0].get("status")
    if status != "TRADING":
        raise RuntimeError(f"{symbol} status is {status}, not TRADING")


def first_candle_ms(symbol: str) -> int:
    rows = request_json("klines", {"symbol": symbol, "interval": "1m", "startTime": 0, "limit": 1})
    if not rows:
        raise RuntimeError(f"{symbol} returned no klines")
    return int(rows[0][0])


def actual_bounds(start: date, end: date, wall_clock: ZoneInfo) -> tuple[int, int]:
    start_local = datetime.combine(start, datetime_time.min, tzinfo=wall_clock)
    end_local = datetime.combine(end + timedelta(days=1), datetime_time.min, tzinfo=wall_clock)
    return int(start_local.timestamp() * 1000), int(end_local.timestamp() * 1000)


def local_date(actual_ms: int, wall_clock: ZoneInfo) -> date:
    return datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock).date()


def stored_timestamp_ms(actual_ms: int, wall_clock: ZoneInfo) -> int:
    local = datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp() * 1000)


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
    start_ms: int,
    end_exclusive_ms: int,
    wall_clock: ZoneInfo,
) -> tuple[int, int, int]:
    """Write every available minute in [start_ms, end_exclusive_ms) to Parquet.

    Returns (rows, unique stored minutes, missing minutes).
    """
    possible = (end_exclusive_ms - start_ms) // MINUTE_MS
    page_span = 1000 * MINUTE_MS
    page_starts = list(range(start_ms, end_exclusive_ms, page_span))
    previous_ms: int | None = None
    row_count = 0
    stored_minutes: set[int] = set()

    def fetch(page_start: int) -> list[list[Any]]:
        return request_json("klines", {
            "symbol": symbol,
            "interval": "1m",
            "startTime": page_start,
            "endTime": min(end_exclusive_ms, page_start + page_span) - 1,
            "limit": 1000,
        })

    writer = pq.ParquetWriter(output, SCHEMA, compression="zstd")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            for offset in range(0, len(page_starts), 48):
                starts = page_starts[offset:offset + 48]
                for page_start, page in zip(starts, executor.map(fetch, starts)):
                    rows = [row for row in page if page_start <= int(row[0]) < end_exclusive_ms]
                    if not rows:
                        continue
                    for row in rows:
                        current_ms = int(row[0])
                        if current_ms % MINUTE_MS:
                            raise RuntimeError(f"kline open time {current_ms} is not minute-aligned")
                        if previous_ms is not None and current_ms <= previous_ms:
                            raise RuntimeError(
                                f"out-of-order Binance data: {previous_ms} followed by {current_ms}"
                            )
                        previous_ms = current_ms
                        stored_minutes.add(stored_timestamp_ms(current_ms, wall_clock))
                    writer.write_table(page_table(rows))
                    row_count += len(rows)
                    if row_count % 250_000 < len(rows):
                        print(f"  downloaded {row_count:,}/{possible:,} minutes", flush=True)
    finally:
        writer.close()

    if row_count == 0:
        raise RuntimeError("Binance returned no rows for the requested range")
    return row_count, len(stored_minutes), possible - row_count


def import_parquet(path: Path, table: str, wall_clock: ZoneInfo) -> None:
    """Load the staged download into the store.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py` as a subprocess to stream ILP; that importer
    existed only for QuestDB and is gone. There is no table to create first
    either -- a shard appears when its first row is written, so the whole
    `create table ... partition by MONTH` step goes with it.

    `dedup=False` IS LOAD-BEARING. The wall-clock conversion makes the DST
    fall-back hour produce two bars with the SAME stored timestamp, and both
    are real -- `main` reports the count and it must not change
    ([[dst-fakes-hourly-bar-gaps]]).
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


def verify_count(table: str, expected: int) -> None:
    """`table` holds exactly `expected` rows.

    No polling: the 300-second retry loop this replaces existed because a
    QuestDB count taken right after an ILP flush understated the table while
    the WAL was still applying ([[questdb-update-read-lag]]).
    """
    observed = pw.row_count(table)
    if observed != expected:
        raise RuntimeError(f"{table} has {observed:,} rows; expected {expected:,}")


def main() -> int:
    args = parse_args()
    try:
        table = safe_identifier(args.table)
        symbol = args.symbol.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]+", symbol):
            raise ValueError(f"invalid Binance symbol: {symbol!r}")

        assert_trading(symbol)
        existing = pw.row_count(table)
        if existing:
            raise RuntimeError(
                f"{table} already holds {existing:,} rows; "
                "use binance_fetch&import_1m.py to extend an existing table"
            )

        listed_ms = first_candle_ms(symbol)
        listed = local_date(listed_ms, args.timezone)
        start = max(args.start, listed) if args.start else listed
        end = args.end or (datetime.now(args.timezone).date() - timedelta(days=1))
        if end < start:
            raise RuntimeError(f"{symbol} lists on {listed}, after the requested end {end}")

        start_ms, end_exclusive_ms = actual_bounds(start, end, args.timezone)
        start_ms = max(start_ms, listed_ms)
        print(
            f"{symbol} -> {table}: local {args.timezone.key} {start} through {end} "
            f"(listed {listed})",
            flush=True,
        )
        with tempfile.TemporaryDirectory(prefix="binance-backfill-") as temporary:
            parquet = Path(temporary) / f"{symbol}_1m_{start}_{end}.parquet"
            rows, unique_stored, missing = download(
                parquet, symbol, start_ms, end_exclusive_ms, args.timezone
            )
            print(f"Importing {rows:,} rows ({missing:,} minutes absent from Binance)...", flush=True)
            import_parquet(parquet, table, args.timezone)
            verify_count(table, rows)

        duplicates = rows - unique_stored
        span = pw.table_span(table)
        first, final = (span[0], span[1]) if span else (None, None)
        print(
            f"Complete: {table} holds {rows:,} rows from {first} to {final}; "
            f"{duplicates} duplicate wall-clock minutes expected from DST fallback.",
            flush=True,
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Binance backfill failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
