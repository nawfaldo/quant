"""Daniel Holmes Trades' TikTok (@danielholmestrades) strategies, as
`cfd_tt_families` families.

Source: 70 posts on the grid (screened by caption 2026-10-04; lifestyle and
funded-account posts aside, one breakout method restated many times); 8
rule posts read. Map: `results/tiktok/danielholmestrades_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite

#: his "high volume times": Asia open, London open, New York open (NY clock)
OPENS = ((19 * 60, 20 * 60), (3 * 60, 4 * 60), (9 * 60 + 30, 10 * 60 + 30))


def _lists(bars, ctx):
    b = F.base(bars, ctx)
    Lb = b["L"]
    return b, Lb["o"], Lb["h"], Lb["l"], Lb["c"]


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _emit(out, i, side, dist, target=None):
    if not (dist > 0) or not fin(dist):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def break_retest_double_tap(bars, ctx, p):
    """"My simple breakout strategy", "How to take high probability breakout
    trades", "Double tap rule" (15m): a support level (the latest confirmed
    `k`-bar swing low that held) is broken by a candle CLOSING below it;
    the NEXT candle wicks back up to retest the level and then trades back
    below the breakout candle's low (the "double tap") -> sell; stop above
    that candle's wick (floored); his 15-25 pips for 25-30 -> ~1:1 to
    1:1.5. `opens` 1 = only during the Asia / London / New York open hours
    (his step 2). Mirror for resistance breakouts."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    real = b["L"]["real"]
    pend = None                       # (side, level, breakout bar extreme, bar)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if pend is not None:
            side, lvl, bx, t0 = pend
            pend = None
            if i == t0 + 1:
                retest = h[i] >= lvl if side == -1 else l[i] <= lvl
                tap = l[i] < bx if side == -1 else h[i] > bx
                if retest and tap and side * (c[i] - lvl) > 0 and _ok(b, i, p):
                    stop = h[i] if side == -1 else l[i]
                    _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
                    continue
        if p["opens"] and not any(s <= real[i] < e for s, e in OPENS):
            continue
        sup, res = pv["l"][i - 1], pv["h"][i - 1]
        if fin(sup) and c[i] < sup <= c[i - 1]:
            pend = (-1, sup, l[i], i)
        elif fin(res) and c[i] > res >= c[i - 1]:
            pend = (1, res, h[i], i)
    return out


def counter_trend_engulf(bars, ctx, p):
    """"Counter trend trading strategy" (15m, "the exact one I'll use in
    2026"): an aggressive move down (`run` bearish candles) with shrinking
    bodies and lower wicks; one candle flips green and ENGULFS the previous
    one; the next candle wicks down once more and then breaks the high of
    the previous candle -> buy; stop beneath the current candle's wick
    (his 25 ticks), target ~1:1 (30 ticks). Mirror for shorts."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["run"]
    armed = None                       # (side, engulf bar)
    out = {}
    for i in range(k + 2, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if armed is not None:
            side, t0 = armed
            armed = None
            if i == t0 + 1:
                wick_more = l[i] < c[t0] if side == 1 else h[i] > c[t0]
                broke = h[i] > h[t0] if side == 1 else l[i] < l[t0]
                if wick_more and broke and _ok(b, i, p):
                    stop = l[i] if side == 1 else h[i]
                    _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
                    continue
        j = i - 1
        seq = range(j - k + 1, j + 1)            # the move ending on the bar before i
        bodies = [c[x] - o[x] for x in seq]
        shrink = all(abs(bodies[t + 1]) <= abs(bodies[t]) for t in range(k - 1))
        if all(v < 0 for v in bodies) and shrink and c[i] > o[i] and c[i] >= o[j] and o[i] <= c[j]:
            armed = (1, i)
        elif all(v > 0 for v in bodies) and shrink and c[i] < o[i] and c[i] <= o[j] and o[i] >= c[j]:
            armed = (-1, i)
    return out


SPECS = {
    "dh_break_retest_double_tap": (break_retest_double_tap, lambda g: {
        "k": (3, 5), "opens": (0, 1), "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, True, None),
    "dh_counter_trend_engulf": (counter_trend_engulf, lambda g: {
        "run": (3, 4), "exit_mode": ("rr_1", "rr_1.5", "time_6"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
