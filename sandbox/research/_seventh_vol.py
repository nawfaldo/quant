"""How much volatility sizing each sleeve actually gets, and at what level.

THERE ARE TWO MECHANISMS AND THEY ARE NOT THE SAME THING.

  1. ATR-SCALED STOP, per trade, per sleeve. `size` computes lots as
     `risk / (distance * money)` where `distance` is the cell's own
     volatility-scaled stop. A wider stop buys fewer lots, so exposure is
     already inverse to instrument volatility AT ENTRY. Every sleeve has this,
     including the ones whose family carries no volatility axis, because it
     comes from the stop rather than from the rule.

  2. EWMA VOLATILITY TARGETING, per SYMBOL, per DAY. `daily_multipliers`
     targets `VOL_TARGET` annualised off a 20-day-halflife EWMA of daily
     closes, capped at `VOL_MAX_MULTIPLIER`, and multiplies the risk request by
     it. It is keyed by symbol, NOT by sleeve
     ([[vol-throttle-is-per-market-not-per-sleeve]]) -- so eight jp225 sleeves
     all throttle together on the same number, and a sleeve cannot be throttled
     independently of its siblings.

This prints the realised multiplier distribution per symbol so the throttle can
be read rather than assumed: a symbol pinned at the 3.0 cap is not being
volatility-targeted at all, it is being scaled up by a constant.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_vol
"""
from __future__ import annotations

import argparse
import pickle
import statistics
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_strategies as ecs


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)

    lo, hi = fr._bounds("full")
    symbols = sorted({k.split(":")[0] for k in ecs.BOOK})
    print(f"EWMA volatility target: {ecs.VOL_TARGET:.0%} annualised, "
          f"halflife {ecs.VOL_HALFLIFE:.0f}d, cap {ecs.VOL_MAX_MULTIPLIER:.1f}x, "
          f"warm-up {ecs.VOL_MIN_DAYS}d")
    print(f"\n{'symbol':10}{'sleeves':>9}{'days':>7}{'p10':>7}{'p50':>7}"
          f"{'p90':>7}{'at cap':>9}{'cutting':>9}  effect")
    for symbol in symbols:
        bars = state["bars_by"].get(symbol)
        if not bars:
            print(f"{symbol:10}{'-':>9}  no bars in cache")
            continue
        closes = {bar[ecs.ef.TS]: bar[ecs.ef.C] for bar in bars}
        mults = ecs.daily_multipliers(closes)
        window = [m for day, m in mults.items()
                  if lo // 86_400 <= day < hi // 86_400]
        if not window:
            continue
        window.sort()
        n = len(window)
        at_cap = 100.0 * sum(1 for m in window
                             if m >= ecs.VOL_MAX_MULTIPLIER - 1e-9) / n
        cutting = 100.0 * sum(1 for m in window if m < 1.0) / n
        held = sum(1 for k in ecs.BOOK if k.startswith(symbol + ":"))
        median = statistics.median(window)
        effect = ("pinned at cap" if at_cap > 80 else
                  "mostly scaling UP" if median > 1.3 else
                  "mostly throttling" if median < 0.8 else "near neutral")
        print(f"{symbol:10}{held:>9}{n:>7}{window[n // 10]:>7.2f}"
              f"{median:>7.2f}{window[min(n - 1, 9 * n // 10)]:>7.2f}"
              f"{at_cap:>8.0f}%{cutting:>8.0f}%  {effect}")


if __name__ == "__main__":
    main()
