"""Do the sixth wave's winners actually trade different bars?

THE QUESTION THE ADMISSION TEST WAS SUPPOSED TO SETTLE, ASKED AGAIN AFTER THE
FACT. Every family in `exness_families` has to name a bar on which it fires and
no existing family does. That argument was made for each of the twenty-seven
about the CONSTRUCT -- a Hurst exponent is not a variance ratio, a bandpass is
not a lowpass -- and the arguments are sound about the constructs. They say
nothing about the CELLS the search actually picked.

And the search can dissolve the distinction. Nine of the new families have the
same shape: a state test on a new statistic, and then

    move = close[i] - close[i - session];  side = sign(move)

as the direction engine. If the search sets the state test permissively -- and
it has every incentive to, because a permissive gate keeps more trades and a
larger sample lifts the t-statistic -- the family collapses onto "session
momentum, trailing stop", which is one rule that nine families are then
reporting nine times.

Measured on ethusd that is exactly what happened to `entropy` (state=random at
threshold 0.85, which a real price series clears on nearly every bar) and
`estimator` (state=gapping at ratio 1.5): identical holdout trade counts,
returns and drawdowns to the decimal.

So this compares SIGNAL SETS rather than statistics. Two families whose winning
cells fire on the same bars with the same sign are one hypothesis with two
names, however different the mathematics that reached them.

    python -m sandbox.research._sixth_overlap --symbols ethusd
"""
from __future__ import annotations

import argparse
import itertools
import json
import os

from sandbox.research import exness_families as ef
from sandbox.research import _sixth_compare as sc


def signals(symbol, bar, families, params_by_family, bars, ctx, lo, hi):
    """`{family: {index: side}}` over `[lo, hi)`."""
    out = {}
    for family in families:
        signal = ef.SIGNALS[family]
        params = params_by_family[family]
        fired = {}
        for index, row in enumerate(bars):
            if not lo <= row[ef.TS] < hi:
                continue
            side = signal(index, bars, ctx, params, {})
            if side:
                fired[index] = side
        out[family] = fired
    return out


def jaccard(a, b):
    """Agreement on (bar, side), over the union of bars either one fired on."""
    if not a and not b:
        return 1.0
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    same = sum(1 for k in keys if a.get(k) is not None and a.get(k) == b.get(k))
    return same / len(keys)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="ethusd")
    parser.add_argument("--bar-minutes", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.70,
                        help="report pairs agreeing on at least this share")
    args = parser.parse_args()

    for symbol in args.symbols.replace(" ", "").split(","):
        payload = sc.load_new(symbol, args.bar_minutes)
        if payload is None:
            print(f"{symbol}: not run yet")
            continue
        ef.resolve(symbol, allow_stale=True)
        winners = {f: ef.rehydrate(w["params"])
                   for f, w in payload["families"].items() if w}
        families = sorted(winners)
        bars, ctx = ef.context(symbol, "validate", args.bar_minutes,
                               set(families))
        fired = signals(symbol, args.bar_minutes, families, winners, bars, ctx,
                        ef.IS_END, ef.OOS_END)

        print(f"\n=== {symbol} {ef.label_bar(args.bar_minutes)}: "
              f"{len(families)} winning cells, signal overlap on the holdout ===")
        print(f"{'family':21}{'signals':>9}{'share of bars':>15}")
        window = sum(1 for row in bars if ef.IS_END <= row[ef.TS] < ef.OOS_END)
        for family in families:
            n = len(fired[family])
            print(f"{family:21}{n:>9,}{100.0 * n / window:>14.1f}%")
        print(f"{'(bars in window)':21}{window:>9,}")

        pairs = []
        for a, b in itertools.combinations(families, 2):
            pairs.append((jaccard(fired[a], fired[b]), a, b))
        pairs.sort(reverse=True)
        print(f"\nPAIRS AGREEING ON >= {100 * args.threshold:.0f}% OF THEIR "
              f"COMBINED SIGNAL BARS")
        hits = [p for p in pairs if p[0] >= args.threshold]
        if not hits:
            print("  none -- every winning cell trades a distinguishable set")
        for share, a, b in hits:
            print(f"  {share * 100:>5.1f}%  {a} <-> {b}")

        print("\nMOST INDEPENDENT PAIRS (lowest agreement)")
        for share, a, b in pairs[-8:]:
            print(f"  {share * 100:>5.1f}%  {a} <-> {b}")

        # How much of the wave is really one rule: the size of the largest
        # cluster of families that all agree with each other past the threshold.
        linked = {f: {f} for f in families}
        for share, a, b in hits:
            merged = linked[a] | linked[b]
            for name in merged:
                linked[name] = merged
        clusters = {frozenset(v) for v in linked.values()}
        big = sorted(clusters, key=len, reverse=True)
        print(f"\nDISTINCT SIGNAL CLUSTERS AT {100 * args.threshold:.0f}%: "
              f"{len(clusters)} from {len(families)} families")
        for cluster in big:
            if len(cluster) > 1:
                print(f"  {len(cluster)}: {', '.join(sorted(cluster))}")


if __name__ == "__main__":
    raise SystemExit(main())
