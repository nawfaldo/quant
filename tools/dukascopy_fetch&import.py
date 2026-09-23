"""Download 1-minute bars from Dukascopy's free feed into the Parquet store.

Dukascopy is the only free source that covers WTI/USOIL without gaps: its
``E_Light`` series runs from 2012 to the current minute, whereas HistData's
WTIUSD archive is missing everything between 2023-12-01 and 2026-06-28.

Dukascopy serves real UTC instants. This repository stores exchange-local
wall-clock timestamps labelled as UTC, so both outputs are converted to
``America/New_York`` - daylight saving included, unlike a fixed offset. The CSV
holds the converted wall clock directly; the Parquet keeps real UTC and lets
``parquet_writer`` do the conversion, which is the contract that
importer expects.

Imports are incremental: only bars newer than the table's latest timestamp are
sent, so re-running is cheap and safe. ``--rebuild`` drops the table first for a
full reload.

NOTE: dukascopy.com is DNS-blocked on this network. Turn Cloudflare Warp on
first or every request dies with a connect timeout.

Usage:
    python 'dukascopy_fetch&import.py' --instrument E_Light --table usoil_1m
    python 'dukascopy_fetch&import.py' --instrument E_Brent --table brent_1m --start 2015-01
    python 'dukascopy_fetch&import.py' --no-import          # CSV/Parquet only
"""


from __future__ import annotations

# Must precede pandas, which hangs on this machine's broken WMI at import.
import _prelude  # noqa: F401  -- see tools/_prelude.py


import argparse
import re
import socket
import sys
import time
from collections import Counter
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import parquet_writer as pw

import dukascopy_python as dk

UTC = timezone.utc
# dukascopy_python calls requests.get() with no timeout, so a stalled socket hangs
# the process forever and its own retry loop never fires. urllib3 falls back to the
# global socket default, which is the only place we can impose a deadline.
REQUEST_TIMEOUT_SECONDS = 90
DEFAULT_OUT_DIR = pw.VENDOR_DIR / "dukascopy"
OFFER_SIDES = {"bid": dk.OFFER_SIDE_BID, "ask": dk.OFFER_SIDE_ASK}
SCHEMA = pa.schema([
    pa.field("ts", pa.timestamp("ms", tz="UTC")),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.float64()),
])


# --------------------------------------------------------------------------- fetch

def month_starts(start: tuple[int, int], end: tuple[int, int]):
    year, month = start
    while (year, month) <= end:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def month_end(year: int, month: int) -> datetime:
    return datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)


def safe_name(instrument: str) -> str:
    """Dukascopy symbols like ``XAU/USD`` need flattening before use in a path."""
    return re.sub(r"[^A-Za-z0-9]+", "_", instrument).strip("_")


def fetch_month(instrument: str, side: str, year: int, month: int,
                cache_dir: Path, attempts: int) -> pd.DataFrame | None:
    """Fetch one month of 1m bars, caching it so reruns only refetch the live month."""
    cached = cache_dir / f"{safe_name(instrument)}_{side}_{year}{month:02d}.parquet"
    today = date.today()
    if cached.exists() and (year, month) < (today.year, today.month):
        return pd.read_parquet(cached)

    for attempt in range(attempts):
        try:
            frame = dk.fetch(instrument, dk.INTERVAL_MIN_1, OFFER_SIDES[side],
                             datetime(year, month, 1), month_end(year, month))
        except Exception as exc:
            print(f"  {year}-{month:02d}: {type(exc).__name__}, retry {attempt + 1}")
            time.sleep(5 * (attempt + 1))
            continue
        if frame.empty:
            print(f"  {year}-{month:02d}: empty")
            return None

        # Dukascopy pads the tail of the window with repeats of the last known bar.
        frame = frame[frame.index < month_end(year, month).replace(tzinfo=UTC)]
        frame.to_parquet(cached)
        print(f"  {year}-{month:02d}: {len(frame):,} bars")
        return frame

    raise RuntimeError(f"{year}-{month:02d} failed after {attempts} attempts")


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frame = pd.concat(frames).sort_index()
    return frame[~frame.index.duplicated(keep="first")]


# --------------------------------------------------------------------------- write

def write_csv(frame: pd.DataFrame, path: Path, wall_clock: ZoneInfo) -> None:
    local = frame.tz_convert(wall_clock)
    out = pd.DataFrame({
        "timestamp": local.index.strftime("%Y-%m-%d %H:%M:%S"),
        "open": local["open"],
        "high": local["high"],
        "low": local["low"],
        "close": local["close"],
        "volume": local["volume"],
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)

    print(f"\nrows: {len(out):,}")
    print(f"range: {out['timestamp'].iloc[0]} -> {out['timestamp'].iloc[-1]}  ({wall_clock.key})")
    for year, count in sorted(Counter(out["timestamp"].str[:4]).items()):
        print(f"  {year}: {count:,}")
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Write real-UTC bars; `import_parquet` converts them to wall clock."""
    table = pa.Table.from_pydict({
        "ts": frame.index.to_pydatetime(),
        "open": frame["open"].to_numpy(),
        "high": frame["high"].to_numpy(),
        "low": frame["low"].to_numpy(),
        "close": frame["close"].to_numpy(),
        "volume": frame["volume"].to_numpy(),
    }, schema=SCHEMA)
    pq.write_table(table, path)


# --------------------------------------------------------------------------- store

def table_exists(table: str) -> bool:
    return bool(pw.table_files(table))


def latest_timestamp(table: str, wall_clock: ZoneInfo) -> datetime | None:
    """The table's newest bar as a real UTC instant.

    Stored values are wall clock labelled as UTC, so they are reinterpreted in
    `wall_clock` before being converted back.
    """
    newest = pw.max_timestamp_ns(table)
    if newest is None:
        return None
    naive = datetime.fromtimestamp(newest / 1e9, tz=UTC).replace(tzinfo=None)
    return naive.replace(tzinfo=wall_clock).astimezone(UTC)


def import_parquet(path: Path, table: str, wall_clock: ZoneInfo) -> None:
    """Load the staged real-UTC bars, converting each to `wall_clock` on the way.

    WRITTEN DIRECTLY RATHER THAN SHELLED OUT. This ran
    `questdb_parquet_importer.py` as a subprocess, which did the timezone
    conversion as part of streaming ILP; that importer existed only for QuestDB
    and is gone, so the conversion moves here -- it is the same one
    `write_csv` already applies, and the contract the module docstring
    describes is unchanged.

    `dedup=False`: the DST fall-back hour legitimately produces two bars with
    the same stored timestamp and both belong in the table
    ([[dst-fakes-hourly-bar-gaps]]).
    """
    handle = pq.ParquetFile(str(path))
    try:
        staged = handle.read(use_threads=False)
    finally:
        handle.close()

    names = ("open", "high", "low", "close", "volume")
    columns = {name: staged[name].to_pylist() for name in names}
    sender = pw.Sender(dedup=False)
    for index, moment in enumerate(staged["ts"].to_pylist()):
        # Real UTC instant -> wall-clock fields relabelled as UTC.
        local = moment.replace(tzinfo=UTC).astimezone(wall_clock)
        stored = int(local.replace(tzinfo=UTC).timestamp())
        sender.row(
            table,
            columns={name: columns[name][index] for name in names},
            at=pw.TimestampNanos(stored * 1_000_000_000),
        )
    sender.flush()


# --------------------------------------------------------------------------- cli

def parse_period(text: str, default_month: int) -> tuple[int, int]:
    parts = text.split("-")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else default_month


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid IANA timezone: {value!r}") from exc


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--instrument", default="E_Light",
                        help="Dukascopy symbol; E_Light is WTI/USOIL, E_Brent is Brent, "
                             "XAU/USD is gold (see dukascopy_python.instruments for the "
                             "full list)")
    parser.add_argument("--start", default="2012-01",
                        help="first month, YYYY or YYYY-MM (E_Light begins 2012-01, "
                             "XAU/USD 2005-01)")
    parser.add_argument("--end", default=f"{today.year}-{today.month:02d}",
                        help="last month, YYYY or YYYY-MM (default: current month)")
    parser.add_argument("--through", type=parse_date,
                        help="last local calendar day to import, YYYY-MM-DD")
    parser.add_argument("--side", default="bid", choices=sorted(OFFER_SIDES),
                        help="quote side to record (default: bid)")
    parser.add_argument("--table", default="usoil_1m", help="destination table")
    parser.add_argument("--timezone", type=parse_timezone, default=ZoneInfo("America/New_York"),
                        help="wall-clock timezone stored as fake UTC (default: America/New_York)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"output directory (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--no-import", action="store_true",
                        help="write CSV/Parquet but skip the store")
    parser.add_argument("--rebuild", action="store_true",
                        help="drop the table and reload every bar instead of appending")
    parser.add_argument("--attempts", type=int, default=5,
                        help="retries per month before giving up (default: 5)")
    args = parser.parse_args()

    start = parse_period(args.start, 1)
    end = parse_period(args.end, 12)
    if start > end:
        parser.error("--start must not be after --end")

    cache_dir = args.out_dir / "months"
    cache_dir.mkdir(parents=True, exist_ok=True)

    socket.setdefaulttimeout(REQUEST_TIMEOUT_SECONDS)

    print(f"{args.instrument} 1m ({args.side}): "
          f"{start[0]}-{start[1]:02d} .. {end[0]}-{end[1]:02d}")
    frames, missing = [], []
    for year, month in month_starts(start, end):
        try:
            frame = fetch_month(args.instrument, args.side, year, month, cache_dir,
                                args.attempts)
        except Exception as exc:
            print(f"  {year}-{month:02d}: FAILED {type(exc).__name__}: {exc}")
            missing.append(f"{year}-{month:02d}")
            continue
        if frame is not None:
            frames.append(frame)

    if missing:
        print(f"\nWARNING: {len(missing)} month(s) missing: {', '.join(missing)}")
        print("re-run to retry them; everything already fetched is cached")

    if not frames:
        print("nothing downloaded - is Cloudflare Warp on? dukascopy.com is DNS-blocked here.")
        return 1

    bars = combine(frames)
    if args.through is not None:
        through_exclusive = datetime.combine(
            args.through + timedelta(days=1), datetime_time.min,
            tzinfo=args.timezone,
        ).astimezone(UTC)
        bars = bars[bars.index < through_exclusive]
        if bars.empty:
            print(f"nothing downloaded through {args.through.isoformat()}")
            return 0
    stem = f"{safe_name(args.instrument)}_1m_{start[0]}_{end[0]}"
    write_csv(bars, args.out_dir / f"{stem}.csv", args.timezone)

    if args.no_import:
        return 0

    if args.rebuild and table_exists(args.table):
        pw.drop_table(args.table)
        print(f"\ndropped {args.table}")
    # No table to create: a shard appears when its first row is written.

    latest = None if args.rebuild else latest_timestamp(args.table, args.timezone)
    pending = bars if latest is None else bars[bars.index > latest]
    before = pw.row_count(args.table)
    print(f"\n{args.table}: {before:,} rows, latest "
          f"{latest.astimezone(args.timezone).strftime('%Y-%m-%d %H:%M') if latest else 'empty'}")

    if pending.empty:
        print("already up to date")
        return 0

    parquet = args.out_dir / f"{stem}_pending.parquet"
    write_parquet(pending, parquet)
    print(f"importing {len(pending):,} bars as {args.timezone.key} wall clock")
    import_parquet(parquet, args.table, args.timezone)

    after = pw.row_count(args.table)
    print(f"{args.table}: {before:,} -> {after:,} rows (+{after - before:,})")
    if after - before != len(pending):
        # No longer a timing warning: a shard is renamed into place complete, so
        # a mismatch here is a real discrepancy rather than a WAL still applying.
        print(f"WARNING: expected +{len(pending):,} rows, got +{after - before:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
