"""One leakage-controlled ML meta-labeler for A5 Air Pocket Fade.

This is deliberately one model, not a tournament:

* standardized L2-logistic regression, C=0.1;
* fixed 0.5 probability threshold and fixed signal-time feature set;
* labels enter anchored training only after the trade has exited;
* six held-out purged blocks provide an all-regime veto;
* rejected trades free occupancy, so later candidates are evaluated honestly;
* a matched-count random filter checks whether the model adds information.

The model may reject a default A5 fade but may not reverse it.  Day, time, and
side are represented in the fixed features rather than optimized as cutoffs.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import json
import math
import random
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

STRATEGY_NAME = "Air Pocket Fade"
MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260729
RANDOM_CONTROLS = 5_000

FEATURE_NAMES = (
    "move_z",
    "count_z",
    "volume_z",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_trade_delta",
    "signed_replenishment",
    "toxicity",
    "log_depth_events",
    "spread",
    "session_progress",
    "weekday_sin",
    "weekday_cos",
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
        "move_z": 1.0,
        "count_z": 0.0,
        "volume_z": 0.0,
        "stop": 10.0,
        "target": 10.0,
        "time_stop": 5,
        "max_spread": 1.25,
        "entry_from": 585,
        "entry_to": 930,
    }
    drift = {
        key: (params.get(key), value)
        for key, value in expected.items()
        if params.get(key) != value
    }
    if drift:
        raise RuntimeError(f"ML population changed: {drift}")
    return params


def model_features(bar, feature, row, side, entry_from, entry_to):
    """Only measurements available at the completed signal minute."""
    sign = 1.0 if side == execution.LONG else -1.0
    move_z, count_z, volume_z, _move, spread = row
    aggressive = (
        feature["aggressive_buy_volume"]
        + feature["aggressive_sell_volume"]
    )
    toxicity = (
        abs(feature["trade_delta"]) / aggressive
        if aggressive > 0.0
        else 0.0
    )
    minute = (bar[TS] % 86_400) // 60
    weekday = (bar[TS] // 86_400 + 3) % 7
    angle = 2.0 * math.pi * weekday / 5.0
    return (
        move_z,
        count_z,
        volume_z,
        sign * feature["top1_imbalance"],
        sign * feature["top5_imbalance"],
        sign * feature["top10_imbalance"],
        sign * (feature["microprice"] - feature["midprice"]) / 0.25,
        math.asinh(sign * feature["trade_delta"] / 100.0),
        sign * feature["replenishment_score"],
        toxicity,
        math.log1p(feature["depth_event_count"]),
        spread,
        (minute - entry_from) / max(1, entry_to - entry_from),
        math.sin(angle),
        math.cos(angle),
    )


def resolve_candidate(bars, entry_index, side, params, spread, session_end):
    """Resolve one hypothetical candidate with common-engine semantics."""
    entry_bar = bars[entry_index]
    entry = entry_bar[O]
    entry_minute = (entry_bar[TS] % 86_400) // 60
    half = spread / 2.0

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
            entry_ts=entry_bar[TS],
            exit_ts=entry_bar[TS],
            side=side,
            points=points,
            price=entry,
            stop=params["stop"],
        )

    for index in range(entry_index + 1, len(bars)):
        bar = bars[index]
        minute = (bar[TS] % 86_400) // 60
        exit_price = None
        if side == execution.LONG:
            if bar[L] <= entry - params["stop"]:
                exit_price = min(bar[O], entry - params["stop"])
            elif bar[H] >= entry + params["target"]:
                exit_price = max(bar[O], entry + params["target"])
        else:
            if bar[H] >= entry + params["stop"]:
                exit_price = max(bar[O], entry + params["stop"])
            elif bar[L] <= entry - params["target"]:
                exit_price = min(bar[O], entry - params["target"])
        if (
            exit_price is None
            and minute >= entry_minute + params["time_stop"]
        ):
            exit_price = bar[O]
        if exit_price is None and minute < session_end:
            nxt = bars[index + 1] if index + 1 < len(bars) else None
            if (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            ):
                exit_price = bar[C]
        if exit_price is None:
            continue
        points = (
            (exit_price - half) - (entry + half)
            if side == execution.LONG
            else (entry - half) - (exit_price + half)
        )
        return index, execution.Fill(
            entry_ts=entry_bar[TS],
            exit_ts=bar[TS],
            side=side,
            points=points,
            price=entry,
            stop=params["stop"],
        )
    raise RuntimeError(f"candidate at index {entry_index} never exits")


def candidate_pool(strategy, bars, feature_rows, context):
    params = fixed_params(strategy)
    out = []
    for index, row in enumerate(context["rows"]):
        if row is None or index + 1 >= len(bars):
            continue
        if bars[index + 1][TS] != bars[index][TS] + 60:
            continue
        minute = (bars[index][TS] % 86_400) // 60
        if not params["entry_from"] <= minute <= params["entry_to"]:
            continue
        move_z, count_z, volume_z, signed_move, spread = row
        if (
            move_z < params["move_z"]
            or count_z > params["count_z"]
            or volume_z > params["volume_z"]
            or spread > params["max_spread"]
        ):
            continue
        side = execution.SHORT if signed_move > 0.0 else execution.LONG
        feature = feature_rows.get(bars[index][TS])
        if feature is None or not feature["book_valid"]:
            continue
        exit_index, fill = resolve_candidate(
            bars,
            index + 1,
            side,
            params,
            strategy.execution.entry_cost,
            strategy.execution.session_end_min,
        )
        out.append(
            Candidate(
                signal_index=index,
                exit_index=exit_index,
                fill=fill,
                features=model_features(
                    bars[index],
                    feature,
                    row,
                    side,
                    params["entry_from"],
                    params["entry_to"],
                ),
            )
        )
    return out


def apply_occupancy(candidates, probabilities=None):
    accepted = []
    free_from = -1
    for index, candidate in enumerate(candidates):
        if candidate.signal_index < free_from:
            continue
        if (
            probabilities is not None
            and probabilities[index] < MODEL_THRESHOLD
        ):
            continue
        accepted.append(candidate)
        free_from = candidate.exit_index
    return accepted


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=MODEL_C,
            class_weight="balanced",
            max_iter=1_000,
            random_state=MODEL_SEED,
        ),
    )


def fit_model(candidates):
    labels = np.asarray([row.fill.points > 0.0 for row in candidates])
    if len(np.unique(labels)) < 2:
        raise RuntimeError("training fold contains only one outcome class")
    model = make_model()
    model.fit(
        np.asarray([row.features for row in candidates]),
        labels,
    )
    return model


def scores(model, candidates):
    if not candidates:
        return np.asarray([])
    return model.predict_proba(
        np.asarray([row.features for row in candidates])
    )[:, 1]


def walk_forward(pool, baseline):
    folds = []
    base_oos, model_oos, pure_oos, random_groups = [], [], [], []
    for _, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.exit_ts < train_hi]
        test_pool = [
            row for row in pool if test_lo <= row.fill.entry_ts < test_hi
        ]
        test_base = [
            row for row in baseline if test_lo <= row.fill.entry_ts < test_hi
        ]
        model = fit_model(train)
        accepted = apply_occupancy(test_pool, scores(model, test_pool))
        base_scores = scores(model, test_base)
        pure = [
            row
            for row, probability in zip(test_base, base_scores)
            if probability >= MODEL_THRESHOLD
        ]
        random_groups.append((test_base, len(pure)))
        folds.append(
            FoldResult(
                month=metrics.month_key(test_lo),
                train=len(train),
                candidates=len(test_pool),
                baseline_trades=len(test_base),
                model_trades=len(accepted),
                baseline_points=sum(row.fill.points for row in test_base),
                model_points=sum(row.fill.points for row in accepted),
            )
        )
        base_oos.extend(test_base)
        model_oos.extend(accepted)
        pure_oos.extend(pure)
    return folds, base_oos, model_oos, pure_oos, random_groups


def purged_blocks(pool, baseline):
    blocks, stitched = [], []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        purge_lo = lo - purged_cv.EMBARGO_DAYS * 86_400
        purge_hi = hi + purged_cv.EMBARGO_DAYS * 86_400
        train = [
            row
            for row in baseline
            if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi
        ]
        test = [row for row in pool if lo <= row.fill.entry_ts < hi]
        accepted = apply_occupancy(test, scores(fit_model(train), test))
        stitched.extend(accepted)
        blocks.append(
            {
                "from": lo_iso,
                "to": hi_iso,
                "train": len(train),
                **point_stats(accepted),
            }
        )
    return blocks, stitched


def point_stats(candidates):
    points = [row.fill.points for row in candidates]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "pf": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
    }


def sized_stats(candidates, strategy):
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])
    sized = execution.size(
        [row.fill for row in candidates], strategy.execution
    )
    return metrics.stats(
        sized, initial=strategy.execution.initial, span=(lo, hi)
    )


def random_control(groups, observed):
    rng = random.Random(MODEL_SEED)
    totals = []
    for _ in range(RANDOM_CONTROLS):
        totals.append(
            sum(
                row.fill.points
                for rows, keep in groups
                for row in rng.sample(rows, keep)
            )
        )
    return {
        "draws": RANDOM_CONTROLS,
        "mean": round(sum(totals) / len(totals), 2),
        "p_ge_model": round(
            sum(value >= observed for value in totals) / len(totals), 4
        ),
    }


def verify_replica(strategy, bars, context, baseline):
    expected = execution.resolve(
        bars,
        strategy.signals(
            bars, context, "all", fixed_params(strategy)
        ),
        strategy.execution,
    )
    actual = [row.fill for row in baseline]
    if len(expected) != len(actual):
        raise RuntimeError(
            f"replica mismatch: {len(expected)} != {len(actual)}"
        )
    for left, right in zip(expected, actual):
        if (
            left.entry_ts,
            left.exit_ts,
            left.side,
            round(left.points, 8),
        ) != (
            right.entry_ts,
            right.exit_ts,
            right.side,
            round(right.points, 8),
        ):
            raise RuntimeError(
                f"replica diverged: expected {left}, actual {right}"
            )


def run(out_path=None, record_trial=False):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=1_000.0)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    feature_rows = data.load_l2_features(strategy.symbol)
    context = strategy.context()
    pool = candidate_pool(strategy, bars, feature_rows, context)
    baseline = apply_occupancy(pool)
    verify_replica(strategy, bars, context, baseline)
    if record_trial:
        trials.record(
            STRATEGY_NAME,
            1,
            "fixed L2-logistic meta-labeler; one model/threshold/feature set",
        )

    folds, base_oos, model_oos, pure_oos, groups = walk_forward(
        pool, baseline
    )
    blocks, block_oos = purged_blocks(pool, baseline)
    base_points = point_stats(base_oos)
    model_points = point_stats(model_oos)
    pure_points = point_stats(pure_oos)
    base_sized = sized_stats(base_oos, strategy)
    model_sized = sized_stats(model_oos, strategy)
    control = random_control(groups, pure_points["points"])

    values = [row.fill.points for row in model_oos]
    ci_lo, ci_hi = walkforward.bootstrap_edge(values)
    edge_t = walkforward.edge_t(values)
    cumulative = trials.total(STRATEGY_NAME)
    deflated = walkforward.deflated_t(edge_t, cumulative)
    positive_folds = sum(row.model_points > 0.0 for row in folds)
    block_values = [row["points"] for row in blocks]
    block_mean = sum(block_values) / len(block_values)
    checks = [
        ("stitched OOS PnL > 0", model_sized["pnl"] > 0.0),
        (
            "profitable folds >= 2/3",
            positive_folds >= math.ceil(2 * len(folds) / 3),
        ),
        ("edge CI excludes zero", ci_lo > 0.0),
        ("deflated t > 0", deflated > 0.0),
        (
            "purged-CV coverage",
            min(block_values) >= -2.0 * abs(block_mean),
        ),
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
        "data_range": list(
            data.bar_range(strategy.bars, strategy.symbol)
        ),
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
            "positive_blocks": sum(row["points"] > 0.0 for row in blocks),
        },
        "gate": {
            "passed": all(ok for _, ok in checks),
            "cumulative_trials": cumulative,
            "edge_t": round(edge_t, 4),
            "deflated_t": round(deflated, 4),
            "edge_ci_95": [round(ci_lo, 4), round(ci_hi, 4)],
            "checks": [
                {"name": name, "passed": ok} for name, ok in checks
            ],
        },
    }

    print(
        f"{STRATEGY_NAME}: {len(pool)} candidates, "
        f"{len(baseline)} baseline trades"
    )
    print("  month     train  cand  base_n  ml_n   base_pts    ml_pts")
    for row in folds:
        print(
            f"  {row.month} {row.train:>6} {row.candidates:>5} "
            f"{row.baseline_trades:>7} {row.model_trades:>5} "
            f"{row.baseline_points:>10.1f} {row.model_points:>9.1f}"
        )
    print(f"\n  baseline OOS: {base_points}")
    print(f"  model OOS:    {model_points}")
    print(
        f"  model sized:  pnl={model_sized['pnl']:.2f}, "
        f"positive months={model_sized['pos_months']}/"
        f"{model_sized['n_months']}"
    )
    print(f"  random control: {control}")
    print(
        f"  purged CV: {result['purged_cv']['positive_blocks']}/6 "
        f"positive, {result['purged_cv']['stitched']['points']:+.1f} points"
    )
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
    parser.add_argument("--record-trial", action="store_true")
    args = parser.parse_args()
    run(args.out, args.record_trial)


if __name__ == "__main__":
    main()
