"""S3 maker execution research with conservative FIFO queue reconstruction.

The experiment is deliberately narrow:

* signal: the measured one-second A2 book-slope continuation;
* execution: post one lot at the prevailing best quote on the signal side;
* queue: latest absolute displayed size at that price, no cancellation credit;
* fill: matching aggressive volume must exceed queue-ahead plus order quantity;
* grid: |slope| cut x order expiry x post-fill hold (3 x 3 x 3);
* cost: observed entry/exit quotes plus 0.20 points of round-trip commission;
* validation: anchored monthly walk-forward and six purged three-month blocks.

Rows are sampled every 30 seconds so the maximum 15-second expiry plus
15-second hold completes before the next decision.  A fill is interval-censored
at 1/5/15 seconds and treated as
though it arrived at the end of that interval.  That deliberately delays the
exit and is conservative for a seconds-horizon continuation signal.

Day/time/side filters, sizing variants, cost sensitivity, and the fixed
regularized logistic meta-labeler are diagnostics on the fold-median rule when
one exists. If selection is flat, they use the existing A2 0.07 slope threshold
and the central 5s/5s grid cell as a fixed reference. They do not feed back into
selection.

Run from the repository root:

    py -m sandbox.research.maker_s3_research \
        --out sandbox/results/maker_s3_result.json --record-trials
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import random
import statistics
import subprocess
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import metrics
from sandbox import purged_cv
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PACKAGE_ROOT

NAME = "S3 Maker Book Slope"
FROM = "2025-02-12T09:35:00Z"
TO = "2026-07-16T15:45:00Z"
ENTRY_FROM = 575
ENTRY_TO = 945
COMMISSION = 0.20
INITIAL = 1_000.0
SLOPES = (0.03, 0.07, 0.15)
EXPIRIES = (1, 5, 15)
HOLDS = (1, 5, 15)
GRID = tuple(
    (slope, expiry, hold)
    for slope in SLOPES
    for expiry in EXPIRIES
    for hold in HOLDS
)
MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260729
SAMPLE_SECONDS = 30
DIAGNOSTIC_CELL = (0.07, 5, 5)
QUEUE_KILL = {
    "sample": "both best quotes every five minutes, 09:35-15:45",
    "orders": 50_398,
    "fills_within_15s": 25_765,
    "fill_rate": 0.511231,
    "bid_fill_1s_signed_mid_move": 0.018223,
    "ask_fill_1s_signed_mid_move": 0.001199,
    "combined_1s_signed_mid_move": 0.009747,
    "kill_threshold": -0.03,
    "passed": True,
}
FEATURE_NAMES = (
    "abs_slope",
    "signed_microprice_ticks",
    "signed_top1",
    "signed_top5",
    "signed_top10",
    "signed_trade_delta",
    "signed_price_change",
    "log_trade_count",
    "log_depth_events",
    "spread",
    "log_queue_ahead",
    "tod_sin",
    "tod_cos",
    "weekday_sin",
    "weekday_cos",
)


@dataclass(frozen=True)
class Observation:
    signal_ts: int
    order_ts: int
    exit_ts: int
    side: str
    slope: float
    queue_ahead: float
    executed: float
    gross_points: float
    features: tuple[float, ...]

    def points(self, commission: float = COMMISSION, quantity: int = 1) -> float:
        return (self.gross_points - commission) * quantity


@dataclass
class QueueCandidate:
    signal_ts: int
    order_ts: int
    side: str
    trade_side: str
    price: float
    slope: float
    queue_ahead: float
    spread: float
    micro_tilt: float
    top1: float
    top5: float
    top10: float
    trade_delta: float
    price_change: float
    trade_count: float
    depth_events: float
    executed: dict[int, float] = field(
        default_factory=lambda: {1: 0.0, 5: 0.0, 15: 0.0}
    )


def query_sql(from_iso: str = FROM, to_iso: str = TO) -> str:
    return f"""
WITH base AS (
 SELECT timestamp,midprice,spread,microprice,top1_imbalance,top5_imbalance,
        top10_imbalance,trade_delta,price_change,trade_count,depth_event_count,
        (ask_depth_distance-bid_depth_distance)/
        (ask_depth_distance+bid_depth_distance) slope
 FROM nq_l2_features_1s
 WHERE source='dbento'
   AND timestamp>='{from_iso}' AND timestamp<'{to_iso}'
   AND book_valid AND spread<=1.25
   AND bid_depth_distance>0 AND ask_depth_distance>0
   AND hour(timestamp)*60+minute(timestamp)>={ENTRY_FROM}
   AND hour(timestamp)*60+minute(timestamp)<{ENTRY_TO}
   AND second(timestamp)%{SAMPLE_SECONDS}=0
), c AS (
 SELECT dateadd('s',1,timestamp) timestamp,timestamp signal_ts,slope,
        CASE WHEN slope>0 THEN 'BID' ELSE 'ASK' END depth_side,
        CASE WHEN slope>0 THEN 'SELL' ELSE 'BUY' END trade_side,
        CASE WHEN slope>0 THEN midprice-spread/2.0
             ELSE midprice+spread/2.0 END price,
        spread,microprice-midprice micro_tilt,top1_imbalance,top5_imbalance,
        top10_imbalance,trade_delta,price_change,trade_count,depth_event_count
 FROM base WHERE abs(slope)>={min(SLOPES)}
 ORDER BY timestamp
), q AS (
 SELECT c.*,d.size queue_ahead
 FROM c ASOF JOIN dbento_nq_depth d
   ON (c.depth_side=d.side AND c.price=d.price)
)
SELECT cast(q.signal_ts AS LONG) signal_us,cast(q.timestamp AS LONG) order_us,
       q.depth_side,q.trade_side,q.price,q.slope,q.queue_ahead,q.spread,
       q.micro_tilt,q.top1_imbalance,q.top5_imbalance,q.top10_imbalance,
       q.trade_delta,q.price_change,q.trade_count,q.depth_event_count
FROM q WHERE q.queue_ahead>0
ORDER BY q.signal_ts
""".strip()


def export_url(sql: str) -> str:
    return "http://127.0.0.1:9000/exp?" + urllib.parse.urlencode(
        {"query": sql, "timeout": 3_600}
    )


def fetch_export_rows(lo: str, hi: str):
    """Fetch one time slice, bisecting only when QuestDB's query timer fires."""
    try:
        response = urllib.request.urlopen(
            export_url(query_sql(lo, hi)), timeout=3_600
        )
        text = (line.decode("utf-8") for line in response)
        reader = csv.reader(text)
        return next(reader), list(reader)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        start = datetime.fromisoformat(lo.replace("Z", "+00:00"))
        end = datetime.fromisoformat(hi.replace("Z", "+00:00"))
        if end - start <= timedelta(hours=6):
            raise RuntimeError(
                f"QuestDB export failed for {lo}..{hi}: {detail}"
            ) from exc
        middle = start + (end - start) / 2
        midpoint = middle.isoformat().replace("+00:00", "Z")
        left_header, left = fetch_export_rows(lo, midpoint)
        right_header, right = fetch_export_rows(midpoint, hi)
        if left_header != right_header:
            raise RuntimeError("QuestDB export schema changed between time slices")
        return left_header, left + right


def source_rows(cache: Path | None, refresh: bool):
    if cache and cache.exists() and not refresh:
        with gzip.open(cache, "rt", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            yield from reader
        return

    start = datetime.fromisoformat(FROM.replace("Z", "+00:00"))
    end = datetime.fromisoformat(TO.replace("Z", "+00:00"))
    windows = [
        (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )
    ]

    handle = None
    writer = None
    temporary_cache = (
        cache.with_suffix(cache.suffix + ".tmp") if cache is not None else None
    )
    part_dir = (
        cache.parent / f"{cache.stem}_parts" if cache is not None else None
    )
    completed = False
    try:
        if cache and len(windows) > 1:
            cache.parent.mkdir(parents=True, exist_ok=True)
            part_dir.mkdir(parents=True, exist_ok=True)
            handle = gzip.open(temporary_cache, "wt", newline="")
            writer = csv.writer(handle)
        elif cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            part_dir.mkdir(parents=True, exist_ok=True)
        for index, (lo, hi) in enumerate(windows, 1):
            print(f"  extracting queue rows {index}/{len(windows)}: {lo[:10]}..{hi[:10]}", flush=True)
            part = (
                part_dir / f"{lo[:10]}_{hi[:10]}.csv.gz"
                if part_dir is not None
                else None
            )
            if part and part.exists():
                part_handle = gzip.open(part, "rt", newline="")
                reader = csv.reader(part_handle)
                header = next(reader)
                part_writer = None
                temporary_part = None
            else:
                header, reader = fetch_export_rows(lo, hi)
                part_handle = None
                temporary_part = (
                    part.with_suffix(part.suffix + ".tmp") if part else None
                )
                part_output = (
                    gzip.open(temporary_part, "wt", newline="")
                    if temporary_part
                    else None
                )
                part_writer = csv.writer(part_output) if part_output else None
                if part_writer:
                    part_writer.writerow(header)
            if writer and index == 1:
                writer.writerow(header)
            part_completed = False
            try:
                for row in reader:
                    if writer:
                        writer.writerow(row)
                    if part_writer:
                        part_writer.writerow(row)
                    yield row
                part_completed = True
            finally:
                if part_handle:
                    part_handle.close()
                if part_writer:
                    part_output.close()
                    if part_completed:
                        os.replace(temporary_part, part)
        completed = True
    finally:
        if handle:
            handle.close()
        if completed and cache and temporary_cache and len(windows) > 1:
            os.replace(temporary_cache, cache)


def feature_vector(
    signal_ts: int,
    side: str,
    slope: float,
    queue: float,
    spread: float,
    micro_tilt: float,
    top1: float,
    top5: float,
    top10: float,
    trade_delta: float,
    price_change: float,
    trade_count: float,
    depth_events: float,
) -> tuple[float, ...]:
    sign = 1.0 if side == "long" else -1.0
    minute = (signal_ts % 86_400) / 60.0
    progress = (minute - ENTRY_FROM) / (ENTRY_TO - ENTRY_FROM)
    tod = 2.0 * math.pi * progress
    weekday = (signal_ts // 86_400 + 3) % 7
    dow = 2.0 * math.pi * weekday / 5.0
    return (
        abs(slope),
        sign * micro_tilt / 0.25,
        sign * top1,
        sign * top5,
        sign * top10,
        math.asinh(sign * trade_delta / 10.0),
        math.asinh(sign * price_change),
        math.log1p(trade_count),
        math.log1p(depth_events),
        spread,
        math.log1p(queue),
        math.sin(tod),
        math.cos(tod),
        math.sin(dow),
        math.cos(dow),
    )


def load_queue_candidates(cache: Path | None, refresh: bool):
    out = []
    for row in source_rows(cache, refresh):
        (
            signal_us,
            order_us,
            depth_side,
            trade_side,
            price,
            slope,
            queue_ahead,
            spread,
            micro_tilt,
            top1,
            top5,
            top10,
            trade_delta,
            price_change,
            trade_count,
            depth_events,
        ) = row
        out.append(
            QueueCandidate(
                int(signal_us) // 1_000_000,
                int(order_us) // 1_000_000,
                "long" if depth_side == "BID" else "short",
                trade_side,
                float(price),
                float(slope),
                float(queue_ahead),
                float(spread),
                float(micro_tilt),
                float(top1),
                float(top5),
                float(top10),
                float(trade_delta),
                float(price_change),
                float(trade_count),
                float(depth_events),
            )
        )
    out.sort(key=lambda row: row.order_ts)
    return out


def stream_sql(sql: str, timeout=3_600):
    request = urllib.request.urlopen(export_url(sql), timeout=timeout)
    text = (line.decode("utf-8") for line in request)
    reader = csv.reader(text)
    next(reader)
    yield from reader


def execution_cache_path(queue_cache: Path | None):
    if queue_cache is None:
        return None
    return queue_cache.with_name(queue_cache.stem + "_executions.csv.gz")


def resolve_executions(candidates, queue_cache: Path | None, refresh: bool):
    cache = execution_cache_path(queue_cache)
    by_signal = {row.signal_ts: row for row in candidates}
    if cache and cache.exists() and not refresh:
        with gzip.open(cache, "rt", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            for signal_ts, one, five, fifteen in reader:
                candidate = by_signal.get(int(signal_ts))
                if candidate:
                    candidate.executed = {
                        1: float(one),
                        5: float(five),
                        15: float(fifteen),
                    }
        return

    by_day = defaultdict(list)
    for candidate in candidates:
        by_day[candidate.order_ts // 86_400].append(candidate)

    for number, (day, rows) in enumerate(sorted(by_day.items()), 1):
        start = rows[0].order_ts
        end = rows[-1].order_ts + 16
        lo = datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z")
        hi = datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z")
        sql = f"""
SELECT cast(cast(timestamp AS TIMESTAMP) AS LONG) ts_us,side,price,size
FROM dbento_nq_ticks
WHERE timestamp>='{lo}' AND timestamp<'{hi}'
ORDER BY timestamp,trade_sequence
""".strip()
        if number % 20 == 1 or number == len(by_day):
            print(
                f"  resolving tick queues {number}/{len(by_day)}: {lo[:10]}",
                flush=True,
            )
        index = 0
        for ts_us, side, price, size in stream_sql(sql):
            event_us = int(ts_us)
            while (
                index < len(rows)
                and event_us > (rows[index].order_ts + 15) * 1_000_000
            ):
                index += 1
            if index >= len(rows):
                break
            candidate = rows[index]
            elapsed = event_us - candidate.order_ts * 1_000_000
            if (
                elapsed <= 0
                or elapsed > 15_000_000
                or side != candidate.trade_side
                or float(price) != candidate.price
            ):
                continue
            volume = float(size)
            if elapsed <= 1_000_000:
                candidate.executed[1] += volume
            if elapsed <= 5_000_000:
                candidate.executed[5] += volume
            candidate.executed[15] += volume

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(cache.suffix + ".tmp")
        with gzip.open(temporary, "wt", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("signal_ts", "exec_1s", "exec_5s", "exec_15s"))
            for row in candidates:
                writer.writerow(
                    (
                        row.signal_ts,
                        row.executed[1],
                        row.executed[5],
                        row.executed[15],
                    )
                )
        os.replace(temporary, cache)


def attach_exit_quotes(candidates):
    needed = set()
    for candidate in candidates:
        for expiry in EXPIRIES:
            if candidate.executed[expiry] >= candidate.queue_ahead + 1:
                for hold in HOLDS:
                    needed.add(candidate.order_ts + expiry + hold)
    quote_seconds = (1, 3, 7, 11, 17, 21, 31, 33, 37, 41, 47, 51)
    seconds = ",".join(str(value) for value in quote_seconds)
    sql = f"""
SELECT cast(timestamp AS LONG) ts_us,midprice,spread
FROM nq_l2_features_1s
WHERE source='dbento' AND timestamp>='{FROM}' AND timestamp<'{TO}'
  AND book_valid AND second(timestamp) IN ({seconds})
ORDER BY timestamp
""".strip()
    quotes = {}
    for ts_us, midprice, spread in stream_sql(sql):
        ts = int(ts_us) // 1_000_000
        if ts in needed:
            quotes[ts] = (float(midprice), float(spread))
    return quotes


def load_observations(cache: Path | None, refresh: bool):
    candidates = load_queue_candidates(cache, refresh)
    print(f"  reconstructed positive queue for {len(candidates):,} candidates")
    resolve_executions(candidates, cache, refresh)
    quotes = attach_exit_quotes(candidates)
    cells: dict[tuple[int, int], list[Observation]] = {
        (expiry, hold): [] for expiry in EXPIRIES for hold in HOLDS
    }
    for candidate in candidates:
        features = feature_vector(
            candidate.signal_ts,
            candidate.side,
            candidate.slope,
            candidate.queue_ahead,
            candidate.spread,
            candidate.micro_tilt,
            candidate.top1,
            candidate.top5,
            candidate.top10,
            candidate.trade_delta,
            candidate.price_change,
            candidate.trade_count,
            candidate.depth_events,
        )
        for expiry in EXPIRIES:
            executed = candidate.executed[expiry]
            if executed < candidate.queue_ahead + 1:
                continue
            for hold in HOLDS:
                exit_ts = candidate.order_ts + expiry + hold
                quote = quotes.get(exit_ts)
                if quote is None:
                    continue
                mid, spread = quote
                exit_price = (
                    mid - spread / 2.0
                    if candidate.side == "long"
                    else mid + spread / 2.0
                )
                gross = (
                    exit_price - candidate.price
                    if candidate.side == "long"
                    else candidate.price - exit_price
                )
                cells[(expiry, hold)].append(
                    Observation(
                        candidate.signal_ts,
                        candidate.order_ts,
                        exit_ts,
                        candidate.side,
                        candidate.slope,
                        candidate.queue_ahead,
                        executed,
                        gross,
                        features,
                    )
                )
    for rows in cells.values():
        rows.sort(key=lambda row: (row.order_ts, row.exit_ts))
    return cells, len(candidates)


def eligible(
    rows: list[Observation],
    slope_cut: float,
    quantity: int = 1,
    lo: int | None = None,
    hi: int | None = None,
    predicate=None,
) -> list[Observation]:
    out = []
    occupied_until = -1
    for row in rows:
        if abs(row.slope) < slope_cut:
            continue
        if row.executed < row.queue_ahead + quantity:
            continue
        if lo is not None and row.order_ts < lo:
            continue
        if hi is not None and row.order_ts >= hi:
            continue
        if predicate is not None and not predicate(row):
            continue
        if row.order_ts < occupied_until:
            continue
        out.append(row)
        occupied_until = row.exit_ts
    return out


def points(rows, commission=COMMISSION, quantity=1, weights=None):
    if weights is None:
        return [row.points(commission, quantity) for row in rows]
    return [row.points(commission, quantity) * weight for row, weight in zip(rows, weights)]


def edge_t(values):
    return walkforward.edge_t(values)


def month_key(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def point_stats(rows, commission=COMMISSION, quantity=1, weights=None, span=None):
    values = points(rows, commission, quantity, weights)
    monthly = defaultdict(float)
    monthly_trades = defaultdict(int)
    for index, row in enumerate(rows):
        weight = 1.0 if weights is None else weights[index]
        key = month_key(row.order_ts)
        monthly[key] += row.points(commission, quantity) * weight
        monthly_trades[key] += 1
    if span:
        start, end = span
        cursor = datetime.fromtimestamp(start, timezone.utc)
        last = datetime.fromtimestamp(end - 1, timezone.utc)
        while (cursor.year, cursor.month) <= (last.year, last.month):
            monthly.setdefault(f"{cursor.year}-{cursor.month:02d}", 0.0)
            monthly_trades.setdefault(f"{cursor.year}-{cursor.month:02d}", 0)
            cursor = (
                datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
                if cursor.month == 12
                else datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)
            )
    wins = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    equity = peak = INITIAL
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    mean = sum(values) / len(values) if values else 0.0
    return {
        "trades": len(values),
        "points": round(sum(values), 4),
        "edge": round(mean, 6),
        "t": round(edge_t(values), 4),
        "pf": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
        "win_rate": round(sum(value > 0 for value in values) / len(values), 4)
        if values
        else 0.0,
        "max_drawdown": round(max_dd, 4),
        "positive_months": sum(value > 0 for value in monthly.values()),
        "month_trades": {
            key: value for key, value in sorted(monthly_trades.items())
        },
        "months": {key: round(value, 4) for key, value in sorted(monthly.items())},
    }


def neighbours(cell):
    values = (SLOPES, EXPIRIES, HOLDS)
    out = []
    for axis in range(3):
        index = values[axis].index(cell[axis])
        for step in (-1, 1):
            other_index = index + step
            if 0 <= other_index < len(values[axis]):
                other = list(cell)
                other[axis] = values[axis][other_index]
                out.append(tuple(other))
    return out


def cell_rows(cells, cell, quantity=1, lo=None, hi=None, predicate=None):
    slope, expiry, hold = cell
    return eligible(cells[(expiry, hold)], slope, quantity, lo, hi, predicate)


def select_cell(cells, train_predicate, min_trades):
    stats = {}
    for cell in GRID:
        rows = [row for row in cell_rows(cells, cell) if train_predicate(row)]
        values = points(rows)
        stats[cell] = {
            "trades": len(rows),
            "edge": sum(values) / len(values) if values else 0.0,
            "t": edge_t(values),
            "points": sum(values),
        }
    selected = []
    for cell, stat in stats.items():
        near = neighbours(cell)
        if stat["trades"] < min_trades or stat["edge"] <= 0:
            continue
        if any(stats[other]["edge"] <= 0 for other in near):
            continue
        plateau_t = sum(stats[other]["t"] for other in near) / len(near)
        selected.append((min(stat["t"], plateau_t), cell))
    selected.sort(reverse=True)
    return (selected[0][1] if selected else None), stats


def walk_forward(cells):
    folds = []
    stitched = []
    for test_from, test_to in walkforward.FOLDS:
        test_lo = metrics.split_ts(test_from)
        test_hi = metrics.split_ts(test_to)
        train_hi = test_lo - 86_400
        months = max(1, round((train_hi - metrics.split_ts("2025-02-12")) / (365.25 / 12 * 86_400)))
        pick, _stats = select_cell(
            cells,
            lambda row, cutoff=train_hi: row.exit_ts < cutoff,
            max(100, 25 * months),
        )
        test_rows = [] if pick is None else cell_rows(cells, pick, lo=test_lo, hi=test_hi)
        stitched.extend(test_rows)
        folds.append(
            {
                "test": test_from[:7],
                "params": None
                if pick is None
                else {"slope": pick[0], "expiry": pick[1], "hold": pick[2]},
                **point_stats(test_rows, span=(test_lo, test_hi)),
            }
        )
    stitched.sort(key=lambda row: row.order_ts)
    return folds, stitched


def median_cell(folds):
    picks = [fold["params"] for fold in folds if fold["params"]]
    if not picks:
        return None
    chosen = []
    for name, values in zip(("slope", "expiry", "hold"), (SLOPES, EXPIRIES, HOLDS)):
        raw = statistics.median(pick[name] for pick in picks)
        chosen.append(min(values, key=lambda value: abs(value - raw)))
    return tuple(chosen)


def purged_cv(cells):
    blocks = []
    for start, end in purged_cv.BLOCKS:
        lo, hi = metrics.split_ts(start), metrics.split_ts(end)
        purge_lo, purge_hi = lo - 86_400, hi + 86_400
        pick, _stats = select_cell(
            cells,
            lambda row, a=purge_lo, b=purge_hi: row.exit_ts < a or row.order_ts >= b,
            500,
        )
        test_rows = [] if pick is None else cell_rows(cells, pick, lo=lo, hi=hi)
        blocks.append(
            {
                "block": f"{start}..{end}",
                "params": None
                if pick is None
                else {"slope": pick[0], "expiry": pick[1], "hold": pick[2]},
                **point_stats(test_rows, span=(lo, hi)),
            }
        )
    return blocks


def diagnostics(cells, cell, oos_lo, oos_hi):
    variants = {
        "baseline": lambda row: True,
        "long_only": lambda row: row.side == "long",
        "short_only": lambda row: row.side == "short",
    }
    for name, weekday in zip(("mon", "tue", "wed", "thu", "fri"), range(5)):
        variants[f"skip_{name}"] = (
            lambda row, day=weekday: (row.order_ts // 86_400 + 3) % 7 != day
        )
    variants.update(
        {
            "open_09:35_11:00": lambda row: 575 <= (row.order_ts % 86_400) // 60 < 660,
            "mid_11:00_14:00": lambda row: 660 <= (row.order_ts % 86_400) // 60 < 840,
            "close_14:00_15:45": lambda row: 840 <= (row.order_ts % 86_400) // 60 < 945,
        }
    )
    baseline_by_fold = {}
    for test_from, test_to in walkforward.FOLDS:
        lo, hi = metrics.split_ts(test_from), metrics.split_ts(test_to)
        baseline_by_fold[test_from[:7]] = sum(
            points(cell_rows(cells, cell, lo=lo, hi=hi))
        )
    out = {}
    for name, predicate in variants.items():
        rows = cell_rows(cells, cell, lo=oos_lo, hi=oos_hi, predicate=predicate)
        improved = 0
        if name != "baseline":
            for test_from, test_to in walkforward.FOLDS:
                lo, hi = metrics.split_ts(test_from), metrics.split_ts(test_to)
                value = sum(
                    points(cell_rows(cells, cell, lo=lo, hi=hi, predicate=predicate))
                )
                improved += value > baseline_by_fold[test_from[:7]]
        out[name] = {
            **point_stats(rows, span=(oos_lo, oos_hi)),
            "improved_folds": improved,
        }
    return out


def sizing_diagnostics(cells, cell, oos_lo, oos_hi):
    baseline = cell_rows(cells, cell, lo=oos_lo, hi=oos_hi)
    out = {}
    for quantity in (1, 2, 4):
        rows = cell_rows(cells, cell, quantity=quantity, lo=oos_lo, hi=oos_hi)
        out[f"queue_aware_{quantity}_lot"] = point_stats(
            rows, quantity=quantity, span=(oos_lo, oos_hi)
        )
    for scale in (0.5, 1.0, 2.0):
        weights = [scale] * len(baseline)
        out[f"fixed_{scale:.1f}x"] = point_stats(
            baseline, weights=weights, span=(oos_lo, oos_hi)
        )

    discovery = cell_rows(cells, cell, hi=oos_lo)
    reference = statistics.median(abs(row.slope) for row in discovery)
    confidence = [
        min(1.5, max(0.5, abs(row.slope) / reference)) for row in baseline
    ]
    out["bounded_slope_confidence"] = point_stats(
        baseline, weights=confidence, span=(oos_lo, oos_hi)
    )

    trailing = []
    past = [row.points() for row in discovery[-500:]]
    for row in baseline:
        vol = statistics.pstdev(past[-500:]) if len(past) >= 20 else 0.0
        target = statistics.pstdev(past) if len(past) >= 20 else vol
        trailing.append(min(1.5, max(0.5, target / vol)) if vol > 0 else 1.0)
        past.append(row.points())
    out["causal_inverse_500_trade_vol"] = point_stats(
        baseline, weights=trailing, span=(oos_lo, oos_hi)
    )
    return out


def ml_features(rows):
    return np.asarray([row.features for row in rows], dtype=float)


def ml_walk_forward(cells, cell):
    base = cell_rows(cells, cell)
    folds = []
    stitched = []
    for test_from, test_to in walkforward.FOLDS:
        test_lo, test_hi = metrics.split_ts(test_from), metrics.split_ts(test_to)
        cutoff = test_lo - 86_400
        train = [row for row in base if row.exit_ts < cutoff]
        candidates = [
            row for row in cells[(cell[1], cell[2])]
            if abs(row.slope) >= cell[0]
            and row.executed >= row.queue_ahead + 1
            and test_lo <= row.order_ts < test_hi
        ]
        labels = np.asarray([row.points() > 0 for row in train], dtype=int)
        if len(train) < 1_000 or len(set(labels.tolist())) < 2:
            selected = []
        else:
            model = make_pipeline(
                StandardScaler(),
                LogisticRegression(
                    C=MODEL_C,
                    max_iter=2_000,
                    random_state=MODEL_SEED,
                ),
            )
            model.fit(ml_features(train), labels)
            probabilities = model.predict_proba(ml_features(candidates))[:, 1]
            accepted = {
                id(row)
                for row, probability in zip(candidates, probabilities)
                if probability >= MODEL_THRESHOLD
            }
            selected = eligible(
                candidates,
                cell[0],
                lo=test_lo,
                hi=test_hi,
                predicate=lambda row, keep=accepted: id(row) in keep,
            )
        stitched.extend(selected)
        folds.append(
            {
                "test": test_from[:7],
                "train_trades": len(train),
                **point_stats(selected, span=(test_lo, test_hi)),
            }
        )
    stitched.sort(key=lambda row: row.order_ts)
    return folds, stitched


def bootstrap(values, draws=10_000):
    if len(values) < 2:
        return (0.0, 0.0)
    rng = random.Random(MODEL_SEED)
    means = []
    for _ in range(draws):
        means.append(sum(values[rng.randrange(len(values))] for _ in values) / len(values))
    means.sort()
    return means[int(draws * 0.025)], means[int(draws * 0.975)]


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def run(cache=None, refresh=False, record_trials=False):
    cells, raw_rows = load_observations(cache, refresh)
    wf_folds, stitched = walk_forward(cells)
    median = median_cell(wf_folds)
    cv = purged_cv(cells)
    oos_lo = metrics.split_ts(walkforward.FOLDS[0][0])
    oos_hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    if record_trials:
        trials.record(NAME, 1, "pre-declared FIFO adverse-selection kill test")
        trials.record(NAME, len(GRID), "anchored walk-forward, 3-axis maker grid")
        trials.record(NAME, len(GRID), "six-block purged CV, same maker grid")

    fixed_rows = (
        [] if median is None else cell_rows(cells, median, lo=oos_lo, hi=oos_hi)
    )
    diagnostic_rows = cell_rows(
        cells, DIAGNOSTIC_CELL, lo=oos_lo, hi=oos_hi
    )
    diagnostics_result = diagnostics(
        cells, DIAGNOSTIC_CELL, oos_lo, oos_hi
    )
    sizing = sizing_diagnostics(
        cells, DIAGNOSTIC_CELL, oos_lo, oos_hi
    )
    costs = {
        f"{cost:.2f}": point_stats(
            diagnostic_rows, commission=cost, span=(oos_lo, oos_hi)
        )
        for cost in (0.0, 0.10, 0.20, 0.25)
    }
    ml_folds, ml_rows = (
        ml_walk_forward(cells, DIAGNOSTIC_CELL)
        if len(diagnostic_rows) >= 1_000
        else ([], [])
    )
    if record_trials:
        trials.record(NAME, len(diagnostics_result), "fixed OOS day/time/side diagnostics")
        trials.record(NAME, len(sizing), "fixed OOS sizing diagnostics")
        trials.record(NAME, len(costs), "fixed execution-cost sensitivity")
        if ml_folds:
            trials.record(NAME, 1, "fixed L2-logistic maker meta-labeler")

    fixed = point_stats(fixed_rows, span=(oos_lo, oos_hi))
    diagnostic = point_stats(diagnostic_rows, span=(oos_lo, oos_hi))
    ml = point_stats(ml_rows, span=(oos_lo, oos_hi))
    cumulative = trials.total(NAME)
    fixed_values = points(fixed_rows)
    fixed_ci = bootstrap(fixed_values)
    positive_folds = sum(fold["points"] > 0 for fold in wf_folds)
    cv_points = [block["points"] for block in cv]
    cv_mean = sum(cv_points) / len(cv_points)
    gates = {
        "stitched_oos_positive": sum(points(stitched)) > 0,
        "profitable_folds_8_of_12": positive_folds >= 8,
        "fixed_edge_ci_excludes_zero": fixed_ci[0] > 0,
        "purged_cv_coverage": any(block["params"] for block in cv)
        and min(cv_points) >= -2 * abs(cv_mean),
        "deflated_t_positive": walkforward.deflated_t(
            edge_t(points(stitched)), cumulative
        )
        > 0,
    }
    result = {
        "strategy": NAME,
        "status": "promoted" if all(gates.values()) else "rejected",
        "data": {
            "from": FROM,
            "to": TO,
            "source": "dbento",
            "raw_join_rows_loaded": raw_rows,
            "git_sha": git_sha(),
        },
        "queue_kill_test": QUEUE_KILL,
        "execution": {
            "sample_seconds": SAMPLE_SECONDS,
            "queue": "FIFO; latest displayed size; no cancellation credit",
            "quantity": 1,
            "commission_points": COMMISSION,
            "entry": "passive best bid/ask",
            "exit": "aggressive observed best bid/ask",
            "fill_interval": "1/5/15s, treated as filled at interval end",
        },
        "grid": {
            "slope": SLOPES,
            "expiry": EXPIRIES,
            "hold": HOLDS,
            "cells": len(GRID),
        },
        "walk_forward": {
            "folds": wf_folds,
            "stitched": point_stats(stitched, span=(oos_lo, oos_hi)),
            "positive_folds": positive_folds,
        },
        "median_params": None
        if median is None
        else {"slope": median[0], "expiry": median[1], "hold": median[2]},
        "fixed_median_oos": fixed,
        "diagnostic_default": {
            "params": {
                "slope": DIAGNOSTIC_CELL[0],
                "expiry": DIAGNOSTIC_CELL[1],
                "hold": DIAGNOSTIC_CELL[2],
            },
            "oos": diagnostic,
        },
        "fixed_edge_bootstrap_95": [round(fixed_ci[0], 6), round(fixed_ci[1], 6)],
        "purged_cv": {
            "blocks": cv,
            "positive_blocks": sum(value > 0 for value in cv_points),
            "total_points": round(sum(cv_points), 4),
        },
        "day_time_side_diagnostics": diagnostics_result,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "ml": {
            "ran": bool(ml_folds),
            "model": "StandardScaler + LogisticRegression(L2,C=0.1)",
            "threshold": MODEL_THRESHOLD,
            "features": FEATURE_NAMES,
            "folds": ml_folds,
            "stitched": ml,
        },
        "cumulative_trials": cumulative,
        "gates": gates,
        "notes": [
            "Calendar, side, sizing, and cost diagnostics inspect OOS outcomes and are not adopted.",
            "ML is a single fixed model/feature set/threshold and may only reject the fixed maker rule.",
            "When walk-forward selects flat, diagnostics use the existing A2 threshold and central grid cell as a fixed reference and cannot override rejection.",
            "Observed quotes already include the passive-entry/aggressive-exit spread; commission is additional.",
        ],
    }
    return result


def print_summary(result):
    print("S3 maker execution research")
    print(f"  status: {result['status'].upper()}")
    print(f"  median: {result['median_params']}")
    wf = result["walk_forward"]["stitched"]
    fixed = result["fixed_median_oos"]
    print(
        f"  stitched WF: {wf['trades']} trades, {wf['points']:+.2f} pts, "
        f"PF {wf['pf']:.3f}, {result['walk_forward']['positive_folds']}/12 folds"
    )
    print(
        f"  fixed median: {fixed['trades']} trades, {fixed['points']:+.2f} pts, "
        f"edge {fixed['edge']:+.4f}, PF {fixed['pf']:.3f}"
    )
    diagnostic = result["diagnostic_default"]["oos"]
    print(
        f"  diagnostic default: {diagnostic['trades']} trades, "
        f"{diagnostic['points']:+.2f} pts, edge {diagnostic['edge']:+.4f}, "
        f"PF {diagnostic['pf']:.3f}"
    )
    print(
        f"  purged CV: {result['purged_cv']['positive_blocks']}/6 positive, "
        f"{result['purged_cv']['total_points']:+.2f} pts"
    )
    if result["ml"]["ran"]:
        ml = result["ml"]["stitched"]
        print(
            f"  fixed ML: {ml['trades']} trades, {ml['points']:+.2f} pts, "
            f"PF {ml['pf']:.3f}"
        )
    print("  gates:")
    for name, passed in result["gates"].items():
        print(f"    {'PASS' if passed else 'FAIL'} {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=PACKAGE_ROOT / ".cache" / "maker_s3_30s_rows.csv.gz",
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = run(args.cache, args.refresh, args.record_trials)
    print_summary(result)
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
