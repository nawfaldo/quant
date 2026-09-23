"""Does the flow signal pay only in its tail?

The hour-scale grid tested every minute with |z| >= 1.5, which is ~5,000
observations per leg.  `hourly_delta_reversal` does not trade that population:
it fires roughly 100-200 times in eighteen months, on raw delta thresholds of
50 and 150 with a trend filter and a spread gate.  A broad screen averaging
5,000 minutes cannot see an effect that lives in the extreme 2%.

So this walks the threshold out -- |z| from 1.5 to 3.5 -- and reports both the
edge and how many observations and sessions are left to support it.  The
question it answers is whether the effect strengthens in the tail (a real
conditional edge) or merely gets noisier (a thinning sample).

The `sessions` column is the honest sample size at these horizons; the
bootstrap resamples whole sessions, so a number below ~100 means the result
cannot be resolved regardless of the point estimate.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from sandbox.research.l2_discovery import (
    build_panel, cumulative, raw_signals, slot_normalize,
)
from sandbox.research.l2_discovery_v2 import pooled_long_short
from sandbox.research.l2_long_horizon import forward

CUTS = (1.5, 2.0, 2.5, 3.0, 3.5)
CASES = [
    ("trade_delta", 60, 60),
    ("trade_delta", 60, 120),
    ("trade_delta", 60, "close"),
    ("trade_delta", 120, 120),
    ("delta_per_range", 60, 120),
    ("ofi_deep", 30, 30),
]
SEED = 101


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_tail.json")
    args = parser.parse_args()

    rows = build_panel()
    sigs = raw_signals(rows)
    close = sigs.pop("close")
    rng = np.random.default_rng(SEED)

    results = []
    for name, window, horizon in CASES:
        z = slot_normalize(cumulative(sigs[name], rows, window), rows)
        fwd = forward(rows, close, horizon)
        print(f"\n{name}, {window}m window, h={horizon}")
        print(f"  {'|z| cut':>8} {'long-short pts':>15} {'t':>7} "
              f"{'ci95':>22} {'hi obs':>8} {'lo obs':>8} {'sessions':>9}")
        for cut in CUTS:
            import sandbox.research.l2_discovery_v2 as v2
            saved = v2.Z_CUT
            v2.Z_CUT = cut
            try:
                got = pooled_long_short(rows, z, fwd, rng)
            finally:
                v2.Z_CUT = saved
            if got is None:
                print(f"  {cut:>8.1f} {'too few observations':>15}")
                continue
            ci = f"[{got['ci95'][0]:+.2f}, {got['ci95'][1]:+.2f}]"
            print(f"  {cut:>8.1f} {got['long_short_points']:>+15.2f} "
                  f"{got['t']:>7.2f} {ci:>22} {got['hi_obs']:>8} "
                  f"{got['lo_obs']:>8} {got['sessions']:>9}")
            results.append({"signal": name, "window": window,
                            "horizon": horizon, "z_cut": cut, **got})

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"cuts": CUTS, "results": results}, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
