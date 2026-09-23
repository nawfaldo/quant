"""Append-only writer for `data/parquet/`. The importers' replacement for ILP.

QuestDB is gone. Every table was exported to Parquet and dropped, so the
importers that used to stream rows over the InfluxDB line protocol now write
files -- and the readers (`sandbox.parquet_store`, and the Rust
`api::parquet_store`) see the result as one continuous series.

WHY THIS IMITATES THE ILP INTERFACE ON PURPOSE.

`Sender` here has the same four methods, the same `row(table, symbols=,
columns=, at=)` signature and the same `TimestampNanos` wrapper as
`questdb.ingress`. That is not laziness dressed up as design: a dozen importers
each build their column dicts differently, and every one of them has painful,
hard-won rules about which rows to skip and how to convert a vendor clock. Making
the WRITE the only thing that changes keeps those rules untouched and makes each
importer's diff two lines -- the import, and the watermark query. A hand-rolled
API here would have meant rewriting twelve loops that nobody wants to re-derive.

LAYOUT. History lives in `data/parquet/<table>.parquet`, exported out of
QuestDB. Everything written here goes to `data/parquet/<table>/<date>.parquet`,
one shard per New York calendar day. The bulk file is never rewritten, so a
collector cannot corrupt years of history by crashing mid-flush, and a shard is
at most a day of rows -- 1,440 for a one-minute feed -- so rewriting one to
append to it is cheap.

WRITES ARE ATOMIC AND IDEMPOTENT. A flush stages a whole shard beside its target
and renames it into place, so a reader either sees the old shard or the new one.
Rows already present at the same timestamp REPLACE the stored ones rather than
duplicating: the vendor feeds have no dedup key, and an importer re-sending a
minute it already sent -- which happens every time one restarts, and every time a
partial bar is later completed -- must not leave the table holding both versions.

TIME. `at=` is New York wall-clock encoded as fake UTC, in nanoseconds, exactly
as it was for ILP. Nothing here converts a zone; the importers already did it.
"""

from __future__ import annotations

# Must precede pyarrow: it imports pandas lazily, and pandas hangs on this
# machine's broken WMI. See `sandbox/parquet_store.py` for the diagnosis.
import platform

platform._wmi = None

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PARQUET_DIR = Path(__file__).resolve().parent.parent / "data" / "parquet"

#: Where importers keep vendor downloads, month caches and run reports.
#:
#: NOT THE STORE, AND NOT ANYWHERE INSIDE THE REPOSITORY. `PARQUET_DIR` is the
#: source of truth that the server and every backtest read; this is scratch on
#: the way in -- a CSV export, the rows pending an import, a cached vendor month
#: so the hourly reconciliation refetches one month instead of two.
#:
#: It lived in the working tree twice: first as `<repo>/parquets/`, one letter
#: from `parquet`, then as `<repo>/data/vendor/`. Both were git-ignored and both
#: still came back on their own, because the feed daemon's hourly reconciliation
#: recreates the month cache within the hour of anyone deleting it -- so the
#: checkout could never be left clean. Scratch that regenerates itself belongs
#: on the machine, not in the tree. `QUANT_VENDOR_DIR` overrides the location;
#: nothing here is a backup, and deleting it costs only a re-download.
_VENDOR_OVERRIDE = os.environ.get("QUANT_VENDOR_DIR")
if _VENDOR_OVERRIDE:
    VENDOR_DIR = Path(_VENDOR_OVERRIDE)
elif sys.platform == "win32":
    VENDOR_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "quant" / "vendor"
else:
    _CACHE_HOME = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    VENDOR_DIR = Path(_CACHE_HOME) / "quant" / "vendor"


_NS_PER_SECOND = 1_000_000_000

#: The timestamp text the QuestDB export produced, which every history file is
#: written in. Shards match it so a table reads as one series and so row-group
#: statistics stay comparable as text.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


class TimestampNanos(int):
    """Epoch nanoseconds. Named for the `questdb.ingress` type it replaces."""

    @classmethod
    def from_datetime(cls, moment: datetime) -> "TimestampNanos":
        return cls(int(moment.timestamp() * _NS_PER_SECOND))


def format_timestamp(moment_ns: int) -> str:
    """`moment_ns` in the ISO text the store is written in."""
    moment = datetime.fromtimestamp(moment_ns // _NS_PER_SECOND, tz=timezone.utc)
    fraction = moment_ns % _NS_PER_SECOND
    return moment.replace(microsecond=fraction // 1_000).strftime(_TIMESTAMP_FORMAT) + "Z"


def shard_path(table: str, moment_ns: int) -> Path:
    """The shard a row stamped `moment_ns` belongs in."""
    day = datetime.fromtimestamp(moment_ns // _NS_PER_SECOND, tz=timezone.utc)
    return PARQUET_DIR / table / f"{day:%Y-%m-%d}.parquet"


def table_files(table: str) -> list[Path]:
    """Every file backing `table`, history first then shards in date order."""
    files = []
    single = PARQUET_DIR / f"{table}.parquet"
    if single.is_file():
        files.append(single)
    directory = PARQUET_DIR / table
    if directory.is_dir():
        files.extend(sorted(directory.glob("*.parquet")))
    return files


def compact(table: str) -> int | None:
    """Fold `<table>/<day>.parquet` into one `<table>.parquet` and drop the dir.

    THE LAYOUT THIS MODULE DOCUMENTS IS THE TWO-PART ONE -- a bulk history file
    plus a shard per day -- and that is right for a COLLECTOR, which must not be
    able to corrupt years of history by crashing mid-flush. It is wrong for a
    finished bulk import: `exness_usdjpy_1m` came out of one as 513 shards, and
    every reader then opens 513 files to answer one question.

    So this is the closing step of an import, not something a live writer calls.
    Lifted here from `exness_tick_import`, which had the only copy, so the 1m
    importer does not need a second one and both mean the same thing by it.

    THE SHARDS ARE REMOVED ONLY AFTER THE SINGLE FILE IS IN PLACE. `os.replace`
    is atomic, so the failure modes are "shards only" (re-runnable) and "both"
    (the shards are then redundant and deleted) -- never "neither".

    An existing single file is folded in as well rather than overwritten, so
    compacting after an incremental run keeps the history already there.

    AND DEDUPED, WHICH IT WAS NOT, AND THAT COST 24-50% OF EIGHTEEN TABLES.
    Folding the history file in without dropping the rows the shards replace
    meant every re-import appended a SECOND copy of every minute it re-fetched.
    Measured 2026-09-20 across `exness_*_1m`: `exness_us500_1m` held 2,080,913
    rows for 1,430,424 distinct minutes, `exness_xniusd_1m` 950,300 for 632,458,
    and only `exness_usdjpy_1m` and `exness_usoil_1m` were clean. Every copy was
    byte-identical to the one it duplicated -- 0 stamps with differing values
    across the three tables checked in full -- so this was pure bloat rather
    than corruption, and a reader taking the last row at a stamp got the right
    answer while opening half again as much file to find it.

    THE LAST ROW AT A STAMP WINS, which is the contract the importers already
    documented as "the writer replaces a row at a timestamp it already holds".
    Sorting is stable and ascending, so the freshly fetched shard rows sort
    after the history rows they replace and it is the newest fetch that
    survives.
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
    order = pc.sort_indices(merged, sort_keys=[("timestamp", "ascending")])
    merged = merged.take(order)
    merged, dropped = _drop_duplicate_stamps(merged)
    if dropped:
        print(f"{table}: dropped {dropped:,} duplicate row(s)", flush=True)

    staged = single.with_suffix(f".{os.getpid()}.tmp")
    pq.write_table(merged, str(staged), compression="zstd", compression_level=7)
    os.replace(staged, single)
    for shard in shards:
        shard.unlink()
    try:
        directory.rmdir()
    except OSError:
        # A stray `.tmp` from a killed writer. Leaving the directory is
        # harmless -- `table_files` reads the single file and an empty dir
        # contributes nothing -- and deleting a file this run did not write is
        # not something an importer should do silently.
        pass
    return merged.num_rows


def _drop_duplicate_stamps(block):
    """`(block, dropped)` -- one row per timestamp, the LAST one kept.

    The block must already be sorted by timestamp, which is the only reason
    this can be a linear scan: duplicates are then adjacent, so a row is kept
    exactly when the next row carries a different stamp, and the final row is
    always kept. Done in Arrow rather than by materialising the column as
    Python strings, because these tables run to four million rows.
    """
    stamps = block.column("timestamp").combine_chunks()
    count = len(stamps)
    if count < 2:
        return block, 0
    changes = pc.not_equal(stamps.slice(0, count - 1), stamps.slice(1))
    # `not_equal` is null where either side is null; a null stamp cannot be
    # matched against anything, so it is kept rather than silently dropped.
    changes = pc.fill_null(changes, True)
    # `slice` on a ChunkedArray returns a ChunkedArray and on an Array an
    # Array, and `not_equal` follows its input -- so the mask is normalised
    # here rather than assumed, which is what broke the first version of this.
    if isinstance(changes, pa.ChunkedArray):
        changes = changes.combine_chunks()
    keep = pa.chunked_array([pa.concat_arrays(
        [changes.cast(pa.bool_()), pa.array([True])])])
    deduped = block.filter(keep)
    return deduped, count - deduped.num_rows


def dedupe(table: str) -> tuple[int, int] | None:
    """Rewrite `table`'s history file with one row per timestamp.

    THE REPAIR FOR WHAT `compact` USED TO LEAVE BEHIND, and separate from it
    because a table that needs repairing has no shards to trigger a compaction
    -- the duplicates are already folded into the single file.

    Returns `(rows_before, rows_after)`, or None when there is no single file.
    Only ever removes rows whose timestamp another row already carries, and the
    write is staged and renamed, so an interrupted repair leaves the original.
    """
    single = PARQUET_DIR / f"{table}.parquet"
    if not single.is_file():
        return None
    block = pq.read_table(str(single))
    before = block.num_rows
    order = pc.sort_indices(block, sort_keys=[("timestamp", "ascending")])
    block = block.take(order)
    block, dropped = _drop_duplicate_stamps(block)
    if not dropped:
        return before, before
    staged = single.with_suffix(f".{os.getpid()}.tmp")
    pq.write_table(block, str(staged), compression="zstd", compression_level=7)
    os.replace(staged, single)
    return before, block.num_rows


def max_timestamp_ns(table: str) -> int | None:
    """Newest stored timestamp in `table`, in epoch nanoseconds.

    This is `SELECT max(timestamp) FROM <table>` and every importer's watermark.
    Read from Parquet footer statistics, so it costs the same on the
    five-billion-row depth table as on a small one -- which matters because the
    live collectors ask for it on every cycle.
    """
    newest = None
    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            if "timestamp" not in handle.schema_arrow.names:
                continue
            index = handle.schema_arrow.names.index("timestamp")
            for group in range(handle.metadata.num_row_groups):
                statistics = handle.metadata.row_group(group).column(index).statistics
                if statistics is None or not statistics.has_min_max:
                    continue
                value = _to_nanoseconds(statistics.max)
                if value is not None and (newest is None or value > newest):
                    newest = value
        finally:
            handle.close()
    return newest


def table_span(table: str) -> tuple[str, str, int] | None:
    """`(first, last, rows)` for `table`, or None when it holds nothing.

    ISO text for the two instants, matching what the SQL returned, and the row
    count from the Parquet footer. NO POLLING: the QuestDB version of this had
    to re-read until the count stopped moving, because a `count()` taken just
    after a flush understated the table by tens of thousands of rows while the
    WAL was still applying ([[questdb-update-read-lag]]). A shard is visible in
    full the instant it is renamed into place, so there is nothing to wait for.
    """
    rows = 0
    first = last = None
    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            rows += handle.metadata.num_rows
            if "timestamp" not in handle.schema_arrow.names:
                continue
            index = handle.schema_arrow.names.index("timestamp")
            for group in range(handle.metadata.num_row_groups):
                statistics = handle.metadata.row_group(group).column(index).statistics
                if statistics is None or not statistics.has_min_max:
                    continue
                low = _to_nanoseconds(statistics.min)
                high = _to_nanoseconds(statistics.max)
                if low is not None and (first is None or low < first):
                    first = low
                if high is not None and (last is None or high > last):
                    last = high
        finally:
            handle.close()
    if first is None:
        return None
    return format_timestamp(first), format_timestamp(last), rows


def max_timestamp_seconds(table: str) -> int | None:
    """`max_timestamp_ns` in whole seconds, which is what most importers compare."""
    newest = max_timestamp_ns(table)
    return None if newest is None else newest // _NS_PER_SECOND


def _to_nanoseconds(value) -> int | None:
    if isinstance(value, str):
        scalar = pc.cast(pa.array([value.rstrip("Z")]), pa.timestamp("ns"))
        return pc.cast(scalar, pa.int64())[0].as_py()
    if isinstance(value, int):
        return value
    if hasattr(value, "timestamp"):
        return int(round(value.timestamp() * _NS_PER_SECOND))
    return None


def read_columns(table: str, columns=None, start=None, end=None):
    """`(names, rows)` for `table` over `[start, end)`, oldest first.

    `start`/`end` are epoch nanoseconds or None. `rows` are plain lists with the
    timestamp FIRST, in epoch nanoseconds, whatever order `columns` asked for --
    which is the shape `cast(timestamp as long) ts, ...` produced, except that
    the unit is always nanoseconds rather than depending on the column's type.

    Row groups outside the range are never decoded, so asking for one day of a
    large table costs about what one day is worth.
    """
    wanted = None if columns is None else [
        name for name in dict.fromkeys(columns) if name != "timestamp"
    ]
    names = ["timestamp"] + (wanted or [])
    out = []
    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            available = handle.schema_arrow.names
            if "timestamp" not in available:
                continue
            projection = None if wanted is None else (
                ["timestamp"] + [name for name in wanted if name in available]
            )
            ts_index = available.index("timestamp")
            for group in range(handle.metadata.num_row_groups):
                statistics = handle.metadata.row_group(group).column(ts_index).statistics
                if not _group_overlaps(statistics, start, end):
                    continue
                chunk = handle.read_row_group(group, columns=projection,
                                              use_threads=False)
                stamps = _column_nanoseconds(chunk["timestamp"])
                values = [
                    chunk[name].to_pylist() if name in chunk.schema.names
                    else [None] * len(stamps)
                    for name in names[1:]
                ]
                for index, stamp in enumerate(stamps):
                    if start is not None and stamp < start:
                        continue
                    if end is not None and stamp >= end:
                        continue
                    out.append([stamp] + [column[index] for column in values])
        finally:
            handle.close()
    out.sort(key=lambda row: row[0])
    return names, out


def _column_nanoseconds(column):
    """int64 epoch nanoseconds from a timestamp column in any stored form."""
    kind = column.type
    if pa.types.is_string(kind) or pa.types.is_large_string(kind):
        column = pc.cast(column, pa.timestamp("ns", tz="UTC"))
        kind = column.type
    if pa.types.is_timestamp(kind):
        column = pc.cast(column, pa.timestamp("ns", tz=kind.tz))
    return pc.cast(column, pa.int64()).to_pylist()


def _group_overlaps(statistics, start, end) -> bool:
    """Whether a row group can hold a row in `[start, end)`."""
    if start is None and end is None:
        return True
    if statistics is None or not statistics.has_min_max:
        return True
    low = _to_nanoseconds(statistics.min)
    high = _to_nanoseconds(statistics.max)
    if low is None or high is None:
        return True
    if start is not None and high < start:
        return False
    if end is not None and low >= end:
        return False
    return True


def day_max_nanoseconds(table: str, start: int | None = None,
                        end: int | None = None) -> dict[str, int]:
    """`{YYYY-MM-DD: last timestamp that day}` over `[start, end)`.

    This replaces two QuestDB queries: `SAMPLE BY 1d` over the derived table and
    `table_partitions()` over the raw ones. The second existed to read day
    coverage from partition metadata rather than scanning hundreds of millions
    of events, and the same trick works here for the same reason.

    A row group whose footer minimum and maximum fall on the SAME DAY already
    states that day's maximum -- no decoding needed. Only a group that straddles
    midnight is read, and with million-row groups against a thirteen-million-row
    trading day that is a handful per day rather than all of them. The answer is
    exact either way; the statistics are just a shortcut where they suffice.
    """
    maxima: dict[str, int] = {}

    def offer(day: str, moment: int) -> None:
        if maxima.get(day, -1) < moment:
            maxima[day] = moment

    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            names = handle.schema_arrow.names
            if "timestamp" not in names:
                continue
            index = names.index("timestamp")
            for group in range(handle.metadata.num_row_groups):
                statistics = handle.metadata.row_group(group).column(index).statistics
                if not _group_overlaps(statistics, start, end):
                    continue
                low = high = None
                if statistics is not None and statistics.has_min_max:
                    low = _to_nanoseconds(statistics.min)
                    high = _to_nanoseconds(statistics.max)
                if (
                    low is not None
                    and high is not None
                    and _day_of(low) == _day_of(high)
                    and (start is None or low >= start)
                    and (end is None or high < end)
                ):
                    offer(_day_of(high), high)
                    continue
                chunk = handle.read_row_group(group, columns=["timestamp"],
                                              use_threads=False)
                for stamp in _column_nanoseconds(chunk["timestamp"]):
                    if start is not None and stamp < start:
                        continue
                    if end is not None and stamp >= end:
                        continue
                    offer(_day_of(stamp), stamp)
        finally:
            handle.close()
    return maxima


def _day_of(moment_ns: int) -> str:
    return datetime.fromtimestamp(
        moment_ns // _NS_PER_SECOND, tz=timezone.utc
    ).strftime("%Y-%m-%d")


def delete_range(table: str, start: int | None, end: int | None,
                 column: str | None = None, values=None, where=None) -> int:
    """Drop every row of `table` in `[start, end)`. Returns how many went.

    This is `ALTER TABLE ... DROP PARTITION`, which is how every backfill
    importer replaces the possibly-partial tail of a table before re-appending
    it. Bounds are epoch nanoseconds; `None` means unbounded on that side.

    `column` with either `values` (membership) or `where` (a predicate on the
    column's value) narrows the delete to matching rows -- which QuestDB could
    not do at all. That absence is why the L2 feature rebuild needed a staging
    table, a backup table and a verified three-way rename to replace one
    source's days, and why the Databento import leaned on `DEDUP UPSERT KEYS`
    to overwrite the rows an interrupted run left behind. Here it deletes
    `source IN ('dbento')` over a date range, or every row a given DBN file
    wrote, and leaves everything else in place.

    A shard that loses all of its rows is deleted. The bulk history file is
    rewritten in place if the range reaches into it, which is slower than
    dropping a partition was -- but it is rare, because the ranges these
    importers replace are recent days and recent days live in shards.

    Every rewrite is staged and renamed, so an interrupted delete leaves the
    old file intact rather than a truncated one.
    """
    removed = 0
    for path in table_files(table):
        # READ AND CLOSE BEFORE TOUCHING THE FILE. Windows refuses to unlink or
        # rename over a handle that is still open, and `pq.ParquetFile` holds
        # one for as long as it is alive -- so the whole read happens inside
        # this block and the handle is dropped before any write.
        handle = pq.ParquetFile(str(path))
        try:
            names = handle.schema_arrow.names
            if "timestamp" not in names:
                continue
            index = names.index("timestamp")
            # Skip a file the range cannot touch, so replacing yesterday does
            # not rewrite eleven years of history.
            if not any(
                _group_overlaps(
                    handle.metadata.row_group(group).column(index).statistics,
                    start,
                    end,
                )
                for group in range(handle.metadata.num_row_groups)
            ):
                continue
            existing = handle.read(use_threads=False)
        finally:
            handle.close()

        stamps = _column_nanoseconds(existing["timestamp"])
        wanted = None
        if column is not None and column in existing.schema.names:
            if where is not None:
                wanted = [where(value) for value in existing[column].to_pylist()]
            else:
                allowed = set(values or ())
                wanted = [value in allowed for value in existing[column].to_pylist()]
        elif column is not None:
            # The file has no such column, so no row of it can match.
            continue

        keep = [
            position
            for position, stamp in enumerate(stamps)
            if (start is not None and stamp < start)
            or (end is not None and stamp >= end)
            or (wanted is not None and not wanted[position])
        ]
        if len(keep) == len(stamps):
            continue
        removed += len(stamps) - len(keep)

        is_shard = path.parent.name == table
        if not keep and is_shard:
            path.unlink()
            continue
        staged = path.with_suffix(f".{os.getpid()}.tmp")
        pq.write_table(existing.take(keep), str(staged), compression="zstd",
                       compression_level=7)
        os.replace(staged, path)
    return removed


def drop_table(table: str) -> None:
    """Delete every file backing `table`. Used only by an explicit rebuild."""
    for path in table_files(table):
        path.unlink()
    directory = PARQUET_DIR / table
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


def row_count(table: str) -> int:
    total = 0
    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            total += handle.metadata.num_rows
        finally:
            handle.close()
    return total


class Sender:
    """Buffers rows and writes them into date shards on `flush`.

    Deliberately shaped like `questdb.ingress.Sender`; see the module docstring.
    `from_conf` accepts and ignores the old ILP configuration string so a caller
    that still passes one keeps working.
    """

    def __init__(self, parquet_dir: Path | None = None, dedup: bool = True):
        self.parquet_dir = Path(parquet_dir) if parquet_dir else PARQUET_DIR
        #: `dedup=False` KEEPS ROWS THAT SHARE A TIMESTAMP, which a bulk import
        #: needs and a live feed must not have.
        #:
        #: The wall-clock convention makes duplicate timestamps REAL DATA: the
        #: hour that repeats at DST fall-back genuinely produces two 01:30 bars,
        #: and `binance_fetch&import_1m` counts them and fails if the number
        #: changes ([[dst-fakes-hourly-bar-gaps]]). Collapsing them would delete
        #: an hour of history a year, quietly.
        #:
        #: A live collector wants the opposite: it re-sends the minute it just
        #: sent every time it restarts, and those really are the same bar.
        self.dedup = dedup
        self._pending: dict[str, list[tuple[int, dict]]] = {}

    @classmethod
    def from_conf(cls, _conf: str | None = None) -> "Sender":
        return cls()

    def establish(self) -> None:
        """No connection to make. Present so callers need no branch."""

    def __enter__(self) -> "Sender":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def row(self, table: str, *, symbols=None, columns=None, at=None) -> None:
        """Buffer one row. `at` is epoch nanoseconds, New York wall-clock."""
        if at is None:
            raise ValueError(f"{table}: a row needs an explicit timestamp")
        record = {}
        # Tag columns become ordinary text columns. QuestDB stored them as
        # SYMBOL, a dictionary-encoded string; the exported Parquet has them as
        # plain strings, so that is what a shard writes.
        for name, value in (symbols or {}).items():
            record[name] = None if value is None else str(value)
        record.update(columns or {})
        self._pending.setdefault(table, []).append((int(at), record))

    def dataframe(self, frame, table_name: str, symbols=None, at: str = "timestamp"):
        """Write a whole pandas frame, as `questdb.ingress.Sender.dataframe` did.

        The bulk path, and it stays bulk: `databento_import` hands this hundreds
        of thousands of rows at a time, so the frame is split by day and each
        day written as one Arrow table. Going through `row()` instead would
        build a Python dict per row and was measurably the slowest part of the
        import when it was tried.

        `symbols` named the columns QuestDB stored as SYMBOL rather than STRING.
        The store has no such distinction -- the CSV export wrote them as plain
        text -- so the argument is accepted and ignored, which keeps the call
        sites identical.

        Rows are written EXACTLY as given, including several sharing a
        timestamp: a depth feed emits many events in the same nanosecond and
        every one of them is real.
        """
        import numpy

        if frame.empty:
            return

        stamps = frame[at].to_numpy(dtype="datetime64[ns]").astype("int64")
        columns = {
            name: frame[name].to_numpy()
            for name in frame.columns
            if name != at
        }
        days = stamps // (86_400 * _NS_PER_SECOND)
        for day in numpy.unique(days):
            selection = numpy.flatnonzero(days == day)
            path = shard_path(table_name, int(stamps[selection[0]]))
            block = {
                name: values[selection].tolist()
                for name, values in columns.items()
            }
            block["timestamp"] = [
                format_timestamp(int(stamps[index])) for index in selection
            ]
            self._append_block(path, pa.table(block))

    def _append_block(self, path: Path, block) -> None:
        """Concatenate `block` onto the shard at `path`, keeping timestamp order."""
        path.parent.mkdir(parents=True, exist_ok=True)
        combined = block
        if path.is_file():
            handle = pq.ParquetFile(str(path))
            try:
                existing = handle.read(use_threads=False)
            finally:
                handle.close()
            combined = pa.concat_tables(
                [existing, block.cast(existing.schema)], promote_options="default"
            )
            order = pc.sort_indices(
                pc.cast(
                    pc.cast(combined["timestamp"], pa.timestamp("ns", tz="UTC")),
                    pa.int64(),
                )
            )
            combined = combined.take(order)
        staged = path.with_suffix(f".{os.getpid()}.tmp")
        pq.write_table(combined, str(staged), compression="zstd", compression_level=7)
        os.replace(staged, path)

    def flush(self) -> None:
        pending, self._pending = self._pending, {}
        for table, rows in pending.items():
            by_shard: dict[Path, list[tuple[int, dict]]] = {}
            for moment, record in rows:
                by_shard.setdefault(shard_path(table, moment), []).append(
                    (moment, record)
                )
            for path, shard_rows in by_shard.items():
                self._write_shard(path, shard_rows)

    def close(self) -> None:
        self.flush()

    def _write_shard(self, path: Path, rows: list[tuple[int, dict]]) -> None:
        """Merge `rows` into the shard at `path` and swap it in atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        merged: list[tuple[int, dict]] = []
        if path.is_file():
            # Read and CLOSE before the rename below: Windows will not replace a
            # file that still has an open handle.
            handle = pq.ParquetFile(str(path))
            try:
                existing = handle.read(use_threads=False).to_pylist()
            finally:
                handle.close()
            for record in existing:
                stamp = _to_nanoseconds(record.pop("timestamp"))
                if stamp is not None:
                    merged.append((stamp, record))

        if self.dedup:
            # New rows go in second, so a resent timestamp replaces the stored
            # one rather than doubling it.
            keyed = {stamp: record for stamp, record in merged}
            keyed.update({stamp: record for stamp, record in rows})
            merged = sorted(keyed.items())
        else:
            merged = sorted(merged + rows, key=lambda pair: pair[0])

        # The union of every record's columns, in first-seen order. A row that
        # omits one contributes a null there rather than shifting the schema.
        names = list(dict.fromkeys(
            name for _, record in merged for name in record
        ))
        columns = {
            name: [record.get(name) for _, record in merged] for name in names
        }
        columns["timestamp"] = [format_timestamp(stamp) for stamp, _ in merged]

        table = pa.table(columns)
        staged = path.with_suffix(f".{os.getpid()}.tmp")
        pq.write_table(table, str(staged), compression="zstd", compression_level=7)
        os.replace(staged, path)


def write_rows(table: str, rows, newer_than: int | None = None) -> int:
    """Append `rows` to `table`, skipping anything at or before `newer_than`.

    `rows` is an iterable of `(timestamp_ns, {column: value})`. Returns how many
    were written. The watermark is the same guard the ILP importers applied:
    the vendor tables have no dedup key, so a partial trailing bar is skipped
    until it is complete and the completed version lands later.
    """
    sender = Sender()
    written = 0
    for moment, columns in rows:
        if newer_than is not None and moment <= newer_than:
            continue
        sender.row(table, columns=columns, at=TimestampNanos(moment))
        written += 1
    sender.flush()
    return written
