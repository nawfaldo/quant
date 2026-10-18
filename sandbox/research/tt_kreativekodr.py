"""kreativekodr's TikTok (@kreativekodr) bot rule, as a `cfd_tt_families`
family.

Source: 120 posts on the grid (0DTE SPY/QQQ options bots, Claude Code /
Cursor demos, indicator explainers), screened by caption 2026-10-04; 6 bot
posts read. Only "VWAPR" states its rules. Map:
`results/tiktok/kreativekodr_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def vwapr(bars, ctx, p):
    """"Meet VWAPR" (QQQ 0DTE calls, paper-traded): RSI collapses below
    `low` (his 15, extreme oversold) with price below VWAP; when RSI starts
    climbing back (within `win` bars) -> long. One trade a session, only
    09:36-11:11 New York; "3 minute max hold" -> short `time_` exits.
    His 28 paper trades: 50% win, 76 cents a trade."""
    b = F.base(bars, ctx)
    c = b["L"]["c"]
    r = F.rsi(bars, ctx, 14).tolist()
    vw = F.vwap(bars, ctx)
    vw = (vw["vwap"] if isinstance(vw, dict) else vw).tolist()
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, day, mins = b["L"]["real"], b["L"]["day"], b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    lo_at = -10 ** 9
    done = set()
    out = {}
    for i in range(1, len(c)):
        if fin(r[i]) and r[i] < p["low"]:
            lo_at = i
        if day[i] in done or not (9 * 60 + 36 <= real[i] <= 11 * 60 + 11) or mins[i] > last:
            continue
        A = a[i]
        if not (A > 0 and fin(r[i]) and fin(r[i - 1]) and fin(vw[i])):
            continue
        if i - lo_at <= p["win"] and r[i] > r[i - 1] and r[i] >= p["low"] and c[i] < vw[i]:
            out[i] = (1, p["stop_atr"] * A, None)
            done.add(day[i])
    return out


SPECS = {
    "kk_vwapr": (vwapr, lambda g: {
        "low": (15, 20), "win": (3, 6), "stop_atr": (1.0, 1.5),
        "exit_mode": ("time_1", "time_3", "rr_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ()
