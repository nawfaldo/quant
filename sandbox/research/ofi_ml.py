"""Leakage-safe ML meta-labeler for Deep OFI Momentum.

The OFI rule remains the signal generator.  This model only decides whether a
candidate is worth taking.  A regularized logistic regression is deliberately
used instead of a tree ensemble: the sample is small, the score is easy to
audit and port, and every coefficient can be exported for deterministic live
inference.

Validation is anchored walk-forward.  Each monthly model sees only trades whose
labels are complete before a one-session embargo.  Features come exclusively
from the closed signal bar.  The test simulation is live-realistic: rejecting a
candidate leaves the strategy flat and permits a later candidate.

Run from the repository root:

    py -3.14 -m sandbox.research.ofi_ml
    py -3.14 -m sandbox.research.ofi_ml \
        --export sandbox/ofi_ml_model.json

The export is a paper-trading artifact.  This script never edits the Rust
strategy or promotes a model based on the sample used to design it.
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
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import ofi_ml_gate
from sandbox import purged_cv
from sandbox import strategies
from sandbox import walkforward
from sandbox.data import C, H, L, O, TS

STRATEGY_NAME = "Deep OFI Momentum"
MODEL_VERSION = 1
MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260727
RANDOM_CONTROLS = 5_000

FEATURE_NAMES = (
    "abs_ofi_z",
    "signed_trade_delta",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_price_change",
    "signed_replenishment",
    "log_trade_count",
    "log_depth_events",
    "spread",
    "session_progress",
)


@dataclass(frozen=True)
class Candidate:
    """One fully labelled candidate; features are known before ``fill``."""

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


def _compiled_params(strategy):
    params = strategy.all_params(None)
    expected = {
        "halflife": 2,
        "ofi_z": 2.0,
        "opposing_imbalance": 0.30,
        "stop": 60,
        "target": 120,
        "time_stop": 20,
        "max_spread": 1.0,
        "scale": "raw",
        "norm": "slot",
        "require_delta_agreement": True,
        # The registered strategy gates every entry; this script's baseline is
        # the rule underneath, which it asks for explicitly.
        "ml_gate": True,
        "direction": "momentum",
        "side": "both",
        "entry_from": 660,
        "entry_to": 900,
    }
    drift = {key: (params.get(key), value)
             for key, value in expected.items() if params.get(key) != value}
    if drift:
        raise RuntimeError(
            "ML dataset no longer matches the compiled OFI strategy: "
            + ", ".join(f"{key}={got!r}, expected {want!r}"
                        for key, (got, want) in drift.items()))
    return params


def _features(bar, feature, row, side, entry_from, entry_to):
    """Delegates to `ofi_ml_gate`, which is what the replica and Rust both use.

    Training and inference must not have two copies of this transform: a change
    made here and not there would silently retrain against features the live
    gate never sees.
    """
    z, spread, _top5, delta = row
    return ofi_ml_gate.features(bar[TS], feature, z, spread, delta,
                                side == execution.LONG, entry_from, entry_to)


def _resolve_candidate(bars, entry_index, side, params, spread):
    """Resolve one hypothetical candidate with the Rust engine's ordering."""
    entry_bar = bars[entry_index]
    entry = entry_bar[O]
    entry_minute = (entry_bar[TS] % 86_400) // 60
    stop = params["stop"]
    target = params["target"]
    session_end = strategies.get(STRATEGY_NAME).execution.session_end_min
    half_spread = spread / 2.0

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
            if (nxt is None
                    or nxt[TS] // 86_400 != bar[TS] // 86_400
                    or (nxt[TS] % 86_400) // 60 >= session_end):
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
    """All rule-qualified candidates, including overlapping opportunities."""
    params = _compiled_params(strategy)
    rows = context[(params["halflife"], params["scale"], params["norm"])]
    out = []

    for index, row in enumerate(rows):
        if row is None or index + 1 >= len(bars):
            continue
        if bars[index + 1][TS] != bars[index][TS] + 60:
            continue

        minute = (bars[index][TS] % 86_400) // 60
        if not params["entry_from"] <= minute <= params["entry_to"]:
            continue

        z, spread, imbalance, delta = row
        if spread is None or spread > params["max_spread"]:
            continue
        if z >= params["ofi_z"]:
            side = execution.LONG
        elif z <= -params["ofi_z"]:
            side = execution.SHORT
        else:
            continue

        if ((imbalance < -params["opposing_imbalance"])
                if side == execution.LONG
                else (imbalance > params["opposing_imbalance"])):
            continue
        if params["require_delta_agreement"] and (
                (delta <= 0) if side == execution.LONG else (delta >= 0)):
            continue

        feature = features.get(bars[index][TS])
        if feature is None:
            continue
        exit_index, fill = _resolve_candidate(
            bars, index + 1, side, params, strategy.execution.entry_cost)
        out.append(Candidate(
            signal_index=index,
            exit_index=exit_index,
            fill=fill,
            features=_features(
                bars[index], feature, row, side,
                params["entry_from"], params["entry_to"]),
        ))
    return out


def apply_occupancy(candidates, scores=None, threshold=MODEL_THRESHOLD):
    """Accept chronological candidates while flat; optional score is the gate."""
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
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=MODEL_C,
            class_weight="balanced",
            max_iter=1_000,
            random_state=MODEL_SEED,
        ),
    )


def _matrix(candidates):
    return np.asarray([candidate.features for candidate in candidates], dtype=float)


def _fit(candidates):
    model = make_model()
    labels = np.asarray([candidate.fill.points > 0 for candidate in candidates])
    model.fit(_matrix(candidates), labels)
    return model


def _score(model, candidates):
    if not candidates:
        return np.asarray([], dtype=float)
    return model.predict_proba(_matrix(candidates))[:, 1]


def walk_forward(pool, baseline):
    """Train on past baseline trades; evaluate dynamic monthly candidate gates."""
    folds = []
    baseline_oos = []
    model_oos = []
    pure_model_oos = []
    pure_keep_counts = []

    for _train_lo, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.entry_ts < train_hi]
        test_pool = [
            row for row in pool if test_lo <= row.fill.entry_ts < test_hi]
        test_baseline = [
            row for row in baseline if test_lo <= row.fill.entry_ts < test_hi]
        if not train or not test_pool:
            continue

        model = _fit(train)
        dynamic = apply_occupancy(test_pool, _score(model, test_pool))
        pure_scores = _score(model, test_baseline)
        pure = [row for row, score in zip(test_baseline, pure_scores)
                if score >= MODEL_THRESHOLD]

        baseline_oos.extend(test_baseline)
        model_oos.extend(dynamic)
        pure_model_oos.extend(pure)
        pure_keep_counts.append((test_baseline, len(pure)))
        folds.append(FoldResult(
            month=metrics.month_key(test_lo),
            train=len(train),
            candidates=len(test_pool),
            baseline_trades=len(test_baseline),
            model_trades=len(dynamic),
            baseline_points=sum(row.fill.points for row in test_baseline),
            model_points=sum(row.fill.points for row in dynamic),
        ))
    return folds, baseline_oos, model_oos, pure_model_oos, pure_keep_counts


def purged_cv(pool, baseline):
    """Six-block regime veto; future blocks may train earlier tests by design."""
    out = []
    stitched = []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo = metrics.split_ts(lo_iso)
        hi = metrics.split_ts(hi_iso)
        purge_lo = lo - purged_cv.EMBARGO_DAYS * 86_400
        purge_hi = hi + purged_cv.EMBARGO_DAYS * 86_400
        train = [
            row for row in baseline
            if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi
        ]
        test = [row for row in pool if lo <= row.fill.entry_ts < hi]
        model = _fit(train)
        accepted = apply_occupancy(test, _score(model, test))
        stitched.extend(accepted)
        stats = _point_stats(accepted)
        out.append({
            "from": lo_iso,
            "to": hi_iso,
            "train": len(train),
            **stats,
        })
    return out, stitched


def coefficient_stability(baseline):
    """Standardized coefficient signs across the 12 anchored training fits."""
    coefficients = []
    for _train_lo, train_hi, _test_lo, _test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.entry_ts < train_hi]
        model = _fit(train)
        logistic = model.named_steps["logisticregression"]
        coefficients.append(logistic.coef_[0])
    matrix = np.asarray(coefficients)
    return {
        name: {
            "positive_folds": int((matrix[:, index] > 0).sum()),
            "folds": len(matrix),
            "median": round(float(np.median(matrix[:, index])), 6),
            "minimum": round(float(matrix[:, index].min()), 6),
            "maximum": round(float(matrix[:, index].max()), 6),
        }
        for index, name in enumerate(FEATURE_NAMES)
    }


def _point_stats(candidates):
    points = np.asarray([row.fill.points for row in candidates], dtype=float)
    wins = points[points > 0]
    losses = -points[points < 0]
    return {
        "trades": len(points),
        "points": round(float(points.sum()), 2),
        "points_per_trade": round(float(points.mean()), 4) if len(points) else 0.0,
        "win_rate": round(float((points > 0).mean()), 4) if len(points) else 0.0,
        "profit_factor": round(float(wins.sum() / losses.sum()), 4)
        if len(losses) and losses.sum() else 0.0,
    }


def _monthly_point_stats(candidates):
    grouped = {}
    for row in candidates:
        grouped.setdefault(metrics.month_key(row.fill.entry_ts), []).append(row)
    return {
        month: _point_stats(rows) for month, rows in sorted(grouped.items())
    }


def _sized_stats(candidates, strategy):
    fills = [row.fill for row in candidates]
    sized = execution.size(fills, strategy.execution)
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])
    return metrics.stats(
        sized, initial=strategy.execution.initial, span=(lo, hi))


def _cluster_t(candidates, unit):
    groups = {}
    for row in candidates:
        key = (row.fill.entry_ts // 86_400 if unit == "day"
               else metrics.month_key(row.fill.entry_ts))
        groups.setdefault(key, []).append(row.fill.points)
    means = [sum(values) / len(values) for values in groups.values()]
    if len(means) < 2:
        return 0.0
    mean = sum(means) / len(means)
    variance = sum((value - mean) ** 2 for value in means) / (len(means) - 1)
    return mean / math.sqrt(variance / len(means)) if variance > 0 else 0.0


def random_control(fold_keeps, observed):
    """Matched-count random filtering on the same non-overlapping trade set."""
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
            sum(value >= observed for value in totals) / len(totals), 4),
    }


def _model_payload(model: Pipeline, research):
    scaler = model.named_steps["standardscaler"]
    logistic = model.named_steps["logisticregression"]
    return {
        "model_version": MODEL_VERSION,
        "strategy": STRATEGY_NAME,
        "status": "paper_only",
        "trained_through": data.bar_range("level_two", "nq")[1],
        "data_range": list(data.bar_range("level_two", "nq")),
        "feature_names": list(FEATURE_NAMES),
        "threshold": MODEL_THRESHOLD,
        "regularization_c": MODEL_C,
        "class_weight": "balanced",
        "scaler_mean": [round(float(value), 12) for value in scaler.mean_],
        "scaler_scale": [round(float(value), 12) for value in scaler.scale_],
        "coefficients": [
            round(float(value), 12) for value in logistic.coef_[0]],
        "intercept": round(float(logistic.intercept_[0]), 12),
        "research": research,
    }


def _verify_replica(strategy, bars, context, baseline):
    """`candidate_pool`'s baseline must equal the *ungated* replica.

    The replica applies the ML gate by default now, matching the registered Rust
    strategy, so the baseline this script trains against has to ask for the rule
    underneath it explicitly.
    """
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(
            bars, context, group, strategy.all_params({"ml_gate": False})))
    expected = execution.resolve(bars, signals, strategy.execution)
    actual = [row.fill for row in baseline]
    if len(expected) != len(actual):
        raise RuntimeError(
            f"candidate replica produced {len(actual)} baseline fills; "
            f"strategy produced {len(expected)}")
    for left, right in zip(expected, actual):
        if (left.entry_ts != right.entry_ts
                or left.exit_ts != right.exit_ts
                or left.side != right.side
                or abs(left.points - right.points) > 1e-9):
            raise RuntimeError(
                f"candidate replica diverged at entry {left.entry_ts}")


def run(export=None):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=1_000.0)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    features = data.load_l2_features(strategy.symbol)
    context = strategy.context()
    pool = candidate_pool(strategy, bars, features, context)
    baseline = apply_occupancy(pool)
    _verify_replica(strategy, bars, context, baseline)

    folds, base_oos, model_oos, pure_oos, fold_keeps = walk_forward(
        pool, baseline)
    base_points = _point_stats(base_oos)
    model_points = _point_stats(model_oos)
    pure_points = _point_stats(pure_oos)
    base_sized = _sized_stats(base_oos, strategy)
    model_sized = _sized_stats(model_oos, strategy)
    control = random_control(fold_keeps, pure_points["points"])
    purged, purged_oos = purged_cv(pool, baseline)
    research = {
        "walk_forward_folds": [asdict(row) for row in folds],
        "baseline_points": base_points,
        "model_points_live_dynamic": model_points,
        "model_points_pure_filter": pure_points,
        "baseline_sized": base_sized,
        "model_sized_live_dynamic": model_sized,
        "model_cluster_t": {
            "day": round(_cluster_t(model_oos, "day"), 4),
            "month": round(_cluster_t(model_oos, "month"), 4),
        },
        "purged_cv": {
            "blocks": purged,
            "positive_blocks": sum(row["points"] > 0 for row in purged),
            "total_points": round(sum(row["points"] for row in purged), 2),
            "worst_block": round(min(row["points"] for row in purged), 2),
            "full_sample_points": _point_stats(purged_oos),
            "full_sample_sized": _sized_stats(purged_oos, strategy),
            "monthly_points": _monthly_point_stats(purged_oos),
        },
        "coefficient_stability": coefficient_stability(baseline),
        "matched_random_control": control,
    }

    print(f"{STRATEGY_NAME} ML meta-labeler")
    print(f"  data {data.bar_range(strategy.bars, strategy.symbol)}, "
          f"{len(pool)} candidates, {len(baseline)} baseline trades")
    print("\n  month      train  cand  base_n  ml_n    base pts      ml pts")
    for row in folds:
        print(f"  {row.month} {row.train:>6} {row.candidates:>5} "
              f"{row.baseline_trades:>7} {row.model_trades:>5} "
              f"{row.baseline_points:>11.1f} {row.model_points:>11.1f}")
    print("\n  stitched OOS, per-unit points")
    for name, stats in (("baseline", base_points),
                        ("ML dynamic", model_points),
                        ("ML pure filter", pure_points)):
        print(f"  {name:<14} n {stats['trades']:>4} "
              f"points {stats['points']:>8.1f} "
              f"edge {stats['points_per_trade']:>6.3f} "
              f"pf {stats['profit_factor']:>5.3f}")
    print(f"\n  matched random-filter p >= ML: {control['p_ge_model']:.4f}")
    print(f"  clustered ML t: day {research['model_cluster_t']['day']:.2f}, "
          f"month {research['model_cluster_t']['month']:.2f}")
    print("  purged CV: "
          f"{research['purged_cv']['positive_blocks']}/{len(purged)} "
          f"positive, total {research['purged_cv']['total_points']:.1f}, "
          f"worst {research['purged_cv']['worst_block']:.1f}")
    print(f"  monthly gates: pos_rate {model_sized['pos_rate']:.3f}, "
          f"mSharpe {model_sized['msharpe']:.3f}, "
          f"loss_streak {model_sized['max_loss_streak']}, "
          f"worst_quarter {model_sized['worst_quarter']:.2f}")

    final_model = _fit(baseline)
    payload = _model_payload(final_model, research)
    if export is not None:
        export.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\n  exported paper model to {export}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", type=Path,
        help="write the full-history paper-trading inference artifact")
    args = parser.parse_args()
    run(args.export)


if __name__ == "__main__":
    main()
