"""tradingwithmustafa's TikTok (@tradingwithmustafa) rule, as a
`cfd_tt_families` family.

Source: 294 posts on the grid (mostly MarketPal Pro / money-mindset promos),
screened by caption 2026-10-04; the 8 "school run" posts read. Map:
`results/tiktok/tradingwithmustafa_strategy_notes.md`.
"""
from __future__ import annotations

import math

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite

#: session open (NY minutes) -> the overnight box that precedes it
BOX = {180: "asia", 570: "overnight_ny"}


def school_run(bars, ctx, p):
    """"The school run strategy" (DAX, 15m, London open; "also works on
    NASDAQ", "London, Asia and New York sessions"): mark the overnight range
    (the box of highs and lows before the open); when the FIRST 15-minute
    candle after the open closes above the box -> long at its close, below
    -> short. Stop at the far end of that candle (`stop` "candle") or the
    middle of the box ("mid"); targets at the liquidity on the left, his
    typical 1:1.2-1:2.5 -> rr exits. `open` 180 = London (03:00 NY), 570 =
    New York cash. On bars of 15 minutes or more, the first bar is the
    candle. One trade a day."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, day, mins = b["L"]["real"], b["L"]["day"], b["L"]["minute"]
    bar = b["bar"]
    start = p["open"]
    span = max(15, bar)
    box = F.clock_window(bars, ctx, BOX[start])
    last = p.get("last_entry_minute", 10 ** 6)
    cur = None                       # day, hi, lo
    out = {}
    for i in range(len(c)):
        m = real[i]
        if not (start <= m < start + span):
            continue
        if cur is None or cur[0] != day[i]:
            cur = [day[i], h[i], l[i]]
        else:
            cur[1], cur[2] = max(cur[1], h[i]), min(cur[2], l[i])
        if m + bar < start + 15:
            continue                 # the 15-minute candle is still forming
        bh, bl, A = box["hi"][i], box["lo"][i], a[i]
        if not (fin(bh) and A > 0) or mins[i] > last:
            continue
        side = 1 if c[i] > bh else -1 if c[i] < bl else 0
        if side:
            lvl = (cur[2] if side == 1 else cur[1]) if p["stop"] == "candle" else (bh + bl) / 2.0
            if side * (c[i] - lvl) > 0:
                out[i] = (side, F.stop_dist(side, c[i], lvl, A), None)
        cur = [None, 0, 0]
    return out


SPECS = {
    "mus_school_run": (school_run, lambda g: {
        "open": (180, 570), "stop": ("candle", "mid"),
        "exit_mode": ("rr_1.5", "rr_2", "rr_1.2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ("stop",)
