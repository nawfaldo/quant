"""Stage 3 diagnostic: one-axis-at-a-time sensitivity over *every* parameter.

This is a measurement, not a selection. Selection happens in `walkforward.py`
over <=3 tied axes; this scan exists to answer two questions the plan asks
before that search is designed:

  * which parameters the result actually depends on, and
  * whether the long/short asymmetry in the compiled brackets is real or is an
    artifact of having searched six bracket axes at once.

Everything is scored on the full sample with every other parameter held at its
compiled value, so the numbers are in-sample and are never used to pick a
winner.
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
    "buy_delta": [0, 25, 50, 75, 100, 150, 200],
    "buy_stop": [20, 30, 40, 50, 60, 75, 90],
    "buy_target": [30, 40, 50, 60, 75, 100, 125],
    "sell_delta": [50, 75, 100, 150, 200, 300, 400],
    "sell_stop": [20, 30, 40, 50, 60, 75, 90],
    "sell_target": [30, 40, 50, 60, 75, 100, 125],
    "short_trend_days": [0, 20, 30, 35, 45, 60, 90],
    "skip_thursday": [True, False],
    "max_spread": [0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 99.0],
    "spread_pct": [0, 0.5, 0.6, 0.7, 0.8, 0.9],
}

FIELDS = ("pnl", "trades", "pf", "msharpe", "pos_rate", "max_loss_streak",
          "worst_quarter", "top_month_share", "max_dd")


def row(label, stat):
    return (f"  {label:>10}  {stat['pnl']:>8.1f} {stat['trades']:>5} "
            f"{stat['pf']:>5.2f} {stat['msharpe']:>6.2f} {stat['pos_rate']:>5.2f} "
            f"{stat['max_loss_streak']:>3} {stat['worst_quarter']:>8.1f} "
            f"{stat['top_month_share']:>5.2f} {stat['max_dd']:>7.1f}")


HEADER = (f"  {'value':>10}  {'pnl':>8} {'tr':>5} {'pf':>5} {'mSh':>6} "
          f"{'pos':>5} {'str':>3} {'wQtr':>8} {'top':>5} {'dd':>7}")


def main():
    strategy = strategies.get("Hourly Delta Reversal")
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()

    base, _ = search.backtest(strategy, ex=ex, bars=bars, context=context)
    print(f"data: {len(bars)} bars, initial ${INITIAL:.0f}, cost {ex.entry_cost}")
    print("\ncompiled baseline")
    print(HEADER)
    print(row("--", base))

    for axis, values in AXES.items():
        print(f"\n{axis}   (compiled {strategy.defaults[axis]})")
        print(HEADER)
        for value in values:
            stat, _ = search.backtest(strategy, {axis: value}, ex=ex, bars=bars,
                                      context=context)
            mark = " *" if value == strategy.defaults[axis] else ""
            print(row(f"{value}{mark}", stat))
        sys.stdout.flush()

    # Risk fraction: pure sizing, so it cannot change which trades are taken --
    # only the compounding path. Worth one look at a $1000 account, where the
    # 0.01 quantity step is coarse relative to the position size.
    print("\nrisk fraction   (compiled 0.005)")
    print(HEADER)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group,
                                        strategy.all_params(None)))
    fills = execution.resolve(bars, signals, ex)
    for risk in (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03):
        stat = metrics.stats(execution.size(fills, ex.with_risk(risk)),
                             initial=INITIAL)
        print(row(f"{risk}", stat))


if __name__ == "__main__":
    main()
