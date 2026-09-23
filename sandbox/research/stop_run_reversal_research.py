"""Complete, leakage-controlled research run for S2 Stop Run Reversal.

The primary experiment is one pre-declared 27-cell anchored walk-forward plus
six-block purged CV.  Day/time/side filters and sizing rules are fixed
diagnostics on the fold-median configuration; they cannot reopen selection.
ML is explicitly refused because the event population is structurally capped
at two per session and therefore cannot reach the required thousands.
"""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
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

STRATEGY_NAME = "Stop Run Reversal"
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


def point_months(fills, lo, hi):
    values = {}
    for fill in fills:
        if lo <= fill.entry_ts < hi:
            key = metrics.month_key(fill.entry_ts)
            values[key] = values.get(key, 0.0) + fill.points
    return {
        key: round(value, 2)
        for key, value in metrics.pad(values, lo, hi).items()
    }


def point_stats(fills):
    points = [fill.points for fill in fills]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "pf_points": (
            round(wins / losses, 4)
            if losses
            else (999.0 if wins else 0.0)
        ),
    }


def score(fills, ex, lo, hi):
    selected = inside(fills, lo, hi)
    sized = execution.size(selected, ex)
    return {
        **metrics.stats(sized, initial=ex.initial, span=(lo, hi)),
        **point_stats(selected),
        "point_months": point_months(selected, lo, hi),
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


def cofire_diagnostic(s2_fills):
    """Entry-time overlap with compiled HDR, within a fixed five-minute band."""
    other = strategies.get("Hourly Delta Reversal")
    bars = data.load_bars(other.bars, other.symbol)
    context = other.context()
    signals = []
    for group in other.groups():
        signals.extend(
            other.signals(
                bars, context, group, other.all_params()
            )
        )
    hdr = execution.resolve(bars, sorted(signals, key=lambda row: row.index),
                            other.execution)
    hits = sum(
        any(abs(fill.entry_ts - row.entry_ts) <= 5 * 60 for row in hdr)
        for fill in s2_fills
    )
    return {
        "band_minutes": 5,
        "s2_trades": len(s2_fills),
        "hdr_trades": len(hdr),
        "cofires": hits,
        "cofire_rate": round(hits / len(s2_fills), 4) if s2_fills else 0.0,
    }


def run(out_path=None, record_trials=False):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    data_lo = bars[0][data.TS]
    study_hi = metrics.split_ts(strategy.defaults["to_date"]) + 86_400
    oos_lo = metrics.split_ts(walkforward.FOLDS[0][0])
    oos_hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    if record_trials:
        trials.record(
            STRATEGY_NAME, 1, "pre-declared default diagnostic before grid"
        )
    default_fills = fills_for(strategy, bars, context, {})
    default_full = score(
        default_fills, strategy.execution, data_lo, study_hi
    )

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
    fixed, _fixed_sized = walkforward.verify(
        wf, median, "fixed fold-median"
    )
    median_fills = fills_for(strategy, bars, context, median)
    fixed["point_months"] = point_months(median_fills, oos_lo, oos_hi)
    fixed.update(point_stats(inside(median_fills, oos_lo, oos_hi)))

    cv = purged_cv.run(
        strategy,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=record_trials,
    )

    baseline_oos = inside(median_fills, oos_lo, oos_hi)
    filters = {}
    for name in FILTER_NAMES:
        selected = filter_fills(median_fills, name)
        improved, folds = fold_improvements(
            inside(selected, oos_lo, oos_hi), baseline_oos
        )
        filters[name] = {
            **score(selected, strategy.execution, oos_lo, oos_hi),
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
            oos_lo,
            oos_hi,
        )
    atr = data.atr_by_day(bars, 20)
    train_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < oos_lo
    )
    target_atr = train_atr[len(train_atr) // 2]
    vol_sized, multipliers = inverse_atr_size(
        baseline_oos, strategy.execution, atr, target_atr
    )
    sizing["causal_inverse_atr_0.50%"] = {
        **metrics.stats(
            vol_sized,
            initial=INITIAL,
            span=(oos_lo, oos_hi),
        ),
        "target_atr": round(target_atr, 4),
        "mean_risk_multiplier": round(
            sum(multipliers) / len(multipliers), 4
        ) if multipliers else 1.0,
    }
    if record_trials:
        trials.record(
            STRATEGY_NAME, 4, "fixed sizing diagnostics; exposure only"
        )

    costs = {}
    for spread in COSTS:
        repriced = cost_adjust(
            median_fills, strategy.execution.entry_cost, spread
        )
        costs[str(spread)] = score(
            repriced,
            replace(strategy.execution, spread=spread),
            oos_lo,
            oos_hi,
        )

    wf["trials"] = trials.total(STRATEGY_NAME)
    gate = walkforward.gate(wf, stitched, cv["blocks"])
    qualifying = default_full["trades"]
    max_structural_events = 2 * len(
        {
            bar[data.TS] // 86_400
            for bar in bars
            if data_lo <= bar[data.TS] < study_hi
            and 570 <= (bar[data.TS] % 86_400) // 60 <= 959
        }
    )

    result = {
        "strategy": STRATEGY_NAME,
        "status": "promoted" if gate["passed"] else "rejected",
        "git_sha": git_sha(),
        "data": {
            "bars": len(bars),
            "loaded_range": [
                datetime.fromtimestamp(
                    bars[0][data.TS], timezone.utc
                ).date().isoformat(),
                datetime.fromtimestamp(
                    bars[-1][data.TS], timezone.utc
                ).date().isoformat(),
            ],
            "study_range": [
                datetime.fromtimestamp(data_lo, timezone.utc).date().isoformat(),
                strategy.defaults["to_date"],
            ],
            "oos_range": [
                walkforward.FOLDS[0][0],
                walkforward.FOLDS[-1][1],
            ],
            "execution": asdict(strategy.execution),
        },
        "hypothesis": (
            "Fade a prior-RTH-extreme sweep only after price closes back "
            "inside and minute trade delta flips against the sweep."
        ),
        "fixed_choices": {
            "session": "09:30-15:59 New York",
            "attempts": "at most one per prior-session side per day",
            "stop": strategy.defaults["stop"],
            "target": strategy.defaults["target"],
            "time_stop": strategy.defaults["time_stop"],
            "entry_window": [
                strategy.defaults["entry_from"],
                strategy.defaults["entry_to"],
            ],
        },
        "grid": strategy.grid,
        "default_full_sample": default_full,
        "walkforward": serialise_walkforward(
            wf, stitched, fixed, cv, gate, median
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "cofire_with_hourly_delta_reversal": cofire_diagnostic(median_fills),
        "ml": {
            "run": False,
            "qualifying_occupied_events": qualifying,
            "structural_max_events": max_structural_events,
            "reason": (
                "The strategy is capped at two events per session and produced "
                f"{qualifying} occupied trades, not the thousands required for "
                "a leakage-controlled meta-labeler."
            ),
        },
        "notes": [
            "Day/time/side outcomes inspect OOS data and are not adopted.",
            "Sizing changes exposure, not the per-unit edge.",
            "July 2026 is partial through July 23; later live rows were excluded.",
            "No ML, bracket, hold, filter, or feature subset was tuned.",
        ],
        "cumulative_trials": trials.total(STRATEGY_NAME),
    }

    print("\nS2 fixed day/time/side diagnostics")
    for name, stat in filters.items():
        print(
            f"  {name:<13} n={stat['trades']:>4} pts={stat['points']:>8.1f} "
            f"edge={stat['edge']:>7.3f} pf={stat['pf_points']:>5.3f} "
            f"improved={stat['improved_folds']}/12"
        )
    print("\nS2 sizing")
    for name, stat in sizing.items():
        print(
            f"  {name:<28} pnl={stat['pnl']:>8.2f} "
            f"dd={stat['max_dd']:>7.2f} pf={stat['pf']:>5.3f}"
        )
    print("\nS2 cost sensitivity")
    for spread, stat in costs.items():
        print(
            f"  spread={spread:<4} pnl={stat['pnl']:>8.2f} "
            f"pts={stat['points']:>8.1f} pf={stat['pf_points']:>5.3f}"
        )
    print(f"\nS2 ML: skipped ({result['ml']['reason']})")

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
