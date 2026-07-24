"""Build normalized one-second L2 features from Bookmap and Databento.

The ``bm_*`` and ``dbento_*`` tables are immutable, Bookmap-compatible
event-level inputs replayed into seconds. Both write the same
``<symbol>_l2_features_1s`` schema and retain a source tag. Separate days run
in parallel worker processes.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import io
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, TextIO
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import urlopen

import requests
from questdb.ingress import Sender, TimestampNanos


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
    questdb_url: str
    ilp_host: str
    ilp_port: int
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
        self.trade_count += 1

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


def query_json(base_url: str, sql: str) -> dict:
    response = requests.get(
        f"{base_url.rstrip('/')}/exec",
        params={"query": sql},
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"QuestDB error: {result['error']}\nSQL: {sql}")
    return result


def table_exists(base_url: str, table: str) -> bool:
    sql = f"SELECT table_name FROM tables() WHERE table_name={sql_literal(table)}"
    return bool(query_json(base_url, sql).get("dataset"))


def require_wal_drained(base_url: str, table: str) -> None:
    rows = query_json(
        base_url,
        f"SELECT wal_pending_row_count,table_suspended FROM tables() "
        f"WHERE table_name={sql_literal(table)}",
    ).get("dataset", [])
    if not rows:
        raise RuntimeError(f"missing required raw table {table}")
    pending, suspended = int(rows[0][0] or 0), bool(rows[0][1])
    if suspended:
        raise RuntimeError(f"{table} WAL is suspended; repair it before building")
    if pending:
        raise RuntimeError(
            f"{table} still has {pending:,} QuestDB WAL rows pending; wait for "
            "wal_pending_row_count to reach zero before building features"
        )


def ensure_empty_target_range(
    base_url: str, table: str, source: str, start: str, end: str
) -> None:
    if not table_exists(base_url, table):
        return
    sql = (
        f"SELECT count() FROM {table} WHERE source={sql_literal(source)} "
        f"AND timestamp >= {sql_literal(start)} "
        f"AND timestamp < {sql_literal(end)}"
    )
    rows = query_json(base_url, sql).get("dataset", [])
    count = int(rows[0][0]) if rows else 0
    if count:
        raise RuntimeError(
            f"{table} already contains {count:,} rows in the requested range; "
            f"source={source!r} was not changed. Choose another date range or "
            f"'<symbol>_l2_features_1s' table."
        )


def count_target_range(
    base_url: str, table: str, source: str, start: str, end: str
) -> int:
    rows = query_json(
        base_url,
        f"SELECT count() FROM {table} WHERE source={sql_literal(source)} "
        f"AND timestamp >= {sql_literal(start)} "
        f"AND timestamp < {sql_literal(end)}",
    ).get("dataset", [])
    return int(rows[0][0]) if rows else 0


def prepare_rebuild_table(base_url: str, table: str) -> str:
    """Create an empty derived staging table without touching the live table."""
    staging = validate_target_table(f"{table}_rebuild")
    backup = safe_identifier(f"{table}_previous", "backup table")
    if table_exists(base_url, backup):
        raise RuntimeError(
            f"{backup} exists from an interrupted replacement; preserve it and "
            "resolve that recovery table before retrying"
        )
    if table_exists(base_url, staging):
        print(
            f"Resuming the existing staging table {staging}; completed days "
            "will not be replayed again.",
            flush=True,
        )
    else:
        create_target_table(base_url, staging)
    return staging


def daily_source_counts(
    base_url: str,
    table: str,
    source: str,
    start: str,
    end: str,
) -> dict[date, int]:
    rows = query_json(
        base_url,
        f"SELECT timestamp,count() FROM {table} "
        f"WHERE source={sql_literal(source)} "
        f"AND timestamp >= {sql_literal(start)} "
        f"AND timestamp < {sql_literal(end)} "
        "SAMPLE BY 1d ALIGN TO CALENDAR",
    ).get("dataset", [])
    return {date.fromisoformat(str(timestamp)[:10]): int(count) for timestamp, count in rows}


def finish_replacement(
    base_url: str,
    table: str,
    staging: str,
    sources: list[str],
    start: str,
    end: str,
    rebuilt_rows: int,
) -> None:
    """Preserve non-replaced rows and swap derived tables with rollback."""
    source_list = ",".join(sql_literal(source) for source in sources)
    preserve_where = (
        f"source NOT IN ({source_list}) OR timestamp < {sql_literal(start)} "
        f"OR timestamp >= {sql_literal(end)}"
    )
    preserved_rows = query_json(
        base_url,
        f"SELECT count() FROM {table} WHERE {preserve_where}",
    ).get("dataset", [])
    preserved = int(preserved_rows[0][0]) if preserved_rows else 0
    query_json(
        base_url,
        f"INSERT INTO {staging} SELECT * FROM {table} WHERE {preserve_where}",
    )
    expected = rebuilt_rows + preserved
    deadline = time.monotonic() + 300
    actual = -1
    while time.monotonic() < deadline:
        visible_rows = query_json(
            base_url, f"SELECT count() FROM {staging}"
        ).get("dataset", [])
        actual = int(visible_rows[0][0]) if visible_rows else 0
        if actual == expected:
            break
        if actual > expected:
            raise RuntimeError(
                f"replacement staging table has {actual:,} rows, exceeding "
                f"the expected {expected:,}"
            )
        time.sleep(0.5)
    if actual != expected:
        raise RuntimeError(
            f"timed out waiting for {expected:,} staged rows; {actual:,} are visible"
        )
    backup = safe_identifier(f"{table}_previous", "backup table")
    query_json(base_url, f"RENAME TABLE {table} TO {backup}")
    try:
        query_json(base_url, f"RENAME TABLE {staging} TO {table}")
        actual_rows = query_json(
            base_url, f"SELECT count() FROM {table}"
        ).get("dataset", [])
        actual = int(actual_rows[0][0]) if actual_rows else 0
        if actual != expected:
            raise RuntimeError(
                f"replacement verification failed: expected {expected:,} rows, "
                f"found {actual:,}"
            )
    except Exception:
        if table_exists(base_url, table):
            failed = safe_identifier(f"{staging}_failed", "failed table")
            if table_exists(base_url, failed):
                query_json(base_url, f"DROP TABLE {failed}")
            query_json(base_url, f"RENAME TABLE {table} TO {failed}")
        query_json(base_url, f"RENAME TABLE {backup} TO {table}")
        raise
    query_json(base_url, f"DROP TABLE {backup}")
    print(
        f"Replaced {table} through a verified table swap; "
        f"the new table contains {expected:,} rows.",
        flush=True,
    )


def day_max_seconds(base_url: str, table: str, where: str) -> dict[str, str]:
    """Map YYYY-MM-DD -> the day's max timestamp truncated to the second."""
    rows = query_json(
        base_url,
        f"SELECT timestamp_floor('d',timestamp) d, max(timestamp) m FROM {table} "
        f"WHERE {where} GROUP BY timestamp_floor('d',timestamp)",
    ).get("dataset", [])
    return {str(d)[:10]: str(m)[:19] for d, m in rows if m}


def classify_built_days(
    base_url: str, table: str, source: str, tick_table: str, start: str, end: str
) -> tuple[set[str], set[str]]:
    """Split already-written days into (complete, partial).

    A day is complete when its last emitted feature second reaches the raw
    data's last second for that day. This is robust to holiday early-closes
    (the raw data simply ends earlier) and flags days truncated by a crash.
    """
    rng = f"timestamp >= {sql_literal(start)} AND timestamp < {sql_literal(end)}"
    feat = day_max_seconds(base_url, table, f"source={sql_literal(source)} AND {rng}")
    raw = day_max_seconds(base_url, tick_table, rng)
    complete: set[str] = set()
    partial: set[str] = set()
    for day, feature_second in feat.items():
        raw_second = raw.get(day)
        if raw_second and feature_second >= raw_second:
            complete.add(day)
        else:
            partial.add(day)
    return complete, partial


def drop_day_partition(base_url: str, table: str, day: str) -> None:
    query_json(base_url, f"ALTER TABLE {table} DROP PARTITION LIST {sql_literal(day)}")


def create_target_table(base_url: str, table: str) -> None:
    """Create only the validated derived table, avoiding ILP creation races."""
    table = validate_target_table(table)
    sql = f"""
CREATE TABLE IF NOT EXISTS {table} (
    timestamp TIMESTAMP,
    source SYMBOL,
    symbol SYMBOL,
    midprice DOUBLE,
    spread DOUBLE,
    microprice DOUBLE,
    top1_imbalance DOUBLE,
    top5_imbalance DOUBLE,
    top10_imbalance DOUBLE,
    bid_add_volume DOUBLE,
    bid_cancel_volume DOUBLE,
    ask_add_volume DOUBLE,
    ask_cancel_volume DOUBLE,
    aggressive_buy_volume DOUBLE,
    aggressive_sell_volume DOUBLE,
    trade_delta DOUBLE,
    price_change DOUBLE,
    delta_per_tick DOUBLE,
    executed_at_bid DOUBLE,
    executed_at_ask DOUBLE,
    bid_replenishment DOUBLE,
    ask_replenishment DOUBLE,
    replenishment_score DOUBLE,
    bid_depth_distance DOUBLE,
    ask_depth_distance DOUBLE,
    depth_weighted_distance DOUBLE,
    trade_count LONG,
    depth_event_count LONG,
    book_valid BOOLEAN
) TIMESTAMP(timestamp) PARTITION BY DAY WAL
""".strip()
    query_json(base_url, sql)


@contextmanager
def csv_rows(base_url: str, sql: str) -> Iterator[Iterator[dict[str, str]]]:
    url = f"{base_url.rstrip('/')}/exp?{urlencode({'query': sql})}"
    response = None
    for attempt in range(6):
        try:
            response = urlopen(url, timeout=180)
            break
        except HTTPError as exc:
            status = exc.code
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            finally:
                exc.close()
            if status != 429 or attempt == 5:
                raise RuntimeError(
                    f"QuestDB export failed with HTTP {status}: {detail}"
                ) from None
            time.sleep(0.5 * (attempt + 1))
    if response is None:
        raise RuntimeError("QuestDB export did not open")
    text: TextIO = io.TextIOWrapper(response, encoding="utf-8", newline="")
    try:
        yield csv.DictReader(text)
    finally:
        text.close()


def depth_events(rows: Iterator[dict[str, str]]) -> Iterator[Event]:
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


def trade_events(rows: Iterator[dict[str, str]]) -> Iterator[Event]:
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
    base_url: str,
    depth_table: str,
    start: str,
    accumulator: FeatureAccumulator,
    tick_size: float,
    seed_ticks: int,
    lookback_days: int = 5,
) -> int:
    # Bound both seed scans to a small window. The opening BBO lives within the
    # day itself, and the resting book only needs the most recent prior session;
    # without these bounds each query scans from `start` to the far end of the
    # table (hundreds of millions of rows), which exhausts QuestDB's memory.
    start_day = date.fromisoformat(start[:10])
    day_end = (start_day + timedelta(days=1)).isoformat()
    lookback = (start_day - timedelta(days=lookback_days)).isoformat()
    bbo_sql = (
        f"SELECT first(best_bid),first(best_ask) FROM {depth_table.removesuffix('_depth')}_ticks "
        f"WHERE timestamp >= {sql_literal(start)} AND timestamp < {sql_literal(day_end)} "
        f"AND best_bid > 0 AND best_ask > 0"
    )
    bbo_rows = query_json(base_url, bbo_sql).get("dataset", [])
    if not bbo_rows or bbo_rows[0][0] is None or bbo_rows[0][1] is None:
        return 0
    best_bid, best_ask = map(float, bbo_rows[0])
    accumulator.previous_mid = (best_bid + best_ask) / 2.0
    lower = best_bid - seed_ticks * tick_size
    upper = best_ask + seed_ticks * tick_size
    seed_sql = (
        f"SELECT side,price,last(size) size FROM {depth_table} "
        f"WHERE timestamp < {sql_literal(start)} AND timestamp >= {sql_literal(lookback)} "
        f"AND price >= {lower} AND price <= {upper} "
        "GROUP BY side,price"
    )
    rows = query_json(base_url, seed_sql).get("dataset", [])
    count = 0
    for side, raw_price, raw_size in rows:
        price, size = float(raw_price), float(raw_size)
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
        job.questdb_url,
        job.depth_table,
        day_start,
        accumulator,
        job.tick_size,
        job.seed_ticks,
    )
    depth_sql = (
        "SELECT timestamp,side,price,size,sequence,stream_id "
        f"FROM {job.depth_table} WHERE timestamp >= {sql_literal(day_start)} "
        f"AND timestamp < {sql_literal(day_end)} ORDER BY timestamp"
    )
    tick_sql = (
        "SELECT timestamp,side,price,size,trade_sequence,best_bid,best_ask,stream_id "
        f"FROM {job.tick_table} WHERE timestamp >= {sql_literal(day_start)} "
        f"AND timestamp < {sql_literal(day_end)} ORDER BY timestamp"
    )
    conf = f"tcp::addr={job.ilp_host}:{job.ilp_port};"
    rows_written = 0
    with ExitStack() as stack:
        depth = stack.enter_context(csv_rows(job.questdb_url, depth_sql))
        ticks = stack.enter_context(csv_rows(job.questdb_url, tick_sql))
        sender = stack.enter_context(Sender.from_conf(conf))
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
        # urllib HTTPError objects retain an unpicklable response stream.
        # Workers must return a plain exception across ProcessPool boundaries.
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
    for source in sources:
        depth_table = f"{source}_{symbol}_depth"
        tick_table = f"{source}_{symbol}_ticks"
        if not table_exists(args.questdb_url, depth_table) or not table_exists(
            args.questdb_url, tick_table
        ):
            raise RuntimeError(
                f"missing required raw tables {depth_table} and/or {tick_table}"
            )
        require_wal_drained(args.questdb_url, tick_table)
        require_wal_drained(args.questdb_url, depth_table)
        if source == "dbento":
            rows = query_json(
                args.questdb_url,
                f"SELECT min(schema_version),max(schema_version) FROM {tick_table}",
            ).get("dataset", [])
            versions = tuple(rows[0]) if rows else ()
            if versions != (
                DATABENTO_SCHEMA_VERSION,
                DATABENTO_SCHEMA_VERSION,
            ):
                raise RuntimeError(
                    "Databento raw events are not schema version 6 New York "
                    "wall-clock data; rerun databento_import.py before building"
                )
    create_target_table(args.questdb_url, table)
    write_table = table
    resuming_rebuild = False
    resumed_rows = 0
    completed_days: set[tuple[str, date]] = set()
    if args.replace_existing:
        for source in sources:
            replaced = count_target_range(
                args.questdb_url, table, source, start, end
            )
            print(
                f"Will replace {replaced:,} existing {source} feature rows in "
                "the requested range after the rebuild succeeds.",
                flush=True,
            )
        staging_name = validate_target_table(f"{table}_rebuild")
        resuming_rebuild = table_exists(args.questdb_url, staging_name)
        write_table = prepare_rebuild_table(args.questdb_url, table)
        if resuming_rebuild:
            for source in sources:
                expected = daily_source_counts(
                    args.questdb_url, table, source, start, end
                )
                staged = daily_source_counts(
                    args.questdb_url, write_table, source, start, end
                )
                for day in (
                    start_day + timedelta(days=offset)
                    for offset in range((end_day - start_day).days + 1)
                ):
                    expected_count = expected.get(day, 0)
                    staged_count = staged.get(day, 0)
                    if staged_count == expected_count:
                        completed_days.add((source, day))
                    elif staged_count:
                        raise RuntimeError(
                            f"{write_table} contains a partial {source} day "
                            f"{day}: {staged_count:,} rows versus the expected "
                            f"{expected_count:,}; preserve the live table and "
                            "remove only the staging table before a full retry"
                        )
                resumed_rows += sum(staged.values())
            print(
                f"Resuming with {resumed_rows:,} staged rows; "
                f"{len(completed_days)} completed source/day partitions will "
                "be skipped.",
                flush=True,
            )
    elif args.skip_existing:
        for source in sources:
            complete, partial = classify_built_days(
                args.questdb_url, table, source, f"{source}_{symbol}_ticks", start, end
            )
            for day_str in complete:
                completed_days.add((source, parse_day(day_str)))
            for day_str in sorted(partial):
                drop_day_partition(args.questdb_url, table, day_str)
            print(
                f"skip-existing[{source}]: {len(complete)} complete day(s) skipped, "
                f"{len(partial)} partial day(s) dropped for rebuild.",
                flush=True,
            )
    else:
        for source in sources:
            ensure_empty_target_range(args.questdb_url, table, source, start, end)

    days = []
    current_day = start_day
    while current_day <= end_day:
        days.append(current_day)
        current_day += timedelta(days=1)
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
                questdb_url=args.questdb_url,
                ilp_host=args.ilp_host,
                ilp_port=args.ilp_port,
                tick_size=args.tick_size,
                seed_ticks=args.seed_ticks,
                flush_rows=args.flush_rows,
            )
            for day in days
            if (source, day) not in completed_days
        )
    if not jobs:
        print("Every requested source/day partition is already staged.", flush=True)
    workers = min(args.workers, MAX_PARALLEL_REPLAY_WORKERS, len(jobs)) if jobs else 0
    if jobs and workers < args.workers:
        print(
            f"Using {workers} replay workers because each worker holds two "
            "QuestDB export streams; this avoids HTTP 429 responses.",
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
            print(
                f"Completed {result.source} {result.day}: {result.rows_written:,} rows "
                f"({result.seeded_levels:,} seeded levels)",
                flush=True,
            )
    else:
        print(
            f"Building {len(jobs)} {symbol.upper()} source/day partitions "
            f"with {workers} workers...",
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(build_day, job) for job in jobs]
            for future in as_completed(futures):
                result = future.result()
                rows_written += result.rows_written
                seeded += result.seeded_levels
                print(
                    f"Completed {result.source} {result.day}: "
                    f"{result.rows_written:,} rows "
                    f"({result.seeded_levels:,} seeded levels)",
                    flush=True,
                )

    if args.replace_existing:
        finish_replacement(
            args.questdb_url,
            table,
            write_table,
            sources,
            start,
            end,
            resumed_rows + rows_written,
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
        build(args)
        return 0
    except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
