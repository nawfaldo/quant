"""Okala's YouTube (@Okala8020, "8020 Trading Pit") rule, as a
`cfd_tt_families` family.

Source: 1 video + 10 Shorts (2025-26), read 2026-10-03; mostly prop-firm talk
and live NQ scalp clips. The method named in the clips: "80/20" levels --
the 20 and 80 marks of every 100 NQ points -- faded on a capitulation candle
("a capitulation candle to the downside into a 20 level -> long") or on a
run of a high rejected at the level. Map:
`results/tiktok/okala8020_strategy_notes.md`.

The level grid is scaled to each market: step = 10^(floor(log10(price))-2)
(100 for NQ near 20,000; 10 for US500; 0.01 = 100 pips for EURUSD), levels at
step x (n + 0.2) and (n + 0.8).
"""
from __future__ import annotations

import math

from sandbox.research import tt_features as F

fin = math.isfinite


def _levels_near(price, lo, hi):
    if not (price > 0):
        return []
    step = 10 ** (math.floor(math.log10(price)) - 2)
    out = []
    n0 = math.floor(lo / step) - 1
    n1 = math.floor(hi / step) + 1
    for n in range(n0, n1 + 1):
        for f in (0.2, 0.8):
            x = (n + f) * step
            if lo <= x <= hi:
                out.append(x)
    return out


def capitulation_fade(bars, ctx, p):
    """Clips #3, #6, #9. A capitulation candle -- range >= `cap` ATR, closing
    in the direction of its move -- that trades through a 20/80 level and
    closes within `tol` ATR of it -> fade on the next bar (long after a down
    capitulation). Stop beyond the candle's extreme (floored); his scalps
    take quick targets (rr 1-2)."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or h[i] - l[i] < p["cap"] * A:
            continue
        if b["L"]["minute"][i] > p.get("last_entry_minute", 10 ** 6):
            continue
        lv = _levels_near(c[i], l[i], h[i])
        if not lv:
            continue
        near = min(lv, key=lambda x: abs(x - c[i]))
        if abs(c[i] - near) > p["tol"] * A:
            continue
        if c[i] < o[i]:                       # down capitulation -> long
            out[i] = (1, F.stop_dist(1, c[i], l[i], A), None)
        elif c[i] > o[i]:
            out[i] = (-1, F.stop_dist(-1, c[i], h[i], A), None)
    return out


SPECS = {
    "o80_capitulation_fade": (capitulation_fade, lambda g: {
        "cap": (1.5, 2.5), "tol": (0.25, 0.5),
        "exit_mode": ("rr_1", "rr_2", "time_6"), "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
