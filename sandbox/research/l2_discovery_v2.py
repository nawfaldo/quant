"""The discovery screen, with the estimator corrected.

`l2_discovery` averaged per-session long-short differences with equal weight.
Sessions where one leg had a single qualifying minute therefore counted as much
as sessions with fifty, and those one-observation differences carry the variance
of a single 60-minute NQ move.  That estimator reported +15.8 points for a
signal whose observation-weighted value is -0.28, and the strategy built on it
lost 1.5 points per trade against its matched control.

This replaces it with the quantity a strategy actually earns -- the pooled,
observation-weighted mean -- and takes standard errors from a block bootstrap
that resamples whole sessions, which keeps the overlapping-window dependence
that made clustering necessary in the first place.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

from sandbox.data import C
from sandbox.research.l2_discovery import (
    HORIZONS, Z_CUT, build_panel, cumulative, forward_returns, raw_signals,
    session_of, slot_normalize,
)

BOOTSTRAP = 400
SEED = 31


def pooled_long_short(rows, z, fwd, rng, draws=BOOTSTRAP):
    """Observation-weighted long-short mean, with a session block bootstrap.

    Returns the edge a strategy taking both legs would average per observation,
    which is directly comparable with the 0.2 spread.
    """
    hi_by_day, lo_by_day = defaultdict(list), defaultdict(list)
    ok = ~np.isnan(z) & ~np.isnan(fwd)
    for i in np.nonzero(ok & (z >= Z_CUT))[0]:
        hi_by_day[session_of(rows[i][0])].append(fwd[i])
    for i in np.nonzero(ok & (z <= -Z_CUT))[0]:
        lo_by_day[session_of(rows[i][0])].append(fwd[i])

    days = sorted(set(hi_by_day) | set(lo_by_day))
    hi_all = [v for d in days for v in hi_by_day[d]]
    lo_all = [v for d in days for v in lo_by_day[d]]
    if len(hi_all) < 200 or len(lo_all) < 200 or len(days) < 40:
        return None

    point = float(np.mean(hi_all) - np.mean(lo_all))
    stats = []
    for _ in range(draws):
        pick = rng.choice(len(days), size=len(days), replace=True)
        h = [v for k in pick for v in hi_by_day[days[k]]]
        lo = [v for k in pick for v in lo_by_day[days[k]]]
        if h and lo:
            stats.append(np.mean(h) - np.mean(lo))
    if len(stats) < draws // 2:
        return None
    sd = float(np.std(stats, ddof=1))
    return {
        "long_short_points": point,
        "t": point / sd if sd > 1e-9 else 0.0,
        "ci95": [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))],
        "hi_mean": float(np.mean(hi_all)), "hi_obs": len(hi_all),
        "lo_mean": float(np.mean(lo_all)), "lo_obs": len(lo_all),
        "sessions": len(days),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_discovery_v2.json")
    parser.add_argument("--windows", default="1,5,60")
    args = parser.parse_args()

    rows = build_panel()
    sigs = raw_signals(rows)
    close = sigs.pop("close")
    fwd_by_h = {h: forward_returns(rows, close, h) for h in HORIZONS}
    rng = np.random.default_rng(SEED)

    results = []
    for name, sig in sorted(sigs.items()):
        for window in [int(w) for w in args.windows.split(",")]:
            series = sig if window == 1 else cumulative(sig, rows, window)
            z = slot_normalize(series, rows)
            label = name if window == 1 else f"{name}@{window}m"
            for horizon in HORIZONS:
                got = pooled_long_short(rows, z, fwd_by_h[horizon], rng)
                if got:
                    results.append({"signal": label, "horizon": horizon, **got})
        print(f"  scored {name}")

    results.sort(key=lambda r: -abs(r["t"]))
    print(f"\n{'signal':<22} {'h':>4} {'long-short pts':>15} {'t':>7} "
          f"{'ci95':>22} {'hi obs':>8} {'lo obs':>8}")
    for r in results[:25]:
        ci = f"[{r['ci95'][0]:+.2f}, {r['ci95'][1]:+.2f}]"
        print(f"{r['signal']:<22} {r['horizon']:>4} "
              f"{r['long_short_points']:>+15.3f} {r['t']:>7.2f} {ci:>22} "
              f"{r['hi_obs']:>8} {r['lo_obs']:>8}")

    tradeable = [r for r in results
                 if abs(r["long_short_points"]) > 1.0 and abs(r["t"]) > 3.0]
    print(f"\nsignals above 1.0 points and t 3.0: {len(tradeable)}")
    for r in tradeable:
        print(f"  {r['signal']}@h{r['horizon']}: "
              f"{r['long_short_points']:+.3f} pts, t {r['t']:+.2f}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"z_cut": Z_CUT, "bootstrap": BOOTSTRAP, "results": results},
                  fh, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
