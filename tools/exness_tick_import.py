#!/usr/bin/env python3
"""Import Exness MT5 BID/ASK tick history as `exness_<broker>_ticks`.

WHY THIS EXISTS. The book prices every sleeve on ONE spread constant per
symbol -- a median that `exness_families.tick_spreads` takes over the last
seven days of terminal history and stores in the spec file. That constant is
charged identically at 03:00 and at the New York open, on a quiet Tuesday and
through a payroll print. It is the only cost the study charges, so its shape
matters, and until now nothing in the repository could see the shape.

This lands the raw quotes so it can be measured instead: what the spread
actually was at each fill's own second, how it moves through the session, and
what the tail costs.

HOW DEEP IT GOES, AND WHY THAT IS THE WHOLE CONSTRAINT. Exness serves M1 bars
back to 1999 but TICKS only from 2026-01-01 -- verified per symbol, and it is
the same date on every one of them, so it is a server retention policy rather
than a thin market::

    GBPJPY 10.2M   USTEC 62.2M   ETHUSD 15.9M
    DE30   17.8M   UKOIL  9.8M   XALUSD  1.8M   (2026-01-01..2026-08-28)

So a tick-priced replay covers eight months of a book validated over six
years, and those eight months sit INSIDE the window the survivor pool was
screened on ([[exness-survivor-pool-is-oos-conditioned]]). Anything measured
here is a cost correction to a known-conditioned sample. It is not a holdout
and must never be reported as one.

TIMEZONE. Identical to `exness_import_1m.py`: MT5 returns a genuine UTC epoch,
every table in this repository holds New York wall clock relabelled as UTC, and
the conversion happens HERE so ticks and bars share one clock and a fill can be
looked up on the bar timestamp that produced it.

ONE FILE PER SYMBOL, WRITTEN AS DAY SHARDS FIRST. The finished table is a
single `data/parquet/exness_<broker>_ticks.parquet`, which is the shape the rest
of the store is in and what every reader here expects. It is not written that
way: a symbol is downloaded into `exness_<broker>_ticks/<day>.parquet` shards
and COMPACTED into the single file at the end. The shards are what make the
download restartable -- a day is written whole and swapped in atomically, so a
run killed halfway leaves complete days and `--resume` continues from them,
where appending to one 62-million-row file would mean rewriting all of it per
chunk and losing everything on a kill. `compact` runs it by itself over tables
left sharded by an interrupted run.

MILLISECONDS, AND WHY DEDUP IS OFF. `time_msc` is the tick's real stamp;
`time` is it truncated to a second, and several hundred ticks a second share
that. The stored timestamp keeps the millisecond, and rows are NOT deduplicated
on it -- two genuine quotes can still land on one millisecond, and collapsing
them would silently thin the very distribution this table exists to measure
([[parquet-tables-hold-duplicate-stamps]] is about readers that assume one row
per stamp; no such reader may be pointed at this table).

    py tools/exness_tick_import.py                    # canon symbols, all history
    py tools/exness_tick_import.py --symbols nq ethusd

This importer reads market data only. It never sends, changes, or closes an
MT5 order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Must precede pyarrow: pandas hangs on this machine's broken WMI.
# See `sandbox/parquet_store.py` for the diagnosis.
import platform

platform._wmi = None

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import MetaTrader5 as _mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)

from parquet_writer import PARQUET_DIR, VENDOR_DIR  # noqa: E402

UTC = timezone.utc
DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

#: The same eleven the bar importer lands, under the same names, so
#: `exness_ustec_1m` and `exness_ustec_ticks` are two views of one instrument.
CANON = {
    "nq": "USTEC",
    "usdjpy": "USDJPY",
    "audusd": "AUDUSD",
    "jp225": "JP225",
    "ethusd": "ETHUSD",
    "gbpjpy": "GBPJPY",
    "gbpusd": "GBPUSD",
    "ukoil": "UKOIL",
    "eurjpy": "EURJPY",
    "xalusd": "XALUSD",
    "uk100": "UK100",
    # NOT IN `exness_import_1m.CANON`, and copying that list is how they were
    # missed on the first pass. The bar importer's eleven are the symbols whose
    # BARS were wanted; the symbols whose TICKS are wanted are the ones the BOOK
    # trades, and `btc:xma_ribbon` and `xniusd:cci` are both canon sleeves.
    "btc": "BTCUSD",
    "xniusd": "XNIUSD",
}

#: No tick is served before this on any symbol, so the walk starts here rather
#: than grinding through six years of empty days to discover it.
EARLIEST = date(2026, 1, 1)

#: A chunk is re-requested until two calls agree, for the same reason the bar
#: importer settles: the first call ASKS the server for that history and
#: returns whatever is cached at the moment it is asked.
SETTLE_ATTEMPTS = 8
SETTLE_SECONDS = 3.0

#: Days requested per call. A month at a time risks the terminal's own buffer
#: on USTEC, which quotes a quarter of a million ticks a day; a day at a time
#: spends more wall clock in round trips than in transfer.
CHUNK_DAYS = 3

#: The request reaches back this far before the chunk's first stored day. A
#: stored day is New York wall clock, so its first hours are the PREVIOUS UTC
#: day and would otherwise be missing from the shard named for it.
LEAD_IN_HOURS = 6


def log(message):
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def safe_table(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table name: {value!r}")
    return value


def offset_seconds(actual_seconds, wall_clock):
    """Seconds to ADD to a real UTC epoch to get New York wall clock as UTC."""
    moment = datetime.fromtimestamp(actual_seconds, tz=UTC)
    return int(moment.astimezone(wall_clock).utcoffset().total_seconds())


def stored_stamps_ns(ticks, wall_clock):
    """`time_msc` converted to the store's clock, in nanoseconds.

    The offset is resolved once per distinct HOUR rather than once per tick:
    a DST change only ever lands on an hour boundary, so an hourly lookup is
    exact, and at sixty million ticks a per-row `astimezone` is by a wide
    margin the slowest thing in the import.
    """
    millis = ticks["time_msc"].astype("int64")
    hours = millis // 3_600_000
    lookup = {
        int(hour): offset_seconds(int(hour) * 3600, wall_clock)
        for hour in np.unique(hours)
    }
    shift = np.array([lookup[int(hour)] for hour in hours], dtype="int64")
    return (millis + shift * 1000) * 1_000_000


def format_column(stamps_ns):
    """`stamps_ns` as the ISO text every table in the store is written in.

    Vectorised through numpy rather than `parquet_writer.format_timestamp` per
    row, for the same reason the offsets are.
    """
    micros = (stamps_ns // 1_000).astype("datetime64[us]")
    return pa.array(np.datetime_as_string(micros, unit="us"))


def settled_ticks(symbol, start, stop):
    """Every BID/ASK tick in `[start, stop)`, waited on until it stops growing.

    THE RANGE IS RE-CHECKED IN PYTHON. `copy_ticks_range` has the same habit
    `copy_rates_range` does of answering a window it holds no history for with
    a stale row dated outside it, and a fabricated quote in a spread study is
    worse than a missing one.
    """
    lo, hi = int(start.timestamp()) * 1000, int(stop.timestamp()) * 1000
    previous = -1
    ticks = None
    for _ in range(SETTLE_ATTEMPTS):
        fetched = mt5.copy_ticks_range(symbol, start, stop, mt5.COPY_TICKS_INFO)
        if fetched is None or not len(fetched):
            if previous == 0:
                return None
            previous, ticks = 0, None
            time.sleep(SETTLE_SECONDS)
            continue
        inside = (fetched["time_msc"] >= lo) & (fetched["time_msc"] < hi)
        ticks = fetched[inside]
        if len(ticks) == previous:
            return ticks
        previous = len(ticks)
        time.sleep(SETTLE_SECONDS)
    return ticks


def write_day(table, day, stamps_ns, ticks):
    """Write one stored day as `<table>/<day>.parquet`, replacing what is there.

    A whole day goes out in one call and is swapped in atomically, so a killed
    run leaves complete days rather than a torn one, and a re-run of a day is a
    replacement rather than a double. That is what makes `--resume` safe at day
    granularity without any dedup on the rows themselves.
    """
    directory = PARQUET_DIR / table
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.parquet"

    order = np.argsort(stamps_ns, kind="stable")
    block = pa.table({
        "bid": pa.array(ticks["bid"][order].astype("float64")),
        "ask": pa.array(ticks["ask"][order].astype("float64")),
        "last": pa.array(ticks["last"][order].astype("float64")),
        "volume": pa.array(ticks["volume_real"][order].astype("float64")),
        "flags": pa.array(ticks["flags"][order].astype("int64")),
        "timestamp": format_column(stamps_ns[order]),
    })
    staged = path.with_suffix(f".{os.getpid()}.tmp")
    pq.write_table(block, str(staged), compression="zstd", compression_level=7)
    os.replace(staged, path)
    return len(block)


def by_stored_day(stamps_ns, ticks):
    """Split into `(day, stamps, rows)` on the STORED day.

    Grouping on the stored day rather than the real UTC one is what makes a
    shard hold the same span the bar table's shards do, so a fill can be looked
    up by the bar timestamp that produced it without a second conversion.
    """
    days = stamps_ns // (86_400 * 1_000_000_000)
    for index in np.unique(days):
        mask = days == index
        stamp = datetime.fromtimestamp(int(index) * 86400, tz=UTC)
        yield f"{stamp:%Y-%m-%d}", stamps_ns[mask], ticks[mask]


def existing_days(table):
    directory = PARQUET_DIR / table
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob("*.parquet")}


def compacted_span(table):
    """`(first_day, last_day)` already in the single-file form, or `None`.

    Resume has to see the compacted file as well as the shards, or a second run
    over a finished symbol re-downloads all eight months and then compacts an
    identical file. The range is read from the footer statistics, so it costs
    the same on a sixty-million-row table as on a small one.
    """
    path = PARQUET_DIR / f"{table}.parquet"
    if not path.is_file():
        return None
    handle = pq.ParquetFile(str(path))
    try:
        names = handle.schema_arrow.names
        if "timestamp" not in names:
            return None
        column = names.index("timestamp")
        low, high = None, None
        for group in range(handle.metadata.num_row_groups):
            stats = handle.metadata.row_group(group).column(column).statistics
            if stats is None or stats.min is None:
                return None
            low = str(stats.min)[:10] if low is None else min(low, str(stats.min)[:10])
            high = str(stats.max)[:10] if high is None else max(high, str(stats.max)[:10])
        return None if low is None else (low, high)
    finally:
        handle.close()


def compact(table):
    """Fold `<table>/<day>.parquet` into one `<table>.parquet` and drop the dir.

    THE SHARDS ARE REMOVED ONLY AFTER THE SINGLE FILE IS IN PLACE. `os.replace`
    is atomic, so the failure modes are "shards only" (re-runnable) and "both"
    (the shards are then redundant and deleted) -- never "neither".

    An existing single file is folded in as well rather than overwritten, so
    compacting after an incremental run keeps the history that is already there.
    """
    directory = PARQUET_DIR / table
    single = PARQUET_DIR / f"{table}.parquet"
    shards = sorted(directory.glob("*.parquet")) if directory.is_dir() else []
    if not shards:
        return None

    blocks = []
    if single.is_file():
        blocks.append(pq.read_table(str(single)))
    for shard in shards:
        blocks.append(pq.read_table(str(shard)))
    merged = pa.concat_tables(blocks, promote_options="default")
    order = pa.compute.sort_indices(merged, sort_keys=[("timestamp", "ascending")])
    merged = merged.take(order)

    staged = single.with_suffix(f".{os.getpid()}.tmp")
    pq.write_table(merged, str(staged), compression="zstd", compression_level=7)
    os.replace(staged, single)
    for shard in shards:
        shard.unlink()
    try:
        directory.rmdir()
    except OSError:
        # A stray `.tmp` from a killed writer. Leaving the directory is
        # harmless -- `table_files` reads the single file and the empty dir
        # contributes nothing -- and deleting a file this run did not write is
        # not something an importer should do silently.
        log(f"  {table}: shard directory not empty, left in place")
    return merged.num_rows


def import_symbol(key, broker, first, last, wall_clock, resume):
    table = safe_table(f"exness_{broker.lower()}_ticks")
    if not mt5.symbol_select(broker, True):
        raise RuntimeError(f"cannot select {broker}: {mt5.last_error()}")

    have = existing_days(table) if resume else set()
    done = compacted_span(table) if resume else None
    total = skipped = 0
    cursor = first
    while cursor <= last:
        stop_day = min(cursor + timedelta(days=CHUNK_DAYS),
                       last + timedelta(days=1))
        wanted = [cursor + timedelta(days=n)
                  for n in range((stop_day - cursor).days)]
        covered = (done is not None
                   and all(done[0] <= day.isoformat() <= done[1]
                           for day in wanted))
        if covered or (have and all(day.isoformat() in have for day in wanted)):
            skipped += len(wanted)
            cursor = stop_day
            continue

        start = (datetime(cursor.year, cursor.month, cursor.day, tzinfo=UTC)
                 - timedelta(hours=LEAD_IN_HOURS))
        stop = datetime(stop_day.year, stop_day.month, stop_day.day, tzinfo=UTC)
        ticks = settled_ticks(broker, start, stop)
        if ticks is None or not len(ticks):
            log(f"  {broker} {cursor}..{stop_day - timedelta(days=1)}: no ticks")
            cursor = stop_day
            continue

        stamps = stored_stamps_ns(ticks, wall_clock)
        written = 0
        for day, day_stamps, day_ticks in by_stored_day(stamps, ticks):
            if not first.isoformat() <= day <= last.isoformat():
                # Overflow from the lead-in, or from the chunk's last UTC hours
                # landing on the next stored day. It belongs to the neighbouring
                # chunk and is written there, whole.
                continue
            written += write_day(table, day, day_stamps, day_ticks)
        total += written
        log(f"  {broker} {cursor}..{stop_day - timedelta(days=1)}: "
            f"{written:,} ticks (running {total:,})")
        cursor = stop_day

    rows = compact(table)
    return {"symbol": key, "broker": broker, "table": table,
            "written": total, "days_skipped": skipped,
            "rows_in_file": rows,
            "file": f"data/parquet/{table}.parquet"}


def parse_args():
    yesterday = date.today() - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", default=sorted(CANON))
    parser.add_argument("--from-date", default=EARLIEST.isoformat())
    parser.add_argument("--to-date", default=yesterday.isoformat())
    parser.add_argument("--terminal", type=Path, default=DEFAULT_TERMINAL)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--no-resume", action="store_true",
                        help="re-download days already on disk")
    parser.add_argument("--compact-only", action="store_true",
                        help="fold existing day shards into one file per "
                             "symbol and exit; downloads nothing")
    parser.add_argument("--report", type=Path,
                        default=VENDOR_DIR / "exness" / "import_ticks_report.json")
    return parser.parse_args()


def main():
    args = parse_args()
    wall_clock = ZoneInfo(args.timezone)
    unknown = [s for s in args.symbols if s.lower() not in CANON]
    if unknown:
        print(f"unknown symbols: {unknown}; known: {sorted(CANON)}",
              file=sys.stderr)
        return 2

    first = date.fromisoformat(args.from_date)
    last = date.fromisoformat(args.to_date)

    if args.compact_only:
        # No terminal, no network. Purely a disk operation, so it can run
        # against tables an interrupted download left sharded.
        for key in args.symbols:
            table = safe_table(f"exness_{CANON[key.lower()].lower()}_ticks")
            rows = compact(table)
            log(f"{key}: {'nothing to compact' if rows is None else f'{rows:,} rows'}"
                f" -> data/parquet/{table}.parquet")
        return 0

    if not mt5.initialize(path=str(args.terminal)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    report = []
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account is unavailable: {mt5.last_error()}")
        log(f"account {account.login} on {account.server} (read-only feed)")
        log(f"ticks {first} .. {last}")
        for key in args.symbols:
            key = key.lower()
            log(f"{key} -> {CANON[key]}")
            try:
                summary = import_symbol(key, CANON[key], first, last,
                                        wall_clock, not args.no_resume)
            except Exception as error:  # noqa: BLE001 - one symbol must not
                # cost the other ten; an unquoted CFD is a normal outcome.
                log(f"{key}: {type(error).__name__}: {error}")
                summary = {"symbol": key, "broker": CANON[key],
                           "error": f"{type(error).__name__}: {error}"}
            report.append(summary)
            log(f"{key}: {summary}")
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2))
    finally:
        mt5.shutdown()

    log(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
