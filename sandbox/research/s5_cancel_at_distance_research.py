"""Leakage-controlled S5 cancel-at-distance research.

The raw Databento depth table is read only.  Reconstructed per-price add and
cancel distances are cached under ``sandbox/.cache``; no builder, server,
or raw table is changed.

Run from the repository root:

    .venv\\Scripts\\python.exe -B \
        -m sandbox.research.s5_cancel_at_distance_research \
        --out sandbox/results/s5_cancel_at_distance_result.json \
        --record-trials
"""

import argparse
import csv
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import gzip
import glob
import hashlib
import http.client
import itertools
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv as purged_cv_module
from sandbox import search
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PROJECT_ROOT
from sandbox.strategies.cancel_at_distance import (
    CACHE_Z,
    CONFIRMATION_LAG_SECONDS,
    Definition,
    ENTRY_FROM_SECOND,
    ENTRY_TO_SECOND,
    HORIZONS_SECONDS,
    FIXED_CANCEL_Z,
    NAME,
    NORMALIZATION_OBSERVATIONS,
    PRIMARY_HORIZON_SECONDS,
    RISK_STOP_POINTS,
    TICK_SIZE,
    selected_events,
)

INITIAL = 10_000.0
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)
ML_MINIMUM = 2_000
ML_C = 0.1
ML_THRESHOLD = 0.5
RAW_WORKERS = 1
RAW_SEED_SECONDS = 5
RAW_SECONDS_PER_QUERY = 250
MIN_RAW_COVERAGE = 0.80
MAX_RAW_COVERAGE = 1.25
FEATURE_SELECT = """
SELECT cast(timestamp AS LONG) ts,midprice,spread,top1_imbalance,
       top5_imbalance,top10_imbalance,bid_add_volume,bid_cancel_volume,
       ask_add_volume,ask_cancel_volume,aggressive_buy_volume,
       aggressive_sell_volume,trade_delta,depth_weighted_distance,book_valid
FROM nq_l2_features_1s
WHERE source='dbento'
""".strip()


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _cache_fingerprint():
    depth = data._table_fingerprint(["dbento_nq_depth"])
    feature = data.query(
        "SELECT min(timestamp),max(timestamp),count() "
        "FROM nq_l2_features_1s WHERE source='dbento'"
    )[0]
    return f"{depth};dbento-features:{feature[0]}|{feature[1]}|{feature[2]}"


def _digest(text):
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _gzip_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"))
    os.replace(temporary, path)


def _read_gzip_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _number(text):
    return float(text) if text else 0.0


class ClockNormalizer:
    """Causal 20-observation z-scores by second of the RTH clock."""

    FIRST = 570 * 60
    LAST = 960 * 60
    WIDTH = LAST - FIRST
    FEATURES = 4

    def __init__(self):
        shape = (
            self.FEATURES,
            self.WIDTH,
            NORMALIZATION_OBSERVATIONS,
        )
        self.values = np.zeros(shape, dtype=np.float32)
        self.sums = np.zeros((self.FEATURES, self.WIDTH), dtype=np.float64)
        self.squares = np.zeros((self.FEATURES, self.WIDTH), dtype=np.float64)
        self.counts = np.zeros((self.FEATURES, self.WIDTH), dtype=np.uint8)
        self.positions = np.zeros((self.FEATURES, self.WIDTH), dtype=np.uint8)

    def score_and_update(self, second, raw_values):
        slot = second - self.FIRST
        transformed = np.log1p(np.asarray(raw_values, dtype=np.float64))
        scores = [None] * self.FEATURES
        for feature, value in enumerate(transformed):
            count = int(self.counts[feature, slot])
            if count >= NORMALIZATION_OBSERVATIONS:
                mean = self.sums[feature, slot] / count
                variance = (
                    self.squares[feature, slot]
                    - self.sums[feature, slot] ** 2 / count
                ) / (count - 1)
                if variance > 1e-12:
                    scores[feature] = (float(value) - mean) / math.sqrt(variance)
            position = int(self.positions[feature, slot])
            if count >= NORMALIZATION_OBSERVATIONS:
                old = float(self.values[feature, slot, position])
                self.sums[feature, slot] -= old
                self.squares[feature, slot] -= old * old
            else:
                self.counts[feature, slot] = count + 1
            stored = float(np.float32(value))
            self.values[feature, slot, position] = stored
            self.sums[feature, slot] += stored
            self.squares[feature, slot] += stored * stored
            self.positions[feature, slot] = (
                position + 1
            ) % NORMALIZATION_OBSERVATIONS
        return scores


def _feature_rows():
    last_ts = None
    for attempt in range(4):
        resume = (
            ""
            if last_ts is None
            else f" AND timestamp>'{_iso_second(last_ts)}'"
        )
        query = f"{FEATURE_SELECT}{resume} ORDER BY timestamp"
        url = "http://127.0.0.1:9000/exp?query=" + urllib.parse.quote(query)
        try:
            with urllib.request.urlopen(url, timeout=3600) as response:
                reader = csv.reader(line.decode("utf-8") for line in response)
                next(reader)
                for row in reader:
                    last_ts = int(row[0]) // 1_000_000
                    yield row
            return
        except (http.client.IncompleteRead, TimeoutError) as error:
            print(
                f"S5 feature export interrupted after {last_ts}; "
                f"resuming ({attempt + 1}/4): {error}",
                flush=True,
            )
    raise RuntimeError("S5 feature export did not complete after four attempts")


def _complete_active(active, ts, mid, completed):
    keep = []
    for event in active:
        offset = ts - event["signal_ts"]
        if offset == 1:
            event["entry_ts"] = ts
            event["entry_mid"] = mid
        if offset in (2, 16, 61):
            event[f"exit_{offset - 1}_mid"] = mid
        if offset == 61:
            if all(
                key in event
                for key in (
                    "entry_mid",
                    "exit_1_mid",
                    "exit_15_mid",
                    "exit_60_mid",
                )
            ):
                completed.append(event)
        else:
            keep.append(event)
    return keep


def _volume_candidates(cache_key):
    path = Path(data.CACHE_DIR) / f"nq_s5_volume_candidates.{cache_key}.json.gz"
    if path.exists():
        return _read_gzip_json(path)

    Path(data.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    normalizer = ClockNormalizer()
    recent = {"BID": deque(), "ASK": deque()}
    active = []
    completed = []
    previous_ts = None
    processed = 0

    for row in _feature_rows():
        processed += 1
        ts = int(row[0]) // 1_000_000
        if previous_ts is not None and ts < previous_ts:
            raise RuntimeError(
                "QuestDB feature chunks were not in designated timestamp order"
            )
        mid = _number(row[1])
        spread = _number(row[2])
        valid_flag = (
            row[14]
            if isinstance(row[14], bool)
            else str(row[14]).lower() == "true"
        )
        valid = valid_flag and mid > 0.0 and spread > 0.0
        contiguous = previous_ts is not None and ts == previous_ts + 1
        if not contiguous:
            active.clear()
            recent["BID"].clear()
            recent["ASK"].clear()
        active = _complete_active(active, ts, mid, completed) if valid else []

        second = ts % 86_400
        if not ClockNormalizer.FIRST <= second < ClockNormalizer.LAST:
            previous_ts = ts
            continue

        bid_add = _number(row[6])
        bid_cancel = _number(row[7])
        ask_add = _number(row[8])
        ask_cancel = _number(row[9])
        scores = normalizer.score_and_update(
            second,
            (bid_add, bid_cancel, ask_add, ask_cancel),
        )
        bid_add_z, bid_cancel_z, ask_add_z, ask_cancel_z = scores
        in_window = valid and ENTRY_FROM_SECOND <= second <= ENTRY_TO_SECOND

        side_values = (
            (
                "BID",
                execution.SHORT,
                bid_add,
                bid_add_z,
                bid_cancel,
                bid_cancel_z,
            ),
            (
                "ASK",
                execution.LONG,
                ask_add,
                ask_add_z,
                ask_cancel,
                ask_cancel_z,
            ),
        )
        for book_side, trade_side, add, add_z, cancel, cancel_z in side_values:
            history = recent[book_side]
            while history and history[0]["ts"] < ts - CONFIRMATION_LAG_SECONDS:
                history.popleft()
            if in_window and cancel_z is not None and cancel_z >= CACHE_Z and history:
                layer = max(history, key=lambda item: item["z"])
                if layer["z"] >= CACHE_Z:
                    active.append(
                        {
                            "signal_ts": ts,
                            "book_side": book_side,
                            "side": trade_side,
                            "layer_ts": layer["ts"],
                            "layer_z": round(layer["z"], 7),
                            "cancel_z": round(cancel_z, 7),
                            "derived_layer_volume": layer["volume"],
                            "derived_cancel_volume": cancel,
                            "layer_mid": layer["mid"],
                            "layer_spread": layer["spread"],
                            "mid": mid,
                            "spread": spread,
                            "top1_imbalance": _number(row[3]),
                            "top5_imbalance": _number(row[4]),
                            "top10_imbalance": _number(row[5]),
                            "trade_delta": _number(row[12]),
                            "aggressive_volume": _number(row[10]) + _number(row[11]),
                            "depth_weighted_distance": _number(row[13]),
                        }
                    )
            if add_z is not None and add_z >= CACHE_Z and valid:
                history.append(
                    {
                        "ts": ts,
                        "z": float(add_z),
                        "volume": add,
                        "mid": mid,
                        "spread": spread,
                    }
                )

        previous_ts = ts
        if processed % 1_000_000 == 0:
            print(
                f"S5 feature scan: {processed:,} seconds, "
                f"{len(completed):,} completed broad candidates",
                flush=True,
            )

    print(
        f"S5 feature scan complete: {processed:,} seconds, "
        f"{len(completed):,} completed broad candidates",
        flush=True,
    )
    _gzip_json(path, completed)
    return completed


def _iso_second(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000000000Z"
    )


def _raw_query(day, seconds):
    lower = f"{day}T09:30:00.000000000Z"
    upper_day = (
        datetime.fromisoformat(day).date() + timedelta(days=1)
    ).isoformat()
    upper = f"{upper_day}T00:00:00.000000000Z"
    # TIMESTAMP_NS casts to nanoseconds.  Full-session partitioned lags over
    # tens of millions of depth updates eventually make QuestDB close later
    # requests.  Read only each wanted second plus a five-second causal seed,
    # then keep lags inside contiguous sampled islands.
    wanted = ",".join(str(ts * 1_000_000_000) for ts in seconds)
    expanded_seconds = sorted(
        {
            ts - offset
            for ts in seconds
            for offset in range(RAW_SEED_SECONDS + 1)
        }
    )
    expanded = ",".join(
        str(ts * 1_000_000_000) for ts in expanded_seconds
    )
    return f"""
WITH sampled AS (
 SELECT timestamp,sequence,side,price,size,stream_id,price_level,
        cast(timestamp AS LONG) event_ns
 FROM dbento_nq_depth
 WHERE timestamp>='{lower}' AND timestamp<'{upper}'
   AND cast(date_trunc('second',timestamp) AS LONG) IN ({expanded})
), marked AS (
 SELECT *,
        lag(event_ns) OVER (ORDER BY timestamp,sequence) prior_event_ns
 FROM sampled
), islanded AS (
 SELECT *,
        sum(
            CASE WHEN prior_event_ns IS NULL
                       OR event_ns-prior_event_ns>1000000000
                 THEN 1 ELSE 0 END
        ) OVER (
            ORDER BY timestamp,sequence
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) island
 FROM marked
), ordered AS (
 SELECT timestamp,sequence,side,price,size,
        lag(size) OVER (
            PARTITION BY island,stream_id,side,price_level
            ORDER BY timestamp,sequence
        ) prior_size
 FROM islanded
), changed AS (
 SELECT timestamp,side,price,
        CASE WHEN prior_size IS NULL THEN 0 ELSE size-prior_size END delta
 FROM ordered
), wanted AS (
 SELECT * FROM changed
 WHERE cast(date_trunc('second',timestamp) AS LONG) IN ({wanted})
)
SELECT cast(date_trunc('second',timestamp) AS LONG) ts,
       sum(CASE WHEN side='BID' AND delta>0 THEN delta ELSE 0 END),
       sum(CASE WHEN side='BID' AND delta<0 THEN -delta ELSE 0 END),
       sum(CASE WHEN side='ASK' AND delta>0 THEN delta ELSE 0 END),
       sum(CASE WHEN side='ASK' AND delta<0 THEN -delta ELSE 0 END),
       sum(CASE WHEN side='BID' AND delta>0 THEN price*delta ELSE 0 END),
       sum(CASE WHEN side='BID' AND delta<0 THEN price*(-delta) ELSE 0 END),
       sum(CASE WHEN side='ASK' AND delta>0 THEN price*delta ELSE 0 END),
       sum(CASE WHEN side='ASK' AND delta<0 THEN price*(-delta) ELSE 0 END)
FROM wanted
GROUP BY ts
ORDER BY ts
""".strip()


def _day_distances(day, seconds, fingerprint):
    key = _digest(
        f"s5-distance-v2:seed={RAW_SEED_SECONDS}:{day}:"
        f"{','.join(map(str, seconds))}:{fingerprint}"
    )
    path = Path(data.CACHE_DIR) / f"nq_s5_distance_{day}.{key}.json.gz"
    if path.exists():
        return day, _read_gzip_json(path), "bounded_seed_cache"
    # Earlier runs used the exact full-session lag with a cache fingerprint
    # that also included the live BM tail.  The DBN depth and dbento candidate
    # populations are immutable and the decompressed candidate checkpoints are
    # identical, so those day rows remain valid and are strictly preferable to
    # the bounded fallback.
    legacy = [
        Path(candidate)
        for candidate in glob.glob(
            str(Path(data.CACHE_DIR) / f"nq_s5_distance_{day}.*.json.gz")
        )
        if Path(candidate) != path
    ]
    if legacy:
        cached = max(legacy, key=lambda candidate: candidate.stat().st_mtime)
        return day, _read_gzip_json(cached), "exact_full_session_cache"
    rows = []
    chunks = [
        seconds[index : index + RAW_SECONDS_PER_QUERY]
        for index in range(0, len(seconds), RAW_SECONDS_PER_QUERY)
    ]
    for chunk_index, chunk in enumerate(chunks, 1):
        error = None
        for attempt in range(1, 4):
            try:
                rows.extend(data.query(_raw_query(day, chunk)))
                break
            except (
                http.client.RemoteDisconnected,
                TimeoutError,
                urllib.error.URLError,
            ) as caught:
                error = caught
                print(
                    f"S5 raw retry {attempt}/3 for {day} "
                    f"chunk {chunk_index}/{len(chunks)}: {caught}",
                    flush=True,
                )
                time.sleep(5 * attempt)
        else:
            raise RuntimeError(
                f"S5 raw distance query failed for {day} "
                f"chunk {chunk_index}/{len(chunks)} after 3 attempts"
            ) from error
    _gzip_json(path, rows)
    return day, rows, "bounded_seed_query"


def _raw_distances(events, fingerprint):
    needed = {}
    for event in events:
        for ts in (event["layer_ts"], event["signal_ts"]):
            day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            needed.setdefault(day, set()).add(ts)

    merged = {}
    modes = {}
    jobs = sorted((day, sorted(seconds)) for day, seconds in needed.items())
    with ThreadPoolExecutor(max_workers=RAW_WORKERS) as pool:
        futures = {
            pool.submit(_day_distances, day, seconds, fingerprint): day
            for day, seconds in jobs
        }
        complete = 0
        for future in as_completed(futures):
            day, rows, mode = future.result()
            complete += 1
            modes[day] = mode
            for row in rows:
                # cast(TIMESTAMP_NS AS LONG) remains nanoseconds.
                merged[int(row[0]) // 1_000_000_000] = list(map(float, row[1:]))
            print(
                f"S5 raw distance replay: {complete}/{len(jobs)} sessions "
                f"(latest {day})",
                flush=True,
            )
    counts = {}
    for mode in modes.values():
        counts[mode] = counts.get(mode, 0) + 1
    return merged, {
        "sessions": len(modes),
        "modes": counts,
        "seed_seconds": RAW_SEED_SECONDS,
        "coverage_gate": [MIN_RAW_COVERAGE, MAX_RAW_COVERAGE],
    }


def _touch_distance(side, mid, spread, weighted_price):
    if side == "BID":
        touch = mid - spread / 2
        return max(0.0, (touch - weighted_price) / TICK_SIZE)
    touch = mid + spread / 2
    return max(0.0, (weighted_price - touch) / TICK_SIZE)


def load_events():
    fingerprint = _cache_fingerprint()
    cache_key = _digest(f"s5-events-v2:{FEATURE_SELECT}:{fingerprint}")
    final_path = Path(data.CACHE_DIR) / f"nq_s5_events.{cache_key}.json.gz"
    metadata_path = Path(data.CACHE_DIR) / f"nq_s5_events.{cache_key}.meta.json"
    if final_path.exists():
        metadata = (
            json.loads(metadata_path.read_text())
            if metadata_path.exists()
            else {"source": "final_event_cache"}
        )
        return _read_gzip_json(final_path), fingerprint, metadata

    candidates = [
        event
        for event in _volume_candidates(cache_key)
        if event["layer_z"] >= min(Definition().grid["layer_z"])
        and event["cancel_z"] >= FIXED_CANCEL_Z
        and event["derived_cancel_volume"]
        >= min(Definition().grid["cancel_ratio"])
        * event["derived_layer_volume"]
    ]
    print(
        f"S5 raw prefilter: {len(candidates):,} candidates can reach "
        "the minimum grid cell",
        flush=True,
    )
    raw, raw_metadata = _raw_distances(candidates, fingerprint)
    out = []
    dropped_missing = 0
    dropped_coverage = 0
    for event in candidates:
        layer = raw.get(event["layer_ts"])
        cancel = raw.get(event["signal_ts"])
        if layer is None or cancel is None:
            dropped_missing += 1
            continue
        if event["book_side"] == "BID":
            layer_volume, cancel_volume = layer[0], cancel[1]
            layer_weighted = layer[4] / layer_volume if layer_volume > 0 else 0.0
            cancel_weighted = cancel[5] / cancel_volume if cancel_volume > 0 else 0.0
        else:
            layer_volume, cancel_volume = layer[2], cancel[3]
            layer_weighted = layer[6] / layer_volume if layer_volume > 0 else 0.0
            cancel_weighted = cancel[7] / cancel_volume if cancel_volume > 0 else 0.0
        if layer_volume <= 0 or cancel_volume <= 0:
            dropped_missing += 1
            continue
        layer_coverage = layer_volume / event["derived_layer_volume"]
        cancel_coverage = cancel_volume / event["derived_cancel_volume"]
        if not (
            MIN_RAW_COVERAGE <= layer_coverage <= MAX_RAW_COVERAGE
            and MIN_RAW_COVERAGE <= cancel_coverage <= MAX_RAW_COVERAGE
        ):
            dropped_coverage += 1
            continue
        event["layer_volume"] = round(layer_volume, 4)
        event["cancel_volume"] = round(cancel_volume, 4)
        event["layer_volume_coverage"] = round(layer_coverage, 6)
        event["cancel_volume_coverage"] = round(cancel_coverage, 6)
        event["layer_distance_ticks"] = round(
            _touch_distance(
                event["book_side"],
                event["layer_mid"],
                event["layer_spread"],
                layer_weighted,
            ),
            6,
        )
        event["cancel_distance_ticks"] = round(
            _touch_distance(
                event["book_side"],
                event["mid"],
                event["spread"],
                cancel_weighted,
            ),
            6,
        )
        out.append(event)
    raw_metadata.update(
        {
            "eligible_candidates": len(candidates),
            "retained_events": len(out),
            "dropped_missing_raw": dropped_missing,
            "dropped_coverage": dropped_coverage,
        }
    )
    _gzip_json(final_path, out)
    metadata_path.write_text(json.dumps(raw_metadata, indent=2) + "\n")
    return out, fingerprint, raw_metadata


def event_fills(events, params, horizon=PRIMARY_HORIZON_SECONDS, spread=0.2):
    """Fixed-horizon, one-position-at-a-time fills."""
    fills = []
    occupied = []
    free_at = -1
    exit_key = f"exit_{horizon}_mid"
    for event in selected_events(events, params):
        if event["entry_ts"] < free_at:
            continue
        sign = 1.0 if event["side"] == execution.LONG else -1.0
        points = sign * (event[exit_key] - event["entry_mid"]) - spread
        fill = execution.Fill(
            event["entry_ts"],
            event["entry_ts"] + horizon,
            event["side"],
            points,
            event["entry_mid"],
            RISK_STOP_POINTS,
        )
        fills.append(fill)
        occupied.append((fill, event))
        free_at = fill.exit_ts
    return fills, occupied


def inside(fills, lo, hi):
    return [fill for fill in fills if lo <= fill.entry_ts < hi]


def point_stats(fills, lo, hi):
    points = [fill.points for fill in fills]
    wins = sum(value for value in points if value > 0)
    losses = -sum(value for value in points if value < 0)
    low, high = walkforward.bootstrap_edge(points, seed=20260731)
    months = {}
    counts = {}
    for fill in fills:
        key = metrics.month_key(fill.entry_ts)
        months[key] = months.get(key, 0.0) + fill.points
        counts[key] = counts.get(key, 0) + 1
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "edge": round(sum(points) / len(points), 6) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "edge_ci95": [round(low, 6), round(high, 6)],
        "pf_points": round(wins / losses, 4)
        if losses
        else (999.0 if wins else 0.0),
        "point_months": {
            key: round(value, 2)
            for key, value in metrics.pad(months, lo, hi).items()
        },
        "trade_months": {
            key: counts.get(key, 0)
            for key in metrics.pad(months, lo, hi)
        },
    }


def score(fills, ex, lo, hi):
    selected = inside(fills, lo, hi)
    return {
        **metrics.stats(
            execution.size(selected, replace(ex, initial=INITIAL)),
            initial=INITIAL,
            span=(lo, hi),
        ),
        **point_stats(selected, lo, hi),
    }


def build_cells(definition, events):
    axes = sorted(definition.grid)
    out = {}
    for values in itertools.product(*(definition.grid[axis] for axis in axes)):
        params = dict(zip(axes, values))
        out[search.freeze(params)] = event_fills(
            events, definition.all_params(params)
        )[0]
    return out


def anchored_walkforward(definition, cells, ex, data_lo, record):
    if record:
        charged = trials.record(
            NAME,
            len(cells),
            f"walkforward, {len(walkforward.FOLDS)} folds, 3 axes",
        )
    else:
        charged = trials.total(NAME)
    picks, stitched, stitched_points, rows = [], [], [], []
    equity = INITIAL
    for index, (_train_lo, train_hi, test_lo, test_hi) in enumerate(
        walkforward.fold_windows(), 1
    ):
        (key, train_stat, plateau), _n, _stats = walkforward.select(
            definition,
            cells,
            ex,
            None,
            train_hi,
            INITIAL,
            data_lo,
        )
        if key is None:
            print(f"  S5 fold {index}: no cell cleared -> flat", flush=True)
            rows.append((index, None, None, None, None, []))
            continue
        test_stat, sized = walkforward.window_stats(
            cells[key], ex, test_lo, test_hi, equity
        )
        points = walkforward.window_points(cells[key], test_lo, test_hi)
        picks.append(key)
        stitched.extend(sized)
        stitched_points.extend(points)
        equity += test_stat["pnl"]
        rows.append((index, dict(key), train_stat, test_stat, plateau, points))
        print(
            f"  S5 fold {index}: {dict(key)} -> "
            f"{sum(points):+.2f} pts / {len(points)} trades",
            flush=True,
        )
    return {
        "cells": cells,
        "ex": ex,
        "picks": picks,
        "rows": rows,
        "stitched": sorted(stitched),
        "n_cells": len(cells),
        "stitched_points": stitched_points,
        "trials": charged,
        "initial": INITIAL,
        "strategy": definition,
        "oos_span": (
            metrics.split_ts(walkforward.FOLDS[0][0]),
            metrics.split_ts(walkforward.FOLDS[-1][1]),
        ),
    }


def run_purged_cv(definition, cells, ex, record):
    if record:
        trials.record(NAME, len(cells), "purged CV, 6 blocks")
    blocks = []
    for lo_iso, hi_iso in purgedcv_module_blocks():
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        key = purged_cv_module.select_on(
            definition,
            cells,
            lo,
            hi,
            walkforward.MIN_TRADES_PER_MONTH * 12,
            ex,
        )
        if key is None:
            blocks.append(
                {
                    "block": lo_iso,
                    "params": None,
                    "points": 0.0,
                    "trades": 0,
                    "t": 0.0,
                }
            )
            continue
        points = walkforward.window_points(cells[key], lo, hi)
        blocks.append(
            {
                "block": lo_iso,
                "params": dict(key),
                "points": round(sum(points), 2),
                "trades": len(points),
                "t": round(walkforward.edge_t(points), 4),
            }
        )
    values = [row["points"] for row in blocks]
    return {
        "blocks": blocks,
        "mean": sum(values) / len(values),
        "worst": min(values),
        "positive": sum(value > 0 for value in values),
    }


def purgedcv_module_blocks():
    return purged_cv_module.BLOCKS


FILTERS = (
    "baseline",
    "long_only",
    "short_only",
    "skip_mon",
    "skip_tue",
    "skip_wed",
    "skip_thu",
    "skip_fri",
    "open_only",
    "mid_only",
    "late_only",
    "skip_open",
    "skip_mid",
    "skip_late",
)


def filter_fills(fills, name):
    weekdays = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
    if name == "baseline":
        return fills
    if name == "long_only":
        return [fill for fill in fills if fill.side == execution.LONG]
    if name == "short_only":
        return [fill for fill in fills if fill.side == execution.SHORT]
    if name.startswith("skip_") and name[5:] in weekdays:
        weekday = weekdays[name[5:]]
        return [
            fill
            for fill in fills
            if (fill.entry_ts // 86_400 + 3) % 7 != weekday
        ]
    windows = {
        "open_only": lambda minute: minute < 660,
        "mid_only": lambda minute: 660 <= minute < 840,
        "late_only": lambda minute: minute >= 840,
        "skip_open": lambda minute: minute >= 660,
        "skip_mid": lambda minute: minute < 660 or minute >= 840,
        "skip_late": lambda minute: minute < 840,
    }
    return [
        fill
        for fill in fills
        if windows[name]((fill.entry_ts % 86_400) // 60)
    ]


def fold_improvements(candidate, baseline):
    rows = []
    improved = 0
    for _train_lo, _train_hi, lo, hi in walkforward.fold_windows():
        left = sum(fill.points for fill in baseline if lo <= fill.entry_ts < hi)
        right = sum(fill.points for fill in candidate if lo <= fill.entry_ts < hi)
        improved += right > left
        rows.append(
            {
                "month": metrics.month_key(lo),
                "baseline_points": round(left, 2),
                "filter_points": round(right, 2),
                "delta_points": round(right - left, 2),
            }
        )
    return improved, rows


def inverse_atr_fills(fills, atr, target):
    adjusted = []
    multipliers = []
    for fill in fills:
        current = atr.get(fill.entry_ts // 86_400)
        multiplier = (
            max(0.5, min(1.5, target / current))
            if current is not None and current > 0
            else 1.0
        )
        multipliers.append(multiplier)
        adjusted.append(replace(fill, stop=fill.stop / multiplier))
    return adjusted, multipliers


class StandardisedLogistic:
    def fit(self, features, labels):
        matrix = np.asarray(features, dtype=float)
        target = np.asarray(labels, dtype=float)
        self.mean = matrix.mean(axis=0)
        self.scale = matrix.std(axis=0)
        self.scale[self.scale == 0.0] = 1.0
        design = np.column_stack(
            [(matrix - self.mean) / self.scale, np.ones(len(matrix))]
        )
        weights = np.zeros(design.shape[1], dtype=float)
        penalty = np.zeros_like(weights)
        penalty[:-1] = 1.0 / ML_C
        for _ in range(30):
            linear = np.clip(design @ weights, -35.0, 35.0)
            probability = 1.0 / (1.0 + np.exp(-linear))
            gradient = design.T @ (probability - target) + penalty * weights
            curvature = probability * (1.0 - probability)
            hessian = design.T @ (design * curvature[:, None])
            hessian += np.diag(penalty + 1e-9)
            step = np.linalg.solve(hessian, gradient)
            weights -= step
            if float(np.max(np.abs(step))) < 1e-8:
                break
        self.weights = weights
        return self

    def predict_proba(self, features):
        matrix = np.asarray(features, dtype=float)
        design = np.column_stack(
            [(matrix - self.mean) / self.scale, np.ones(len(matrix))]
        )
        linear = np.clip(design @ self.weights, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-linear))


@dataclass(frozen=True)
class MLRow:
    fill: execution.Fill
    features: tuple


def ml_features(event, atr):
    minute = (event["entry_ts"] % 86_400) // 60
    weekday = (event["entry_ts"] // 86_400 + 3) % 7
    session_angle = 2 * math.pi * (minute - 570) / 390
    weekday_angle = 2 * math.pi * weekday / 5
    side = 1.0 if event["side"] == execution.LONG else -1.0
    imbalance = (
        event["trade_delta"] / event["aggressive_volume"]
        if event["aggressive_volume"] > 0
        else 0.0
    )
    causal_atr = atr.get(event["entry_ts"] // 86_400, 0.0)
    return (
        event["layer_z"],
        event["cancel_z"],
        math.log1p(event["layer_volume"]),
        math.log1p(event["cancel_volume"]),
        event["cancel_volume"] / event["layer_volume"],
        event["layer_distance_ticks"],
        event["cancel_distance_ticks"],
        event["spread"],
        event["top5_imbalance"],
        imbalance,
        event["depth_weighted_distance"],
        side,
        math.sin(session_angle),
        math.cos(session_angle),
        math.sin(weekday_angle),
        math.cos(weekday_angle),
        math.log1p(causal_atr),
    )


def fit_model(rows):
    labels = np.asarray([row.fill.points > 0 for row in rows], dtype=int)
    if len(rows) < 200 or len(set(labels.tolist())) < 2:
        return None
    return StandardisedLogistic().fit(
        [row.features for row in rows],
        labels,
    )


def predict(model, rows):
    if model is None or not rows:
        return []
    probabilities = model.predict_proba([row.features for row in rows])
    return [
        row
        for row, probability in zip(rows, probabilities)
        if probability >= ML_THRESHOLD
    ]


def matched_random_control(folds, model_points, draws=1_000):
    rng = random.Random(20260731)
    totals = []
    for _ in range(draws):
        total = 0.0
        for base, keep in folds:
            sample = rng.sample(base, keep) if keep else []
            total += sum(row.fill.points for row in sample)
        totals.append(total)
    return {
        "draws": draws,
        "model_points": round(model_points, 2),
        "random_mean_points": round(sum(totals) / len(totals), 2),
        "random_p_ge_model": round(
            (1 + sum(value >= model_points for value in totals))
            / (draws + 1),
            4,
        ),
    }


def ml_research(rows, ex, oos_lo, oos_hi):
    folds = []
    baseline_oos = []
    model_oos = []
    controls = []
    for index, (_train_lo, train_hi, test_lo, test_hi) in enumerate(
        walkforward.fold_windows(), 1
    ):
        train = [row for row in rows if row.fill.exit_ts < train_hi]
        test = [
            row for row in rows if test_lo <= row.fill.entry_ts < test_hi
        ]
        chosen = predict(fit_model(train), test)
        baseline_oos.extend(test)
        model_oos.extend(chosen)
        controls.append((test, len(chosen)))
        base_points = sum(row.fill.points for row in test)
        chosen_points = sum(row.fill.points for row in chosen)
        folds.append(
            {
                "fold": index,
                "month": metrics.month_key(test_lo),
                "train": len(train),
                "baseline_trades": len(test),
                "selected_trades": len(chosen),
                "baseline_points": round(base_points, 2),
                "model_points": round(chosen_points, 2),
                "delta_points": round(chosen_points - base_points, 2),
            }
        )
    cv = []
    for lo_iso, hi_iso in purgedcv_module_blocks():
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        train = [
            row
            for row in rows
            if row.fill.exit_ts < lo - 86_400
            or row.fill.entry_ts >= hi + 86_400
        ]
        test = [row for row in rows if lo <= row.fill.entry_ts < hi]
        chosen = predict(fit_model(train), test)
        cv.append(
            {
                "block": lo_iso,
                "train": len(train),
                "test": len(test),
                "selected": len(chosen),
                "points": round(sum(row.fill.points for row in chosen), 2),
            }
        )
    base_fills = [row.fill for row in baseline_oos]
    chosen_fills = [row.fill for row in model_oos]
    model_points = sum(fill.points for fill in chosen_fills)
    return {
        "run": True,
        "model": "standardised Newton-solved L2 logistic regression",
        "C": ML_C,
        "threshold": ML_THRESHOLD,
        "features": [
            "layer_z",
            "cancel_z",
            "log_layer_volume",
            "log_cancel_volume",
            "cancel_layer_ratio",
            "layer_distance_ticks",
            "cancel_distance_ticks",
            "spread",
            "top5_imbalance",
            "trade_imbalance",
            "depth_weighted_distance",
            "side",
            "session_sin",
            "session_cos",
            "weekday_sin",
            "weekday_cos",
            "log_causal_atr20",
        ],
        "folds": folds,
        "baseline_oos": score(base_fills, ex, oos_lo, oos_hi),
        "model_oos": score(chosen_fills, ex, oos_lo, oos_hi),
        "purged_cv": cv,
        "matched_random_control": matched_random_control(controls, model_points),
        "positive_model_folds": sum(row["model_points"] > 0 for row in folds),
        "improved_folds": sum(row["delta_points"] > 0 for row in folds),
        "note": (
            "One fixed classifier, C, threshold, and feature set; no model "
            "family or probability-threshold search."
        ),
    }


def serialise_walkforward(wf, stitched, fixed, cv, gate, median):
    return {
        "folds": [
            {
                "fold": index,
                "params": params,
                "train": train,
                "test": test,
                "plateau_t": plateau,
                "test_points": round(sum(points), 2),
            }
            for index, params, train, test, plateau, points in wf["rows"]
        ],
        "stitched": stitched,
        "median_params": median,
        "median_fixed": fixed,
        "purged_cv": cv,
        "gate": {
            "passed": gate["passed"],
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in gate["checks"]
            ],
        },
    }


def run(out_path=None, record_trials=False):
    definition = Definition()
    ex = replace(definition.execution, initial=INITIAL)
    events, fingerprint, raw_metadata = load_events()
    data_lo = metrics.split_ts("2025-02-12")
    study_hi = metrics.split_ts("2026-07-17")
    oos_lo = metrics.split_ts(walkforward.FOLDS[0][0])
    oos_hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    effects = {}
    for horizon in HORIZONS_SECONDS:
        net = event_fills(
            events, definition.defaults, horizon, ex.entry_cost
        )[0]
        gross = event_fills(events, definition.defaults, horizon, 0.0)[0]
        effects[str(horizon)] = {
            "net": point_stats(inside(net, data_lo, study_hi), data_lo, study_hi),
            "gross": point_stats(
                inside(gross, data_lo, study_hi), data_lo, study_hi
            ),
        }
    primary = effects[str(PRIMARY_HORIZON_SECONDS)]["net"]
    kill_passed = (
        primary["edge"] > 0
        and primary["edge_t"] >= 2.0
        and primary["edge_ci95"][0] > 0
    )
    if record_trials:
        trials.record(NAME, 1, "pre-declared non-overlapping 15-second kill test")
        trials.record(NAME, 2, "fixed 1/60-second horizon diagnostics")

    default_fills, default_occupied = event_fills(
        events, definition.defaults, PRIMARY_HORIZON_SECONDS, ex.entry_cost
    )
    default_full = score(default_fills, ex, data_lo, study_hi)
    cells = build_cells(definition, events)
    wf = anchored_walkforward(
        definition, cells, ex, data_lo, record_trials
    )
    _reported_median, stitched = walkforward.report(wf)
    median = (
        walkforward.median_params(wf["picks"], definition)
        if wf["picks"]
        else definition.defaults
    )
    median_key = search.freeze(median)
    fixed_stat, _fixed_sized = walkforward.window_stats(
        cells[median_key], ex, oos_lo, oos_hi, INITIAL, span=(oos_lo, oos_hi)
    )
    fixed_stat.update(
        point_stats(inside(cells[median_key], oos_lo, oos_hi), oos_lo, oos_hi)
    )
    cv = run_purged_cv(definition, cells, ex, record_trials)

    median_fills = cells[median_key]
    baseline_oos = inside(median_fills, oos_lo, oos_hi)
    filters = {}
    for name in FILTERS:
        selected = inside(filter_fills(median_fills, name), oos_lo, oos_hi)
        improved, deltas = fold_improvements(selected, baseline_oos)
        filters[name] = {
            **score(selected, ex, oos_lo, oos_hi),
            "improved_folds": improved,
            "fold_deltas": deltas,
        }
    if record_trials:
        trials.record(
            NAME, len(FILTERS) - 1, "fixed OOS day/time/side diagnostics"
        )

    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        sizing[f"fixed_fraction_{risk * 100:.2f}%"] = score(
            median_fills, replace(ex, risk=risk), oos_lo, oos_hi
        )
    bars = data.load_cached_level_two_bars()
    atr = data.atr_by_day(bars, 20)
    prior_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < oos_lo
    )
    target_atr = prior_atr[len(prior_atr) // 2]
    inverse, multipliers = inverse_atr_fills(baseline_oos, atr, target_atr)
    sizing["causal_inverse_atr_0.50_to_1.50x"] = {
        **score(inverse, ex, oos_lo, oos_hi),
        "target_atr": round(target_atr, 4),
        "mean_multiplier": round(sum(multipliers) / len(multipliers), 4)
        if multipliers
        else 1.0,
    }
    if record_trials:
        trials.record(NAME, 4, "fixed OOS sizing diagnostics")

    costs = {}
    for spread in COSTS:
        adjusted = [
            replace(fill, points=fill.points + ex.entry_cost - spread)
            for fill in median_fills
        ]
        costs[str(spread)] = score(
            adjusted, replace(ex, spread=spread), oos_lo, oos_hi
        )
    if record_trials:
        trials.record(NAME, len(COSTS) - 1, "fixed execution-cost sensitivity")

    default_event_map = {
        (fill.entry_ts, fill.side): event for fill, event in default_occupied
    }
    ml_rows = [
        MLRow(fill, ml_features(default_event_map[(fill.entry_ts, fill.side)], atr))
        for fill in default_fills
        if (fill.entry_ts, fill.side) in default_event_map
    ]
    if len(ml_rows) >= ML_MINIMUM:
        ml = ml_research(ml_rows, ex, oos_lo, oos_hi)
        ml["observations"] = len(ml_rows)
        ml["minimum_observations"] = ML_MINIMUM
        if record_trials:
            trials.record(NAME, 1, "fixed L2-logistic meta-labeler")
    else:
        ml = {
            "run": False,
            "observations": len(ml_rows),
            "minimum_observations": ML_MINIMUM,
            "reason": "occupied population is below the pre-declared minimum",
        }

    cumulative_trials = trials.total(NAME)
    wf["trials"] = cumulative_trials
    gate = walkforward.gate(wf, stitched, cv["blocks"])
    overall_passed = kill_passed and gate["passed"]
    result = {
        "strategy": NAME,
        "status": "promoted" if overall_passed else "rejected",
        "git_sha": git_sha(),
        "data": {
            "eligible_min_grid_candidates": raw_metadata["eligible_candidates"],
            "retained_grid_eligible_events": len(events),
            "study_range": ["2025-02-12", "2026-07-16"],
            "oos_range": [
                walkforward.FOLDS[0][0],
                walkforward.FOLDS[-1][1],
            ],
            "raw_fingerprint": fingerprint,
            "raw_reconstruction": raw_metadata,
            "timestamp_convention": "New York wall clock encoded as UTC",
            "raw_tables_mutated": False,
        },
        "predeclared_definition": {
            "normalization": (
                "causal log1p volume z-score by second-of-session over the "
                "prior 20 observations"
            ),
            "confirmation_lag_seconds": CONFIRMATION_LAG_SECONDS,
            "direction": "canceled bid layer -> short; canceled ask layer -> long",
            "ambiguity": "skip seconds where both sides qualify",
            "entry": "next contiguous valid second midprice",
            "exit": f"fixed {PRIMARY_HORIZON_SECONDS}-second hold",
            "risk_sizing_stop_points": RISK_STOP_POINTS,
            "spread_points": ex.spread,
            "ml_minimum": ML_MINIMUM,
        },
        "grid": definition.grid,
        "kill_test": {
            "passed": kill_passed,
            "rule": (
                "default 15-second net edge > 0, t >= 2, and bootstrap "
                "95% CI excludes zero"
            ),
            "horizons": effects,
        },
        "default_full_sample": default_full,
        "walkforward": serialise_walkforward(
            wf, stitched, fixed_stat, cv, gate, median
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "ml": ml,
        "cumulative_trials": cumulative_trials,
        "notes": [
            "All QuestDB access was read-only; caches live under sandbox/.cache.",
            "No server, feature builder, or raw table was changed.",
            "Calendar/side filters and sizing rows inspect OOS and are not adopted.",
            "The ML experiment uses one fixed model and cannot authorize a retry.",
            "Promotion requires both the pre-grid kill test and the standard walk-forward/CV gate.",
            "Databento ends 2026-07-16, so July 2026 is partial.",
        ],
    }

    print("\nS5 fixed non-overlapping effects")
    for horizon in HORIZONS_SECONDS:
        net = effects[str(horizon)]["net"]
        gross = effects[str(horizon)]["gross"]
        print(
            f"  {horizon:>2}s n={net['trades']:>6} "
            f"gross_edge={gross['edge']:+.4f} "
            f"net_edge={net['edge']:+.4f} net_t={net['edge_t']:+.2f}"
        )
    print(f"  kill test: {'PASS' if kill_passed else 'FAIL'}")
    print("\nS5 fixed filters (diagnostic only)")
    for name, stat in filters.items():
        print(
            f"  {name:<13} n={stat['trades']:>6} "
            f"pts={stat['points']:>10.2f} improved={stat['improved_folds']}/12"
        )
    print("\nS5 sizing")
    for name, stat in sizing.items():
        print(
            f"  {name:<36} pnl={stat['pnl']:>10.2f} "
            f"dd={stat['max_dd']:>10.2f} pf={stat['pf']:>6.3f}"
        )
    if ml["run"]:
        print(
            "\nS5 ML OOS: "
            f"baseline {ml['baseline_oos']['points']:+.2f} pts / "
            f"{ml['baseline_oos']['trades']} trades; "
            f"model {ml['model_oos']['points']:+.2f} pts / "
            f"{ml['model_oos']['trades']} trades; "
            f"improved {ml['improved_folds']}/12 folds"
        )
    print(f"\nS5 verdict: {result['status'].upper()}")
    if out_path:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    run(args.out, args.record_trials)


if __name__ == "__main__":
    main()
