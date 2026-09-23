"""Build normalized one-second L2 features from Bookmap and Databento.

The ``bm_*`` and ``dbento_*`` tables are immutable, Bookmap-compatible
event-level inputs replayed into seconds. Both write the same
``<symbol>_l2_features_1s`` schema and retain a source tag. Separate days run
in parallel worker processes.
"""


from __future__ import annotations


import argparse
import heapq
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

try:
    import parquet_writer as pw
    from parquet_writer import Sender, TimestampNanos
except ImportError:
    from tools import parquet_writer as pw
    from tools.parquet_writer import Sender, TimestampNanos


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
RAW_PREFIXES = ("bm_", "dbento_")
FEATURE_TABLE = re.compile(
    r"^[a-z][a-z0-9]*_l2_features_1s(?:_rebuild)?$"
)
DATABENTO_SCHEMA_VERSION = 6
MAX_PARALLEL_REPLAY_WORKERS = 5


def safe_identifier(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"unsafe {label}: {value!r}")
    return normalized


def validate_target_table(value: str) -> str:
    table = safe_identifier(value, "target table")
    if table.startswith(RAW_PREFIXES):
        raise ValueError(f"refusing to write to raw market-data table {table!r}")
    if not FEATURE_TABLE.fullmatch(table):
        raise ValueError(
            "derived feature tables must use '<symbol>_l2_features_1s'"
        )
    return table


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value!r}") from exc


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def timestamp_ns(value: str) -> int:
    """Parse a QuestDB ISO timestamp without losing nanosecond precision."""
    text = value.removesuffix("Z")
    head, dot, fraction = text.partition(".")
    instant = datetime.fromisoformat(head).replace(tzinfo=timezone.utc)
    nanos = int((fraction + "000000000")[:9]) if dot else 0
    return int(instant.timestamp()) * 1_000_000_000 + nanos


def finite(value: float, default: float = 0.0) -> float:
    return value if math.isfinite(value) else default


@dataclass(order=True)
class Event:
    timestamp_ns: int
    priority: int
    sequence: int
    kind: str = field(compare=False)
    side: str = field(compare=False)
    price: float = field(compare=False)
    size: float = field(compare=False)
    best_bid: float = field(default=0.0, compare=False)
    best_ask: float = field(default=0.0, compare=False)
    stream_id: str = field(default="", compare=False)


@dataclass
class FeatureRow:
    timestamp_ns: int
    values: dict[str, float | int | bool]


@dataclass(frozen=True)
class DayJob:
    day: date
    symbol: str
    source: str
    target_table: str
    depth_table: str
    tick_table: str
    tick_size: float
    seed_ticks: int
    flush_rows: int


@dataclass(frozen=True)
class DayResult:
    day: date
    source: str
    rows_written: int
    seeded_levels: int


class FeatureAccumulator:
    """Replay Bookmap events and emit end-of-second book/flow features."""

    def __init__(self, tick_size: float, levels: int = 10) -> None:
        if tick_size <= 0:
            raise ValueError("tick size must be positive")
        self.tick_size = tick_size
        self.levels = levels
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.bucket: int | None = None
        self.previous_mid: float | None = None
        self.stream_id: str | None = None
        self.last_trade_ns: int | None = None
        self.last_trade_side: str | None = None
        self.last_trade_price: float | None = None
        self.reset_bucket()

    def reset_bucket(self) -> None:
        self.bid_add = self.bid_cancel = 0.0
        self.ask_add = self.ask_cancel = 0.0
        self.buy_volume = self.sell_volume = 0.0
        self.executed_at_bid = self.executed_at_ask = 0.0
        self.trade_count = self.depth_event_count = 0
        self.bid_adds_by_price: dict[float, float] = {}
        self.ask_adds_by_price: dict[float, float] = {}
        self.bid_exec_by_price: dict[float, float] = {}
        self.ask_exec_by_price: dict[float, float] = {}

    def seed(self, side: str, price: float, size: float) -> None:
        if size <= 0:
            return
        (self.bids if side == "BID" else self.asks)[price] = size

    def on_event(self, event: Event) -> list[FeatureRow]:
        emitted: list[FeatureRow] = []
        second = event.timestamp_ns // 1_000_000_000
        if self.bucket is None:
            self.bucket = second
        elif second != self.bucket:
            row = self.emit()
            if row is not None:
                emitted.append(row)
            self.bucket = second
            self.reset_bucket()

        if event.stream_id and self.stream_id not in (None, event.stream_id):
            # A collector reconnect begins with a fresh Bookmap snapshot.
            self.bids.clear()
            self.asks.clear()
            self.last_trade_ns = None
            self.last_trade_side = None
            self.last_trade_price = None
        if event.stream_id:
            self.stream_id = event.stream_id

        if event.kind == "D":
            self.apply_depth(event)
        else:
            self.apply_trade(event)
            self.remove_crossed_seed_levels(event.best_bid, event.best_ask)
        return emitted

    def apply_depth(self, event: Event) -> None:
        book = self.bids if event.side == "BID" else self.asks
        previous = book.get(event.price, 0.0)
        current = max(event.size, 0.0)
        difference = current - previous
        if current == 0:
            book.pop(event.price, None)
        else:
            book[event.price] = current

        if event.side == "BID":
            if difference >= 0:
                self.bid_add += difference
                self.bid_adds_by_price[event.price] = (
                    self.bid_adds_by_price.get(event.price, 0.0) + difference
                )
            else:
                self.bid_cancel -= difference
        else:
            if difference >= 0:
                self.ask_add += difference
                self.ask_adds_by_price[event.price] = (
                    self.ask_adds_by_price.get(event.price, 0.0) + difference
                )
            else:
                self.ask_cancel -= difference
        self.depth_event_count += 1

    def apply_trade(self, event: Event) -> None:
        if event.side == "BUY":
            self.buy_volume += event.size
            self.executed_at_ask += event.size
            self.ask_exec_by_price[event.price] = (
                self.ask_exec_by_price.get(event.price, 0.0) + event.size
            )
        elif event.side == "SELL":
            self.sell_volume += event.size
            self.executed_at_bid += event.size
            self.bid_exec_by_price[event.price] = (
                self.bid_exec_by_price.get(event.price, 0.0) + event.size
            )
        
        # Sub-millisecond sweep reconstruction: unbundled execution fragments from
        # a single matching engine sweep within 1ms on the same aggressive side
        # belong to the same trade event.
        is_sweep_continuation = (
            self.last_trade_ns is not None
            and (event.timestamp_ns - self.last_trade_ns) <= 1_000_000
            and event.side == self.last_trade_side
        )
        if not is_sweep_continuation:
            self.trade_count += 1

        self.last_trade_ns = event.timestamp_ns
        self.last_trade_side = event.side
        self.last_trade_price = event.price

    def remove_crossed_seed_levels(self, best_bid: float, best_ask: float) -> None:
        # Older rows may predate stream_id. BBO values remove stale levels that
        # would otherwise leave a crossed book after a day-boundary seed.
        if best_ask > 0:
            self.bids = {price: size for price, size in self.bids.items() if price < best_ask}
        if best_bid > 0:
            self.asks = {price: size for price, size in self.asks.items() if price > best_bid}

    @staticmethod
    def imbalance(bids: list[tuple[float, float]], asks: list[tuple[float, float]], n: int) -> float:
        bid_size = sum(size for _, size in bids[:n])
        ask_size = sum(size for _, size in asks[:n])
        total = bid_size + ask_size
        return (bid_size - ask_size) / total if total > 0 else 0.0

    @staticmethod
    def weighted_distance(
        levels: list[tuple[float, float]], mid: float, limit: int
    ) -> tuple[float, float]:
        selected = levels[:limit]
        total = sum(size for _, size in selected)
        if total <= 0:
            return 0.0, 0.0
        distance = sum(abs(price - mid) * size for price, size in selected) / total
        return distance, total

    @staticmethod
    def replenished(adds: dict[float, float], executions: dict[float, float]) -> float:
        return sum(min(added, executions.get(price, 0.0)) for price, added in adds.items())

    def emit(self) -> FeatureRow | None:
        if self.bucket is None:
            return None
        bids = sorted(self.bids.items(), reverse=True)
        asks = sorted(self.asks.items())
        book_valid = bool(bids and asks and bids[0][0] < asks[0][0])
        if book_valid:
            best_bid, bid_size = bids[0]
            best_ask, ask_size = asks[0]
            mid = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
            microprice = (best_ask * bid_size + best_bid * ask_size) / (bid_size + ask_size)
            top1 = self.imbalance(bids, asks, 1)
            top5 = self.imbalance(bids, asks, 5)
            top10 = self.imbalance(bids, asks, self.levels)
            bid_distance, bid_depth = self.weighted_distance(bids, mid, self.levels)
            ask_distance, ask_depth = self.weighted_distance(asks, mid, self.levels)
            total_depth = bid_depth + ask_depth
            depth_distance = (
                (ask_distance * ask_depth - bid_distance * bid_depth) / total_depth
                if total_depth > 0
                else 0.0
            )
        else:
            mid = spread = microprice = top1 = top5 = top10 = 0.0
            bid_distance = ask_distance = depth_distance = 0.0
            bid_depth = ask_depth = 0.0

        price_change = 0.0 if self.previous_mid is None or not book_valid else mid - self.previous_mid
        if book_valid:
            self.previous_mid = mid
        trade_delta = self.buy_volume - self.sell_volume
        ticks_moved = max(abs(price_change) / self.tick_size, 1.0)
        bid_replenishment = self.replenished(self.bid_adds_by_price, self.bid_exec_by_price)
        ask_replenishment = self.replenished(self.ask_adds_by_price, self.ask_exec_by_price)
        replenishment_total = bid_replenishment + ask_replenishment
        replenishment_score = (
            (bid_replenishment - ask_replenishment) / replenishment_total
            if replenishment_total > 0
            else 0.0
        )
        values: dict[str, float | int | bool] = {
            "midprice": finite(mid),
            "spread": finite(spread),
            "microprice": finite(microprice),
            "top1_imbalance": finite(top1),
            "top5_imbalance": finite(top5),
            "top10_imbalance": finite(top10),
            "bid_add_volume": self.bid_add,
            "bid_cancel_volume": self.bid_cancel,
            "ask_add_volume": self.ask_add,
            "ask_cancel_volume": self.ask_cancel,
            "aggressive_buy_volume": self.buy_volume,
            "aggressive_sell_volume": self.sell_volume,
            "trade_delta": trade_delta,
            "price_change": finite(price_change),
            "delta_per_tick": finite(trade_delta / ticks_moved),
            "executed_at_bid": self.executed_at_bid,
            "executed_at_ask": self.executed_at_ask,
            "bid_replenishment": bid_replenishment,
            "ask_replenishment": ask_replenishment,
            "replenishment_score": replenishment_score,
            "bid_depth_distance": finite(bid_distance),
            "ask_depth_distance": finite(ask_distance),
            "depth_weighted_distance": finite(depth_distance),
            "bid_standing_depth": finite(bid_depth),
            "ask_standing_depth": finite(ask_depth),
            "trade_count": self.trade_count,
            "depth_event_count": self.depth_event_count,
            "book_valid": book_valid,
        }
        return FeatureRow(self.bucket * 1_000_000_000, values)

    def finish(self) -> FeatureRow | None:
        return self.emit()

    def flush_completed(self, current_second: int) -> FeatureRow | None:
        """Emit an elapsed bucket without creating artificial empty seconds."""
        if self.bucket is None or self.bucket >= current_second:
            return None
        row = self.emit()
        self.bucket = None
        self.reset_bucket()
        return row


def iso_nanoseconds(value: str) -> int:
    """An ISO day or instant as epoch nanoseconds, for a range bound."""
    text = value.removesuffix("Z")
    if len(text) == 10:
        text += "T00:00:00"
    return timestamp_ns(text)


def table_exists(table: str) -> bool:
    return bool(pw.table_files(table))


def require_wal_drained(table: str) -> None:
    """Only that the table exists.

    THE WAL PREFLIGHT IS GONE BECAUSE THE WAL IS. This used to refuse to build
    while `wal_pending_row_count` was above zero, because an ILP flush confirmed
    only that QuestDB had accepted a transaction -- not that the table writer
    had applied it -- and an 8.6-million-row build once looked complete right
    before a restart revealed most partitions had never become durable. A
    Parquet shard is written to a temporary file and renamed into place, so it
    is either wholly visible or wholly absent and there is nothing to wait for.
    """
    if not table_exists(table):
        raise RuntimeError(f"missing required raw table {table}")


def verify_day_result(table: str, symbol: str, result: DayResult) -> None:
    """Require one completed worker result to be visible in its day."""
    start = iso_nanoseconds(result.day.isoformat())
    end = iso_nanoseconds((result.day + timedelta(days=1)).isoformat())
    _, rows = pw.read_columns(table, ["source"], start, end)
    stamps = [row[0] for row in rows if row[1] == result.source]
    actual = len(stamps)
    if actual != result.rows_written:
        raise RuntimeError(
            f"durability check failed for {result.source} {result.day}: "
            f"worker wrote {result.rows_written:,} rows but the store exposes "
            f"{actual:,}"
        )
    if actual:
        raw_max = ""
        for kind in ("ticks", "depth"):
            coverage = pw.day_max_nanoseconds(
                f"{result.source}_{symbol}_{kind}", start, end
            )
            candidate = coverage.get(result.day.isoformat())
            if candidate is not None:
                raw_max = max(raw_max, iso_second(candidate))
        feature_max = iso_second(max(stamps))
        if raw_max != feature_max:
            raise RuntimeError(
                f"coverage check failed for {result.source} {result.day}: "
                f"feature max {feature_max!r} != raw max {raw_max!r}"
            )


def iso_second(moment_ns: int) -> str:
    """`YYYY-MM-DDTHH:MM:SS` -- the 19-character form the checks compare."""
    return datetime.fromtimestamp(
        moment_ns // 1_000_000_000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S")


def count_target_range(table: str, source: str, start: str, end: str) -> int:
    if not table_exists(table):
        return 0
    _, rows = pw.read_columns(
        table, ["source"], iso_nanoseconds(start), iso_nanoseconds(end)
    )
    return sum(1 for row in rows if row[1] == source)


def ensure_empty_target_range(
    table: str, source: str, start: str, end: str
) -> None:
    count = count_target_range(table, source, start, end)
    if count:
        raise RuntimeError(
            f"{table} already contains {count:,} rows in the requested range; "
            f"source={source!r} was not changed. Choose another date range or "
            f"'<symbol>_l2_features_1s' table."
        )


def daily_source_counts(
    table: str, source: str, start: str, end: str
) -> dict[date, int]:
    if not table_exists(table):
        return {}
    _, rows = pw.read_columns(
        table, ["source"], iso_nanoseconds(start), iso_nanoseconds(end)
    )
    counts: dict[date, int] = {}
    for stamp, row_source in rows:
        if row_source != source:
            continue
        day = datetime.fromtimestamp(
            stamp // 1_000_000_000, tz=timezone.utc
        ).date()
        counts[day] = counts.get(day, 0) + 1
    return counts


def replace_existing_range(
    table: str, sources: list[str], start: str, end: str
) -> int:
    """Delete the source/date range that is about to be rebuilt. Returns rows dropped.

    THIS IS THE WHOLE OF WHAT USED TO BE A TABLE SWAP. QuestDB could not delete
    rows, so replacing one source's days meant: create a `<table>_rebuild`
    staging table, replay into it, `INSERT INTO staging SELECT * FROM table
    WHERE <everything being preserved>`, poll until the row count matched, then
    a three-way rename through `<table>_previous` with a rollback path if any
    step failed. Every one of those steps existed to work around the missing
    delete.

    The store deletes rows. Everything outside the range is never touched, so
    there is nothing to copy, nothing to swap, and no recovery table to leave
    behind after an interrupted run.
    """
    return pw.delete_range(
        table,
        iso_nanoseconds(start),
        iso_nanoseconds(end),
        column="source",
        values=sources,
    )


def day_max_seconds(table: str, start: str, end: str,
                    source: str | None = None) -> dict[str, str]:
    """Map YYYY-MM-DD -> the day's max timestamp truncated to the second."""
    lower, upper = iso_nanoseconds(start), iso_nanoseconds(end)
    if source is None:
        return {
            day: iso_second(moment)
            for day, moment in pw.day_max_nanoseconds(table, lower, upper).items()
        }
    if not table_exists(table):
        return {}
    _, rows = pw.read_columns(table, ["source"], lower, upper)
    maxima: dict[str, int] = {}
    for stamp, row_source in rows:
        if row_source != source:
            continue
        day = iso_second(stamp)[:10]
        if maxima.get(day, -1) < stamp:
            maxima[day] = stamp
    return {day: iso_second(moment) for day, moment in maxima.items()}


def raw_day_max_seconds(
    tick_table: str, depth_table: str, start: str, end: str
) -> dict[str, str]:
    """Raw day coverage, read from row-group statistics rather than by scanning.

    `partition_day_max_seconds` used to read this out of `table_partitions()`
    for the same reason: the depth table holds billions of events and scanning
    it to find each day's last second is not affordable. `day_max_nanoseconds`
    keeps that property -- a row group whose footer minimum and maximum fall on
    one day already answers for that day, so only the groups that straddle
    midnight are decoded.
    """
    coverage = day_max_seconds(tick_table, start, end)
    for day, timestamp in day_max_seconds(depth_table, start, end).items():
        coverage[day] = max(coverage.get(day, ""), timestamp)
    return coverage


def classify_built_days(
    table: str, source: str, tick_table: str, start: str, end: str
) -> tuple[set[str], set[str]]:
    """Split already-written days into (complete, partial).

    A day is complete when its last emitted feature second reaches the raw
    data's last second for that day. This is robust to holiday early-closes
    (the raw data simply ends earlier) and flags days truncated by a crash.
    """
    feat = day_max_seconds(table, start, end, source=source)
    raw = day_max_seconds(tick_table, start, end)
    complete: set[str] = set()
    partial: set[str] = set()
    for day, feature_second in feat.items():
        raw_second = raw.get(day)
        if raw_second and feature_second >= raw_second:
            complete.add(day)
        else:
            partial.add(day)
    return complete, partial


def verify_source_coverage(
    table: str,
    source: str,
    tick_table: str,
    depth_table: str,
    start: str,
    end: str,
) -> None:
    """Require every raw source day to reach the same final second."""
    feature = day_max_seconds(table, start, end, source=source)
    raw = raw_day_max_seconds(tick_table, depth_table, start, end)
    missing = sorted(set(raw) - set(feature))
    extra = sorted(set(feature) - set(raw))
    mismatched = sorted(
        day for day in set(raw) & set(feature) if raw[day] != feature[day]
    )
    if missing or extra or mismatched:
        detail = []
        if missing:
            detail.append(f"missing={missing[:8]}")
        if extra:
            detail.append(f"extra={extra[:8]}")
        if mismatched:
            detail.append(f"truncated={mismatched[:8]}")
        raise RuntimeError(
            f"full coverage check failed for {source}: " + ", ".join(detail)
        )


def drop_day(table: str, day: str, sources: list[str] | None = None) -> None:
    """Delete one built day so it can be replayed.

    `ALTER TABLE ... DROP PARTITION` took the whole calendar day for EVERY
    source, because a QuestDB partition holds them all -- which is why the
    caller then had to clear the completion markers for every other source on
    that day and replay them too. The store deletes by source as well, so
    passing `sources` drops only what is being rebuilt.
    """
    start = iso_nanoseconds(day)
    end = start + 86_400 * 1_000_000_000
    pw.delete_range(
        table, start, end,
        column=None if sources is None else "source",
        values=sources,
    )


def create_target_table(table: str) -> None:
    """Nothing to create; validate the name and move on.

    The `CREATE TABLE IF NOT EXISTS ... PARTITION BY DAY WAL` this replaces
    existed to avoid ILP creating the table implicitly with the wrong schema.
    A shard takes its schema from the rows written to it, so the only part
    still worth doing is refusing a target that is not a derived feature table.
    """
    validate_target_table(table)


@contextmanager
def table_rows(
    table: str, columns: list[str], start: str, end: str
) -> Iterator[Iterator[dict[str, object]]]:
    """Rows of `table` over `[start, end)`, oldest first, as column dicts.

    Shaped like the `csv.DictReader` this replaces so `depth_events` and
    `trade_events` are untouched -- but the values are native Python rather
    than text, which is why those two now coerce defensively.

    A context manager for the same reason it was one before: the caller merges
    two of these with `heapq.merge` inside an `ExitStack`, and nothing about
    that changes.
    """
    _, rows = pw.read_columns(
        table, columns, iso_nanoseconds(start), iso_nanoseconds(end)
    )
    names = ["timestamp"] + [name for name in columns if name != "timestamp"]

    def stream() -> Iterator[dict[str, object]]:
        for row in rows:
            yield dict(zip(names, row))

    yield stream()


def depth_events(rows: Iterator[dict[str, object]]) -> Iterator[Event]:
    pending: list[Event] = []
    pending_timestamp: int | None = None
    for row in rows:
        event = Event(
            timestamp_ns=timestamp_ns(row["timestamp"]),
            priority=0,
            sequence=int(row["sequence"] or 0),
            kind="D",
            side=row["side"],
            price=float(row["price"]),
            size=float(row["size"]),
            stream_id=row.get("stream_id") or "",
        )
        if pending_timestamp is not None and event.timestamp_ns != pending_timestamp:
            yield from sorted(pending)
            pending.clear()
        pending_timestamp = event.timestamp_ns
        pending.append(event)
    yield from sorted(pending)


def trade_events(rows: Iterator[dict[str, object]]) -> Iterator[Event]:
    pending: list[Event] = []
    pending_timestamp: int | None = None
    for row in rows:
        event = Event(
            timestamp_ns=timestamp_ns(row["timestamp"]),
            priority=1,
            sequence=int(row["trade_sequence"] or 0),
            kind="T",
            side=row["side"],
            price=float(row["price"]),
            size=float(row["size"]),
            best_bid=float(row["best_bid"] or 0),
            best_ask=float(row["best_ask"] or 0),
            stream_id=row.get("stream_id") or "",
        )
        if pending_timestamp is not None and event.timestamp_ns != pending_timestamp:
            yield from sorted(pending)
            pending.clear()
        pending_timestamp = event.timestamp_ns
        pending.append(event)
    yield from sorted(pending)


def seed_book(
    depth_table: str,
    start: str,
    accumulator: FeatureAccumulator,
    tick_size: float,
    seed_ticks: int,
    lookback_days: int = 5,
) -> int:
    # Bound both seed scans to a small window. The opening BBO lives within the
    # day itself, and the resting book only needs the most recent prior session;
    # without these bounds each read covers `start` to the far end of the table
    # (hundreds of millions of rows).
    start_day = date.fromisoformat(start[:10])
    day_end = (start_day + timedelta(days=1)).isoformat()
    lookback = (start_day - timedelta(days=lookback_days)).isoformat()

    # `SELECT first(best_bid),first(best_ask) ... WHERE best_bid > 0 AND best_ask > 0`
    tick_table = f"{depth_table.removesuffix('_depth')}_ticks"
    _, tick_rows = pw.read_columns(
        tick_table,
        ["best_bid", "best_ask"],
        iso_nanoseconds(start),
        iso_nanoseconds(day_end),
    )
    opening = next(
        (
            (row[1], row[2])
            for row in tick_rows
            if row[1] is not None and row[2] is not None and row[1] > 0 and row[2] > 0
        ),
        None,
    )
    if opening is None:
        return 0
    best_bid, best_ask = float(opening[0]), float(opening[1])
    accumulator.previous_mid = (best_bid + best_ask) / 2.0
    lower = best_bid - seed_ticks * tick_size
    upper = best_ask + seed_ticks * tick_size

    # `SELECT side,price,last(size) size ... GROUP BY side,price`. Rows arrive
    # in timestamp order, so the last write per (side, price) is `last(size)`.
    _, depth_rows = pw.read_columns(
        depth_table,
        ["side", "price", "size"],
        iso_nanoseconds(lookback),
        iso_nanoseconds(start),
    )
    resting: dict[tuple[str, float], float] = {}
    for _, side, price, size in depth_rows:
        if price is None or size is None:
            continue
        price = float(price)
        if lower <= price <= upper:
            resting[(side, price)] = float(size)

    count = 0
    for (side, price), size in resting.items():
        valid = (side == "BID" and price < best_ask) or (side == "ASK" and price > best_bid)
        if valid and size > 0:
            accumulator.seed(side, price, size)
            count += 1
    return count


def write_feature(sender: Sender, table: str, source: str, symbol: str, row: FeatureRow) -> None:
    sender.row(
        table,
        symbols={"source": source, "symbol": symbol},
        columns=row.values,
        at=TimestampNanos(row.timestamp_ns),
    )


def build_bookmap_day(job: DayJob) -> DayResult:
    """Replay one independently seeded Bookmap-compatible source day."""
    day_start = job.day.isoformat()
    day_end = (job.day + timedelta(days=1)).isoformat()
    accumulator = FeatureAccumulator(job.tick_size)
    seeded = seed_book(
        job.depth_table,
        day_start,
        accumulator,
        job.tick_size,
        job.seed_ticks,
    )
    rows_written = 0
    with ExitStack() as stack:
        depth = stack.enter_context(table_rows(
            job.depth_table,
            ["side", "price", "size", "sequence", "stream_id"],
            day_start,
            day_end,
        ))
        ticks = stack.enter_context(table_rows(
            job.tick_table,
            ["side", "price", "size", "trade_sequence", "best_bid", "best_ask",
             "stream_id"],
            day_start,
            day_end,
        ))
        sender = stack.enter_context(Sender())
        for event in heapq.merge(depth_events(depth), trade_events(ticks)):
            for feature in accumulator.on_event(event):
                write_feature(sender, job.target_table, job.source, job.symbol, feature)
                rows_written += 1
                if rows_written % job.flush_rows == 0:
                    sender.flush()
        final = accumulator.finish()
        if final is not None:
            write_feature(sender, job.target_table, job.source, job.symbol, final)
            rows_written += 1
        sender.flush()
    return DayResult(job.day, job.source, rows_written, seeded)


def build_day(job: DayJob) -> DayResult:
    try:
        if job.source in {"bm", "dbento"}:
            return build_bookmap_day(job)
        raise ValueError(f"unsupported source: {job.source}")
    except Exception as exc:
        # A worker's exception has to survive pickling back across the
        # ProcessPool boundary, and an Arrow error carries state that does not,
        # so it is reduced to its text -- the same reason the QuestDB version
        # did this for urllib's HTTPError.
        raise RuntimeError(
            f"{job.source} {job.day} failed: {type(exc).__name__}: {exc}"
        ) from None


def build(args: argparse.Namespace) -> int:
    symbol = safe_identifier(args.symbol, "symbol")
    requested_source = safe_identifier(args.source, "source")
    if requested_source not in {"bm", "dbento", "both"}:
        raise ValueError("--source must be bm, dbento, or both")
    sources = ["dbento", "bm"] if requested_source == "both" else [requested_source]
    if "dbento" in sources and symbol != "nq":
        raise ValueError("the Databento event import currently supports only NQ")
    table = validate_target_table(args.table or f"{symbol}_l2_features_1s")
    start_day, end_day = parse_day(args.from_date), parse_day(args.to_date)
    if end_day < start_day:
        raise ValueError("--to must be on or after --from")
    start = start_day.isoformat()
    end = (end_day + timedelta(days=1)).isoformat()
    source_days: dict[str, set[date]] = {}
    for source in sources:
        depth_table = f"{source}_{symbol}_depth"
        tick_table = f"{source}_{symbol}_ticks"
        if not table_exists(depth_table) or not table_exists(tick_table):
            raise RuntimeError(
                f"missing required raw tables {depth_table} and/or {tick_table}"
            )
        require_wal_drained(tick_table)
        require_wal_drained(depth_table)
        source_days[source] = {
            parse_day(day)
            for day in raw_day_max_seconds(tick_table, depth_table, start, end)
        }
        if source == "dbento":
            _, schema_rows = pw.read_columns(tick_table, ["schema_version"])
            seen = {row[1] for row in schema_rows}
            if seen != {DATABENTO_SCHEMA_VERSION}:
                raise RuntimeError(
                    "Databento raw events are not schema version 6 New York "
                    "wall-clock data; rerun databento_import.py before building"
                )
    create_target_table(table)
    write_table = table
    completed_days: set[tuple[str, date]] = set()
    if args.replace_existing:
        # DELETED UP FRONT, IN PLACE, RATHER THAN REBUILT INTO A STAGING TABLE.
        # QuestDB could not delete rows, so this used to replay into
        # `<table>_rebuild`, copy every preserved row across, poll for the
        # count, and swap three tables with a rollback path. The store deletes
        # by source and range, so the rows outside the range are simply never
        # touched and there is no staging table to resume from or clean up.
        for source in sources:
            replaced = count_target_range(table, source, start, end)
            print(
                f"Replacing {replaced:,} existing {source} feature rows in "
                "the requested range.",
                flush=True,
            )
        dropped = replace_existing_range(table, sources, start, end)
        print(f"Dropped {dropped:,} row(s); rebuilding.", flush=True)
    elif args.skip_existing:
        for source in sources:
            complete, partial = classify_built_days(
                table, source, f"{source}_{symbol}_ticks", start, end
            )
            for day_str in complete:
                completed_days.add((source, parse_day(day_str)))
            for day_str in sorted(partial):
                # Only this source's rows for the day, so a partial `bm` day no
                # longer forces `dbento` to be replayed with it.
                drop_day(table, day_str, [source])
            print(
                f"skip-existing[{source}]: {len(complete)} complete day(s) skipped, "
                f"{len(partial)} partial day(s) dropped for rebuild.",
                flush=True,
            )
    else:
        for source in sources:
            ensure_empty_target_range(table, source, start, end)

    jobs = []
    for source in sources:
        jobs.extend(
            DayJob(
                day=day,
                symbol=symbol,
                source=source,
                target_table=write_table,
                depth_table=f"{source}_{symbol}_depth",
                tick_table=f"{source}_{symbol}_ticks",
                tick_size=args.tick_size,
                seed_ticks=args.seed_ticks,
                flush_rows=args.flush_rows,
            )
            for day in sorted(source_days[source])
            if (source, day) not in completed_days
        )
    if not jobs:
        print("Every requested source/day partition is already staged.", flush=True)
    workers = min(args.workers, MAX_PARALLEL_REPLAY_WORKERS, len(jobs)) if jobs else 0
    if jobs and workers < args.workers:
        print(
            f"Using {workers} replay workers; each holds two whole source days "
            "in memory, which is what the cap is now protecting.",
            flush=True,
        )
    rows_written = seeded = 0
    if workers == 0:
        pass
    elif workers == 1:
        results = map(build_day, jobs)
        for result in results:
            rows_written += result.rows_written
            seeded += result.seeded_levels
            verify_day_result(write_table, symbol, result)
            print(
                f"Completed {result.source} {result.day}: {result.rows_written:,} rows "
                f"({result.seeded_levels:,} seeded levels; verified)",
                flush=True,
            )
    else:
        print(
            f"Building {len(jobs)} {symbol.upper()} source/day partitions "
            f"with {workers} workers in batches of "
            f"{args.wal_batch_jobs} jobs...",
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for offset in range(0, len(jobs), args.wal_batch_jobs):
                batch = jobs[offset : offset + args.wal_batch_jobs]
                futures = [executor.submit(build_day, job) for job in batch]
                completed = [future.result() for future in as_completed(futures)]
                # Batching still bounds how many raw days are open at once,
                # which is a memory limit rather than the WAL limit it was.
                for result in completed:
                    verify_day_result(write_table, symbol, result)
                    rows_written += result.rows_written
                    seeded += result.seeded_levels
                    print(
                        f"Completed {result.source} {result.day}: "
                        f"{result.rows_written:,} rows "
                        f"({result.seeded_levels:,} seeded levels; verified)",
                        flush=True,
                    )

    for source in sources:
        verify_source_coverage(
            write_table,
            source,
            f"{source}_{symbol}_ticks",
            f"{source}_{symbol}_depth",
            start,
            end,
        )

    print(
        f"Built {rows_written:,} rows in {table} from read-only "
        f"{', '.join(sources)} raw tables; "
        f"seeded {seeded:,} total day-opening book levels with {workers} worker(s)."
    )
    return rows_written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build safe one-second Bookmap and/or Databento L2 features."
    )
    parser.add_argument("--symbol", required=True, help="nq or es")
    parser.add_argument(
        "--source",
        choices=("bm", "dbento", "both"),
        default="both",
        help="raw source to normalize (default: both)",
    )
    parser.add_argument("--from", dest="from_date", required=True, help="first YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="last YYYY-MM-DD")
    parser.add_argument(
        "--table",
        help="target table; defaults to <symbol>_l2_features_1s",
    )
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--seed-ticks", type=int, default=200)
    parser.add_argument("--flush-rows", type=int, default=10_000)
    parser.add_argument(
        "--wal-batch-jobs",
        type=int,
        default=5,
        help=(
            "maximum source/day jobs admitted before verifying the batch "
            "(default: 5)"
        ),
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="delete and rebuild only the selected derived source/date range",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="resume-safe: skip days already fully built, drop+rebuild partial days, build the rest",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(2, os.cpu_count() or 1),
        help="parallel day processes (default: 2; use 1 for sequential replay)",
    )
    parser.add_argument(
        "--questdb-url",
        default="http://127.0.0.1:9000",
        help="QuestDB HTTP origin",
    )
    parser.add_argument("--ilp-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.workers < 1:
            raise ValueError("--workers must be at least 1")
        if args.wal_batch_jobs < 1:
            raise ValueError("--wal-batch-jobs must be at least 1")
        build(args)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
