"""Leakage-controlled S4 raw-tape sweep continuation research.

The run includes the pre-declared non-overlapping forward-return measurement,
a 27-cell three-axis anchored walk-forward and purged CV, fixed OOS
day/time/side and sizing diagnostics, co-firing against Absorption Reversal,
and one fixed logistic meta-labeler because the occupied population is in the
thousands.  Diagnostics inspect OOS outcomes but cannot become shipped rules.

Run from the repository root:

    .venv\\Scripts\\python.exe -B \
        -m sandbox.research.s4_sweep_trade_through_research \
        --out sandbox/results/s4_sweep_trade_through_result.json \
        --record-trials
"""

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import subprocess

import numpy as np

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PROJECT_ROOT

NAME = "S4 Sweep Trade Through"
INITIAL = 10_000.0
PRIMARY_HORIZON = 30
HORIZONS = (15, PRIMARY_HORIZON, 60)
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)
ML_MINIMUM = 2_000
ML_C = 0.1
ML_THRESHOLD = 0.5


@dataclass(frozen=True)
class MLRow:
    fill: execution.Fill
    features: tuple


class StandardisedLogistic:
    """Small deterministic L2-logistic fit; avoids an optional sklearn install."""

    def fit(self, features, labels):
        matrix = np.asarray(features, dtype=float)
        target = np.asarray(labels, dtype=float)
        self.mean = matrix.mean(axis=0)
        self.scale = matrix.std(axis=0)
        self.scale[self.scale == 0.0] = 1.0
        standard = (matrix - self.mean) / self.scale
        design = np.column_stack([standard, np.ones(len(standard))])
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
        standard = (matrix - self.mean) / self.scale
        design = np.column_stack([standard, np.ones(len(standard))])
        linear = np.clip(design @ self.weights, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-linear))


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def iso_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def inside(fills, lo, hi):
    return [fill for fill in fills if lo <= fill.entry_ts < hi]


def point_stats(fills, lo, hi):
    points = [fill.points for fill in fills]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    low, high = walkforward.bootstrap_edge(points, seed=20260731)
    months = {}
    for fill in fills:
        key = metrics.month_key(fill.entry_ts)
        months[key] = months.get(key, 0.0) + fill.points
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "edge_ci95": [round(low, 4), round(high, 4)],
        "pf_points": (
            round(wins / losses, 4)
            if losses
            else (999.0 if wins else 0.0)
        ),
        "point_months": {
            key: round(value, 2)
            for key, value in metrics.pad(months, lo, hi).items()
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


def fills_for(strategy, bars, context, params):
    signals = strategy.signals(
        bars, context, "all", strategy.all_params(params)
    )
    return execution.resolve(bars, signals, strategy.execution)


def fixed_horizon_fills(bars, candidates, horizon, spread):
    """Globally non-overlapping forward windows from the pre-declared default."""
    out = []
    free_at = -1
    for row in candidates:
        if row["entry_ts"] < free_at:
            continue
        exit_index = row["index"] + horizon
        if exit_index >= len(bars):
            continue
        entry_bar = bars[row["index"]]
        exit_bar = bars[exit_index]
        if (
            exit_bar[data.TS] != row["entry_ts"] + horizon * 60
            or exit_bar[data.TS] // 86_400 != row["entry_ts"] // 86_400
        ):
            continue
        sign = 1.0 if row["side"] == execution.LONG else -1.0
        points = sign * (exit_bar[data.O] - entry_bar[data.O]) - spread
        out.append(
            execution.Fill(
                row["entry_ts"],
                exit_bar[data.TS],
                row["side"],
                points,
                entry_bar[data.O],
                15.0,
            )
        )
        free_at = exit_bar[data.TS]
    return out


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


def cost_adjust(fills, old_spread, new_spread):
    return [
        replace(fill, points=fill.points + old_spread - new_spread)
        for fill in fills
    ]


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


def absorption_cofire(s4_fills, bars):
    """Fixed +/- five-minute entry overlap for S4's stated orthogonality kill."""
    other = strategies.get("Absorption Reversal")
    features = data.load_cached_l2_features(other.symbol)
    context = {
        window: other._statistics(bars, features, window)
        for window in range(1, 4)
    }
    signals = []
    for group in other.groups():
        signals.extend(
            other.signals(
                bars, context, group, other.all_params()
            )
        )
    fills = execution.resolve(
        bars, sorted(signals, key=lambda row: row.index), other.execution
    )
    other_ts = sorted(fill.entry_ts for fill in fills)
    hits = 0
    cursor = 0
    for fill in sorted(s4_fills, key=lambda row: row.entry_ts):
        while cursor < len(other_ts) and other_ts[cursor] < fill.entry_ts - 300:
            cursor += 1
        hits += cursor < len(other_ts) and other_ts[cursor] <= fill.entry_ts + 300
    return {
        "band_minutes": 5,
        "s4_trades": len(s4_fills),
        "absorption_trades": len(fills),
        "cofires": hits,
        "cofire_rate": round(hits / len(s4_fills), 4) if s4_fills else 0.0,
        "kill_threshold": 0.30,
        "passed": bool(s4_fills) and hits / len(s4_fills) <= 0.30,
    }


def ml_features(row, atr):
    minute = (row["entry_ts"] % 86_400) // 60
    weekday = (row["entry_ts"] // 86_400 + 3) % 7
    session_angle = 2 * math.pi * (minute - 570) / 390
    weekday_angle = 2 * math.pi * weekday / 5
    side = 1.0 if row["side"] == execution.LONG else -1.0
    causal_atr = atr.get(row["entry_ts"] // 86_400, 0.0)
    return (
        math.log1p(row["levels"]),
        math.log1p(row["volume"]),
        math.log1p(row["trades"]),
        math.log1p(row["duration_ms"]),
        row["price_range"],
        row["quote_spread"],
        side,
        math.sin(session_angle),
        math.cos(session_angle),
        math.sin(weekday_angle),
        math.cos(weekday_angle),
        math.log1p(causal_atr),
    )


def ml_dataset(strategy, bars, context, atr):
    params = strategy.all_params()
    candidates = {
        row["entry_ts"]: row
        for row in strategy.candidates(bars, context, params)
    }
    fills = fills_for(strategy, bars, context, {})
    return [
        MLRow(fill, ml_features(candidates[fill.entry_ts], atr))
        for fill in fills
        if fill.entry_ts in candidates
    ]


def fit_model(train):
    labels = np.asarray([row.fill.points > 0 for row in train], dtype=int)
    if len(train) < 200 or len(set(labels.tolist())) < 2:
        return None
    return StandardisedLogistic().fit(
        [row.features for row in train],
        labels,
    )


def predict(model, test):
    if model is None or not test:
        return []
    probabilities = model.predict_proba([row.features for row in test])
    return [
        row
        for row, probability in zip(test, probabilities)
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
    fold_rows = []
    baseline_oos = []
    selected_oos = []
    controls = []
    for index, (_train_lo, train_hi, test_lo, test_hi) in enumerate(
        walkforward.fold_windows(), 1
    ):
        train = [row for row in rows if row.fill.exit_ts < train_hi]
        test = [
            row
            for row in rows
            if test_lo <= row.fill.entry_ts < test_hi
        ]
        chosen = predict(fit_model(train), test)
        baseline_oos.extend(test)
        selected_oos.extend(chosen)
        controls.append((test, len(chosen)))
        base_points = sum(row.fill.points for row in test)
        model_points = sum(row.fill.points for row in chosen)
        fold_rows.append(
            {
                "fold": index,
                "month": metrics.month_key(test_lo),
                "train": len(train),
                "baseline_trades": len(test),
                "selected_trades": len(chosen),
                "baseline_points": round(base_points, 2),
                "model_points": round(model_points, 2),
                "delta_points": round(model_points - base_points, 2),
            }
        )

    base_fills = [row.fill for row in baseline_oos]
    model_fills = [row.fill for row in selected_oos]
    cv = []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        purge_lo = lo - 86_400
        purge_hi = hi + 86_400
        train = [
            row
            for row in rows
            if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi
        ]
        test = [row for row in rows if lo <= row.fill.entry_ts < hi]
        chosen = predict(fit_model(train), test)
        points = sum(row.fill.points for row in chosen)
        cv.append(
            {
                "block": lo_iso,
                "train": len(train),
                "test": len(test),
                "selected": len(chosen),
                "points": round(points, 2),
            }
        )
    model_points = sum(fill.points for fill in model_fills)
    return {
        "run": True,
        "model": "standardised Newton-solved L2 logistic regression",
        "C": ML_C,
        "threshold": ML_THRESHOLD,
        "features": [
            "log_levels",
            "log_volume",
            "log_trade_count",
            "log_duration_ms",
            "price_range",
            "mean_event_spread",
            "side",
            "session_sin",
            "session_cos",
            "weekday_sin",
            "weekday_cos",
            "log_causal_atr20",
        ],
        "folds": fold_rows,
        "baseline_oos": score(base_fills, ex, oos_lo, oos_hi),
        "model_oos": score(model_fills, ex, oos_lo, oos_hi),
        "purged_cv": cv,
        "matched_random_control": matched_random_control(
            controls, model_points
        ),
        "positive_model_folds": sum(
            row["model_points"] > 0 for row in fold_rows
        ),
        "improved_folds": sum(
            row["delta_points"] > 0 for row in fold_rows
        ),
        "note": (
            "The single fixed classifier filters the unchanged occupied "
            "default population; no algorithms, thresholds, or feature "
            "subsets were searched."
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
    strategy = strategies.get(NAME)
    strategy.execution = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    data_lo = bars[0][data.TS]
    study_hi = metrics.split_ts(strategy.defaults["to_date"]) + 86_400
    oos_lo = metrics.split_ts(walkforward.FOLDS[0][0])
    oos_hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    default_candidates = strategy.candidates(
        bars, context, strategy.all_params()
    )
    effects = {}
    for horizon in HORIZONS:
        fills = fixed_horizon_fills(
            bars, default_candidates, horizon, strategy.execution.entry_cost
        )
        fills = inside(fills, data_lo, study_hi)
        effects[str(horizon)] = point_stats(fills, data_lo, study_hi)
    if record_trials:
        trials.record(NAME, 1, "pre-declared 30-minute non-overlapping effect")
        trials.record(NAME, 2, "fixed 15/60-minute horizon diagnostics")

    default_fills = fills_for(strategy, bars, context, {})
    default_full = score(default_fills, strategy.execution, data_lo, study_hi)

    wf = walkforward.run(
        strategy,
        initial=INITIAL,
        progress=True,
        record_trials=record_trials,
    )
    _reported_median, stitched = walkforward.report(wf)
    median = (
        walkforward.median_params(wf["picks"], strategy)
        if wf["picks"]
        else {axis: strategy.defaults[axis] for axis in strategy.grid}
    )
    fixed, _fixed_sized = walkforward.verify(wf, median, "fixed fold-median")
    median_fills = fills_for(strategy, bars, context, median)
    fixed.update(point_stats(inside(median_fills, oos_lo, oos_hi), oos_lo, oos_hi))

    cv = purged_cv.run(
        strategy,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=record_trials,
    )

    baseline_oos = inside(median_fills, oos_lo, oos_hi)
    filters = {}
    for name in FILTERS:
        selected = inside(filter_fills(median_fills, name), oos_lo, oos_hi)
        improved, deltas = fold_improvements(selected, baseline_oos)
        filters[name] = {
            **score(selected, strategy.execution, oos_lo, oos_hi),
            "improved_folds": improved,
            "fold_deltas": deltas,
        }
    if record_trials:
        trials.record(NAME, len(FILTERS) - 1, "fixed OOS day/time/side diagnostics")

    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        sizing[f"fixed_fraction_{risk * 100:.2f}%"] = score(
            median_fills,
            replace(strategy.execution, risk=risk),
            oos_lo,
            oos_hi,
        )
    atr = data.atr_by_day(bars, 20)
    prior_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < oos_lo
    )
    target_atr = prior_atr[len(prior_atr) // 2]
    inverse, multipliers = inverse_atr_fills(
        baseline_oos, atr, target_atr
    )
    sizing["causal_inverse_atr_0.50_to_1.50x"] = {
        **score(inverse, strategy.execution, oos_lo, oos_hi),
        "target_atr": round(target_atr, 4),
        "mean_multiplier": round(sum(multipliers) / len(multipliers), 4)
        if multipliers
        else 1.0,
    }
    if record_trials:
        trials.record(NAME, 4, "fixed OOS sizing diagnostics")

    costs = {}
    for spread in COSTS:
        costs[str(spread)] = score(
            cost_adjust(median_fills, strategy.execution.entry_cost, spread),
            replace(strategy.execution, spread=spread),
            oos_lo,
            oos_hi,
        )
    if record_trials:
        trials.record(NAME, len(COSTS) - 1, "fixed execution-cost sensitivity")

    cofire = absorption_cofire(default_fills, bars)
    ml_rows = ml_dataset(strategy, bars, context, atr)
    if len(ml_rows) >= ML_MINIMUM:
        ml = ml_research(ml_rows, strategy.execution, oos_lo, oos_hi)
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

    wf["trials"] = trials.total(NAME)
    gate = walkforward.gate(wf, stitched, cv["blocks"])
    overall_passed = gate["passed"] and cofire["passed"]

    result = {
        "strategy": NAME,
        "status": "promoted" if overall_passed else "rejected",
        "git_sha": git_sha(),
        "data": {
            "bars": len(bars),
            "loaded_range": [iso_day(bars[0][data.TS]), iso_day(bars[-1][data.TS])],
            "study_range": [iso_day(data_lo), strategy.defaults["to_date"]],
            "oos_range": [walkforward.FOLDS[0][0], walkforward.FOLDS[-1][1]],
            "raw_cached_bursts": {
                str(gap): len(rows)
                for gap, rows in context["bursts"].items()
            },
            "timestamp_convention": "New York wall clock encoded as UTC",
            "bar_source": strategy.bars,
        },
        "predeclared_definition": {
            "burst": (
                "maximal same-side raw-trade run with adjacent receive-time "
                "gaps <= gap_ms"
            ),
            "directional_levels": (
                "BUY: (last ask-first ask)/0.25; "
                "SELL: (first bid-last bid)/0.25"
            ),
            "entry": "strongest qualifying burst per minute; next minute open",
            "position_limit": "one open position",
            "stop": strategy.defaults["stop"],
            "target": strategy.defaults["target"],
            "time_stop_minutes": strategy.defaults["time_stop"],
            "spread_points": strategy.execution.entry_cost,
            "ml_minimum": ML_MINIMUM,
        },
        "grid": strategy.grid,
        "non_overlapping_effect": {
            "primary_horizon_minutes": PRIMARY_HORIZON,
            "horizons": effects,
        },
        "default_full_sample": default_full,
        "walkforward": serialise_walkforward(
            wf, stitched, fixed, cv, gate, median
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "cofire_with_absorption_reversal": cofire,
        "ml": ml,
        "cumulative_trials": trials.total(NAME),
        "notes": [
            "All raw tables were queried read-only; caches live under sandbox/.cache.",
            "Day/time/side, sizing, and cost tables are diagnostics and are not adopted.",
            "The fixed ML test does not authorize a second model or threshold search.",
            "S4 is rejected unless both the standard promotion gate and <=30% cofire pass.",
            "The raw Databento history ends 2026-07-16; no later Bookmap proxy is spliced.",
        ],
    }

    print("\nS4 non-overlapping fixed-horizon effects")
    for horizon in HORIZONS:
        stat = effects[str(horizon)]
        print(
            f"  {horizon:>2}m n={stat['trades']:>5} "
            f"points={stat['points']:>9.1f} edge={stat['edge']:>7.3f} "
            f"t={stat['edge_t']:>6.2f}"
        )
    print("\nS4 fixed filters (diagnostic only)")
    for name, stat in filters.items():
        print(
            f"  {name:<13} n={stat['trades']:>5} pts={stat['points']:>9.1f} "
            f"edge={stat['edge']:>7.3f} improved={stat['improved_folds']}/12"
        )
    print("\nS4 sizing")
    for name, stat in sizing.items():
        print(
            f"  {name:<36} pnl={stat['pnl']:>9.2f} "
            f"dd={stat['max_dd']:>9.2f} pf={stat['pf']:>6.3f}"
        )
    print(
        "\nS4 cofire: "
        f"{cofire['cofires']}/{cofire['s4_trades']} "
        f"({100 * cofire['cofire_rate']:.2f}%)"
    )
    if ml["run"]:
        print(
            "S4 ML OOS: "
            f"baseline {ml['baseline_oos']['points']:+.1f} pts / "
            f"{ml['baseline_oos']['trades']} trades; model "
            f"{ml['model_oos']['points']:+.1f} pts / "
            f"{ml['model_oos']['trades']} trades; "
            f"improved {ml['improved_folds']}/12 folds"
        )
    print(f"\nS4 verdict: {result['status'].upper()}")

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
