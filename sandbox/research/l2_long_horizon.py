"""Flow-aggregate signals at hour-scale windows and holds.

The decay study found that book-state signals are flat in points at every
horizon -- ~0.10 points whether you wait one second or five minutes -- while
`trade_delta` is the one signal whose point-edge *grows*: -0.03 at 1s, -0.11 at
5s, -0.41 at 60s, -0.56 at 300s.  Extrapolating that column is the whole reason
`hourly_delta_reversal` works and nothing else does.

So this looks where the growth points: trailing windows of 30 to 240 minutes,
forward holds of 30 minutes to the session close.  The earlier screen stopped at
60 minutes on both axes and would have missed anything that only pays over
hours.

Two things are controlled for, because at these horizons the innocent
explanations get strong:

* the trailing price move on its own, since intraday mean reversion is an OHLCV
  effect that needs no level-two data;
* the number of genuinely independent observations, which collapses as the
  horizon grows -- 225 sessions is the real sample size, not 85,000 minutes,
  so the bootstrap resamples whole sessions.

2025 only.  Estimator is the corrected pooled one from `l2_discovery_v2`.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

import numpy as np

from sandbox.data import C
from sandbox.research.l2_discovery import (
    EPS, Z_CUT, build_panel, cumulative, raw_signals, session_of, slot_normalize,
)
from sandbox.research.l2_discovery_v2 import pooled_long_short

WINDOWS = (30, 60, 120, 240)
HORIZONS = (30, 60, 120, 240, "close")
FLOW_SIGNALS = ("trade_delta", "agg_ratio", "delta_unpaid", "delta_per_range",
                "imb_top1", "ofi_deep")
SEED = 77


def forward(rows, close, horizon):
    """Forward move in points; `close` means the session's last bar."""
    n = len(rows)
    out = np.full(n, np.nan)
    if horizon == "close":
        last_of = {}
        for i in range(n - 1, -1, -1):
            last_of.setdefault(session_of(rows[i][0]), i)
        for i in range(n):
            j = last_of[session_of(rows[i][0])]
            if j > i:
                out[i] = close[j] - close[i]
        return out
    for i in range(n):
        j = i + horizon
        if j < n and session_of(rows[j][0]) == session_of(rows[i][0]):
            out[i] = close[j] - close[i]
    return out


def past_move(rows, close, window):
    n = len(rows)
    out = np.full(n, np.nan)
    for i in range(n):
        j = i - window
        if j >= 0 and session_of(rows[j][0]) == session_of(rows[i][0]):
            out[i] = close[i] - close[j]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_long_horizon.json")
    args = parser.parse_args()

    rows = build_panel()
    sigs = raw_signals(rows)
    close = sigs.pop("close")
    fwd_by_h = {h: forward(rows, close, h) for h in HORIZONS}
    rng = np.random.default_rng(SEED)

    results = []
    header = "".join(f"{('h=' + str(h)):>20}" for h in HORIZONS)

    for name in FLOW_SIGNALS:
        print(f"\n{name}: long-short points (bootstrap t), 2025")
        print(f"{'window':>8}{header}")
        for window in WINDOWS:
            z = slot_normalize(cumulative(sigs[name], rows, window), rows)
            cells = ""
            for h in HORIZONS:
                got = pooled_long_short(rows, z, fwd_by_h[h], rng)
                if got is None:
                    cells += f"{'n/a':>20}"
                    continue
                cells += f"{got['long_short_points']:>+13.2f} ({got['t']:>4.2f})"
                results.append({"signal": name, "window": window, "horizon": h,
                                **got})
            print(f"{window:>8}{cells}")

    print(f"\ncontrol - trailing price move alone, no level-two data")
    print(f"{'window':>8}{header}")
    for window in WINDOWS:
        z = slot_normalize(past_move(rows, close, window), rows)
        cells = ""
        for h in HORIZONS:
            got = pooled_long_short(rows, z, fwd_by_h[h], rng)
            if got is None:
                cells += f"{'n/a':>20}"
                continue
            cells += f"{got['long_short_points']:>+13.2f} ({got['t']:>4.2f})"
            results.append({"signal": "past_price_move", "window": window,
                            "horizon": h, **got})
        print(f"{window:>8}{cells}")

    strong = [r for r in results
              if abs(r["t"]) > 3.0 and abs(r["long_short_points"]) > 2.0]
    strong.sort(key=lambda r: -abs(r["t"]))
    print(f"\nabove 2.0 points and t 3.0: {len(strong)}")
    for r in strong[:15]:
        print(f"  {r['signal']}@{r['window']}m h={r['horizon']}: "
              f"{r['long_short_points']:+.2f} pts, t {r['t']:+.2f}, "
              f"CI [{r['ci95'][0]:+.2f}, {r['ci95'][1]:+.2f}], "
              f"{r['sessions']} sessions")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"windows": WINDOWS, "horizons": [str(h) for h in HORIZONS],
                   "z_cut": Z_CUT, "results": results}, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
