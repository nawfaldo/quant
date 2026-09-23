"""Complete, reproducible A5 rule-based research run.

The primary experiment is the pre-declared 27-cell anchored walk-forward plus
six-block purged CV.  Day/time/side filters, sizing rules, and execution costs
are fixed diagnostics on the walk-forward median.  They are reported rather
than used to reopen a failed entry search.
"""

import argparse
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import subprocess

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PROJECT_ROOT

STRATEGY_NAME = "Air Pocket Fade"
INITIAL = 1_000.0
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def inside(fills, lo, hi):
    return [fill for fill in fills if lo <= fill.entry_ts < hi]


def point_stats(fills):
    points = [fill.points for fill in fills]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "pf": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
    }


def score(fills, ex, lo, hi):
    selected = inside(fills, lo, hi)
    sized = execution.size(selected, ex)
    return {
        **metrics.stats(sized, initial=ex.initial, span=(lo, hi)),
        **point_stats(selected),
    }


def fills_for(strategy, bars, context, params):
    signals = strategy.signals(
        bars, context, "all", strategy.all_params(params)
    )
    return execution.resolve(bars, signals, strategy.execution)


def filter_fills(fills, name):
    weekdays = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
    if name == "baseline":
        return fills
    if name == "long_only":
        return [fill for fill in fills if fill.side == execution.LONG]
    if name == "short_only":
        return [fill for fill in fills if fill.side == execution.SHORT]
    if name.startswith("skip_") and name[5:] in weekdays:
        day = weekdays[name[5:]]
        return [
            fill
            for fill in fills
            if (fill.entry_ts // 86_400 + 3) % 7 != day
        ]

    def minute(fill):
        return (fill.entry_ts % 86_400) // 60

    windows = {
        "open_only": lambda value: value < 660,
        "mid_only": lambda value: 660 <= value < 840,
        "late_only": lambda value: value >= 840,
        "skip_open": lambda value: value >= 660,
        "skip_mid": lambda value: value < 660 or value >= 840,
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
    improved = 0
    rows = []
    for _, _, lo, hi in walkforward.fold_windows():
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
        # execution.size allocates risk / stop; changing the sizing stop only
        # changes quantity and leaves the already-resolved trade outcome intact.
        adjusted.append(replace(fill, stop=fill.stop / multiplier))
    return execution.size(adjusted, ex), multipliers


def serialise_walkforward(result, stitched, fixed, cv, gate, median):
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
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    if record_trials:
        trials.record(
            STRATEGY_NAME, 1, "pre-declared default diagnostic before any sweep"
        )
    default_fills = fills_for(strategy, bars, context, {})
    default_full = score(
        default_fills,
        strategy.execution,
        bars[0][data.TS],
        bars[-1][data.TS] + 60,
    )

    wf = walkforward.run(
        strategy,
        initial=INITIAL,
        progress=False,
        record_trials=record_trials,
    )
    _reported_median, stitched = walkforward.report(wf)
    median = (
        walkforward.median_params(wf["picks"], strategy)
        if wf["picks"]
        else {axis: strategy.defaults[axis] for axis in strategy.grid}
    )
    fixed, _fixed_sized = walkforward.verify(
        wf, median, "fixed fold-median"
    )
    cv = purged_cv.run(
        strategy,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=record_trials,
    )
    median_fills = fills_for(strategy, bars, context, median)
    filtered = {
        name: filter_fills(median_fills, name) for name in FILTER_NAMES
    }
    filters = {}
    baseline_oos = inside(filtered["baseline"], lo, hi)
    for name, fills in filtered.items():
        improved, folds = fold_improvements(
            inside(fills, lo, hi), baseline_oos
        )
        filters[name] = {
            **score(fills, strategy.execution, lo, hi),
            "improved_folds": improved,
            "fold_deltas": folds,
        }
    if record_trials:
        trials.record(
            STRATEGY_NAME,
            len(FILTER_NAMES) - 1,
            "fixed OOS day/time/side diagnostics; not selected",
        )

    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        sizing[f"fixed_fraction_{100 * risk:.2f}%"] = score(
            median_fills,
            replace(strategy.execution, risk=risk),
            lo,
            hi,
        )
    atr = data.atr_by_day(bars, 20)
    train_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < lo
    )
    target_atr = train_atr[len(train_atr) // 2]
    vol_sized, multipliers = inverse_atr_size(
        baseline_oos, strategy.execution, atr, target_atr
    )
    sizing["causal_inverse_atr_0.50%"] = {
        **metrics.stats(vol_sized, initial=INITIAL, span=(lo, hi)),
        "target_atr": round(target_atr, 4),
        "mean_risk_multiplier": round(
            sum(multipliers) / len(multipliers), 4
        ),
    }
    if record_trials:
        trials.record(
            STRATEGY_NAME,
            4,
            "fixed sizing diagnostics; exposure only",
        )

    # The final search haircut includes every family trial persisted above, not
    # only the count captured when the walk-forward object was first created.
    wf["trials"] = trials.total(STRATEGY_NAME)
    gate = walkforward.gate(wf, stitched, cv["blocks"])

    costs = {}
    for spread in COSTS:
        repriced = cost_adjust(
            median_fills, strategy.execution.entry_cost, spread
        )
        costs[str(spread)] = score(
            repriced,
            replace(strategy.execution, slippage=spread, commission_per_lot=0.0),
            lo,
            hi,
        )

    result = {
        "strategy": STRATEGY_NAME,
        "status": "promoted" if gate["passed"] else "rejected",
        "git_sha": git_sha(),
        "data": {
            "bars": len(bars),
            "loaded_range": list(strategy.date_range),
            "oos_range": [
                walkforward.FOLDS[0][0],
                walkforward.FOLDS[-1][1],
            ],
            "execution": asdict(strategy.execution),
        },
        "hypothesis": (
            "Fade a large one-minute price displacement when causal, "
            "source/slot-normalized trade count and aggressive volume are low."
        ),
        "fixed_choices": {
            "normalization_sessions": 20,
            "stop": strategy.defaults["stop"],
            "target": strategy.defaults["target"],
            "time_stop": strategy.defaults["time_stop"],
            "entry_window": [
                strategy.defaults["entry_from"],
                strategy.defaults["entry_to"],
            ],
            "max_spread": strategy.defaults["max_spread"],
        },
        "grid": strategy.grid,
        "default_full_sample": default_full,
        "walkforward": serialise_walkforward(
            wf, stitched, fixed, cv, gate, median
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "ml": {
            "run_separately": True,
            "reason": (
                f"The default produced {default_full['trades']} occupied "
                "trades, enough for one fixed regularized meta-labeler."
            ),
        },
        "notes": [
            "Day/time/side results inspect OOS outcomes and are not adopted.",
            "Sizing changes exposure, not per-unit edge.",
            "No bracket, hold, model family, threshold, or feature subset was tuned.",
        ],
    }
    result["cumulative_trials"] = trials.total(STRATEGY_NAME)

    print("\nA5 fixed day/time/side diagnostics")
    for name, stat in filters.items():
        print(
            f"  {name:<13} n={stat['trades']:>4} pts={stat['points']:>8.1f} "
            f"edge={stat['edge']:>7.3f} pf={stat['pf']:>5.3f} "
            f"improved={stat['improved_folds']}/12"
        )
    print("\nA5 sizing")
    for name, stat in sizing.items():
        print(
            f"  {name:<28} pnl={stat['pnl']:>8.2f} "
            f"dd={stat['max_dd']:>7.2f} pf={stat['pf']:>5.3f}"
        )
    print("\nA5 cost sensitivity")
    for spread, stat in costs.items():
        print(
            f"  spread={spread:<4} pnl={stat['pnl']:>8.2f} "
            f"pts={stat['points']:>8.1f} pf={stat['pf']:>5.3f}"
        )

    if out_path:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    run(args.out, args.record_trials)


if __name__ == "__main__":
    main()
