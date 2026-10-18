"""MacMoney's TikTok (@macmoney82) rule, as a `cfd_tt_families` family.

Source: 37 posts on the grid (day-trading education: SMC, order flow,
VWAP, 80/20 reversals), screened by caption 2026-10-04; 3 read. The drift
VWAP pullback and the 80/20 reversal are covered by existing families. Map:
`results/tiktok/macmoney82_strategy_notes.md`.
"""
from __future__ import annotations

import math

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def ema9_vwap_bounce(bars, ctx, p):
    """"Mastering Day Trading with the 9 EMA & VWAP Crossover Strategy" (5m):
    the 9 EMA crosses the session VWAP (the alert); wait for a pullback --
    a candle that touches the 9 EMA and closes back on the trend side of it
    in the cross's direction (the bounce) -> enter at its close, within
    `win` bars of the cross; stop just outside the bounce candle (floored);
    target 1:2. One entry per cross. Mirror."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    e = F.ema(bars, ctx, 9).tolist()
    vw = F.vwap(bars, ctx)
    vw = (vw["vwap"] if isinstance(vw, dict) else vw).tolist()
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    day, mins = b["L"]["day"], b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    arm = None                       # (side, bar of the cross)
    out = {}
    for i in range(1, len(c)):
        if day[i] != day[i - 1]:
            arm = None
        if not all(fin(x) for x in (e[i], e[i - 1], vw[i], vw[i - 1])):
            continue
        if e[i - 1] <= vw[i - 1] and e[i] > vw[i]:
            arm = (1, i)
            continue
        if e[i - 1] >= vw[i - 1] and e[i] < vw[i]:
            arm = (-1, i)
            continue
        if arm is None:
            continue
        side, t0 = arm
        if i - t0 > p["win"] or side * (e[i] - vw[i]) <= 0:
            arm = None
            continue
        A = a[i]
        touch = l[i] <= e[i] if side == 1 else h[i] >= e[i]
        bounce = side * (c[i] - e[i]) > 0 and side * (c[i] - o[i]) > 0
        if touch and bounce and A > 0 and mins[i] <= last:
            stop = l[i] if side == 1 else h[i]
            dist = F.stop_dist(side, c[i], stop, A)
            if dist > 0 and fin(dist):
                out[i] = (side, dist, None)
            arm = None
    return out


SPECS = {
    "mac_ema9_vwap_bounce": (ema9_vwap_bounce, lambda g: {
        "win": (12, 24), "exit_mode": ("rr_2", "rr_1.5", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
