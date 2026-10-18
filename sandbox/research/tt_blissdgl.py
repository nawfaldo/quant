"""Jooviers Gems' TikTok (@blissdgl_) strategies, as `cfd_tt_families`
families.

Source: 74 posts on the grid, screened by caption 2026-10-04; the 18 named
strategy videos read from their English captions (long-form, 5-25 minutes,
mostly "full course" funnels to a paid inner circle). Two rules are stated as
checklists precise enough to code; his London session breakout is already
`tmb_london_body_break` (TradingMythbusters tested it: "FAILS in real data").
Map and SKIP list: `results/tiktok/blissdgl_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close.
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
    if i not in out:
        out[i] = (side, dist, target)
    return True


def open_swing_break(bars, ctx, p):
    """"This scalping strategy is boring, but it made me profitable" (YM 1m,
    "my YM market open breakout strategy"): one minute before the cash open,
    mark the most recent swing high and swing low (`k`-bar pivots, wick to
    wick); stop orders at both; the first one price breaks after the open is
    the trade. Fixed risk (his 10 ticks for 30: here `stop` x ATR) and a
    fixed 3:1. Only the first `win` minutes of the session; one a day."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    since, day = b["L"]["since"], b["L"]["day"]
    first = b["first"].tolist()
    lv_day, hi, lo, done = None, NAN, NAN, None
    out = {}
    for i in range(1, len(c)):
        if first[i]:
            # levels known at the bar BEFORE the open (the last pre-open bar)
            lv_day, hi, lo = day[i], pv["h"][i - 1], pv["l"][i - 1]
        A = a[i]
        if day[i] != lv_day or day[i] == done or not (A > 0) or since[i] > p["win"]:
            continue
        if not (fin(hi) and fin(lo)) or not _ok(b, i, p):
            continue
        if h[i] > hi and c[i] > hi:
            _emit(out, i, 1, p["stop"] * A, None)
            done = day[i]
        elif l[i] < lo and c[i] < lo:
            _emit(out, i, -1, p["stop"] * A, None)
            done = day[i]
    return out


def _heikin(bars, ctx):
    def build():
        b = F.base(bars, ctx)
        o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        n = len(c)
        hc = (o + h + l + c) / 4.0
        ho = numpy.empty(n)
        if n:
            ho[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ho[i] = (ho[i - 1] + hc[i - 1]) / 2.0
        hh = numpy.maximum(h, numpy.maximum(ho, hc))
        hl = numpy.minimum(l, numpy.minimum(ho, hc))
        return ho.tolist(), hh.tolist(), hl.tolist(), hc.tolist()
    return F.cached(ctx, "jg_ha", build)


def ha_ema_pullback(bars, ctx, p):
    """"The 1 minute scalping strategy (that actually works)" and "The Heikin
    Ashi scalping strategy": Heikin Ashi candles and a 100 EMA. Above the EMA
    look only for buys, below only for sells. A CLEAN pullback = at least
    `pull` consecutive Heikin Ashi candles against the trend with no wick on
    the trend side (for a sell setup: green candles with no lower wick);
    then a Heikin Ashi DOJI (body <= `doji` x its range) -> enter with the
    trend. Stop beyond the doji (floored); target 1:1 minimum (his)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    ho, hh, hl, hc = _heikin(bars, ctx)
    e = F.ema(bars, ctx, 100)
    run_up = run_dn = 0              # clean HA candles going up / down in a row
    out = {}
    for i in range(1, len(c)):
        rng = hh[i] - hl[i]
        body = abs(hc[i] - ho[i])
        green, red = hc[i] > ho[i], hc[i] < ho[i]
        no_low_wick = min(ho[i], hc[i]) - hl[i] <= 1e-12 * max(1.0, abs(hl[i]))
        no_up_wick = hh[i] - max(ho[i], hc[i]) <= 1e-12 * max(1.0, abs(hh[i]))
        is_doji = rng > 0 and body <= p["doji"] * rng
        A = a[i]
        if A > 0 and fin(e[i]) and is_doji and _ok(b, i, p):
            if c[i] < e[i] and run_up >= p["pull"]:
                _emit(out, i, -1, F.stop_dist(-1, c[i], max(h[i], hh[i]), A), None)
            elif c[i] > e[i] and run_dn >= p["pull"]:
                _emit(out, i, 1, F.stop_dist(1, c[i], min(l[i], hl[i]), A), None)
        run_up = run_up + 1 if (green and no_low_wick) else 0
        run_dn = run_dn + 1 if (red and no_up_wick) else 0
    return out


SPECS = {
    "jg_open_swing_break": (open_swing_break, lambda g: {
        "k": (3, 5), "win": (30, 60), "stop": (0.5, 1.0),
        "exit_mode": ("rr_3", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
    "jg_ha_ema_pullback": (ha_ema_pullback, lambda g: {
        "pull": (2, 3), "doji": (0.2, 0.35),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
