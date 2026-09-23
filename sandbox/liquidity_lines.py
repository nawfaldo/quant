"""Causal top-10 liquidity-line reconstruction and cache loading.

Raw depth stays immutable.  The companion builder streams one source/session
at a time and stores compact gzip caches under ``sandbox/.cache``.
"""
from __future__ import annotations

import bisect
import glob
import gzip
import json
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path

from tools.build_nq_l2_features_1s import FeatureAccumulator, FeatureRow
from sandbox import data

SCHEMA_VERSION = 1
TICK_SIZE = 0.25
NORM_SESSIONS = 20
TIME_BUCKET_MINUTES = 30


class LineAccumulator(FeatureAccumulator):
    """Feature replay enriched with exact largest-level state and flows."""

    def __init__(self, tick_size=TICK_SIZE, levels=10):
        self.birth: dict[tuple[str, float], int] = {}
        super().__init__(tick_size, levels)

    def reset_bucket(self):
        super().reset_bucket()
        self.bid_cancels_by_price: dict[float, float] = {}
        self.ask_cancels_by_price: dict[float, float] = {}
        self.trade_prices: list[float] = []

    def seed(self, side, price, size):
        super().seed(side, price, size)
        if size > 0:
            self.birth[(side, price)] = self.bucket or 0

    def apply_depth(self, event):
        book = self.bids if event.side == "BID" else self.asks
        previous = book.get(event.price, 0.0)
        current = max(event.size, 0.0)
        super().apply_depth(event)
        key = (event.side, event.price)
        if previous <= 0 < current:
            self.birth[key] = event.timestamp_ns // 1_000_000_000
        elif current <= 0:
            self.birth.pop(key, None)
        if current < previous:
            cancels = (self.bid_cancels_by_price if event.side == "BID"
                       else self.ask_cancels_by_price)
            cancels[event.price] = cancels.get(event.price, 0.0) + previous - current

    def apply_trade(self, event):
        super().apply_trade(event)
        if event.price > 0:
            self.trade_prices.append(event.price)

    def on_event(self, event):
        emitted = []
        second = event.timestamp_ns // 1_000_000_000
        if self.bucket is None:
            self.bucket = second
            self.birth = {key: second if born == 0 else born
                          for key, born in self.birth.items()}
        elif second != self.bucket:
            row = self.emit()
            if row is not None:
                emitted.append(row)
            self.bucket = second
            self.reset_bucket()

        if event.stream_id and self.stream_id not in (None, event.stream_id):
            self.bids.clear()
            self.asks.clear()
            self.birth.clear()
        if event.stream_id:
            self.stream_id = event.stream_id
        if event.kind == "D":
            self.apply_depth(event)
        else:
            self.apply_trade(event)
            self.remove_crossed_seed_levels(event.best_bid, event.best_ask)
        return emitted

    def _line(self, side, selected, same_side_depth):
        if not selected:
            return [0.0] * 8
        price, size = max(selected, key=lambda item: item[1])
        best = selected[0][0]
        distance = ((best - price) if side == "BID" else (price - best)) / self.tick_size
        adds = (self.bid_adds_by_price if side == "BID" else self.ask_adds_by_price)
        cancels = (self.bid_cancels_by_price if side == "BID"
                   else self.ask_cancels_by_price)
        executions = (self.bid_exec_by_price if side == "BID" else self.ask_exec_by_price)
        born = self.birth.get((side, price), self.bucket or 0)
        age = max(0, (self.bucket or 0) - born + 1)
        return [
            price,
            size,
            size / same_side_depth if same_side_depth > 0 else 0.0,
            distance,
            age,
            adds.get(price, 0.0),
            cancels.get(price, 0.0),
            executions.get(price, 0.0),
        ]

    def emit(self):
        base = super().emit()
        if base is None:
            return None
        bids = sorted(self.bids.items(), reverse=True)[: self.levels]
        asks = sorted(self.asks.items())[: self.levels]
        bid_depth = sum(size for _, size in bids)
        ask_depth = sum(size for _, size in asks)
        prices = self.trade_prices
        values = base.values
        values["trade_ohlc"] = (
            [prices[0], max(prices), min(prices), prices[-1]]
            if prices else [0.0, 0.0, 0.0, 0.0]
        )
        values["bid_line"] = self._line("BID", bids, bid_depth)
        values["ask_line"] = self._line("ASK", asks, ask_depth)
        values["bids"] = [[price, size] for price, size in bids]
        values["asks"] = [[price, size] for price, size in asks]
        return FeatureRow(base.timestamp_ns, values)


def compact(source, row):
    """Convert an enriched FeatureRow to the stable compact cache schema."""
    value = row.values
    bids, asks = value["bids"], value["asks"]
    best_bid = bids[0][0] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    return [
        row.timestamp_ns // 1_000_000_000,
        source,
        best_bid,
        best_ask,
        value["midprice"],
        value["microprice"],
        value["top5_imbalance"],
        value["trade_delta"],
        *value["trade_ohlc"],
        *value["bid_line"],
        *value["ask_line"],
        bids,
        asks,
        bool(value["book_valid"]),
    ]


@dataclass(frozen=True)
class LineRow:
    ts: int
    source: str
    best_bid: float
    best_ask: float
    mid: float
    micro: float
    top5: float
    delta: float
    trade_open: float
    trade_high: float
    trade_low: float
    trade_close: float
    bid: tuple
    ask: tuple
    bids: tuple
    asks: tuple
    valid: bool
    bid_pct: float = 0.0
    ask_pct: float = 0.0


def expand(raw):
    return LineRow(
        int(raw[0]), raw[1], *map(float, raw[2:12]),
        tuple(map(float, raw[12:20])), tuple(map(float, raw[20:28])),
        tuple((float(p), float(s)) for p, s in raw[28]),
        tuple((float(p), float(s)) for p, s in raw[29]), bool(raw[30]),
    )


def _cache_files(source, from_date=None, to_date=None):
    pattern = str(Path(data.CACHE_DIR) / f"nq_lines_v{SCHEMA_VERSION}_{source}_*.json.gz")
    files = []
    for name in glob.glob(pattern):
        day = Path(name).name.split("_")[4].split(".")[0]
        if (from_date is None or day >= from_date) and (to_date is None or day < to_date):
            files.append((day, name))
    # A rerun with a new fingerprint can leave two files for a day. Newest wins.
    newest = {}
    for day, name in files:
        if day not in newest or Path(name).stat().st_mtime > Path(newest[day]).stat().st_mtime:
            newest[day] = name
    return [newest[day] for day in sorted(newest)]


def load(source="dbento", from_date=None, to_date=None, normalize=True):
    rows = []
    for path in _cache_files(source, from_date, to_date):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows.extend(expand(raw) for raw in json.load(handle)["rows"])
    rows.sort(key=lambda row: row.ts)
    return attach_percentiles(rows) if normalize else rows


def iter_days(source="dbento", from_date=None, to_date=None):
    """Yield one expanded source/session at a time to keep memory bounded."""
    for path in _cache_files(source, from_date, to_date):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = [expand(raw) for raw in payload["rows"]]
        rows.sort(key=lambda row: row.ts)
        # Fail closed on truncated imports. A legitimate US half-session still
        # has more than 10k active seconds; the observed broken partitions have
        # only a handful of rows and must not enter normalization or PnL.
        if len(rows) < 10_000:
            continue
        yield payload["day"], rows


def iter_normalized_days(source="dbento", from_date=None, to_date=None):
    """Yield days with ranks fitted only on the prior 20 completed sessions."""
    history = defaultdict(lambda: deque(maxlen=NORM_SESSIONS))
    for day, rows in iter_days(source, from_date, to_date):
        samples = {
            key: sorted(value for session in sessions for value in session)
            for key, sessions in history.items()
        }
        today = defaultdict(list)
        normalized = []
        for row in rows:
            minute = (row.ts % 86_400) // 60
            time_bucket = minute // TIME_BUCKET_MINUTES
            ranks = []
            for side, line in (("bid", row.bid), ("ask", row.ask)):
                distance_bucket = min(9, max(0, int(line[3])))
                key = (row.source, side, time_bucket, distance_bucket)
                ranks.append(_rank(samples.get(key, ()), line[1]))
                if line[1] > 0:
                    today[key].append(line[1])
            normalized.append(replace(row, bid_pct=ranks[0], ask_pct=ranks[1]))
        yield day, normalized
        for key, values in today.items():
            history[key].append(values)


def _rank(sample, value):
    if not sample:
        return 0.0
    ordered = sorted(sample)
    return 100.0 * bisect.bisect_right(ordered, value) / len(ordered)


def attach_percentiles(rows):
    """Attach causal ranks from the prior 20 completed source sessions."""
    history = defaultdict(lambda: deque(maxlen=NORM_SESSIONS))
    out = []
    day_rows = []
    day = None

    def finish(batch):
        if not batch:
            return
        today = defaultdict(list)
        for row in batch:
            minute = (row.ts % 86_400) // 60
            time_bucket = minute // TIME_BUCKET_MINUTES
            ranks = []
            for side, line in (("bid", row.bid), ("ask", row.ask)):
                distance_bucket = min(9, max(0, int(line[3])))
                key = (row.source, side, time_bucket, distance_bucket)
                sample = [value for session in history[key] for value in session]
                ranks.append(_rank(sample, line[1]))
                if line[1] > 0:
                    today[key].append(line[1])
            out.append(replace(row, bid_pct=ranks[0], ask_pct=ranks[1]))
        for key, values in today.items():
            history[key].append(values)

    for row in rows:
        current = row.ts // 86_400
        if day is not None and current != day:
            finish(day_rows)
            day_rows = []
        day = current
        day_rows.append(row)
    finish(day_rows)
    return out


def level_size(levels, price):
    for level_price, size in levels:
        if level_price == price:
            return size
    return 0.0
