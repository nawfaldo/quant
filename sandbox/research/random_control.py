"""Is 2026's edge the signal, or does any bracket work in 2026?

`year_split.py` shows Deep OFI Momentum earning -0.22 points per trade in 2025
and +3.33 in 2026, on a fixed 60/120 bracket, while the market's mean daily move
grew 32% and its mean session range 24%. Those two facts have an innocent joint
explanation: a fixed-distance target is easier to reach in a market that travels
further, so the bracket alone would earn more in 2026 whatever the entry was.

This is the control that separates them. Random entries are drawn in the same
entry window, with the same side balance, the same bracket and the same time
stop as the real signals, and the same count per year -- so the only thing that
differs is *when* the entries happen. If the real entries are not clearly better
than the random distribution in a given year, the signal contributed nothing
that year and what looks like edge is the bracket meeting the regime.

The same control is run for Hourly Delta Reversal, whose per-unit edge barely
moves between the years, as the negative case: a strategy whose entry actually
carries information should beat its random twin in *both*.
"""
import random
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import strategies
from sandbox.data import TS
from sandbox.execution import LONG, SHORT, Signal

INITIAL = 1000.0
TRIALS = 200
SEED = 20260727

#: (name, stop, target, time stop, entry window) -- each strategy's own bracket
SPECS = [
    ("Deep OFI Momentum", 60.0, 120.0, 20, (585, 930), 945),
    ("Hourly Delta Reversal", None, None, None, (570, 959), 960),
]


def year(ts):
    return metrics.month_key(ts)[:4]


def real_fills(name):
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, strategy.all_params(None)))
    return bars, ex, signals, execution.resolve(bars, signals, ex)


def eligible(bars, window):
    """Bar indices inside the entry window, by year."""
    lo, hi = window
    out = {"2025": [], "2026": []}
    for i, bar in enumerate(bars):
        minute = (bar[TS] % 86_400) // 60
        if lo <= minute <= hi:
            out[year(bar[TS])].append(i)
    return out


def points_per_trade(fills):
    return sum(f.points for f in fills) / len(fills) if fills else 0.0


def control(name, stop, target, time_stop, window):
    bars, ex, signals, fills = real_fills(name)
    pool = eligible(bars, window)
    rng = random.Random(SEED)

    print(f"\n  {name}  (bracket {stop}/{target}, time stop {time_stop})")
    print(f"    {'year':>6} {'n':>6} {'real pts/tr':>12} {'random mean':>12} "
          f"{'random sd':>10} {'z':>7} {'better than':>12}")

    for y in ("2025", "2026"):
        real = [f for f in fills if year(f.entry_ts) == y]
        if not real:
            continue
        n = len(real)
        longs = sum(1 for f in real if f.side == LONG)
        # Each strategy's own brackets, so a strategy with per-trade brackets
        # (ATR-scaled) is controlled against its own distribution of widths.
        widths = [(f.stop, abs(f.points) if False else None) for f in real]
        by_signal = {s.index: s for s in signals}
        real_brackets = [(by_signal[i].stop, by_signal[i].target, by_signal[i].max_minutes)
                         for i in sorted(by_signal)
                         if year(bars[i][TS]) == y] or [(stop, target, time_stop)]

        draws = []
        for _ in range(TRIALS):
            picks = rng.sample(pool[y], min(n, len(pool[y])))
            fake = []
            for k, index in enumerate(picks):
                bracket = real_brackets[k % len(real_brackets)]
                side = LONG if k < longs else SHORT
                fake.append(Signal(index, side, bracket[0], bracket[1], bracket[2]))
            fake.sort(key=lambda s: s.index)
            draws.append(points_per_trade(execution.resolve(bars, fake, ex)))

        mean = sum(draws) / len(draws)
        sd = (sum((d - mean) ** 2 for d in draws) / len(draws)) ** 0.5
        actual = points_per_trade(real)
        z = (actual - mean) / sd if sd else 0.0
        beat = 100 * sum(1 for d in draws if d < actual) / len(draws)
        print(f"    {y:>6} {n:>6} {actual:>12.3f} {mean:>12.3f} {sd:>10.3f} "
              f"{z:>+7.2f} {beat:>11.1f}%")
    _ = widths


def main():
    print(f"random-entry control: {TRIALS} draws per year, seed {SEED}, "
          f"$1000 Forex NQ")
    print("  'better than' = share of random draws the real entries beat")
    for name, stop, target, time_stop, window, _end in SPECS:
        control(name, stop, target, time_stop, window)


if __name__ == "__main__":
    main()
