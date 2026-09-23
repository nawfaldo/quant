#!/usr/bin/env python3
"""Import Databento MBP-10 as Bookmap-compatible event-level NQ tables.

Every trade remains event-level. Every change between consecutive top-10
snapshots is emitted as an absolute price-level update, including zero-size
removals, so replay has the same semantics as ``bm_nq_depth``. Stored
timestamps use New York wall-clock values encoded as UTC. UTC exists only
transiently during conversion and is never stored.
"""


from __future__ import annotations

# Must precede pandas, which hangs on this machine's broken WMI at import.
try:
    import _prelude  # noqa: F401  -- see tools/_prelude.py
except ImportError:
    from tools import _prelude  # noqa: F401


import argparse
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import databento as db
import numpy as np
import pandas as pd
import requests

try:
    import parquet_writer as pw
    from parquet_writer import Sender
except ImportError:
    from tools import parquet_writer as pw
    from tools.parquet_writer import Sender


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "databento"
DEFAULT_MANIFEST = ROOT / ".databento_imported_bookmap.json"
SCHEMA_VERSION = 6
NQ_TICK_SIZE_FIXED = 250_000_000
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

SOURCE_LEVEL_COLUMNS = [
    column
    for level in range(10)
    for column in (
        f"bid_px_{level:02d}",
        f"ask_px_{level:02d}",
        f"bid_sz_{level:02d}",
        f"ask_sz_{level:02d}",
        f"bid_ct_{level:02d}",
        f"ask_ct_{level:02d}",
    )
]
REQUIRED_COLUMNS = {
    "ts_recv",
    "ts_event",
    "publisher_id",
    "instrument_id",
    "action",
    "side",
    "depth",
    "price",
    "size",
    "flags",
    "sequence",
    *SOURCE_LEVEL_COLUMNS,
}

TICK_SCHEMA = [
    ("side", "SYMBOL"),
    ("price", "DOUBLE"),
    ("size", "DOUBLE"),
    ("is_otc", "BOOLEAN"),
    ("is_exec_start", "BOOLEAN"),
    ("is_exec_end", "BOOLEAN"),
    ("buy_vol", "DOUBLE"),
    ("sell_vol", "DOUBLE"),
    ("delta", "DOUBLE"),
    ("timestamp", "TIMESTAMP_NS"),
    ("best_bid", "DOUBLE"),
    ("best_ask", "DOUBLE"),
    ("alias", "SYMBOL"),
    ("stream_id", "SYMBOL"),
    ("trade_sequence", "LONG"),
    ("schema_version", "LONG"),
]

DEPTH_SCHEMA = [
    ("side", "SYMBOL"),
    ("price", "DOUBLE"),
    ("size", "DOUBLE"),
    ("price_level", "LONG"),
    ("size_level", "LONG"),
    ("sequence", "LONG"),
    ("timestamp", "TIMESTAMP_NS"),
    ("alias", "SYMBOL"),
    ("stream_id", "SYMBOL"),
    ("schema_version", "LONG"),
]


@dataclass(frozen=True)
class ImportJob:
    path: str
    source_file: str
    tick_table: str
    depth_table: str
    host: str
    port: int
    chunk_size: int


@dataclass(frozen=True)
class ImportResult:
    source_file: str
    identity: dict[str, int]
    source_rows: int
    ticks: int
    depth_events: int


@dataclass(frozen=True)
class ValidationResult:
    source_file: str
    source_rows: int
    tick_rows: int
    depth_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import event-level Databento NQ in Bookmap-compatible schemas."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--symbol", default="nq")
    parser.add_argument("--table-prefix", default="dbento_")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate event transformation in multiple processes without writing",
    )
    return parser.parse_args()


def safe_identifier(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"unsafe {label}: {value!r}")
    return normalized


def file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid import manifest: {path}")
    return value


def save_manifest(path: Path, value: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def manifest_matches(entry: object, identity: dict[str, int]) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("size") == identity["size"]
        and entry.get("mtime_ns") == identity["mtime_ns"]
        and entry.get("schema_version") == SCHEMA_VERSION
    )


def clear_source_file(
    tick_table: str, depth_table: str, source_file: str, span: tuple[int, int] | None
) -> int:
    """Delete every row a previous, interrupted run of `source_file` wrote.

    THIS REPLACES A GUARANTEE, NOT A NO-OP. Both tables declared
    `DEDUP UPSERT KEYS` on the timestamp plus `stream_id` and the file's row
    ordinal, so re-importing a partially written file OVERWROTE its orphan rows
    in place and no cleanup pass was needed. The store appends, so the same
    retry would leave two copies of every row the first attempt managed to
    write -- which is the one thing a raw event table must never contain.

    `stream_id` is `"<source file>:<instrument id>"`, so a prefix match selects
    exactly this file's rows. `span` bounds the scan to the days the file
    covers; without it this would read the whole five-billion-row depth table
    to clean up one file.
    """
    prefix = f"{source_file}:"
    start, end = span if span else (None, None)
    removed = 0
    for table in (tick_table, depth_table):
        removed += pw.delete_range(
            table, start, end,
            column="stream_id",
            where=lambda value: bool(value) and value.startswith(prefix),
        )
    return removed


def stored_span(store: "db.DBNStore") -> tuple[int, int] | None:
    """The day range a DBN file writes into, widened for the clock shift.

    The file's own metadata is real UTC and the rows land in New York wall
    clock, so the span is widened by a day on each side rather than converted
    -- it only has to BOUND the cleanup scan, and a day of slack costs one
    extra shard read while getting the offset wrong would leave orphan rows.
    """
    metadata = getattr(store, "metadata", None)
    start = getattr(metadata, "start", None)
    end = getattr(metadata, "end", None)
    if not start or not end:
        return None
    day = 86_400 * 1_000_000_000
    return int(start) - day, int(end) + day


def validate_source_frame(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"DBN frame is missing MBP-10 columns: {missing}")


def new_york_wall_clock_as_utc(values: pd.Series) -> pd.Series:
    """Encode New York local wall time in the repository's fake-UTC convention."""
    utc = pd.to_datetime(values, utc=True)
    return (
        utc.dt.tz_convert("America/New_York")
        .dt.tz_localize(None)
        .dt.tz_localize("UTC")
    )


def prepare_source(
    source: pd.DataFrame, source_row_start: int
) -> pd.DataFrame:
    frame = source.reset_index()
    validate_source_frame(frame)
    ts_recv = pd.to_datetime(frame["ts_recv"], utc=True)
    frame["_ts_recv"] = new_york_wall_clock_as_utc(ts_recv)
    frame["_bucket"] = frame["_ts_recv"].dt.floor("s")
    frame["_source_row"] = pd.RangeIndex(
        source_row_start, source_row_start + len(frame), dtype="int64"
    )
    return frame


@dataclass
class TransformState:
    books: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = field(
        default_factory=dict
    )
    volume: dict[int, tuple[float, float]] = field(default_factory=dict)


def alias_values(instrument_ids: np.ndarray) -> list[str]:
    return [f"NQ:{int(value)}" for value in instrument_ids]


def stream_values(source_file: str, instrument_ids: np.ndarray) -> list[str]:
    return [f"{source_file}:{int(value)}" for value in instrument_ids]


def compact_ticks(
    frame: pd.DataFrame,
    source_file: str,
    state: TransformState | None = None,
) -> pd.DataFrame:
    state = state or TransformState()
    trades = frame.loc[frame["action"].astype(str) == "T"]
    if trades.empty:
        return pd.DataFrame(columns=[name for name, _ in TICK_SCHEMA])
    raw_side = trades["side"].astype(str)
    instrument_ids = trades["instrument_id"].to_numpy(dtype=np.int64)
    sizes = trades["size"].to_numpy(dtype=np.float64)
    buy_volume = np.zeros(len(trades), dtype=np.float64)
    sell_volume = np.zeros(len(trades), dtype=np.float64)
    is_buy = raw_side.to_numpy() == "B"
    for instrument_id in np.unique(instrument_ids):
        positions = np.flatnonzero(instrument_ids == instrument_id)
        prior_buy, prior_sell = state.volume.get(int(instrument_id), (0.0, 0.0))
        buy_volume[positions] = prior_buy + np.cumsum(
            np.where(is_buy[positions], sizes[positions], 0.0)
        )
        sell_volume[positions] = prior_sell + np.cumsum(
            np.where(is_buy[positions], 0.0, sizes[positions])
        )
        state.volume[int(instrument_id)] = (
            float(buy_volume[positions[-1]]),
            float(sell_volume[positions[-1]]),
        )

    return pd.DataFrame(
        {
            "side": raw_side.map({"B": "BUY", "A": "SELL"}).fillna("UNKNOWN"),
            "price": trades["price"].astype("int64") / 1_000_000_000.0,
            "size": sizes,
            "is_otc": False,
            "is_exec_start": False,
            "is_exec_end": False,
            "buy_vol": buy_volume,
            "sell_vol": sell_volume,
            "delta": buy_volume - sell_volume,
            "timestamp": trades["_ts_recv"],
            "best_bid": trades["bid_px_00"].astype("int64") / 1_000_000_000.0,
            "best_ask": trades["ask_px_00"].astype("int64") / 1_000_000_000.0,
            "alias": alias_values(instrument_ids),
            "stream_id": stream_values(source_file, instrument_ids),
            "trade_sequence": trades["_source_row"].astype("int64") + 1,
            "schema_version": SCHEMA_VERSION,
        },
        index=trades.index,
    )[[name for name, _ in TICK_SCHEMA]]


def side_depth_changes(
    frame: pd.DataFrame,
    positions: np.ndarray,
    source_file: str,
    instrument_id: int,
    side_name: str,
    previous_prices: np.ndarray,
    previous_sizes: np.ndarray,
) -> tuple[list[pd.DataFrame], np.ndarray, np.ndarray]:
    prefix = "bid" if side_name == "BID" else "ask"
    price_columns = [f"{prefix}_px_{level:02d}" for level in range(10)]
    size_columns = [f"{prefix}_sz_{level:02d}" for level in range(10)]
    current_prices = frame.iloc[positions][price_columns].to_numpy(dtype=np.int64)
    current_sizes = frame.iloc[positions][size_columns].to_numpy(dtype=np.int64)
    prior_prices = np.vstack((previous_prices, current_prices[:-1]))
    prior_sizes = np.vstack((previous_sizes, current_sizes[:-1]))

    matches = (
        (current_prices[:, :, None] > 0)
        & (prior_prices[:, None, :] > 0)
        & (current_prices[:, :, None] == prior_prices[:, None, :])
    )
    matched_sizes = np.where(matches, prior_sizes[:, None, :], 0).max(axis=2)
    changed = (current_prices > 0) & (
        ~matches.any(axis=2) | (current_sizes != matched_sizes)
    )
    remains = (
        (prior_prices[:, :, None] > 0)
        & (current_prices[:, None, :] > 0)
        & (prior_prices[:, :, None] == current_prices[:, None, :])
    ).any(axis=2)
    removed = (prior_prices > 0) & (prior_sizes > 0) & ~remains

    timestamps = frame["_ts_recv"].to_numpy()[positions]
    source_rows = frame["_source_row"].to_numpy(dtype=np.int64)[positions]
    outputs: list[pd.DataFrame] = []
    side_offset = 0 if side_name == "BID" else 20
    for mask, prices, sizes, ordinal_offset in (
        (changed, current_prices, current_sizes, side_offset),
        (removed, prior_prices, np.zeros_like(prior_sizes), side_offset + 10),
    ):
        row_indexes, levels = np.nonzero(mask)
        if not len(row_indexes):
            continue
        raw_prices = prices[row_indexes, levels]
        raw_sizes = sizes[row_indexes, levels]
        selected_ids = np.full(len(row_indexes), instrument_id, dtype=np.int64)
        outputs.append(
            pd.DataFrame(
                {
                    "side": side_name,
                    "price": raw_prices / 1_000_000_000.0,
                    "size": raw_sizes.astype(np.float64),
                    "price_level": np.rint(
                        raw_prices / NQ_TICK_SIZE_FIXED
                    ).astype(np.int64),
                    "size_level": raw_sizes.astype(np.int64),
                    "sequence": (
                        source_rows[row_indexes] * 64
                        + ordinal_offset
                        + levels
                        + 1
                    ),
                    "timestamp": timestamps[row_indexes],
                    "alias": alias_values(selected_ids),
                    "stream_id": stream_values(source_file, selected_ids),
                    "schema_version": SCHEMA_VERSION,
                }
            )
        )
    return outputs, current_prices[-1].copy(), current_sizes[-1].copy()


def bookmap_depth_events(
    frame: pd.DataFrame,
    source_file: str,
    state: TransformState | None = None,
) -> pd.DataFrame:
    state = state or TransformState()
    if frame.empty:
        return pd.DataFrame(columns=[name for name, _ in DEPTH_SCHEMA])
    instrument_ids = frame["instrument_id"].to_numpy(dtype=np.int64)
    outputs: list[pd.DataFrame] = []
    empty = np.zeros(10, dtype=np.int64)
    for instrument_id in np.unique(instrument_ids):
        positions = np.flatnonzero(instrument_ids == instrument_id)
        prior = state.books.get(
            int(instrument_id),
            (empty, empty, empty, empty),
        )
        bid_outputs, bid_prices, bid_sizes = side_depth_changes(
            frame,
            positions,
            source_file,
            int(instrument_id),
            "BID",
            prior[0],
            prior[1],
        )
        ask_outputs, ask_prices, ask_sizes = side_depth_changes(
            frame,
            positions,
            source_file,
            int(instrument_id),
            "ASK",
            prior[2],
            prior[3],
        )
        outputs.extend(bid_outputs)
        outputs.extend(ask_outputs)
        state.books[int(instrument_id)] = (
            bid_prices,
            bid_sizes,
            ask_prices,
            ask_sizes,
        )
    if not outputs:
        return pd.DataFrame(columns=[name for name, _ in DEPTH_SCHEMA])
    result = pd.concat(outputs, ignore_index=True)
    result.sort_values(["timestamp", "sequence"], inplace=True, kind="stable")
    return result[[name for name, _ in DEPTH_SCHEMA]]


def write_frame(
    sender: Sender,
    frame: pd.DataFrame,
    table: str,
    symbols: list[str],
) -> int:
    if frame.empty:
        return 0
    sender.dataframe(frame, table_name=table, symbols=symbols, at="timestamp")
    return len(frame)


def import_file(job: ImportJob) -> ImportResult:
    path = Path(job.path)
    identity = file_identity(path)
    store = db.DBNStore.from_file(path)
    # Anything a previous interrupted attempt wrote for this file goes first.
    # See `clear_source_file`: the store appends where QuestDB upserted.
    clear_source_file(
        job.tick_table, job.depth_table, job.source_file, stored_span(store)
    )
    source_rows = ticks = depth_events = source_row = 0
    state = TransformState()

    # `dedup=False`: a depth feed emits many events in the same nanosecond and
    # every one of them is a distinct book update.
    with Sender(dedup=False) as sender:
        for source in store.to_df(
            price_type="fixed",
            pretty_ts=True,
            map_symbols=False,
            count=job.chunk_size,
        ):
            frame = prepare_source(source, source_row)
            source_row += len(frame)
            source_rows += len(frame)
            if frame.empty:
                continue
            ticks += write_frame(
                sender,
                compact_ticks(frame, job.source_file, state),
                job.tick_table,
                ["side", "alias", "stream_id"],
            )
            depth_events += write_frame(
                sender,
                bookmap_depth_events(frame, job.source_file, state),
                job.depth_table,
                ["side", "alias", "stream_id"],
            )
            sender.flush()

    return ImportResult(
        source_file=job.source_file,
        identity=identity,
        source_rows=source_rows,
        ticks=ticks,
        depth_events=depth_events,
    )


def validate_file(path_text: str) -> ValidationResult:
    path = Path(path_text)
    source = next(
        iter(
            db.DBNStore.from_file(path).to_df(
                price_type="fixed",
                pretty_ts=True,
                map_symbols=False,
                count=100_000,
            )
        )
    )
    frame = prepare_source(source, 0)
    state = TransformState()
    ticks = compact_ticks(frame, path.name, state)
    depth = bookmap_depth_events(frame, path.name, state)
    if list(ticks.columns) != [name for name, _ in TICK_SCHEMA]:
        raise RuntimeError("Bookmap-compatible tick columns do not match schema")
    if list(depth.columns) != [name for name, _ in DEPTH_SCHEMA]:
        raise RuntimeError("Bookmap-compatible depth columns do not match schema")
    if not depth["timestamp"].is_monotonic_increasing:
        raise RuntimeError("event-level depth rows are not monotonic")
    return ValidationResult(path.name, len(frame), len(ticks), len(depth))


def validate_only(paths: list[Path], workers: int) -> None:
    selected = paths[: min(workers, len(paths))]
    with ProcessPoolExecutor(max_workers=len(selected)) as executor:
        futures = [executor.submit(validate_file, str(path)) for path in selected]
        for future in as_completed(futures):
            result = future.result()
            print(
                f"Validated {result.source_file}: {result.source_rows:,} source rows -> "
                f"{result.tick_rows:,} trades + {result.depth_rows:,} L2 seconds",
                flush=True,
            )
    print(
        f"Compact multicore validation passed with {len(selected)} worker(s); "
        "no writes performed."
    )


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be at least 1")
    source = args.input.resolve()
    if not source.is_dir():
        raise SystemExit(f"Databento directory does not exist: {source}")
    files = sorted(source.rglob("*.dbn.zst"))
    if not files:
        raise SystemExit(f"No .dbn.zst files found below {source}")
    if args.validate_only:
        validate_only(files, args.workers)
        return 0

    symbol = safe_identifier(args.symbol, "symbol")
    prefix = safe_identifier(args.table_prefix, "table prefix")
    tick_table = f"{prefix}{symbol}_ticks"
    depth_table = f"{prefix}{symbol}_depth"
    if (tick_table, depth_table) != ("dbento_nq_ticks", "dbento_nq_depth"):
        raise SystemExit("event import is restricted to the two NQ Databento tables")

    manifest_path = args.manifest.resolve()
    if source == manifest_path or source in manifest_path.parents:
        raise SystemExit("Manifest must be outside the retained DBN directory")
    manifest = load_manifest(manifest_path)
    # Nothing to create or schema-check: a shard takes its columns from the
    # rows written to it, and `TICK_SCHEMA`/`DEPTH_SCHEMA` remain the record of
    # what those columns are.

    jobs: list[ImportJob] = []
    for path in files:
        key = str(path.relative_to(source)).replace("\\", "/")
        identity = file_identity(path)
        if manifest_matches(manifest.get(key), identity):
            print(f"Skipping verified event-level file: {key}")
            continue
        # Rows left behind by an interrupted run ARE cleaned up, in
        # `import_file`, bounded to the days that file covers. QuestDB did not
        # need this because its DEDUP keys overwrote them in place; the store
        # appends, so a retry would otherwise double every row the first
        # attempt managed to write.
        jobs.append(
            ImportJob(
                path=str(path),
                source_file=key,
                tick_table=tick_table,
                depth_table=depth_table,
                chunk_size=args.chunk_size,
            )
        )

    if not jobs:
        print("All retained DBN files are verified in the event-level manifest.")
        return 0

    workers = min(args.workers, len(jobs))
    total_source = total_ticks = total_depth = 0
    print(
        f"Importing {len(jobs)} files with {workers} workers into event-level "
        f"{tick_table}/{depth_table}...",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(import_file, job) for job in jobs]
        for future in as_completed(futures):
            result = future.result()
            total_source += result.source_rows
            total_ticks += result.ticks
            total_depth += result.depth_events
            manifest[result.source_file] = {
                **result.identity,
                "schema_version": SCHEMA_VERSION,
                "source_rows": result.source_rows,
                "ticks": result.ticks,
                "depth_events": result.depth_events,
            }
            save_manifest(manifest_path, manifest)
            print(
                f"Imported {result.source_file}: {result.source_rows:,} source rows -> "
                f"{result.ticks:,} trades + {result.depth_events:,} L2 events",
                flush=True,
            )

    print(
        f"Event import submitted: {total_source:,} source records -> "
        f"{total_ticks:,} trades and {total_depth:,} L2 events."
    )
    # No WAL to drain before building features. Every shard was renamed into
    # place complete, so both tables are queryable the moment this returns.
    for table_name in (tick_table, depth_table):
        print(f"{table_name}: {pw.row_count(table_name):,} rows", flush=True)
    print(f"Source DBN files remain unchanged below {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
