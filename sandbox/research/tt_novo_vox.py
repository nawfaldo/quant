"""Novo Legacy's TikTok (@novo_vox) rule, as a `cfd_tt_families` family.

Source: 123 posts on the grid (candle-range-theory / ICT one-candle posts,
coaching promos), screened by caption 2026-10-04; 5 rule posts read. His
15m ORB + FVG retrace and 15m ORB fake-out are covered by the ORB families.
Map: `results/tiktok/novo_vox_strategy_notes.md`.
"""
from __future__ import annotations

import math

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def crt_5am(bars, ctx, p):
    """"The 4hr candle that tells me where Nasdaq is going every morning" /
    "Backtested my strategy 10,000 times": mark the high and low of the
    05:00 New York 4-hour candle (05:00-09:00). From the 09:30 open (`win`
    minutes), price sweeps one side -- the low taken first = bullish, the
    high first = bearish -- then a change of state (a close above the
    previous candle's high after a low sweep; mirror) -> enter; stop beyond
    the sweep extreme (floored); target the opposite side of the 4h range
    (`level`). One trade a day."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, day, mins = b["L"]["real"], b["L"]["day"], b["L"]["minute"]
    cw = F.clock_window(bars, ctx, "h5_4")
    last = p.get("last_entry_minute", 10 ** 6)
    open_m = 9 * 60 + 30
    st = {}                          # day -> [bias, extreme, done]
    out = {}
    for i in range(1, len(c)):
        m, d = real[i], day[i]
        if not (open_m <= m <= open_m + p["win"]):
            continue
        hi, lo, A = cw["hi"][i], cw["lo"][i], a[i]
        if not (fin(hi) and A > 0) or hi <= lo:
            continue
        s = st.setdefault(d, [0, NAN, False])
        if s[2]:
            continue
        if s[0] == 0:
            if l[i] < lo and not h[i] > hi:
                s[0], s[1] = 1, l[i]
            elif h[i] > hi and not l[i] < lo:
                s[0], s[1] = -1, h[i]
            elif l[i] < lo and h[i] > hi:
                s[2] = True          # both sides in one bar: no read
            continue
        if s[0] == 1:
            s[1] = min(s[1], l[i])
            shift = c[i] > h[i - 1] and c[i] > o[i] and day[i - 1] == d
        else:
            s[1] = max(s[1], h[i])
            shift = c[i] < l[i - 1] and c[i] < o[i] and day[i - 1] == d
        if not shift or mins[i] > last:
            continue
        side = s[0]
        room = side * ((hi if side == 1 else lo) - c[i])
        if room > 0:
            out[i] = (side, F.stop_dist(side, c[i], s[1], A), {"far": room})
        s[2] = True
    return out


SPECS = {
    "novo_crt_5am": (crt_5am, lambda g: {
        "win": (60, 150), "exit_mode": ("level", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ()
