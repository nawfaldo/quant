"""Leakage-controlled ML meta-labeler for A2 Book Slope Asymmetry.

This is intentionally one fixed experiment, not a model tournament:

* standardized L2-logistic regression, C=0.1;
* fixed 0.5 decision threshold and fixed feature set;
* labels enter training only after their trades have exited;
* twelve anchored monthly test folds and six purged regime blocks;
* dynamic occupancy, so rejecting a trade can expose a later opportunity;
* matched-count random filtering as a sanity check.

The model may reject continuation entries but may not reverse them.  That keeps
the ML question narrow: can state known at signal time identify which of the
original A2 entries are worth taking?
"""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward
from sandbox.data import C, H, L, O, TS
from sandbox.strategies.book_slope_asymmetry import WINDOWS

STRATEGY_NAME = "Book Slope Asymmetry"
MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260729
RANDOM_CONTROLS = 5_000

FEATURE_NAMES = (
    "abs_slope",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_trade_delta",
    "signed_price_change",
    "signed_replenishment",
    "toxicity",
    "log_trade_count",
    "log_depth_events",
    "asinh_depth_weighted_distance",
    "spread",
    "session_progress",
)


@dataclass(frozen=True)
class Candidate:
    signal_index: int
    exit_index: int
    fill: execution.Fill
    features: tuple[float, ...]


@dataclass(frozen=True)
class FoldResult:
    month: str
    train: int
    candidates: int
    baseline_trades: int
    model_trades: int
    baseline_points: float
    model_points: float


def fixed_params(strategy):
    params = strategy.all_params()
    expected = {
        "slope_cut": 0.07,
        "entry_window": "all",
        "stop": 20,
        "rr": 1.5,
        "time_stop": 10,
        "max_spread": 1.25,
        "confirmation": "none",
    }
    drift = {key: (params.get(key), value) for key, value in expected.items()
             if params.get(key) != value}
    if drift:
        raise RuntimeError(f"ML population changed: {drift}")
    return params


def model_features(bar, feature, slope, side, entry_from, entry_to):
    """Measurements available at the signal-minute close only."""
    sign = 1.0 if side == execution.LONG else -1.0
    aggressive = feature["aggressive_buy_volume"] + feature["aggressive_sell_volume"]
    toxicity = abs(feature["trade_delta"]) / aggressive if aggressive > 0 else 0.0
    minute = (bar[TS] % 86_400) // 60
    return (
        abs(slope),
        sign * feature["top1_imbalance"],
        sign * feature["top5_imbalance"],
        sign * feature["top10_imbalance"],
        sign * (feature["microprice"] - feature["midprice"]) / 0.25,
        math.asinh(sign * feature["trade_delta"] / 100.0),
        math.asinh(sign * feature["price_change"] / 2.0),
        sign * feature["replenishment_score"],
        toxicity,
        math.log1p(feature["trade_count"]),
        math.log1p(feature["depth_event_count"]),
        math.asinh(feature["depth_weighted_distance"]),
        feature["spread"],
        (minute - entry_from) / max(1, entry_to - entry_from),
    )


def resolve_candidate(bars, entry_index, side, params, spread, session_end):
    entry_bar = bars[entry_index]
    entry = entry_bar[O]
    entry_minute = (entry_bar[TS] % 86_400) // 60
    stop = params["stop"]
    target = stop * params["rr"]
    half = spread / 2.0

    # ``execution.resolve`` may flatten on the entry bar itself when it is the
    # feed's last bar before the session boundary.
    nxt = bars[entry_index + 1] if entry_index + 1 < len(bars) else None
    if (
        entry_minute < session_end
        and (
            nxt is None
            or nxt[TS] // 86_400 != entry_bar[TS] // 86_400
            or (nxt[TS] % 86_400) // 60 >= session_end
        )
    ):
        exit_price = entry_bar[C]
        points = (
            (exit_price - half) - (entry + half)
            if side == execution.LONG
            else (entry - half) - (exit_price + half)
        )
        return entry_index, execution.Fill(
            entry_ts=entry_bar[TS], exit_ts=entry_bar[TS], side=side,
            points=points, price=entry, stop=stop,
        )

    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        minute = (bar[TS] % 86_400) // 60
        exit_price = None
        if side == execution.LONG:
            if bar[L] <= entry - stop:
                exit_price = min(bar[O], entry - stop)
            elif bar[H] >= entry + target:
                exit_price = max(bar[O], entry + target)
        else:
            if bar[H] >= entry + stop:
                exit_price = max(bar[O], entry + stop)
            elif bar[L] <= entry - target:
                exit_price = min(bar[O], entry - target)
        if exit_price is None and minute >= entry_minute + params["time_stop"]:
            exit_price = bar[O]
        if exit_price is None and minute < session_end:
            nxt = bars[index + 1] if index + 1 < len(bars) else None
            if (nxt is None or nxt[TS] // 86_400 != bar[TS] // 86_400
                    or (nxt[TS] % 86_400) // 60 >= session_end):
                exit_price = bar[C]
        if exit_price is None:
            continue
        points = ((exit_price - half) - (entry + half)
                  if side == execution.LONG
                  else (entry - half) - (exit_price + half))
        return index, execution.Fill(
            entry_ts=entry_bar[TS], exit_ts=bar[TS], side=side, points=points,
            price=entry, stop=stop,
        )
    raise RuntimeError(f"candidate at index {entry_index} never exits")


def candidate_pool(strategy, bars, features, context):
    params = fixed_params(strategy)
    entry_from, entry_to = WINDOWS[params["entry_window"]]
    out = []
    for index, row in enumerate(context["rows"]):
        if row is None or index + 1 >= len(bars):
            continue
        if bars[index + 1][TS] != bars[index][TS] + 60:
            continue
        minute = (bars[index][TS] % 86_400) // 60
        if not entry_from <= minute <= entry_to:
            continue
        slope, spread = row
        if spread > params["max_spread"]:
            continue
        if slope >= params["slope_cut"]:
            side = execution.LONG
        elif slope <= -params["slope_cut"]:
            side = execution.SHORT
        else:
            continue
        feature = features.get(bars[index][TS])
        if feature is None or not feature["book_valid"]:
            continue
        exit_index, fill = resolve_candidate(
            bars, index + 1, side, params, strategy.execution.entry_cost,
            strategy.execution.session_end_min,
        )
        out.append(Candidate(
            signal_index=index,
            exit_index=exit_index,
            fill=fill,
            features=model_features(
                bars[index], feature, slope, side, entry_from, entry_to
            ),
        ))
    return out


def apply_occupancy(candidates, scores=None):
    accepted = []
    free_from = -1
    for index, candidate in enumerate(candidates):
        if candidate.signal_index < free_from:
            continue
        if scores is not None and scores[index] < MODEL_THRESHOLD:
            continue
        accepted.append(candidate)
        free_from = candidate.exit_index
    return accepted


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=MODEL_C, class_weight="balanced", max_iter=1_000,
            random_state=MODEL_SEED,
        ),
    )


def fit_model(candidates):
    labels = np.asarray([row.fill.points > 0 for row in candidates])
    if len(np.unique(labels)) < 2:
        raise RuntimeError("training fold contains only one outcome class")
    model = make_model()
    model.fit(np.asarray([row.features for row in candidates]), labels)
    return model


def scores(model, candidates):
    if not candidates:
        return np.asarray([])
    return model.predict_proba(np.asarray([row.features for row in candidates]))[:, 1]


def walk_forward(pool, baseline):
    folds, base_oos, model_oos, pure_oos, random_groups = [], [], [], [], []
    for _train_lo, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.exit_ts < train_hi]
        test_pool = [row for row in pool if test_lo <= row.fill.entry_ts < test_hi]
        test_base = [row for row in baseline if test_lo <= row.fill.entry_ts < test_hi]
        model = fit_model(train)
        accepted = apply_occupancy(test_pool, scores(model, test_pool))
        # The random control uses the baseline population because those trades
        # have identical occupancy before filtering.
        pure_scores = scores(model, test_base)
        pure = [row for row, score in zip(test_base, pure_scores)
                if score >= MODEL_THRESHOLD]
        random_groups.append((test_base, len(pure)))
        folds.append(FoldResult(
            month=metrics.month_key(test_lo), train=len(train),
            candidates=len(test_pool), baseline_trades=len(test_base),
            model_trades=len(accepted),
            baseline_points=sum(row.fill.points for row in test_base),
            model_points=sum(row.fill.points for row in accepted),
        ))
        base_oos.extend(test_base)
        model_oos.extend(accepted)
        pure_oos.extend(pure)
    return folds, base_oos, model_oos, pure_oos, random_groups


def purged_blocks(pool, baseline):
    out, stitched = [], []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        purge_lo = lo - purged_cv.EMBARGO_DAYS * 86_400
        purge_hi = hi + purged_cv.EMBARGO_DAYS * 86_400
        train = [row for row in baseline
                 if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi]
        test = [row for row in pool if lo <= row.fill.entry_ts < hi]
        accepted = apply_occupancy(test, scores(fit_model(train), test))
        stitched.extend(accepted)
        out.append({
            "from": lo_iso, "to": hi_iso, "train": len(train),
            **point_stats(accepted),
        })
    return out, stitched


def point_stats(candidates):
    points = np.asarray([row.fill.points for row in candidates])
    wins, losses = points[points > 0], -points[points < 0]
    return {
        "trades": len(points),
        "points": round(float(points.sum()), 2),
        "edge": round(float(points.mean()), 4) if len(points) else 0.0,
        "pf": round(float(wins.sum() / losses.sum()), 4)
        if len(losses) and losses.sum() else 0.0,
    }


def sized_stats(candidates, strategy):
    lo, hi = metrics.split_ts(walkforward.FOLDS[0][0]), metrics.split_ts(
        walkforward.FOLDS[-1][1]
    )
    sized = execution.size([row.fill for row in candidates], strategy.execution)
    return metrics.stats(sized, initial=strategy.execution.initial, span=(lo, hi))


def random_control(groups, observed):
    rng = random.Random(MODEL_SEED)
    totals = []
    for _ in range(RANDOM_CONTROLS):
        totals.append(sum(
            row.fill.points
            for rows, keep in groups
            for row in rng.sample(rows, keep)
        ))
    return {
        "draws": RANDOM_CONTROLS,
        "mean": round(sum(totals) / len(totals), 2),
        "p_ge_model": round(sum(value >= observed for value in totals) / len(totals), 4),
    }


def verify_replica(strategy, bars, context, baseline):
    expected = execution.resolve(
        bars, strategy.signals(bars, context, "all", fixed_params(strategy)),
        strategy.execution,
    )
    actual = [row.fill for row in baseline]
    if len(expected) != len(actual):
        raise RuntimeError(f"replica mismatch: {len(expected)} != {len(actual)}")
    for left, right in zip(expected, actual):
        if (left.entry_ts, left.exit_ts, left.side) != (
                right.entry_ts, right.exit_ts, right.side):
            raise RuntimeError(f"replica diverged: expected {left}, actual {right}")


def run(out_path=None):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=1_000.0)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    features = data.load_l2_features(strategy.symbol)
    context = strategy.context()
    pool = candidate_pool(strategy, bars, features, context)
    baseline = apply_occupancy(pool)
    verify_replica(strategy, bars, context, baseline)
    trials.record(STRATEGY_NAME, 1, "fixed L2-logistic meta-labeler")

    folds, base_oos, model_oos, pure_oos, random_groups = walk_forward(pool, baseline)
    blocks, block_oos = purged_blocks(pool, baseline)
    base_points, model_points = point_stats(base_oos), point_stats(model_oos)
    pure_points = point_stats(pure_oos)
    base_sized, model_sized = sized_stats(base_oos, strategy), sized_stats(model_oos, strategy)
    control = random_control(random_groups, pure_points["points"])
    values = [row.fill.points for row in model_oos]
    ci_lo, ci_hi = walkforward.bootstrap_edge(values)
    t = walkforward.edge_t(values)
    cumulative = trials.total(STRATEGY_NAME)
    deflated = walkforward.deflated_t(t, cumulative)
    positive_folds = sum(row.model_points > 0 for row in folds)
    block_values = [row["points"] for row in blocks]
    block_mean = sum(block_values) / len(block_values)
    checks = [
        ("stitched OOS PnL > 0", model_sized["pnl"] > 0),
        ("profitable folds >= 2/3", positive_folds >= math.ceil(2 * len(folds) / 3)),
        ("edge CI excludes zero", ci_lo > 0),
        ("deflated t > 0", deflated > 0),
        ("purged-CV coverage", min(block_values) >= -2 * abs(block_mean)),
        ("random-filter p < 0.05", control["p_ge_model"] < 0.05),
    ]
    result = {
        "strategy": STRATEGY_NAME,
        "status": "promoted" if all(ok for _, ok in checks) else "rejected",
        "model": {
            "family": "standardized_l2_logistic_regression",
            "regularization_c": MODEL_C,
            "threshold": MODEL_THRESHOLD,
            "features": list(FEATURE_NAMES),
        },
        "data_range": list(data.bar_range(strategy.bars, strategy.symbol)),
        "candidate_count": len(pool),
        "baseline_trade_count": len(baseline),
        "folds": [asdict(row) for row in folds],
        "baseline_oos_points": base_points,
        "model_oos_points": model_points,
        "pure_filter_oos_points": pure_points,
        "baseline_oos_sized": base_sized,
        "model_oos_sized": model_sized,
        "matched_random_control": control,
        "purged_cv": {
            "blocks": blocks,
            "stitched": point_stats(block_oos),
            "positive_blocks": sum(row["points"] > 0 for row in blocks),
        },
        "gate": {
            "passed": all(ok for _, ok in checks),
            "cumulative_trials": cumulative,
            "edge_t": round(t, 4),
            "deflated_t": round(deflated, 4),
            "edge_ci_95": [round(ci_lo, 4), round(ci_hi, 4)],
            "checks": [{"name": name, "passed": ok} for name, ok in checks],
        },
    }

    print(f"{STRATEGY_NAME}: {len(pool)} candidates, {len(baseline)} baseline trades")
    print("  month     train  cand  base_n  ml_n   base_pts    ml_pts")
    for row in folds:
        print(f"  {row.month} {row.train:>6} {row.candidates:>5} "
              f"{row.baseline_trades:>7} {row.model_trades:>5} "
              f"{row.baseline_points:>10.1f} {row.model_points:>9.1f}")
    print(f"\n  baseline OOS: {base_points}")
    print(f"  model OOS:    {model_points}")
    print(f"  model sized:  pnl={model_sized['pnl']:.2f}, "
          f"positive months={model_sized['pos_months']}/{model_sized['n_months']}")
    print(f"  random control: {control}")
    print(f"  purged CV: {result['purged_cv']['positive_blocks']}/6 positive, "
          f"{result['purged_cv']['stitched']['points']:+.1f} points")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"  => {result['status'].upper()}")

    if out_path:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    run(args.out)


if __name__ == "__main__":
    main()
