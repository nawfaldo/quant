"""Robustness checks on `l2_discovery`'s survivors, still 2025 only.

A long-short spread of 10-30 NQ points is large for microstructure, so the
default assumption here is that it is an artifact.  Four ways it could be one,
each tested:

1. it is carried by a handful of violent sessions (April 2025);
2. it is unstable month to month;
3. it is the `hourly_delta_reversal` effect already trading in `live_trade/`,
   wearing a different name;
4. the machinery leaks, in which case a randomised signal scores too.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from sandbox import metrics
from sandbox.data import C, H, L
from sandbox.research.l2_discovery import (
    HORIZONS, Z_CUT, build_panel, cumulative, forward_returns, raw_signals,
    session_of, slot_normalize,
)

CANDIDATES = [
    ("trade_delta", 60, 60),        # the known effect, as the reference
    ("delta_per_range", 60, 30),
    ("book_vs_tape", 60, 30),
    ("replenish", 60, 60),
    ("add_ratio", 5, 60),
    ("cancel_ratio", 5, 60),
    ("agg_ratio", 60, 60),
]


def spread_by_session(rows, z, fwd):
    """Per-session (high mean - low mean), for sessions holding both legs."""
    hi, lo = defaultdict(list), defaultdict(list)
    for i in np.nonzero(z >= Z_CUT)[0]:
        if not math.isnan(fwd[i]):
            hi[session_of(rows[i][0])].append(fwd[i])
    for i in np.nonzero(z <= -Z_CUT)[0]:
        if not math.isnan(fwd[i]):
            lo[session_of(rows[i][0])].append(fwd[i])
    return {d: float(np.mean(hi[d]) - np.mean(lo[d]))
            for d in sorted(set(hi) & set(lo))}


def tstat(values):
    arr = np.array(list(values), dtype=float)
    if len(arr) < 5:
        return 0.0, 0.0
    se = arr.std(ddof=1) / math.sqrt(len(arr))
    return float(arr.mean()), float(arr.mean() / se) if se > 1e-9 else 0.0


def main():
    rows = build_panel()
    sigs = raw_signals(rows)
    close = sigs.pop("close")
    fwd_by_h = {h: forward_returns(rows, close, h) for h in HORIZONS}

    # session range, to identify the violent days
    rng_by_day = defaultdict(lambda: [-1e18, 1e18])
    for ts, bar, _f in rows:
        d = session_of(ts)
        rng_by_day[d][0] = max(rng_by_day[d][0], bar[H])
        rng_by_day[d][1] = min(rng_by_day[d][1], bar[L])
    ranges = {d: hi - lo for d, (hi, lo) in rng_by_day.items()}
    violent = {d for d, _ in sorted(ranges.items(), key=lambda kv: -kv[1])[:10]}

    zs = {}
    print(f"\n{'candidate':<26} {'all':>9} {'t':>6} {'drop 10 wild':>13} {'t':>6} "
          f"{'placebo':>9} {'t':>6} {'pos months':>11}")
    for name, window, horizon in CANDIDATES:
        series = cumulative(sigs[name], rows, window) if window > 1 else sigs[name]
        z = slot_normalize(series, rows)
        zs[f"{name}@{window}m"] = z
        fwd = fwd_by_h[horizon]

        per_day = spread_by_session(rows, z, fwd)
        mean_all, t_all = tstat(per_day.values())
        kept = [v for d, v in per_day.items() if d not in violent]
        mean_cut, t_cut = tstat(kept)

        rng = np.random.default_rng(4)
        placebo = z.copy()
        finite = np.nonzero(~np.isnan(placebo))[0]
        placebo[finite] = placebo[rng.permutation(finite)]
        mean_p, t_p = tstat(spread_by_session(rows, placebo, fwd).values())

        by_month = defaultdict(list)
        for d, v in per_day.items():
            by_month[metrics.month_key(d * 86_400)].append(v)
        pos = sum(1 for vals in by_month.values() if np.mean(vals) > 0)
        sign = "+" if mean_all > 0 else "-"
        pos = pos if mean_all > 0 else len(by_month) - pos

        print(f"{name + '@' + str(window) + 'm h' + str(horizon):<26} "
              f"{mean_all:>+9.2f} {t_all:>6.2f} {mean_cut:>+13.2f} {t_cut:>6.2f} "
              f"{mean_p:>+9.2f} {t_p:>6.2f} "
              f"{str(pos) + '/' + str(len(by_month)) + ' ' + sign:>11}")

    print("\ncorrelation of each candidate's z with trade_delta@60m's z")
    base = zs["trade_delta@60m"]
    for key, z in zs.items():
        if key == "trade_delta@60m":
            continue
        both = ~np.isnan(base) & ~np.isnan(z)
        r = float(np.corrcoef(base[both], z[both])[0, 1])
        print(f"  {key:<24} r = {r:+.3f}   ({both.sum()} shared minutes)")


if __name__ == "__main__":
    main()
