"""Leakage-safe ML meta-labeler for the adds-only LWI candidate.

The rule in ``strategies/liquidity_withdrawal.py`` remains the signal generator.
This experiment asks one pre-declared question: can a fixed, regularized
logistic model reject weak LWI candidates using information already known at
the close of the signal minute?

Nothing is selected on the full sample:

* model family: L2-regularized logistic regression only;
* regularization: C=0.1, fixed before the run;
* decision threshold: 0.5, fixed before the run;
* feature set: fixed market-state measurements listed in ``FEATURE_NAMES``;
* validation: twelve anchored monthly folds plus six-block purged CV;
* training labels: only trades whose exits precede the fold embargo.

The model is deliberately a meta-labeler rather than a new entry generator.
Rejecting a trade leaves the strategy flat, so the dynamic test can accept a
later opportunity exactly as a live implementation could. A matched-count
random filter checks whether any improvement is more than the mechanical effect
of taking fewer trades.

This is a research script, not a production export. Run from the repository root:

    py -m sandbox.research.lwi_ml \
        --out sandbox/results/liquidity_withdrawal_ml_result.json
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

STRATEGY_NAME = "Liquidity Withdrawal (adds-only)"
MODEL_VERSION = 1
MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260729
RANDOM_CONTROLS = 5_000

FEATURE_NAMES = (
    "abs_lwi_z",
    "joint_withdrawal",
    "signed_trade_delta",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_price_change",
    "signed_replenishment",
    "toxicity",
    "log_trade_count",
    "log_depth_events",
    "spread",
    "session_progress",
)


@dataclass(frozen=True)
class Candidate:
    """One hypothetical LWI trade with pre-entry features and a final label."""

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
    """Freeze the pre-optimization defaults used to define the ML population."""
    params = strategy.all_params(None)
    expected = {
        "lwi_z": 2.0,
        "norm_sessions": 20,
        "stop": 25,
        "rr": 1.5,
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
        details = ", ".join(
            f"{key}={got!r}, expected {want!r}"
            for key, (got, want) in drift.items()
        )
        raise RuntimeError(f"LWI ML population changed: {details}")
    return params


def lwi_sides(feature):
    """Bid/ask withdrawal ratios, or ``None`` when either is undefined."""
    bid_activity = feature["bid_add_volume"] + feature["bid_cancel_volume"]
    ask_activity = feature["ask_add_volume"] + feature["ask_cancel_volume"]
    if bid_activity <= 0.0 or ask_activity <= 0.0:
        return None
    return (
        feature["bid_cancel_volume"] / bid_activity,
        feature["ask_cancel_volume"] / ask_activity,
    )


def model_features(bar, feature, row, side, entry_from, entry_to):
    """Measurements available at the signal-bar close only."""
    zscore, spread = row
    sign = 1.0 if side == execution.LONG else -1.0
    bid_lwi, ask_lwi = lwi_sides(feature)
    aggressive = (
        feature["aggressive_buy_volume"] + feature["aggressive_sell_volume"]
    )
    toxicity = abs(feature["trade_delta"]) / aggressive if aggressive > 0 else 0.0
    minute = (bar[TS] % 86_400) // 60
    span = max(1, entry_to - entry_from)

    return (
        min(abs(zscore), 6.0),
        (bid_lwi + ask_lwi) / 2.0,
        math.asinh(sign * feature["trade_delta"] / 100.0),
        sign * feature["top1_imbalance"],
        sign * feature["top5_imbalance"],
        sign * feature["top10_imbalance"],
        sign * (feature["microprice"] - feature["midprice"]) / 0.25,
        math.asinh(sign * feature["price_change"] / 2.0),
        sign * feature["replenishment_score"],
        toxicity,
        math.log1p(feature["trade_count"]),
        math.log1p(feature["depth_event_count"]),
        spread,
        (minute - entry_from) / span,
    )


def resolve_candidate(bars, entry_index, side, params, round_trip_spread):
    """Resolve one hypothetical trade with ``execution.resolve`` ordering."""
    entry_bar = bars[entry_index]
    entry = entry_bar[O]
    entry_minute = (entry_bar[TS] % 86_400) // 60
    stop = params["stop"]
    target = stop * params["rr"]
    session_end = strategies.get(STRATEGY_NAME).execution.session_end_min
    half_spread = round_trip_spread / 2.0

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

        if exit_price is None and session_end is not None and minute < session_end:
            nxt = bars[index + 1] if index + 1 < len(bars) else None
            if (
                nxt is None
                or nxt[TS] // 86_400 != bar[TS] // 86_400
                or (nxt[TS] % 86_400) // 60 >= session_end
            ):
                exit_price = bar[C]

        if exit_price is None:
            continue

        if side == execution.LONG:
            points = (exit_price - half_spread) - (entry + half_spread)
        else:
            points = (entry - half_spread) - (exit_price + half_spread)
        return index, execution.Fill(
            entry_ts=entry_bar[TS],
            exit_ts=bar[TS],
            side=side,
            points=points,
            price=entry,
            stop=stop,
        )
    raise RuntimeError(f"candidate at index {entry_index} never exits")


def candidate_pool(strategy, bars, features, context):
    """All fixed-rule candidates, including opportunities while occupied."""
    params = fixed_params(strategy)
    rows = context["rows"][params["norm_sessions"]]
    out = []

    for index, row in enumerate(rows):
        if row is None or index + 1 >= len(bars):
            continue
        if bars[index + 1][TS] != bars[index][TS] + 60:
            continue

        minute = (bars[index][TS] % 86_400) // 60
        if not params["entry_from"] <= minute <= params["entry_to"]:
            continue

        zscore, spread = row
        if spread > params["max_spread"]:
            continue
        if zscore >= params["lwi_z"]:
            side = execution.LONG
        elif zscore <= -params["lwi_z"]:
            side = execution.SHORT
        else:
            continue

        feature = features.get(bars[index][TS])
        if feature is None or not feature["book_valid"]:
            continue
        if lwi_sides(feature) is None:
            continue

        exit_index, fill = resolve_candidate(
            bars,
            index + 1,
            side,
            params,
            strategy.execution.entry_cost,
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


def apply_occupancy(candidates, scores=None, threshold=MODEL_THRESHOLD):
    """Accept chronological candidates while flat, optionally behind the model."""
    if scores is not None and len(scores) != len(candidates):
        raise ValueError("one score is required per candidate")
    accepted = []
    free_from = -1
    for index, candidate in enumerate(candidates):
        if candidate.signal_index < free_from:
            continue
        if scores is not None and scores[index] < threshold:
            continue
        accepted.append(candidate)
        free_from = candidate.exit_index
    return accepted


def make_model():
    """The experiment's only model configuration."""
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=MODEL_C,
            class_weight="balanced",
            max_iter=1_000,
            random_state=MODEL_SEED,
        ),
    )


def matrix(candidates):
    return np.asarray([candidate.features for candidate in candidates], dtype=float)


def fit_model(candidates):
    labels = np.asarray([candidate.fill.points > 0 for candidate in candidates])
    if len(np.unique(labels)) < 2:
        raise RuntimeError("training fold contains only one outcome class")
    model = make_model()
    model.fit(matrix(candidates), labels)
    return model


def score_model(model, candidates):
    if not candidates:
        return np.asarray([], dtype=float)
    return model.predict_proba(matrix(candidates))[:, 1]


def walk_forward(pool, baseline):
    """Fit only on completed pre-embargo labels; score each month once."""
    folds = []
    baseline_oos = []
    model_oos = []
    pure_model_oos = []
    pure_keep_counts = []

    for _train_lo, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.exit_ts < train_hi]
        test_pool = [
            row for row in pool if test_lo <= row.fill.entry_ts < test_hi
        ]
        test_baseline = [
            row for row in baseline if test_lo <= row.fill.entry_ts < test_hi
        ]
        if not train or not test_pool:
            continue

        model = fit_model(train)
        dynamic = apply_occupancy(test_pool, score_model(model, test_pool))
        pure_scores = score_model(model, test_baseline)
        pure = [
            row
            for row, score in zip(test_baseline, pure_scores)
            if score >= MODEL_THRESHOLD
        ]

        baseline_oos.extend(test_baseline)
        model_oos.extend(dynamic)
        pure_model_oos.extend(pure)
        pure_keep_counts.append((test_baseline, len(pure)))
        folds.append(
            FoldResult(
                month=metrics.month_key(test_lo),
                train=len(train),
                candidates=len(test_pool),
                baseline_trades=len(test_baseline),
                model_trades=len(dynamic),
                baseline_points=sum(row.fill.points for row in test_baseline),
                model_points=sum(row.fill.points for row in dynamic),
            )
        )
    return folds, baseline_oos, model_oos, pure_model_oos, pure_keep_counts


def purged_cv(pool, baseline):
    """Six held-out regime blocks; a coverage veto, never a selector."""
    blocks = []
    stitched = []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo = metrics.split_ts(lo_iso)
        hi = metrics.split_ts(hi_iso)
        purge_lo = lo - purged_cv.EMBARGO_DAYS * 86_400
        purge_hi = hi + purged_cv.EMBARGO_DAYS * 86_400
        train = [
            row
            for row in baseline
            if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi
        ]
        test = [row for row in pool if lo <= row.fill.entry_ts < hi]
        model = fit_model(train)
        accepted = apply_occupancy(test, score_model(model, test))
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
    points = np.asarray([row.fill.points for row in candidates], dtype=float)
    wins = points[points > 0]
    losses = -points[points < 0]
    return {
        "trades": len(points),
        "points": round(float(points.sum()), 2),
        "points_per_trade": round(float(points.mean()), 4) if len(points) else 0.0,
        "win_rate": round(float((points > 0).mean()), 4) if len(points) else 0.0,
        "profit_factor": (
            round(float(wins.sum() / losses.sum()), 4)
            if len(losses) and losses.sum()
            else 0.0
        ),
    }


def sized_stats(candidates, strategy):
    fills = [row.fill for row in candidates]
    sized = execution.size(fills, strategy.execution)
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])
    return metrics.stats(
        sized,
        initial=strategy.execution.initial,
        span=(lo, hi),
    )


def clustered_t(candidates, unit):
    groups = {}
    for row in candidates:
        key = (
            row.fill.entry_ts // 86_400
            if unit == "day"
            else metrics.month_key(row.fill.entry_ts)
        )
        groups.setdefault(key, []).append(row.fill.points)
    means = [sum(values) / len(values) for values in groups.values()]
    if len(means) < 2:
        return 0.0
    mean = sum(means) / len(means)
    variance = sum((value - mean) ** 2 for value in means) / (len(means) - 1)
    return mean / math.sqrt(variance / len(means)) if variance > 0 else 0.0


def coefficient_stability(baseline):
    """Standardized coefficient sign stability across anchored training fits."""
    coefficients = []
    for _train_lo, train_hi, _test_lo, _test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.exit_ts < train_hi]
        model = fit_model(train)
        coefficients.append(model.named_steps["logisticregression"].coef_[0])
    values = np.asarray(coefficients)
    return {
        name: {
            "positive_folds": int((values[:, index] > 0).sum()),
            "folds": len(values),
            "median": round(float(np.median(values[:, index])), 6),
            "minimum": round(float(values[:, index].min()), 6),
            "maximum": round(float(values[:, index].max()), 6),
        }
        for index, name in enumerate(FEATURE_NAMES)
    }


def random_control(fold_keeps, observed):
    """Matched-count random filtering of each fold's baseline trades."""
    rng = random.Random(MODEL_SEED)
    totals = []
    for _ in range(RANDOM_CONTROLS):
        total = 0.0
        for rows, keep in fold_keeps:
            total += sum(row.fill.points for row in rng.sample(rows, keep))
        totals.append(total)
    return {
        "draws": RANDOM_CONTROLS,
        "mean": round(sum(totals) / len(totals), 2),
        "p_ge_model": round(
            sum(value >= observed for value in totals) / len(totals),
            4,
        ),
    }


def verify_replica(strategy, bars, context, baseline):
    """Ensure the candidate pool reproduces the fixed rule before ML."""
    params = fixed_params(strategy)
    signals = strategy.signals(bars, context, "all", params)
    expected = execution.resolve(bars, signals, strategy.execution)
    actual = [row.fill for row in baseline]
    if len(expected) != len(actual):
        raise RuntimeError(
            f"candidate replica produced {len(actual)} fills; strategy produced "
            f"{len(expected)}"
        )
    for left, right in zip(expected, actual):
        if (
            left.entry_ts != right.entry_ts
            or left.exit_ts != right.exit_ts
            or left.side != right.side
            or abs(left.points - right.points) > 1e-9
        ):
            raise RuntimeError(f"candidate replica diverged at {left.entry_ts}")


def run(out_path=None):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=1_000.0)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    features = data.load_l2_features(strategy.symbol)
    context = strategy.context()
    pool = candidate_pool(strategy, bars, features, context)
    baseline = apply_occupancy(pool)
    verify_replica(strategy, bars, context, baseline)

    folds, base_oos, model_oos, pure_oos, fold_keeps = walk_forward(
        pool,
        baseline,
    )
    blocks, block_oos = purged_cv(pool, baseline)
    base_points = point_stats(base_oos)
    model_points = point_stats(model_oos)
    pure_points = point_stats(pure_oos)
    base_sized = sized_stats(base_oos, strategy)
    model_sized = sized_stats(model_oos, strategy)
    control = random_control(fold_keeps, pure_points["points"])
    model_values = [row.fill.points for row in model_oos]
    ci_lo, ci_hi = walkforward.bootstrap_edge(model_values)
    edge_t = walkforward.edge_t(model_values)
    cumulative_trials = trials.total(STRATEGY_NAME)
    deflated_t = walkforward.deflated_t(edge_t, cumulative_trials)
    positive_folds = sum(row.model_points > 0 for row in folds)
    cv_values = [row["points"] for row in blocks]
    cv_mean = sum(cv_values) / len(cv_values)
    cv_worst = min(cv_values)
    gate_checks = [
        {
            "name": "stitched OOS PnL > 0",
            "passed": model_sized["pnl"] > 0,
            "detail": f"{model_sized['pnl']:+.2f}",
        },
        {
            "name": "profitable folds >= 2/3",
            "passed": positive_folds >= math.ceil(2 * len(folds) / 3),
            "detail": f"{positive_folds}/{len(folds)}",
        },
        {
            "name": "per-trade edge CI excludes 0",
            "passed": ci_lo > 0,
            "detail": (
                f"mean {model_points['points_per_trade']:+.3f}, "
                f"95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}]"
            ),
        },
        {
            "name": "deflated t > 0",
            "passed": deflated_t > 0,
            "detail": (
                f"{deflated_t:+.2f} vs {cumulative_trials} cumulative trials"
            ),
        },
        {
            "name": "purged-CV coverage",
            "passed": cv_worst >= -2 * abs(cv_mean),
            "detail": f"worst {cv_worst:+.1f}, mean {cv_mean:+.1f}",
        },
        {
            "name": "matched random-filter p < 0.05",
            "passed": control["p_ge_model"] < 0.05,
            "detail": f"p={control['p_ge_model']:.4f}",
        },
    ]
    promoted = all(check["passed"] for check in gate_checks)

    result = {
        "strategy": STRATEGY_NAME,
        "status": "promoted" if promoted else "rejected",
        "model": {
            "version": MODEL_VERSION,
            "family": "standardized_l2_logistic_regression",
            "regularization_c": MODEL_C,
            "class_weight": "balanced",
            "threshold": MODEL_THRESHOLD,
            "feature_names": list(FEATURE_NAMES),
        },
        "data_range": list(data.bar_range(strategy.bars, strategy.symbol)),
        "candidate_count": len(pool),
        "baseline_trade_count": len(baseline),
        "walk_forward_folds": [asdict(row) for row in folds],
        "baseline_points": base_points,
        "model_points_live_dynamic": model_points,
        "model_points_pure_filter": pure_points,
        "baseline_sized": base_sized,
        "model_sized_live_dynamic": model_sized,
        "clustered_model_t": {
            "day": round(clustered_t(model_oos, "day"), 4),
            "month": round(clustered_t(model_oos, "month"), 4),
        },
        "matched_random_control": control,
        "purged_cv": {
            "blocks": blocks,
            "positive_blocks": sum(row["points"] > 0 for row in blocks),
            "total_points": round(sum(row["points"] for row in blocks), 2),
            "worst_block": min(row["points"] for row in blocks),
            "stitched": point_stats(block_oos),
        },
        "coefficient_stability": coefficient_stability(baseline),
        "promotion_gate": {
            "passed": promoted,
            "cumulative_trials": cumulative_trials,
            "edge_t": round(edge_t, 4),
            "deflated_t": round(deflated_t, 4),
            "checks": gate_checks,
        },
    }

    print(f"{STRATEGY_NAME} fixed logistic meta-labeler")
    print(
        f"  data {result['data_range']}, {len(pool)} candidates, "
        f"{len(baseline)} baseline trades"
    )
    print("\n  month      train  cand  base_n  ml_n    base pts      ml pts")
    for row in folds:
        print(
            f"  {row.month} {row.train:>6} {row.candidates:>5} "
            f"{row.baseline_trades:>7} {row.model_trades:>5} "
            f"{row.baseline_points:>11.1f} {row.model_points:>11.1f}"
        )

    print("\n  stitched anchored OOS, per-unit points")
    for name, stats in (
        ("baseline", base_points),
        ("ML dynamic", model_points),
        ("ML pure filter", pure_points),
    ):
        print(
            f"  {name:<14} n {stats['trades']:>4} "
            f"points {stats['points']:>8.1f} "
            f"edge {stats['points_per_trade']:>7.3f} "
            f"pf {stats['profit_factor']:>5.3f}"
        )
    print(
        f"\n  matched random-filter p >= ML: {control['p_ge_model']:.4f}"
    )
    print(
        f"  clustered ML t: day {result['clustered_model_t']['day']:.2f}, "
        f"month {result['clustered_model_t']['month']:.2f}"
    )
    print(
        f"  purged CV: {result['purged_cv']['positive_blocks']}/"
        f"{len(blocks)} positive, total {result['purged_cv']['total_points']:.1f}, "
        f"worst {result['purged_cv']['worst_block']:.1f}"
    )
    print(
        f"  monthly: pnl {model_sized['pnl']:.2f}, "
        f"positive {model_sized['pos_months']}/{model_sized['n_months']}, "
        f"mSharpe {model_sized['msharpe']:.3f}, "
        f"loss streak {model_sized['max_loss_streak']}"
    )
    print("\n  promotion gate")
    for check in gate_checks:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"  [{mark}] {check['name']:<36} {check['detail']}")
    print(f"  => {'PROMOTED' if promoted else 'NOT PROMOTED'}")

    if out_path is not None:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\n  wrote research result to {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write the research result JSON")
    args = parser.parse_args()
    run(args.out)


if __name__ == "__main__":
    main()
