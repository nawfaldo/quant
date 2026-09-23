"""Stage 3 diagnostic for Deep OFI Momentum: every parameter, one axis at a time.

A measurement, not a selection. Selection happens in `walkforward.py` over <=3
tied axes; this scan exists to answer what the plan asks before that search is
designed -- which parameters the result depends on at all, and whether the
compiled values sit on anything wider than a point.

Everything is scored on the full sample with every other parameter at its
compiled value, so these numbers are in-sample and never pick a winner.

The one axis here that is not a parameter of the compiled strategy is
`direction`. Deep OFI aggregates ten book levels, and adds at levels 2-10 are
largely non-executable liquidity that is pulled as price approaches -- so the
sign of the deep statistic is not obviously the sign of the move. Whether this
signal is momentum or mean reversion is the first thing worth knowing about a
strategy sitting at pf 0.99, and it costs one column to ask.
"""
import sys
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import search
from sandbox import strategies

INITIAL = 1000.0

AXES = {
    "halflife": [1, 2, 3, 5, 8, 12],
    "ofi_z": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "stop": [10, 15, 20, 30, 40, 50, 60],
    "target": [15, 20, 30, 45, 60, 80, 100],
    "time_stop": [5, 10, 15, 20, 30, 45, 60],
    "opposing_imbalance": [0.1, 0.2, 0.3, 0.5, 1.0],
    "max_spread": [0.5, 0.75, 1.0, 1.5, 2.0, 99.0],
    "entry_from": [570, 585, 600, 630, 660],
    "entry_to": [840, 870, 900, 930, 944],
    "scale": ["raw", "ratio"],
    "norm": ["global", "slot"],
    "require_delta_agreement": [False, True],
    "direction": ["momentum", "reversion"],
    "side": ["both", "long", "short"],
}

#: (halflife, scale, norm) keys the scan needs from `context()`
KEYS = [(h, s, n) for h in AXES["halflife"] for s in AXES["scale"]
        for n in AXES["norm"]]

FIELDS = ("pnl", "trades", "pf", "msharpe", "pos_rate", "max_loss_streak",
          "worst_quarter", "top_month_share", "max_dd")

HEADER = (f"  {'value':>12}  {'pnl':>8} {'tr':>5} {'pf':>5} {'mSh':>6} "
          f"{'pos':>5} {'str':>3} {'wQtr':>8} {'top':>5} {'dd':>7}")


def row(label, stat):
    return (f"  {label:>12}  {stat['pnl']:>8.1f} {stat['trades']:>5} "
            f"{stat['pf']:>5.2f} {stat['msharpe']:>6.2f} {stat['pos_rate']:>5.2f} "
            f"{stat['max_loss_streak']:>3} {stat['worst_quarter']:>8.1f} "
            f"{stat['top_month_share']:>5.2f} {stat['max_dd']:>7.1f}")


def wide_context(strategy, bars, features):
    """`context()` over every (halflife, scale, norm) this scan touches."""
    return {key: strategy._statistics(bars, features, *key) for key in KEYS}


def main():
    strategy = strategies.get("Deep OFI Momentum")
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    features = data.load_l2_features(strategy.symbol)
    context = wide_context(strategy, bars, features)

    base, _ = search.backtest(strategy, ex=ex, bars=bars, context=context)
    print(f"data: {len(bars)} bars, initial ${INITIAL:.0f}, cost {ex.entry_cost} (spread {ex.spread} + slippage {ex.slippage} + commission)")
    print(f"range: {data.bar_range(strategy.bars, strategy.symbol)}")
    print("\ncompiled baseline")
    print(HEADER)
    print(row("--", base))
    print("  months", base["months"])

    for axis, values in AXES.items():
        compiled = strategy.defaults.get(axis, "-")
        print(f"\n{axis}   (compiled {compiled})")
        print(HEADER)
        for value in values:
            stat, _ = search.backtest(strategy, {axis: value}, ex=ex, bars=bars,
                                      context=context)
            mark = " *" if value == compiled else ""
            print(row(f"{value}{mark}", stat))
        sys.stdout.flush()

    # Sizing only -- it cannot change which trades are taken, only the
    # compounding path and how coarse the 0.01 step is at $1000.
    print("\nrisk fraction   (compiled 0.005)")
    print(HEADER)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group,
                                        strategy.all_params(None)))
    fills = execution.resolve(bars, signals, ex)
    for risk in (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02):
        stat = metrics.stats(execution.size(fills, ex.with_risk(risk)),
                             initial=INITIAL)
        print(row(f"{risk}", stat))

    # Cost sensitivity: 3843 trades on a 45/30 bracket, so the spread is a
    # first-order term rather than a rounding one. NQ's real spread is 0.75-1.0.
    print("\nspread   (environment 0.2)")
    print(HEADER)
    for spread in (0.0, 0.1, 0.2, 0.25, 0.4, 0.5, 0.75, 1.0):
        stat, _ = search.backtest(strategy, ex=replace(ex, slippage=spread, commission_per_lot=0.0),
                                  bars=bars, context=context)
        print(row(f"{spread}", stat))


if __name__ == "__main__":
    main()
