"""Run the complete leakage-controlled S6 experiment.

The experiment is intentionally one-shot:

* 27 coarse rule cells, selected only inside anchored training windows;
* six purged CV blocks as a coverage veto;
* fixed day/time/side and sizing diagnostics, never selectors;
* one standardized L2-logistic meta-labeler when the raw default population is
  at least 2,000 candidates;
* no algorithm, feature-set, threshold, or filter tournament.

All writes stay under ``sandbox``. Market data and the existing execution
primitives are read-only dependencies; the Rust server is never called.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import subprocess

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data, execution, metrics, purged_cv, search, walkforward
from sandbox.data import C, H, L, O, TS

from sandbox.strategies.s6_iceberg_exhaustion import (
    NAME,
    IcebergExhaustionBreakout,
)

SANDBOX_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SANDBOX_ROOT.parent
DEFAULT_JSON = SANDBOX_ROOT / "results" / "s6_iceberg_exhaustion_result.json"
DEFAULT_MARKDOWN = SANDBOX_ROOT / "results" / "S6_ICEBERG_EXHAUSTION.md"
INITIAL = 1_000.0
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)
ML_MINIMUM = 2_000
ML_C = 0.1
ML_THRESHOLD = 0.5
ML_SEED = 20260731
RANDOM_CONTROLS = 1_000

FEATURE_NAMES = (
    "directional_replenishment_z",
    "opposite_replenishment_z",
    "signed_break_points",
    "directional_aggression",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_trade_delta",
    "defending_replenishment_share",
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


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def resolve_candidate(bars, entry_index, side, params, spread, session_end):
    """Resolve one hypothetical entry without imposing strategy occupancy."""
    entry_bar = bars[entry_index]
    entry = entry_bar[O]
    entry_minute = (entry_bar[TS] % 86_400) // 60
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
        if exit_price is None and minute >= entry_minute + params["time_stop"]:
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
            exit_price - entry - spread
            if side == execution.LONG
            else entry - exit_price - spread
        )
        return index, execution.Fill(
            entry_ts=entry_bar[TS],
            exit_ts=bar[TS],
            side=side,
            points=points,
            price=entry,
            stop=params["stop"],
        )
    raise RuntimeError(f"candidate at bar {entry_index} did not exit")


def model_features(bar, row, side, params):
    feature = row["feature"]
    sign = 1.0 if side == execution.LONG else -1.0
    directional_z = (
        row["ask_replenishment_z"]
        if side == execution.LONG
        else row["bid_replenishment_z"]
    )
    opposite_z = (
        row["bid_replenishment_z"]
        if side == execution.LONG
        else row["ask_replenishment_z"]
    )
    bid_rep = feature["bid_replenishment"]
    ask_rep = feature["ask_replenishment"]
    rep_total = bid_rep + ask_rep
    defending_share = (
        (ask_rep if side == execution.LONG else bid_rep) / rep_total
        if rep_total > 0.0
        else 0.0
    )
    aggressive = feature["executed_at_bid"] + feature["executed_at_ask"]
    toxicity = abs(feature["trade_delta"]) / aggressive if aggressive > 0.0 else 0.0
    minute = (bar[TS] % 86_400) // 60
    weekday = (bar[TS] // 86_400 + 3) % 7
    angle = 2.0 * math.pi * weekday / 5.0
    return (
        directional_z,
        opposite_z,
        sign * row["price_change"],
        sign * (row["ask_execution_share"] - 0.5) * 2.0,
        sign * feature["top1_imbalance"],
        sign * feature["top5_imbalance"],
        sign * feature["top10_imbalance"],
        sign * (feature["microprice"] - feature["midprice"]) / 0.25,
        math.asinh(sign * feature["trade_delta"] / 100.0),
        defending_share,
        toxicity,
        math.log1p(feature["depth_event_count"]),
        row["spread"],
        (minute - params["entry_from"]) /
        max(1, params["entry_to"] - params["entry_from"]),
        math.sin(angle),
        math.cos(angle),
    )


def candidate_pool(strategy, bars, context):
    params = strategy.all_params()
    output = []
    for index, row in enumerate(context["rows"]):
        if row is None or index + 1 >= len(bars):
            continue
        nxt = bars[index + 1]
        if nxt[TS] != bars[index][TS] + 60:
            continue
        minute = (bars[index][TS] % 86_400) // 60
        if (
            not params["entry_from"] <= minute <= params["entry_to"]
            or row["spread"] > params["max_spread"]
        ):
            continue
        side = strategy.side_for(row, params)
        if side is None:
            continue
        exit_at, fill = resolve_candidate(
            bars,
            index + 1,
            side,
            params,
            strategy.execution.entry_cost,
            strategy.execution.session_end_min,
        )
        output.append(
            Candidate(
                signal_index=index,
                exit_index=exit_at,
                fill=fill,
                features=model_features(bars[index], row, side, params),
            )
        )
    return output


def apply_occupancy(candidates, probabilities=None):
    accepted = []
    free_from = -1
    for index, candidate in enumerate(candidates):
        if candidate.signal_index < free_from:
            continue
        if probabilities is not None and probabilities[index] < ML_THRESHOLD:
            continue
        accepted.append(candidate)
        free_from = candidate.exit_index
    return accepted


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=ML_C,
            class_weight="balanced",
            max_iter=1_000,
            random_state=ML_SEED,
        ),
    )


def fit_model(candidates):
    labels = np.asarray([candidate.fill.points > 0.0 for candidate in candidates])
    if len(candidates) < 100 or len(np.unique(labels)) < 2:
        return None
    model = make_model()
    model.fit(
        np.asarray([candidate.features for candidate in candidates]),
        labels,
    )
    return model


def probabilities(model, candidates):
    if model is None or not candidates:
        return np.zeros(len(candidates))
    return model.predict_proba(
        np.asarray([candidate.features for candidate in candidates])
    )[:, 1]


def point_summary(fills):
    points = [fill.points for fill in fills]
    wins = sum(point for point in points if point > 0.0)
    losses = -sum(point for point in points if point < 0.0)
    edge = sum(points) / len(points) if points else 0.0
    lo, hi = walkforward.bootstrap_edge(points, draws=5_000, seed=ML_SEED)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "mean_points": round(edge, 4),
        "t": round(walkforward.edge_t(points), 3),
        "bootstrap_95": [round(lo, 4), round(hi, 4)],
        "point_pf": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
    }


def sized_summary(fills, ex, lo, hi):
    kept = [fill for fill in fills if lo <= fill.entry_ts < hi]
    sized = execution.size(kept, replace(ex, initial=INITIAL))
    return metrics.stats(sized, initial=INITIAL, span=(lo, hi)), sized


def run_ml(strategy, bars, context, lo, hi):
    pool = candidate_pool(strategy, bars, context)
    baseline = apply_occupancy(pool)
    if len(pool) < ML_MINIMUM:
        return {
            "ran": False,
            "raw_candidates": len(pool),
            "occupied_candidates": len(baseline),
            "reason": f"raw candidate population is below {ML_MINIMUM}",
        }

    folds = []
    baseline_oos = []
    model_oos = []
    random_groups = []
    for _, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [row for row in baseline if row.fill.exit_ts < train_hi]
        test_pool = [
            row for row in pool if test_lo <= row.fill.entry_ts < test_hi
        ]
        test_baseline = [
            row for row in baseline if test_lo <= row.fill.entry_ts < test_hi
        ]
        model = fit_model(train)
        accepted = apply_occupancy(test_pool, probabilities(model, test_pool))
        baseline_oos.extend(test_baseline)
        model_oos.extend(accepted)
        random_groups.append((test_baseline, len(accepted)))
        folds.append(
            {
                "month": metrics.month_key(test_lo),
                "train": len(train),
                "raw_candidates": len(test_pool),
                "baseline_trades": len(test_baseline),
                "model_trades": len(accepted),
                "baseline_points": round(sum(row.fill.points for row in test_baseline), 2),
                "model_points": round(sum(row.fill.points for row in accepted), 2),
            }
        )

    cv_blocks = []
    cv_oos = []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        test_lo, test_hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        purge_lo = test_lo - purged_cv.EMBARGO_DAYS * 86_400
        purge_hi = test_hi + purged_cv.EMBARGO_DAYS * 86_400
        train = [
            row for row in baseline
            if row.fill.exit_ts < purge_lo or row.fill.entry_ts >= purge_hi
        ]
        test = [row for row in pool if test_lo <= row.fill.entry_ts < test_hi]
        accepted = apply_occupancy(test, probabilities(fit_model(train), test))
        cv_oos.extend(accepted)
        cv_blocks.append(
            {
                "from": lo_iso,
                "to": hi_iso,
                "train": len(train),
                **point_summary([row.fill for row in accepted]),
            }
        )

    baseline_fills = [row.fill for row in baseline_oos]
    model_fills = [row.fill for row in model_oos]
    baseline_stats, _ = sized_summary(baseline_fills, strategy.execution, lo, hi)
    model_stats, _ = sized_summary(model_fills, strategy.execution, lo, hi)

    rng = random.Random(ML_SEED)
    random_points = []
    for _ in range(RANDOM_CONTROLS):
        total = 0.0
        for rows, count in random_groups:
            if not rows or count <= 0:
                continue
            selected = rng.sample(rows, min(count, len(rows)))
            total += sum(row.fill.points for row in selected)
        random_points.append(total)
    observed = sum(fill.points for fill in model_fills)
    random_p = (
        sum(value >= observed for value in random_points) + 1
    ) / (len(random_points) + 1)

    return {
        "ran": True,
        "model": "StandardScaler + L2 LogisticRegression",
        "C": ML_C,
        "threshold": ML_THRESHOLD,
        "feature_names": FEATURE_NAMES,
        "raw_candidates": len(pool),
        "occupied_candidates": len(baseline),
        "folds": folds,
        "baseline_oos": {**point_summary(baseline_fills), "sized": baseline_stats},
        "model_oos": {**point_summary(model_fills), "sized": model_stats},
        "improved_total_points_folds": sum(
            row["model_points"] > row["baseline_points"] for row in folds
        ),
        "positive_model_folds": sum(row["model_points"] > 0.0 for row in folds),
        "matched_random": {
            "draws": RANDOM_CONTROLS,
            "p_value": round(random_p, 4),
            "median_points": round(float(np.median(random_points)), 2),
        },
        "purged_cv": {
            "blocks": cv_blocks,
            "points": round(sum(row.fill.points for row in cv_oos), 2),
            "positive_blocks": sum(block["points"] > 0.0 for block in cv_blocks),
        },
    }


def filter_fills(fills, name):
    if name == "baseline":
        return list(fills)
    if name == "long_only":
        return [fill for fill in fills if fill.side == execution.LONG]
    if name == "short_only":
        return [fill for fill in fills if fill.side == execution.SHORT]
    weekdays = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
    if name.startswith("skip_") and name[5:] in weekdays:
        target = weekdays[name[5:]]
        return [
            fill for fill in fills
            if datetime.fromtimestamp(fill.entry_ts, tz=timezone.utc).weekday() != target
        ]

    def minute(fill):
        return (fill.entry_ts % 86_400) // 60

    windows = {
        "open_only": lambda value: value < 690,
        "mid_only": lambda value: 690 <= value < 840,
        "late_only": lambda value: value >= 840,
        "skip_open": lambda value: value >= 690,
        "skip_mid": lambda value: value < 690 or value >= 840,
        "skip_late": lambda value: value < 840,
    }
    return [fill for fill in fills if windows[name](minute(fill))]


FILTER_NAMES = (
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


def fold_improvements(candidate, baseline):
    rows = []
    for _, _, lo, hi in walkforward.fold_windows():
        base = sum(fill.points for fill in baseline if lo <= fill.entry_ts < hi)
        filtered = sum(fill.points for fill in candidate if lo <= fill.entry_ts < hi)
        rows.append(
            {
                "month": metrics.month_key(lo),
                "baseline_points": round(base, 2),
                "filter_points": round(filtered, 2),
                "delta_points": round(filtered - base, 2),
            }
        )
    return sum(row["delta_points"] > 0.0 for row in rows), rows


def cost_adjust(fills, old_spread, new_spread):
    return [
        replace(fill, points=fill.points + old_spread - new_spread)
        for fill in fills
    ]


def inverse_atr_size(fills, ex, atr, target):
    adjusted = []
    multipliers = []
    for fill in fills:
        current = atr.get(fill.entry_ts // 86_400)
        multiplier = (
            max(0.5, min(1.5, target / current))
            if current is not None and current > 0.0
            else 1.0
        )
        multipliers.append(multiplier)
        adjusted.append(replace(fill, stop=fill.stop / multiplier))
    return execution.size(adjusted, ex), multipliers


def serialise_walkforward(result, stitched, fixed, median, cv, gate):
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
            for index, params, train, test, plateau, points in result["rows"]
        ],
        "stitched": stitched,
        "median_params": median,
        "fixed_median_or_default": fixed,
        "purged_cv": cv,
        "gate": {
            "passed": gate["passed"],
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in gate["checks"]
            ],
        },
    }


def markdown_report(result):
    wf = result["walkforward"]
    fixed = wf["fixed_median_or_default"]
    ml = result["ml"]
    lines = [
        "# S6 Iceberg Exhaustion Breakout",
        "",
        f"Status: **{result['status']}**. "
        + ("Eligible for further validation." if result["status"] == "promoted" else "Do not promote this version."),
        "",
        "S6 tests continuation after unusually strong same-side replenishment",
        "fails to hold price. It is a side-level proxy built from the retained",
        "one-minute rollup, not price-level proof of an individual iceberg.",
        "",
        "## Headline result",
        "",
        f"The fixed default produced **{result['default_full']['trades']:,} trades**, "
        f"**{result['default_effect']['points']:+,.2f} net points**, and a "
        f"{result['default_effect']['mean_points']:+.4f}-point mean edge "
        f"(t={result['default_effect']['t']:+.2f}). The stitched walk-forward "
        f"result was **{wf['stitched']['pnl']:+,.2f}** over "
        f"{wf['stitched']['trades']:,} trades.",
        "",
        "## Monthly performance",
        "",
        "The selected walk-forward series is the deployable result. Fixed-rule",
        "and ML columns are diagnostics over the same 2025-08 through 2026-07 span.",
        "",
        "| Month | Selected WF | Fixed rule | ML |",
        "| --- | ---: | ---: | ---: |",
    ]
    selected_months = wf["stitched"]["months"]
    fixed_months = fixed["months"]
    ml_months = ml.get("model_oos", {}).get("sized", {}).get("months", {})
    for month in sorted(set(selected_months) | set(fixed_months) | set(ml_months)):
        lines.append(
            f"| {month} | {selected_months.get(month, 0.0):+.2f} | "
            f"{fixed_months.get(month, 0.0):+.2f} | {ml_months.get(month, 0.0):+.2f} |"
        )
    lines.extend([
        "",
        "## Robustness",
        "",
        f"- Grid: {result['trial_accounting']['grid_cells']} cells across three coarse axes.",
        f"- Profitable selected folds: {sum((row['test'] or {}).get('pnl', 0) > 0 for row in wf['folds'])}/12.",
        f"- Purged-CV positive blocks: {wf['purged_cv']['positive']}/6.",
        f"- Cumulative trial count used for deflation: {result['trial_accounting']['cumulative_trials']}.",
    ])
    if ml.get("ran"):
        lines.extend([
            "",
            "## Machine learning",
            "",
            f"The single fixed L2-logistic meta-labeler saw {ml['raw_candidates']:,} raw "
            f"candidates and selected {ml['model_oos']['trades']:,} anchored OOS trades. "
            f"It produced {ml['model_oos']['points']:+,.2f} points, improved total "
            f"points in {ml['improved_total_points_folds']}/12 folds, and its "
            f"matched-random p-value was {ml['matched_random']['p_value']:.4f}. "
            f"Its mean edge was {ml['model_oos']['mean_points']:+.4f} points/trade "
            f"versus {ml['baseline_oos']['mean_points']:+.4f} for the fixed rule, "
            "so the smaller dollar loss came from lower exposure, not a better edge.",
        ])
    lines.extend([
        "",
        "Day/time/side filters and sizing variants are retained in the JSON as",
        "post-selection diagnostics. None is adopted from this same history.",
        "",
        "## Reproduce",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\python.exe -B -m unittest sandbox.tests.test_s6_iceberg_exhaustion -v",
        "py -B -m sandbox.research.s6_iceberg_exhaustion_research",
        "```",
        "",
    ])
    return "\n".join(lines)


def run(out_json=DEFAULT_JSON, out_markdown=DEFAULT_MARKDOWN):
    strategy = IcebergExhaustionBreakout()
    bars = data.load_cached_level_two_bars(strategy.symbol)
    context = strategy.context()
    ex = strategy.execution
    oos_lo = metrics.split_ts(walkforward.FOLDS[0][0])
    oos_hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    default_signals = strategy.signals(
        bars, context, "all", strategy.all_params()
    )
    default_fills = execution.resolve(bars, default_signals, ex)
    default_sized = execution.size(default_fills, ex)
    default_full = metrics.stats(
        default_sized,
        initial=INITIAL,
        span=(bars[0][TS], bars[-1][TS] + 60),
    )
    default_effect = point_summary(default_fills)
    default_effect["gross_mean_before_cost"] = round(
        default_effect["mean_points"] + ex.entry_cost, 4
    )
    default_effect["kill_test_passed"] = (
        default_effect["trades"] >= 100
        and default_effect["mean_points"] > 0.0
        and default_effect["bootstrap_95"][0] > 0.0
    )

    wf = walkforward.run(
        strategy,
        initial=INITIAL,
        progress=False,
        record_trials=False,
    )
    _, stitched = walkforward.report(wf)
    median = (
        walkforward.median_params(wf["picks"], strategy)
        if wf["picks"]
        else {axis: strategy.defaults[axis] for axis in strategy.grid}
    )
    fixed, _ = walkforward.verify(wf, median, "fixed fold-median/default")
    cv = purged_cv.run(
        strategy,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=False,
    )

    fixed_key = search.freeze({axis: median[axis] for axis in strategy.grid})
    median_fills = wf["cells"][fixed_key]
    baseline_oos = [fill for fill in median_fills if oos_lo <= fill.entry_ts < oos_hi]

    filters = {}
    for name in FILTER_NAMES:
        selected = filter_fills(baseline_oos, name)
        stat, _ = sized_summary(selected, ex, oos_lo, oos_hi)
        improved, rows = fold_improvements(selected, baseline_oos)
        filters[name] = {
            **point_summary(selected),
            "sized": stat,
            "improved_folds": improved,
            "fold_deltas": rows,
        }

    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        sized = execution.size(
            baseline_oos,
            replace(ex, initial=INITIAL, risk=risk),
        )
        sizing[f"fixed_fraction_{100 * risk:.2f}%"] = metrics.stats(
            sized, initial=INITIAL, span=(oos_lo, oos_hi)
        )
    atr = data.atr_by_day(bars, 20)
    train_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < oos_lo
    )
    atr_target = train_atr[len(train_atr) // 2]
    inverse_sized, multipliers = inverse_atr_size(
        baseline_oos, replace(ex, initial=INITIAL), atr, atr_target
    )
    sizing["causal_inverse_atr_0.50%"] = {
        **metrics.stats(inverse_sized, initial=INITIAL, span=(oos_lo, oos_hi)),
        "training_median_atr": round(atr_target, 4),
        "mean_risk_multiplier": round(sum(multipliers) / len(multipliers), 4)
        if multipliers else 0.0,
    }

    costs = {}
    for spread in COSTS:
        adjusted = cost_adjust(baseline_oos, ex.entry_cost, spread)
        stat, _ = sized_summary(
            adjusted, replace(ex, spread=spread), oos_lo, oos_hi
        )
        costs[str(spread)] = {**point_summary(adjusted), "sized": stat}

    ml = run_ml(strategy, bars, context, oos_lo, oos_hi)
    cumulative_trials = (
        1
        + len(wf["cells"])
        + len(wf["cells"])
        + len(FILTER_NAMES) - 1
        + 4
        + (3 if ml.get("ran") else 0)
    )
    wf["trials"] = cumulative_trials
    gate = walkforward.gate(wf, stitched, cv["blocks"])

    result = {
        "strategy": NAME,
        "status": "promoted" if gate["passed"] else "rejected",
        "git_sha": git_sha(),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "hypothesis": (
            "Continue a price break when unusually strong same-side "
            "replenishment and directional aggression failed to hold price."
        ),
        "scope": {
            "writes": "sandbox S6 strategy/research/test/results only",
            "server_changed": False,
            "data_access": "read-only cached QuestDB-derived inputs",
        },
        "data": {
            "bars": len(bars),
            "loaded_from": datetime.fromtimestamp(bars[0][TS], tz=timezone.utc).date().isoformat(),
            "loaded_to": datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc).date().isoformat(),
            "oos_from": walkforward.FOLDS[0][0],
            "oos_to_exclusive": walkforward.FOLDS[-1][1],
        },
        "fixed_definition": {
            "normalization_observations": 20,
            "entry": "next contiguous minute open",
            "bracket": {"stop": 12.0, "target": 20.0, "time_stop_minutes": 15},
            "entry_window": [575, 930],
            "session_flatten_minute": 945,
            "max_observed_spread": 1.25,
            "execution": asdict(ex),
        },
        "grid": strategy.grid,
        "default_full": default_full,
        "default_effect": default_effect,
        "walkforward": serialise_walkforward(
            wf, stitched, fixed, median, cv, gate
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "ml": ml,
        "trial_accounting": {
            "grid_cells": len(wf["cells"]),
            "cumulative_trials": cumulative_trials,
            "components": {
                "default": 1,
                "anchored_grid_selection": len(wf["cells"]),
                "purged_cv_grid_selection": len(wf["cells"]),
                "filter_diagnostics": len(FILTER_NAMES) - 1,
                "sizing_diagnostics": 4,
                "ml_wf_cv_random": 3 if ml.get("ran") else 0,
            },
        },
        "verdict": (
            "Promote only after independent implementation validation."
            if gate["passed"]
            else "Rejected. Do not tune more filters, thresholds, brackets, or ML models on this history."
        ),
    }

    out_json = Path(out_json)
    out_markdown = Path(out_markdown)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_markdown.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    out_markdown.write_text(markdown_report(result), encoding="utf-8")
    print(f"\nwrote {out_json}")
    print(f"wrote {out_markdown}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()
    run(args.out, args.markdown)


if __name__ == "__main__":
    main()
