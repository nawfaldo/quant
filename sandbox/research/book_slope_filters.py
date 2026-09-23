"""Small, fixed filter and sizing ablation for A2 Book Slope Asymmetry.

This is intentionally not another optimizer.  Six mechanically motivated
configurations are scored once on the anchored walk-forward OOS span, using the
unchanged baseline threshold and bracket.  Risk fractions are then applied to
the unfiltered fills as exposure controls; they cannot change per-unit edge.
"""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward

STRATEGY_NAME = "Book Slope Asymmetry"
INITIAL = 1_000.0

VARIANTS = (
    ("baseline", {}),
    ("top5_agreement", {"confirmation": "top5"}),
    ("trade_delta_agreement", {"confirmation": "delta"}),
    ("both_agree", {"confirmation": "both"}),
    ("tight_spread", {"max_spread": 0.75}),
    ("both_agree_tight_spread", {"confirmation": "both", "max_spread": 0.75}),
)


def fills_for(strategy, bars, context, overrides):
    params = strategy.all_params(overrides)
    signals = strategy.signals(bars, context, "all", params)
    return execution.resolve(bars, signals, strategy.execution)


def score(fills, ex, lo, hi):
    inside = [fill for fill in fills if lo <= fill.entry_ts < hi]
    sized = execution.size(inside, ex)
    stat = metrics.stats(sized, initial=ex.initial, span=(lo, hi))
    points = [fill.points for fill in inside]
    stat["points"] = round(sum(points), 2)
    stat["edge"] = round(sum(points) / len(points), 4) if points else 0.0
    return stat


def run(out_path=None):
    strategy = strategies.get(STRATEGY_NAME)
    strategy.execution = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    lo = metrics.split_ts(walkforward.FOLDS[0][0])
    hi = metrics.split_ts(walkforward.FOLDS[-1][1])

    fills = {
        name: fills_for(strategy, bars, context, overrides)
        for name, overrides in VARIANTS
    }
    variants = {
        name: score(candidate_fills, strategy.execution, lo, hi)
        for name, candidate_fills in fills.items()
    }
    trials.record(
        STRATEGY_NAME,
        len(VARIANTS),
        "fixed OOS filter ablation: confirmation and spread",
    )

    baseline = fills["baseline"]
    sizing = {}
    for risk in (0.0025, 0.005, 0.01):
        ex = replace(strategy.execution, risk=risk)
        sizing[f"{100 * risk:.2f}%"] = score(baseline, ex, lo, hi)

    result = {
        "strategy": STRATEGY_NAME,
        "status": "diagnostic_only",
        "oos_range": [walkforward.FOLDS[0][0], walkforward.FOLDS[-1][1]],
        "filter_variants": variants,
        "risk_fraction_sizing": sizing,
        "note": (
            "Sizing is exposure control only. It cannot rescue negative "
            "per-unit edge or improve profit factor."
        ),
    }
    print("A2 fixed OOS filter ablation")
    for name, stat in variants.items():
        print(
            f"  {name:<25} n={stat['trades']:>5} pts={stat['points']:>9.1f} "
            f"edge={stat['edge']:>7.3f} pf={stat['pf']:>5.3f} "
            f"pos={stat['pos_months']}/{stat['n_months']}"
        )
    print("\nRisk-fraction sizing on the unchanged baseline")
    for risk, stat in sizing.items():
        print(
            f"  {risk:>5} pnl={stat['pnl']:>9.2f} dd={stat['max_dd']:>8.2f} "
            f"pf={stat['pf']:>5.3f}"
        )
    if out_path:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    run(args.out)


if __name__ == "__main__":
    main()
