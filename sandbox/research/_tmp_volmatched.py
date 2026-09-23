"""Bracket drag as a function of entry-time volatility.

L2 signals fire when the book is busy, i.e. when the market is moving.  A fixed
12/20 bracket does not cost the same at every volatility.  This measures the
no-information baseline separately per volatility bucket, so a strategy's result
can be compared with the right control instead of the all-bars average.
"""
import random
import statistics

from sandbox import data, execution
from sandbox.data import TS, H, L
from sandbox.execution import LONG, SHORT, Signal

STOP, TARGET, TIME_STOP = 12.0, 20.0, 15
WINDOW = (575, 930)
EX = execution.Execution(initial=1000.0, session_end_min=945)
LOOKBACK = 5
PER_BUCKET = 6000
DRAWS = 8


def main():
    bars = data.load_bars("level_two", "nq")

    # trailing range over the LOOKBACK completed bars, same session only
    vol = [None] * len(bars)
    for i in range(LOOKBACK, len(bars)):
        window = bars[i - LOOKBACK:i]
        if window[0][TS] // 86_400 != bars[i][TS] // 86_400:
            continue
        vol[i] = max(b[H] for b in window) - min(b[L] for b in window)

    pool = [i for i, b in enumerate(bars)
            if vol[i] is not None and WINDOW[0] <= (b[TS] % 86_400) // 60 <= WINDOW[1]]
    ranked = sorted(pool, key=lambda i: vol[i])
    n = len(ranked)
    buckets = {
        "q1 calmest": ranked[: n // 4],
        "q2": ranked[n // 4: n // 2],
        "q3": ranked[n // 2: 3 * n // 4],
        "q4 fastest": ranked[3 * n // 4:],
        "top 5%": ranked[int(0.95 * n):],
    }

    print(f"no-information baseline, bracket {STOP}/{TARGET}, time stop {TIME_STOP}m")
    print(f"{'bucket':<12} {'5m range':>9} {'gross/trade':>12} {'sd':>7} "
          f"{'win rate':>9} {'avg win':>8} {'avg loss':>9}")
    rng = random.Random(11)
    for label, idxs in buckets.items():
        means, wins, losses, nw, nl = [], [], [], 0, 0
        for _ in range(DRAWS):
            picks = rng.sample(idxs, min(PER_BUCKET, len(idxs)))
            sigs = [Signal(i, LONG if k % 2 == 0 else SHORT, STOP, TARGET, TIME_STOP)
                    for k, i in enumerate(sorted(picks))]
            fills = execution.resolve(bars, sigs, EX)
            g = [f.points + EX.entry_cost for f in fills]
            means.append(sum(g) / len(g))
            for p in g:
                if p >= 0:
                    wins.append(p)
                    nw += 1
                else:
                    losses.append(-p)
                    nl += 1
        print(f"{label:<12} {statistics.mean([vol[i] for i in idxs]):>9.1f} "
              f"{statistics.mean(means):>+12.3f} {statistics.pstdev(means):>7.3f} "
              f"{nw / (nw + nl):>9.3f} {statistics.mean(wins):>8.2f} "
              f"{statistics.mean(losses):>9.2f}")


if __name__ == "__main__":
    main()
