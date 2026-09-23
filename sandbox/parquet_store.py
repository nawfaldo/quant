"""The one door to `data/parquet/`. Everything that reads market data goes here.

QuestDB is gone. It was the source for bars, ticks, depth and L2 features, and
every table was exported to `data/parquet/<table>.parquet` and then dropped, so
there is no fallback to fall back to -- a missing file is a missing table.

WHY A MODULE RATHER THAN `pq.read_table` AT EACH CALL SITE.

Three things about this data are easy to get wrong once and impossible to
notice afterwards, and all three were live in the tree before this module
existed:

  *Timestamps are strings.*  The CSV export wrote `2008-12-11T02:38:00.000000Z`,
  not an Arrow timestamp.  `pc.strptime(col, format="%Y-%m-%dT%H:%M:%S.%fZ")`
  looks like the obvious parse and returns 100% nulls, because Arrow's strptime
  has no `%f`.  The cast that works is to `timestamp(unit, tz="UTC")` -- without
  the tz it refuses the trailing `Z` with "expected no zone offset".

  *The dataset API deadlocks on this machine.*  `pq.read_table` and anything
  else that builds Arrow data from Python objects imports pandas, whose
  `compat._constants` calls `platform.machine()`, which on Python 3.14 asks WMI,
  which is broken here and blocks forever.  `platform._wmi = None` below makes
  `_wmi_query` raise `OSError` immediately so `platform` takes its registry
  path.  It must run before pyarrow is imported, hence its position.

  *A cache key must cover the rows.*  Keys are built from `fingerprint`, which
  reads the footer -- row count, size and mtime -- so re-importing a table
  invalidates every cache derived from it without anyone remembering to.

TIME.  Market timestamps are New York wall-clock encoded as fake UTC (AGENT.md).
Nothing here converts a zone; `tz="UTC"` is how Arrow is persuaded to accept the
`Z` suffix, and the epoch value it returns is the wall-clock reading. Callers
work in epoch SECONDS. Internally everything is epoch nanoseconds, because the
tick and depth tables carry nanosecond timestamps and the bar tables do not.

LAYOUT.  A table is `data/parquet/<table>.parquet`, or the directory
`data/parquet/<table>/*.parquet`, or BOTH -- the bulk history in one file and a
live collector appending one shard per day beside it. `table_files` reads the
pair as one series, so a running feed never rewrites history.
"""

from __future__ import annotations

# Must precede pyarrow: it imports pandas lazily, and pandas hangs on this
# machine's broken WMI. See the module docstring.
import platform

platform._wmi = None

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PARQUET_DIR = Path(__file__).resolve().parent.parent / "data" / "parquet"

#: bar tuple indices, matching `sandbox.data`
TS, O, H, L, C, V = range(6)

_NS_PER_SECOND = 1_000_000_000


class MissingTable(FileNotFoundError):
    """Raised for a table with no Parquet file. There is no other source."""


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #


def table_files(table):
    """Every Parquet file backing `table`, oldest first.

    BOTH LAYOUTS AT ONCE, WHICH IS THE POINT. `<table>.parquet` is the bulk
    history exported out of QuestDB and `<table>/<date>.parquet` is what a live
    collector appends, so a table normally has one of each and the reader has to
    see the pair as one series. Returning only the single file when it exists --
    which this did at first -- makes a running feed invisible to every reader
    while looking like it is working.

    Shards sort by name, and their names are dates, so file order is time order
    and `read_bars` never has to sort what it reads.
    """
    files = []
    single = PARQUET_DIR / f"{table}.parquet"
    if single.is_file():
        files.append(single)
    directory = PARQUET_DIR / table
    if directory.is_dir():
        files.extend(sorted(directory.glob("*.parquet")))
    return files


def has_table(table):
    return bool(table_files(table))


def tables():
    """Every table name in the store, both layouts."""
    if not PARQUET_DIR.is_dir():
        return set()
    found = set()
    for entry in PARQUET_DIR.iterdir():
        if entry.is_file() and entry.suffix == ".parquet":
            found.add(entry.stem)
        elif entry.is_dir() and any(entry.glob("*.parquet")):
            found.add(entry.name)
    return found


def _require(table):
    files = table_files(table)
    if not files:
        raise MissingTable(f"no Parquet for table {table!r} under {PARQUET_DIR}")
    return files


_FINGERPRINTS = {}


def fingerprint(table):
    """`rows|bytes|mtime` across the table's files -- the data part of a cache key.

    Read from the Parquet footer rather than by scanning, so fingerprinting the
    five-billion-row depth table costs the same as fingerprinting a small one.
    """
    if table in _FINGERPRINTS:
        return _FINGERPRINTS[table]
    files = table_files(table)
    if not files:
        value = f"{table}:absent"
    else:
        rows = sum(pq.ParquetFile(str(path)).metadata.num_rows for path in files)
        size = sum(path.stat().st_size for path in files)
        stamp = max(int(path.stat().st_mtime) for path in files)
        value = f"{table}:{rows}|{size}|{stamp}"
    _FINGERPRINTS[table] = value
    return value


def fingerprints(names):
    return ";".join(fingerprint(name) for name in names)


def column_names(table):
    return list(pq.ParquetFile(str(_require(table)[0])).schema_arrow.names)


def volume_column(table, preferred=None):
    """The volume column this table actually has.

    NOT DERIVABLE FROM THE TIMEFRAME. The rule this replaces was "30m tables
    quote `tick_volume`", which is true of the MT5 exports (`xagaud_30m`,
    `xniusd_30m`) and false of `es_30m`, a Yahoo table with a plain `volume`
    column -- so ES was being asked for a column that does not exist.
    """
    available = column_names(table)
    for candidate in (preferred, "volume", "tick_volume", "real_volume"):
        if candidate and candidate in available:
            return candidate
    raise KeyError(f"{table} has no volume column; has {available}")


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #


def to_nanoseconds(moment):
    """Epoch nanoseconds for a bound given as ISO text, epoch seconds, or None."""
    if moment is None:
        return None
    if isinstance(moment, (int, float)):
        return int(moment) * _NS_PER_SECOND
    text = str(moment).rstrip("Z")
    if len(text) == 10:  # a bare date is the midnight that opens it
        text += "T00:00:00"
    scalar = pc.cast(pa.array([text]), pa.timestamp("ns"))
    return pc.cast(scalar, pa.int64())[0].as_py()


def timestamp_nanoseconds(column):
    """int64 epoch nanoseconds from a timestamp column in any of its three forms.

    The store is being migrated from CSV-exported strings to native int64, so a
    reader has to accept both, plus the Arrow timestamp type a normalized file
    may carry instead.
    """
    kind = column.type
    if pa.types.is_string(kind) or pa.types.is_large_string(kind):
        column = pc.cast(column, pa.timestamp("ns", tz="UTC"))
        kind = column.type
    if pa.types.is_timestamp(kind):
        column = pc.cast(column, pa.timestamp("ns", tz=kind.tz))
        return pc.cast(column, pa.int64()).to_numpy(zero_copy_only=False)
    return pc.cast(column, pa.int64()).to_numpy(zero_copy_only=False)


def iso_text(moment_ns):
    """`moment_ns` as the ISO text the bar tables are written in.

    Six fractional digits and a `Z`, matching the CSV export. FOR DISPLAY AND
    FOR REPORTS -- never for a row-group comparison, which needs `_iso_bound`.
    """
    scalar = pa.scalar(moment_ns, pa.int64()).cast(pa.timestamp("ns"))
    return scalar.as_py().strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _iso_bound(moment_ns):
    """`moment_ns` as text that compares correctly against EITHER stored width.

    NINE FRACTIONAL DIGITS AND NO `Z`, WHICH IS NOT AN OVERSIGHT. The bar tables
    were exported with six fractional digits and the tick and depth tables with
    nine, and this one string is compared against both, so it has to be
    conservative in both directions:

      * against a nine-digit tick stamp `...017974016Z`, a six-digit bound
        `...017974Z` would compare GREATER at the seventh fraction character
        (`Z` > `0`) and prune away a row group that does hold matching rows.
      * against a six-digit bar stamp `...000000Z`, this bound compares LESS at
        the same character (`0` < `Z`), so an equal instant keeps the group.

    Erring towards keeping a group is free -- `iter_batches` filters the rows
    exactly afterwards -- while erring the other way silently loses data.
    """
    scalar = pa.scalar(moment_ns, pa.int64()).cast(pa.timestamp("ns"))
    return scalar.as_py().strftime("%Y-%m-%dT%H:%M:%S.%f") + "000"


def _stat_nanoseconds(value):
    if isinstance(value, int):
        return value
    if hasattr(value, "timestamp"):
        return int(round(value.timestamp() * _NS_PER_SECOND))
    return int(value)


def _row_group_overlaps(statistics, start_ns, end_ns):
    """Whether a row group can hold a row in `[start_ns, end_ns)`.

    Pruning is done in the file's OWN domain rather than by converting the
    group's bounds: ISO-8601 at a fixed width sorts lexicographically in
    chronological order, so a string table compares as text and a normalized one
    compares as numbers, and neither needs a parse per row group.
    """
    if statistics is None or not statistics.has_min_max:
        return True
    low, high = statistics.min, statistics.max
    if isinstance(low, str):
        if start_ns is not None and high < _iso_bound(start_ns):
            return False
        if end_ns is not None and low >= _iso_bound(end_ns):
            return False
        return True
    low_ns, high_ns = _stat_nanoseconds(low), _stat_nanoseconds(high)
    if start_ns is not None and high_ns < start_ns:
        return False
    if end_ns is not None and low_ns >= end_ns:
        return False
    return True


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #


def iter_batches(table, columns=None, start=None, end=None):
    """Yield `(ts_ns, arrow_table)` per row group inside `[start, end)`.

    Row groups outside the range are never decoded, which is the difference
    between reading a day out of `dbento_nq_depth` and reading forty gigabytes.

    `use_threads=False` throughout: the threaded reader and the dataset API both
    reach code that imports pandas, and pandas blocks forever on this machine.
    """
    start_ns, end_ns = to_nanoseconds(start), to_nanoseconds(end)
    wanted = None if columns is None else list(dict.fromkeys([*columns, "timestamp"]))
    for path in _require(table):
        handle = pq.ParquetFile(str(path))
        ts_index = handle.schema_arrow.names.index("timestamp")
        for group in range(handle.metadata.num_row_groups):
            statistics = handle.metadata.row_group(group).column(ts_index).statistics
            if not _row_group_overlaps(statistics, start_ns, end_ns):
                continue
            chunk = handle.read_row_group(group, columns=wanted, use_threads=False)
            if chunk.num_rows == 0:
                continue
            stamps = timestamp_nanoseconds(chunk["timestamp"])
            keep = np.ones(len(stamps), dtype=bool)
            if start_ns is not None:
                keep &= stamps >= start_ns
            if end_ns is not None:
                keep &= stamps < end_ns
            if not keep.all():
                if not keep.any():
                    continue
                chunk = chunk.filter(pa.array(keep))
                stamps = stamps[keep]
            yield stamps, chunk


def scan(table, columns=None, start=None, end=None):
    """`(ts_ns, arrow_table)` for the whole of `[start, end)`, concatenated."""
    stamp_parts, table_parts = [], []
    for stamps, chunk in iter_batches(table, columns, start, end):
        stamp_parts.append(stamps)
        table_parts.append(chunk)
    if not table_parts:
        schema = pq.ParquetFile(str(_require(table)[0])).schema_arrow
        if columns is not None:
            names = dict.fromkeys([*columns, "timestamp"])
            schema = pa.schema([schema.field(name) for name in names])
        return np.empty(0, dtype=np.int64), schema.empty_table()
    return np.concatenate(stamp_parts), pa.concat_tables(table_parts)


def bounds(table):
    """`(first, last)` ISO-day strings, read from footer statistics alone."""
    low = high = None
    for path in table_files(table):
        handle = pq.ParquetFile(str(path))
        try:
            index = handle.schema_arrow.names.index("timestamp")
        except ValueError:
            return None
        for group in range(handle.metadata.num_row_groups):
            statistics = handle.metadata.row_group(group).column(index).statistics
            if statistics is None or not statistics.has_min_max:
                return _bounds_by_scan(table)
            first, last = _as_iso(statistics.min), _as_iso(statistics.max)
            low = first if low is None else min(low, first)
            high = last if high is None else max(high, last)
    return None if low is None else (low[:10], high[:10])


def _as_iso(value):
    if isinstance(value, str):
        return value
    return iso_text(_stat_nanoseconds(value))


def _bounds_by_scan(table):
    stamps, _ = scan(table, columns=["timestamp"])
    if not len(stamps):
        return None
    return iso_text(int(stamps.min()))[:10], iso_text(int(stamps.max()))[:10]


# --------------------------------------------------------------------------- #
# bars
# --------------------------------------------------------------------------- #


def read_bars(table, bar_minutes=1, start=None, end=None, shift_hours=0,
              volume_col=None):
    """`[(ts, open, high, low, close, volume)]` in epoch seconds, oldest first.

    This is `SAMPLE BY <n>m FILL(NONE) ALIGN TO CALENDAR` and nothing else: a
    bucket with no source row is absent rather than carried forward, buckets are
    aligned to the epoch, and `shift_hours` moves the grid before bucketing so
    the Asian indices land one cash session to a day.

    Aggregating to a FINER bar than the table's own is refused upstream, in
    `exness_families.all_bars`, which knows the table's native resolution.
    """
    volume = volume_column(table, volume_col)
    columns = ["open", "high", "low", "close", volume]
    stamps, chunk = scan(table, columns=columns, start=start, end=end)
    if not len(stamps):
        return []

    seconds = stamps // _NS_PER_SECOND + shift_hours * 3600
    opens = _floats(chunk["open"])
    highs = _floats(chunk["high"])
    lows = _floats(chunk["low"])
    closes = _floats(chunk["close"])
    volumes = _floats(chunk[volume])

    if len(seconds) > 1 and not np.all(np.diff(seconds) >= 0):
        order = np.argsort(seconds, kind="stable")
        seconds, opens, highs = seconds[order], opens[order], highs[order]
        lows, closes, volumes = lows[order], closes[order], volumes[order]

    width = bar_minutes * 60
    buckets = seconds // width * width
    starts = np.flatnonzero(np.concatenate(([True], buckets[1:] != buckets[:-1])))
    ends = np.append(starts[1:], len(buckets))

    return list(zip(
        buckets[starts].tolist(),
        opens[starts].tolist(),
        np.maximum.reduceat(highs, starts).tolist(),
        np.minimum.reduceat(lows, starts).tolist(),
        closes[ends - 1].tolist(),
        np.add.reduceat(volumes, starts).tolist(),
    ))


def _floats(column):
    filled = pc.fill_null(column, 0.0) if column.null_count else column
    return pc.cast(filled, pa.float64()).to_numpy(zero_copy_only=False)
