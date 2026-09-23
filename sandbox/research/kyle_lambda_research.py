"""Persist the complete A3 result without silently reopening optimization.

The primary 27-cell walk-forward and purged-CV selections are reproduced from
the already charged grid.  The filter variants are fixed diagnostics on the
median configuration, not another optimizer:

* top-five book pressure must agree with persistent aggressive flow;
* the observed spread cap tightens from 1.25 to 0.75 points;
* both gates together.

Sizing is evaluated separately because it cannot change per-unit edge:
0.25/0.50/1.00% fixed-fraction risk and one causal volatility-targeted version.
The volatility target is the median 20-session ATR known before OOS begins;
the multiplier is target/current ATR, clipped to [0.5, 1.5].
"""

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward

STRATEGY_NAME = "Kyle Lambda Continuation"
INITIAL = 1_000.0
MEDIAN_PARAMS = {"lambda_z": -0.5, "flow_lookback": 3, "stop": 30}
FILTERS = (
    ("baseline", {}),
    ("top5_agreement", {"confirmation": "top5"}),
    ("tight_spread", {"max_spread": 0.75}),
    (
        "top5_agreement_tight_spread",
        {"confirmation": "top5", "max_spread": 0.75},
    ),
)
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)


def inside(fills, lo, hi):
    return [fill for fill in fills if lo <= fill.entry_ts < hi]


def score(fills, ex, lo, hi):
    selected = inside(fills, lo, hi)
    sized = execution.size(selected, ex)
    stat = metrics.stats(sized, initial=ex.initial, span=(lo, hi))
    points = [fill.points for fill in selected]
    stat["points"] = round(sum(points), 2)
    stat["edge"] = round(sum(points) / len(points), 4) if points else 0.0
    stat["edge_t"] = round(walkforward.edge_t(points), 3)
    return stat


def fills_for(strategy, bars, context, overrides):
    params = strategy.all_params({**MEDIAN_PARAMS, **overrides})
    signals = strategy.signals(bars, context, "all", params)
    return execution.resolve(bars, signals, strategy.execution)


def cost_adjust(fills, original_spread, new_spread):
    """Reprice resolved fills because spread changes points, not exit timing."""
    adjustment = original_spread - new_spread
    return [replace(fill, points=fill.points + adjustment) for fill in fills]


def volatility_sized(fills, ex, atr, target):
    """Fixed-fraction sizing with a causal, capped inverse-ATR multiplier."""
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


def serialise_walkforward(result, stitched, median_fixed, cv, gate):
    rows = []
    for index, params, train, test, plateau, points in result["rows"]:
        rows.append(
            {
                "fold": index,
                "params": params,
                "train": train,
                "test": test,
                "plateau_t": plateau,
                "test_points": round(sum(points), 2),
            }
        )
    return {
        "folds": rows,
        "stitched": stitched,
        "median_params": MEDIAN_PARAMS,
        "median_fixed": median_fixed,
        "purged_cv": cv,
        "gate": {
            "passed": gate["passed"],
            "checks": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in gate["checks"]
            ],
        },
    }


def run(out_path=None, record_filter_trials=False):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    wf = walkforward.run(
        strategy, initial=INITIAL, progress=False, record_trials=False
    )
    _reported_median, stitched = walkforward.report(wf)
    median_fixed, _median_sized = walkforward.verify(
        wf, MEDIAN_PARAMS, "frozen diagnostic median"
    )
    cv = purged_cv.run(
        strategy,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=False,
    )
    gate = walkforward.gate(wf, stitched, cv["blocks"])

    filter_fills = {
        name: fills_for(strategy, bars, context, overrides)
        for name, overrides in FILTERS
    }
    filters = {
        name: score(fills, strategy.execution, lo, hi)
        for name, fills in filter_fills.items()
    }
    if record_filter_trials:
        trials.record(
            STRATEGY_NAME,
            len(FILTERS),
            "fixed diagnostic OOS ablation: top5 agreement and tighter spread",
        )

    baseline = inside(filter_fills["baseline"], lo, hi)
    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        ex = replace(strategy.execution, risk=risk)
        sizing[f"fixed_fraction_{100 * risk:.2f}%"] = score(
            filter_fills["baseline"], ex, lo, hi
        )

    atr = data.atr_by_day(bars, 20)
    training_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < lo
    )
    target_atr = training_atr[len(training_atr) // 2]
    vol_sized, multipliers = volatility_sized(
        baseline, strategy.execution, atr, target_atr
    )
    vol_stat = metrics.stats(vol_sized, initial=INITIAL, span=(lo, hi))
    vol_stat["target_atr"] = round(target_atr, 4)
    vol_stat["mean_risk_multiplier"] = round(
        sum(multipliers) / len(multipliers), 4
    )
    sizing["causal_inverse_atr_0.50%"] = vol_stat

    costs = {}
    original_spread = strategy.execution.entry_cost
    for spread in COSTS:
        repriced = cost_adjust(baseline, original_spread, spread)
        ex = replace(strategy.execution, slippage=spread, commission_per_lot=0.0)
        costs[str(spread)] = score(repriced, ex, lo, hi)

    result = {
        "strategy": STRATEGY_NAME,
        "status": "rejected",
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
            "Continuation in the direction of persistent aggressive flow when "
            "minute Kyle lambda is cheap versus its causal source/slot history."
        ),
        "grid": strategy.grid,
        "cumulative_trials": trials.total(STRATEGY_NAME),
        "ml": {
            "run": False,
            "reason": (
                "Only 101 stitched OOS trades and 197 fixed-median OOS trades; "
                "the population is not in the requested thousands."
            ),
        },
        "walkforward": serialise_walkforward(
            wf, stitched, median_fixed, cv, gate
        ),
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "notes": [
            "Filter and sizing results are diagnostic only because the entry failed.",
            "Sizing changes exposure, not the negative per-unit edge.",
            "No filter threshold, ML model, or sizing target was selected on PnL.",
        ],
    }
    # Reflect the optional ledger write in the persisted count.
    result["cumulative_trials"] = trials.total(STRATEGY_NAME)

    print("\nA3 fixed diagnostic filters")
    for name, stat in filters.items():
        print(
            f"  {name:<31} n={stat['trades']:>4} pts={stat['points']:>8.1f} "
            f"edge={stat['edge']:>7.3f} pf={stat['pf']:>5.3f} "
            f"pos={stat['pos_months']}/{stat['n_months']}"
        )
    print("\nA3 sizing")
    for name, stat in sizing.items():
        print(
            f"  {name:<31} pnl={stat['pnl']:>8.2f} "
            f"dd={stat['max_dd']:>7.2f} pf={stat['pf']:>5.3f}"
        )
    print("\nA3 cost sensitivity")
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
    parser.add_argument("--record-filter-trials", action="store_true")
    args = parser.parse_args()
    run(args.out, args.record_filter_trials)


if __name__ == "__main__":
    main()
