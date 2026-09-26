"""Fingerprint every family's output, so a refactor can prove it changed nothing.

WHY THIS EXISTS. The context arrays are being converted from Python lists with a
`None` warm-up to compact float arrays with a `NaN` warm-up, to cut the
per-worker footprint from 448 MB to roughly 150 MB. That is a good trade -- it
is the difference between 4 workers and 12 on the FX pairs -- but the sentinel
change is silent in exactly the way `None` is not:

    value is None       ->  False for NaN, so the guard passes
    if not risk         ->  False for NaN, because NaN is truthy
    x > threshold       ->  False for NaN, which is usually right, but only
                            usually

A missed guard does not raise. It produces a slightly different set of trades
and a plausible number, on a study whose sealed selections were chosen with the
old arithmetic and whose holdout is about to be spent against them.

So: run `capture` before the change, `verify` after. It scores a fixed sample of
cells from every family on symbols chosen to have different shapes, and compares
the full statistic dict exactly. Anything that moves is a bug, not a rounding
difference -- the arithmetic is identical float64 either way.

    python -m sandbox.research.exness_regression capture
    ...refactor...
    python -m sandbox.research.exness_regression verify
"""
from __future__ import annotations

import argparse
import json
import os
import random

from sandbox.research import cfd_families as ef

BASELINE = os.path.join(os.path.dirname(__file__), "..", "results",
                        "regression_baseline_30m.json")

#: Chosen for SHAPE, not for interest. `tsla` is a short stock series with a
#: benchmark, so it exercises every `relative` path; `eurusd` is the longest
#: series in the universe and the one whose warm-up prefixes are longest, which
#: is where a sentinel bug hides; `btc` quotes through the weekend, so its
#: session and calendar handling differ from both.
SYMBOLS = ("tsla", "eurusd", "btc")

#: Cells per family per symbol. Sampled with a fixed seed rather than taken from
#: the front of the grid: the first cells of a grid share `direction` and
#: `exit_mode`, so they exercise one path repeatedly.
PER_FAMILY = 14


def sample(symbol, bar=30):
    """`(family, params)` pairs, deterministic across runs."""
    grid = ef.axes(symbol, bar)
    jobs = []
    for family in sorted(grid):
        cells = ef.candidates(grid[family])
        rng = random.Random(f"{symbol}:{family}")
        jobs.extend((family, cell)
                    for cell in rng.sample(cells, min(PER_FAMILY, len(cells))))
    return jobs


def fingerprint(bar=30):
    """The full statistic dict for every sampled cell, keyed reproducibly."""
    out = {}
    for symbol in SYMBOLS:
        ef.resolve(symbol, allow_stale=True)
        ef.BAR_MINUTES = bar
        bars, ctx = ef.context(symbol, "select", bar)
        for family, params in sample(symbol, bar):
            stat = ef.backtest(family, bars, ctx, params)
            # `annual` is nested and `trade_log` is absent; everything else is
            # a scalar. Compared whole rather than on a summary, because a
            # sentinel bug can move the trade count while leaving the return
            # almost unchanged.
            key = f"{symbol}|{family}|{ef.es.frozen(params)}"
            out[key] = {k: v for k, v in stat.items() if k != "annual"}
            out[key]["annual"] = stat["annual"]
        print(f"  {symbol}: {sum(1 for k in out if k.startswith(symbol))} cells",
              flush=True)
    return out


def capture(path=BASELINE):
    data = fingerprint()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True, indent=1)
    print(f"captured {len(data)} cells -> {os.path.abspath(path)}")


def verify(path=BASELINE):
    with open(path, encoding="utf-8") as handle:
        before = json.load(handle)
    after = fingerprint()

    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = []
    for key in sorted(set(before) & set(after)):
        if before[key] != after[key]:
            fields = [f for f in set(before[key]) | set(after[key])
                      if before[key].get(f) != after[key].get(f)]
            changed.append((key, sorted(fields)))

    print(f"\n{len(before)} baseline cells, {len(after)} now")
    if missing:
        print(f"MISSING {len(missing)} (grid changed):")
        for key in missing[:10]:
            print(f"  {key}")
    if added:
        print(f"ADDED {len(added)} (grid changed):")
        for key in added[:10]:
            print(f"  {key}")
    if changed:
        print(f"CHANGED {len(changed)} cells:")
        for key, fields in changed[:15]:
            symbol, family, _ = key.split("|", 2)
            print(f"  {symbol:8}{family:22}{','.join(fields)}")
            for field in fields[:3]:
                print(f"      {field}: {before[key].get(field)} "
                      f"-> {after[key].get(field)}")
    if not (missing or added or changed):
        print("IDENTICAL -- every sampled cell reproduces exactly")
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "verify"))
    args = parser.parse_args()
    if args.command == "capture":
        capture()
        return 0
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
