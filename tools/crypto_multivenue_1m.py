"""Fetch 1-minute crypto bars from free non-Binance venues into the store.

Binance spot only reaches back to a symbol's listing date and omits minutes with
no trades. This script covers the two remaining jobs:

``prepend``
    Fetch history older than a table's first row from a venue that listed the
    pair earlier, e.g. Bitfinex ETHUSD back to 2016.

``gapfill``
    Fill multi-minute outage blocks inside an existing table. Isolated
    single-minute holes are left alone: on a thin pair those mean nothing
    traded, which is real information rather than missing data.

Every row carries a ``source`` tag naming the venue, so a spliced series can
always be filtered back to one exchange or tested for a level break at the
join. Only OHLC and base volume are portable across venues; the Binance-specific
columns (quote_volume, trades, taker_buy_*) are left null on foreign rows.

Timestamps follow the repository convention: exchange-local wall-clock values
labelled as UTC (default ``America/New_York``), matching ``btc_1m``.
"""

from __future__ import annotations


import argparse
import re
import sys
import tempfile
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

import parquet_writer as pw
import requests


UTC = timezone.utc
MINUTE_MS = 60_000
SCHEMA = pa.schema([
    pa.field("ts", pa.timestamp("ms", tz="UTC")),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()),
    pa.field("source", pa.string()),
])
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (quant-research data backfill)"


# --------------------------------------------------------------------------
# HTTP helper
# --------------------------------------------------------------------------
def request_json(url: str, params: dict[str, Any] | None = None) -> Any:
    for attempt in range(8):
        try:
            response = SESSION.get(url, params=params, timeout=40)
            if response.status_code in (418, 429):
                delay = float(response.headers.get("Retry-After", min(60, 2 ** attempt)))
                time.sleep(delay)
                continue
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 7:
                raise RuntimeError(f"request failed after retries: {url}: {exc}") from exc
            time.sleep(min(30.0, 2.0 ** attempt))
    raise RuntimeError(f"request exhausted retries: {url}")


Row = tuple[int, float, float, float, float, float]


# --------------------------------------------------------------------------
# Venue adapters: fetch(symbol, start_ms, end_exclusive_ms) -> sorted rows
# --------------------------------------------------------------------------
def fetch_bitfinex(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """Bitfinex v2: up to 10000 candles per call, ascending with sort=1."""
    cursor = start_ms
    while cursor < end_ms:
        payload = request_json(
            f"https://api-pub.bitfinex.com/v2/candles/trade:1m:t{symbol}/hist",
            {"start": cursor, "end": end_ms, "limit": 10000, "sort": 1},
        ) or []
        if not payload:
            return
        for ts, o, c, h, l, v in payload:  # note Bitfinex order: O C H L V
            if start_ms <= int(ts) < end_ms:
                yield int(ts), float(o), float(h), float(l), float(c), float(v)
        last = int(payload[-1][0])
        if last < cursor:
            return
        cursor = last + MINUTE_MS
        time.sleep(2.2)  # public candle limit is ~30 req/min; 0.35s gets you banned


def fetch_bitstamp(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """Bitstamp v2 OHLC: 1000 rows per call, paged forward from ``start``.

    Passing ``end`` alongside ``start`` makes Bitstamp ignore ``start`` and
    return the last 1000 rows before ``end`` instead, so only ``start`` is sent
    and the window is closed here.
    """
    cursor = start_ms
    while cursor < end_ms:
        payload = request_json(
            f"https://www.bitstamp.net/api/v2/ohlc/{symbol}/",
            {"step": 60, "limit": 1000, "start": cursor // 1000},
        )
        rows = (payload or {}).get("data", {}).get("ohlc") or []
        if not rows:
            return
        for row in rows:
            ts = int(row["timestamp"]) * 1000
            if start_ms <= ts < end_ms:
                yield (ts, float(row["open"]), float(row["high"]),
                       float(row["low"]), float(row["close"]), float(row["volume"]))
        last = int(rows[-1]["timestamp"]) * 1000
        if last < cursor:
            return
        cursor = last + MINUTE_MS
        time.sleep(0.15)


def fetch_bitbank(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """bitbank: one file per UTC day, 1440 rows, ohlcv = [o,h,l,c,vol,ts_ms]."""
    day = datetime.fromtimestamp(start_ms / 1000, tz=UTC).date()
    last_day = datetime.fromtimestamp((end_ms - 1) / 1000, tz=UTC).date()
    while day <= last_day:
        payload = request_json(
            f"https://public.bitbank.cc/{symbol}/candlestick/1min/{day:%Y%m%d}"
        )
        candles = (payload or {}).get("data", {}).get("candlestick") or []
        for entry in candles:
            for o, h, l, c, v, ts in entry.get("ohlcv", []):
                ts = int(ts)
                if start_ms <= ts < end_ms:
                    yield ts, float(o), float(h), float(l), float(c), float(v)
        day += timedelta(days=1)
        time.sleep(0.12)


def fetch_upbit(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """Upbit: 200 rows per call, descending, paged backwards from `to`."""
    cursor = end_ms
    while cursor > start_ms:
        stamp = datetime.fromtimestamp(cursor / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = request_json(
            "https://api.upbit.com/v1/candles/minutes/1",
            {"market": symbol, "count": 200, "to": stamp},
        ) or []
        if not payload:
            return
        oldest = cursor
        for row in payload:
            ts = int(datetime.fromisoformat(
                row["candle_date_time_utc"]).replace(tzinfo=UTC).timestamp() * 1000)
            oldest = min(oldest, ts)
            if start_ms <= ts < end_ms:
                yield (ts, float(row["opening_price"]), float(row["high_price"]),
                       float(row["low_price"]), float(row["trade_price"]),
                       float(row["candle_acc_trade_volume"]))
        if oldest >= cursor:
            return
        cursor = oldest
        time.sleep(0.12)  # limit is 600/min


def fetch_coinbase(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """Coinbase Exchange: max 300 candles per call, [ts,low,high,open,close,vol]."""
    cursor = start_ms
    span = 300 * MINUTE_MS
    while cursor < end_ms:
        upper = min(end_ms, cursor + span)
        payload = request_json(
            f"https://api.exchange.coinbase.com/products/{symbol}/candles",
            {"granularity": 60,
             "start": datetime.fromtimestamp(cursor / 1000, tz=UTC).isoformat().replace("+00:00", "Z"),
             "end": datetime.fromtimestamp((upper - 1) / 1000, tz=UTC).isoformat().replace("+00:00", "Z")},
        ) or []
        for ts, low, high, o, c, v in sorted(payload, key=lambda r: r[0]):
            ts = int(ts) * 1000
            if start_ms <= ts < end_ms:
                yield ts, float(o), float(high), float(low), float(c), float(v)
        cursor = upper
        time.sleep(0.25)


def fetch_binance(symbol: str, start_ms: int, end_ms: int) -> Iterable[Row]:
    """Binance spot klines, 1000 per call.

    Useful for a pair whose alternate quote leg listed earlier than the one a
    table was built from -- SOLBUSD opens 2020-04-10, four months before
    SOLUSDT.
    """
    cursor = start_ms
    span = 1000 * MINUTE_MS
    while cursor < end_ms:
        payload = request_json(
            "https://data-api.binance.vision/api/v3/klines",
            {"symbol": symbol, "interval": "1m", "startTime": cursor,
             "endTime": min(end_ms, cursor + span) - 1, "limit": 1000},
        ) or []
        for row in payload:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                yield (ts, float(row[1]), float(row[2]), float(row[3]),
                       float(row[4]), float(row[5]))
        cursor += span


VENUES: dict[str, Callable[[str, int, int], Iterable[Row]]] = {
    "binance": fetch_binance,
    "bitfinex": fetch_bitfinex,
    "bitstamp": fetch_bitstamp,
    "bitbank": fetch_bitbank,
    "upbit": fetch_upbit,
    "coinbase": fetch_coinbase,
}


# --------------------------------------------------------------------------
# store helpers
# --------------------------------------------------------------------------
def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table identifier: {value!r}")
    return value


def stored_datetime(moment_ns: int) -> datetime:
    """A stored epoch-nanosecond instant as the fake-UTC datetime it represents."""
    return datetime.fromtimestamp(moment_ns / 1e9, tz=UTC)


def stored_ms(actual_ms: int, wall_clock: ZoneInfo) -> int:
    local = datetime.fromtimestamp(actual_ms / 1000, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp() * 1000)


def actual_from_stored(stored: datetime, wall_clock: ZoneInfo) -> int:
    """Interpret a stored fake-UTC timestamp as wall-clock and return true UTC ms."""
    naive = stored.replace(tzinfo=None)
    return int(naive.replace(tzinfo=wall_clock).timestamp() * 1000)


def wall_clock_roundtrips(stored: datetime, wall_clock: ZoneInfo) -> bool:
    """True when a stored wall-clock minute corresponds to a real instant.

    The hour skipped by a spring-forward transition does not exist locally;
    zoneinfo silently resolves it using the pre-transition offset, so the only
    way to detect it is to convert to UTC and back and check it survives.
    """
    return stored_ms(actual_from_stored(stored, wall_clock), wall_clock) == int(
        stored.replace(tzinfo=UTC).timestamp() * 1000)


def write_parquet(path: Path, rows: list[Row], source: str) -> int:
    if not rows:
        return 0
    table = pa.Table.from_arrays([
        pa.array([r[0] for r in rows], type=SCHEMA.field("ts").type),
        pa.array([r[1] for r in rows], type=pa.float64()),
        pa.array([r[2] for r in rows], type=pa.float64()),
        pa.array([r[3] for r in rows], type=pa.float64()),
        pa.array([r[4] for r in rows], type=pa.float64()),
        pa.array([r[5] for r in rows], type=pa.float64()),
        pa.array([source] * len(rows), type=pa.string()),
    ], schema=SCHEMA)
    pq.write_table(table, path, compression="zstd")
    return len(rows)


def import_parquet(path: Path, table: str, wall_clock: ZoneInfo) -> None:
    """Load the staged real-UTC bars, converting each to `wall_clock` on the way.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py` as a subprocess, which did the timezone
    conversion while streaming ILP; that importer existed only for QuestDB and
    is gone. `source` was a `--tag-cols` SYMBOL column and is now an ordinary
    text column, which is what the export wrote it as anyway.

    `dedup=False`: the DST fall-back hour legitimately produces two bars with
    the same stored timestamp ([[dst-fakes-hourly-bar-gaps]]).
    """
    handle = pq.ParquetFile(str(path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()

    names = [name for name in staged.schema.names if name != "ts"]
    columns = {name: staged[name].to_pylist() for name in names}
    sender = pw.Sender(dedup=False)
    for index, moment in enumerate(staged["ts"].to_pylist()):
        local = moment.replace(tzinfo=UTC).astimezone(wall_clock)
        stored = int(local.replace(tzinfo=UTC).timestamp())
        sender.row(
            table,
            columns={name: columns[name][index] for name in names},
            at=pw.TimestampNanos(stored * 1_000_000_000),
        )
    sender.flush()


def collect(venue: str, symbol: str, start_ms: int, end_ms: int, label: str) -> list[Row]:
    fetch = VENUES[venue]
    seen: dict[int, Row] = {}
    for row in fetch(symbol, start_ms, end_ms):
        if row[0] % MINUTE_MS:
            continue
        seen[row[0]] = row
        if len(seen) % 100_000 == 0:
            print(f"  {label}: {len(seen):,} minutes", flush=True)
    return [seen[key] for key in sorted(seen)]


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------
def run_prepend(args: argparse.Namespace, table: str) -> int:
    span = pw.table_span(table)
    if span is None:
        raise RuntimeError(f"{table} is empty; use --mode create")
    first_stored = stored_datetime(pw.read_columns(table, [])[1][0][0])
    end_ms = actual_from_stored(first_stored, args.timezone)
    start_ms = int(datetime.combine(args.start, datetime_time.min, tzinfo=UTC).timestamp() * 1000)
    if start_ms >= end_ms:
        print(f"Nothing to prepend: {table} already starts at {first_stored.isoformat()}")
        return 0
    print(f"Prepending {args.venue} {args.symbol} into {table}: "
          f"{datetime.fromtimestamp(start_ms/1000, tz=UTC):%Y-%m-%d} up to "
          f"{datetime.fromtimestamp(end_ms/1000, tz=UTC):%Y-%m-%d %H:%M} UTC", flush=True)
    rows = collect(args.venue, args.symbol, start_ms, end_ms, "prepend")
    if not rows:
        raise RuntimeError("venue returned no rows for the requested range")
    possible = (rows[-1][0] - rows[0][0]) // MINUTE_MS + 1
    print(f"Fetched {len(rows):,} minutes ({possible - len(rows):,} absent at the venue); importing...",
          flush=True)
    with tempfile.TemporaryDirectory(prefix="multivenue-") as tmp:
        parquet = Path(tmp) / f"{table}_{args.venue}_prepend.parquet"
        write_parquet(parquet, rows, args.source_tag or args.venue)
        import_parquet(parquet, table, args.timezone)
    return len(rows)


def find_gaps(table: str, min_run: int,
              sources: str | None = None) -> list[tuple[datetime, datetime]]:
    """Return stored-timestamp gap blocks of at least min_run missing minutes.

    ``sources`` restricts the scan to rows from those venues, so an outage is
    measured within one exchange's own series. Without it, the splice boundary
    to a prepended venue and that venue's thin early minutes both register as
    gaps to fill, which is not the intent.
    """
    allowed = None
    if sources:
        allowed = {value.strip() for value in sources.split(",")}
    _, rows = pw.read_columns(table, ["source"])

    # `select distinct timestamp`, then the LEAD window function, written out.
    minutes = sorted({
        row[0] // 60_000_000_000
        for row in rows
        if allowed is None or row[1] in allowed
    })
    blocks = []
    for current, following in zip(minutes, minutes[1:]):
        missing = following - current - 1
        if missing >= min_run:
            # `timestamp` .. `dateadd('m', -1, lead_ts)`: the block spans the
            # bar AFTER the last present one through the bar BEFORE the next.
            blocks.append((
                stored_datetime((current + 1) * 60_000_000_000),
                stored_datetime((following - 1) * 60_000_000_000),
            ))
    return blocks


def run_gapfill(args: argparse.Namespace, table: str) -> int:
    blocks = find_gaps(table, args.min_gap, args.gap_sources)
    if not blocks:
        print(f"No gaps of >= {args.min_gap} minutes in {table}.")
        return 0
    total_missing = sum(
        int((end - start).total_seconds() // 60) for start, end in blocks
    )
    print(f"{table}: {len(blocks)} outage blocks, {total_missing:,} missing minutes "
          f"(>= {args.min_gap} consecutive)", flush=True)
    rows: list[Row] = []
    skipped = 0
    for index, (before, gap_end) in enumerate(blocks, 1):
        if not wall_clock_roundtrips(gap_end, args.timezone):
            # Spring-forward: 02:00-02:59 never happens in New York, so this
            # hour is legitimately absent rather than an outage. Fetching it
            # returns real instants that map back to 03:00-03:59 and duplicate
            # rows that already exist.
            print(f"  block {index}/{len(blocks)} {before:%Y-%m-%d %H:%M}: "
                  f"DST spring-forward hour, not an outage -- skipped", flush=True)
            skipped += 1
            continue
        start_ms = actual_from_stored(before, args.timezone) + MINUTE_MS
        end_ms = actual_from_stored(gap_end, args.timezone) + MINUTE_MS
        # Keep only minutes strictly inside the hole: the block bounds are
        # already known to be absent, so this is the dedupe -- no need to pull
        # millions of existing timestamps over HTTP to compare against.
        got = [r for r in collect(args.venue, args.symbol, start_ms, end_ms, f"block {index}")
               if start_ms <= r[0] < end_ms]
        rows.extend(got)
        expected = int((gap_end - before).total_seconds() // 60)
        print(f"  block {index}/{len(blocks)} {before:%Y-%m-%d %H:%M} + "
              f"{expected}m -> {len(got):,} rows", flush=True)
    if not rows:
        print("Venue had no data for any gap block; nothing imported.")
        return 0
    fresh = rows
    print(f"Importing {len(fresh):,} filled minutes...", flush=True)
    with tempfile.TemporaryDirectory(prefix="multivenue-") as tmp:
        parquet = Path(tmp) / f"{table}_{args.venue}_gapfill.parquet"
        write_parquet(parquet, fresh, args.source_tag or args.venue)
        import_parquet(parquet, table, args.timezone)
    return len(fresh)


def run_create(args: argparse.Namespace, table: str) -> int:
    # No table to create: a shard appears when its first row is written.
    existing = pw.row_count(table)
    if existing:
        raise RuntimeError(f"{table} already holds {existing:,} rows; use --mode prepend/gapfill")
    end = args.end or (datetime.now(args.timezone).date() - timedelta(days=1))
    start_ms = int(datetime.combine(args.start, datetime_time.min, tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end + timedelta(days=1), datetime_time.min,
                                 tzinfo=args.timezone).timestamp() * 1000)
    print(f"Creating {table} from {args.venue} {args.symbol}: "
          f"{args.start} through {end}", flush=True)
    rows = collect(args.venue, args.symbol, start_ms, end_ms, "create")
    if not rows:
        raise RuntimeError("venue returned no rows")
    with tempfile.TemporaryDirectory(prefix="multivenue-") as tmp:
        parquet = Path(tmp) / f"{table}_{args.venue}.parquet"
        write_parquet(parquet, rows, args.source_tag or args.venue)
        import_parquet(parquet, table, args.timezone)
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True, choices=("create", "prepend", "gapfill"))
    parser.add_argument("--venue", required=True, choices=sorted(VENUES))
    parser.add_argument("--symbol", required=True, help="venue-native symbol, e.g. ETHUSD / KRW-BTC")
    parser.add_argument("--table", required=True)
    parser.add_argument("--start", type=lambda v: date.fromisoformat(v),
                        help="earliest UTC date to fetch (create/prepend)")
    parser.add_argument("--end", type=lambda v: date.fromisoformat(v), help="create mode only")
    parser.add_argument("--source-tag",
                        help="value for the source column (default: the venue name); use when one "
                             "venue supplies a different quote leg, e.g. binance_busd")
    parser.add_argument("--gap-sources", default="binance,binance_busd",
                        help="gapfill: comma-separated source tags whose series defines a gap "
                             "(default: binance,binance_busd)")
    parser.add_argument("--min-gap", type=int, default=5,
                        help="gapfill: minimum consecutive missing minutes to fill (default 5)")
    parser.add_argument("--timezone", type=lambda v: ZoneInfo(v), default=ZoneInfo("America/New_York"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        table = safe_identifier(args.table)
        if args.mode in ("create", "prepend") and not args.start:
            raise ValueError(f"--start is required for --mode {args.mode}")
        handler = {"create": run_create, "prepend": run_prepend, "gapfill": run_gapfill}[args.mode]
        written = handler(args, table)
        if written:
            lo, hi, total = pw.table_span(table) or (None, None, 0)
            print(f"Complete: {table} wrote {written:,} rows; now {total:,} rows "
                  f"from {lo} to {hi}.", flush=True)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Multi-venue fetch failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
