"""Mulham Trading's TikTok (@mulhamtrading) strategy, as a
`cfd_tt_families` family.

Source: 193 posts on the grid (screened by caption 2026-10-04: ICT/SMC
lessons, a 30-day psychology series, course funnels); 10 "mechanical"
strategy posts read from English captions. One is stated as a fully
mechanical rule set; the others are concepts (candle continuity, "strength
vs weakness", a backtested Silver Bullet without the rules on screen) that
existing sweep / reclaim families already cover. Map and SKIP list:
`results/tiktok/mulhamtrading_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _lists(bars, ctx):
    b = F.base(bars, ctx)
    Lb = b["L"]
    return b, Lb["o"], Lb["h"], Lb["l"], Lb["c"]


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _emit(out, i, side, dist, target):
    if not (dist > 0) or not fin(dist):
        return False
    if isinstance(target, dict) and any(not (v > 0) or not fin(v) for v in target.values()):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _two_lines(bars, ctx):
    """Per bar: the open of the latest 17:00 New York candle (yesterday's
    17:00 until today's has printed) and of today's midnight candle, read
    from the full (unfiltered) bar series in real New York time."""
    def build():
        b = F.base(bars, ctx)
        n = b["n"]
        l17 = numpy.full(n, NAN)
        lmid = numpy.full(n, NAN)
        full = ctx.get("tt_full")
        if full is None or not len(full["ts"]):
            return l17.tolist(), lmid.tolist()
        shift = b["shift"] * 3600
        fts = full["ts"] - shift
        fmin = (fts // 60) % 1440
        fdate = fts // 86400
        fo = full["o"]
        o17, omid = {}, {}
        for j in range(len(fts)):
            d, m = int(fdate[j]), int(fmin[j])
            if 17 * 60 <= m < 17 * 60 + 60 and d not in o17:
                o17[d] = (int(fts[j]), fo[j])
            if m < 60 and d not in omid:
                omid[d] = (int(fts[j]), fo[j])
        bts = b["ts"] - shift
        bdate = bts // 86400
        for i in range(n):
            d = int(bdate[i])
            t = int(bts[i])
            cand = o17.get(d)
            if cand is None or cand[0] > t:
                cand = o17.get(d - 1)
            if cand is not None and cand[0] <= t:
                l17[i] = cand[1]
            mm = omid.get(d)
            if mm is not None and mm[0] <= t:
                lmid[i] = mm[1]
        return l17.tolist(), lmid.tolist()
    return F.cached(ctx, "mul_two_lines", build)


def two_lines(bars, ctx, p):
    """"This is my 2 Lines trading strategy. Built on M15. Fully mechanical."
    Lines: the opening price of the 17:00 candle and of the midnight candle
    (New York time). Bias: price above BOTH lines -> sell only; below both
    -> buy only; between -> no trade. Windows: London 02:00-05:00 and New
    York 07:00-10:00 (New York time). Setup: a WEAKNESS candle -- it takes
    the highest high of the prior `k` bars but fails to close above it (a
    wick rejection) -- then confirmation: an opposite-colour close (the same
    candle if it already closed bearish, else the next bar). Mirror for
    buys. Stop above the weakness high (floored); `level` = the nearer
    line (the over-extension reverting)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    l17, lmid = _two_lines(bars, ctx)
    real = b["L"]["real"]
    k = p["k"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], k)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], k)[:-1])).tolist()
    windows = ((2 * 60, 5 * 60), (7 * 60, 10 * 60))
    pending = None                   # (side, extreme, bar)
    out = {}
    for i in range(len(c)):
        A = a[i]
        m = real[i]
        inside = any(s <= m < e for s, e in windows)
        if not (A > 0 and fin(l17[i]) and fin(lmid[i]) and fin(hh[i])) or not inside:
            pending = None
            continue
        hi_l, lo_l = max(l17[i], lmid[i]), min(l17[i], lmid[i])
        if pending is not None:
            side, ext, t0 = pending
            if i - t0 == 1 and side * (c[i] - o[i]) > 0 and _ok(b, i, p):
                near = lo_l if side == -1 else hi_l
                _emit(out, i, side, F.stop_dist(side, c[i], ext, A),
                      {"far": F.tgt(side, c[i], near)})
            pending = None
        if c[i] > hi_l and h[i] > hh[i] and c[i] < hh[i]:          # weakness at highs
            if c[i] < o[i] and _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A),
                      {"far": F.tgt(-1, c[i], hi_l)})
            else:
                pending = (-1, h[i], i)
        elif c[i] < lo_l and l[i] < ll[i] and c[i] > ll[i]:        # weakness at lows
            if c[i] > o[i] and _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A),
                      {"far": F.tgt(1, c[i], lo_l)})
            else:
                pending = (1, l[i], i)
    return out


SPECS = {
    "mul_two_lines": (two_lines, lambda g: {
        "k": (4, 8), "exit_mode": ("level", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ()
