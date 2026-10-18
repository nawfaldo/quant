"""PipLab's TikTok (@piplabtrading) rule, as a `cfd_tt_families` family.

Source: 288 posts on the grid (mostly ICT trade recaps and tips), screened by
caption 2026-10-04; their ORB posts and two backtest-result posts read. Map:
`results/tiktok/piplabtrading_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def prelondon_box(bars, ctx, p):
    """"How to trade forex using the opening range breakout": a box over the
    `hours` before London opens (08:00 London = 03:00 New York; his 3 hours
    = 00:00-03:00 NY), 30m chart; a candle breaking above the box -> long
    (`entry` "close": on a close beyond it; "touch": his alternative limit at
    the box edge -- the first bar trading through it); stop at the other
    side of the box; target 1.5:1. One trade a day, until `cut` minutes
    after London opens. Mirror."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, day = b["L"]["real"], b["L"]["day"]
    cw = F.clock_window(bars, ctx, "prelondon%d" % p["hours"])
    done = set()
    out = {}
    for i in range(len(c)):
        d, m = day[i], real[i]
        hi, lo = cw["hi"][i], cw["lo"][i]
        if not fin(hi) or m < 3 * 60 or m > 3 * 60 + p["cut"] or d in done:
            continue
        A = a[i]
        if not (A > 0) or b["L"]["minute"][i] > p.get("last_entry_minute", 10 ** 6):
            continue
        if p["entry"] == "close":
            up, dn = c[i] > hi, c[i] < lo
        else:
            up, dn = h[i] > hi, l[i] < lo
        if up:
            out[i] = (1, F.stop_dist(1, c[i], lo, A), None)
            done.add(d)
        elif dn:
            out[i] = (-1, F.stop_dist(-1, c[i], hi, A), None)
            done.add(d)
    return out


SPECS = {
    "plab_prelondon_box": (prelondon_box, lambda g: {
        "hours": (2, 3), "cut": (180, 360), "entry": ("close", "touch"),
        "exit_mode": ("rr_1.5", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ("entry",)
