"""tomtrades' YouTube (@itstomtrades) strategies, as `cfd_tt_families`
families.

Source: 59 videos listed 2026-10-03; the 20 strategy/backtest videos read
from the English captions. Tom trades gold and USDJPY from Asia to London
with one entry model -- a lower-timeframe market-structure shift -- timed by
the hourly candle and placed by the session/range condition. His numbers are
his own journal (2,400 trades, "70-88% win rate, ~1:2"). Map:
`results/tiktok/tomtrades_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s. These need sub-hour bars: on 30m
an hour is two bars and the "minute of the hour" timing is coarse.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _base(bars, ctx):
    return F.base(bars, ctx)


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _hours(bars, ctx):
    """Per bar: the clock hour's running high/low, the previous hour's
    high/low, and the minute within the hour (bar END, bar-size aware)."""
    def build():
        b = _base(bars, ctx)
        n = b["n"]
        h, l = b["h"], b["l"]
        mins = b["minute"]
        day = b["day"]
        bar = b["bar"]
        key = day * 100 + (mins // 60)
        rh = numpy.empty(n)
        rl = numpy.empty(n)
        ph = numpy.full(n, NAN)
        pl = numpy.full(n, NAN)
        cur, ch, cl = None, -numpy.inf, numpy.inf
        last_h = last_l = NAN
        for i in range(n):
            if key[i] != cur:
                if cur is not None and key[i] == cur + 1:
                    last_h, last_l = ch, cl
                elif cur is not None:
                    last_h = last_l = NAN
                cur, ch, cl = key[i], h[i], l[i]
            else:
                ch, cl = max(ch, h[i]), min(cl, l[i])
            rh[i], rl[i], ph[i], pl[i] = ch, cl, last_h, last_l
        mih = (mins % 60) + bar
        return {"rh": rh, "rl": rl, "ph": ph, "pl": pl, "mih": mih}
    return F.cached(ctx, "tom_hours", build)


def hour_extension_fade(bars, ctx, p):
    """#21, #27, #30 (2025-26). "The 1H pattern": the new hourly candle runs
    above the previous hour's high (or below its low) by at least `ext` ATR;
    then, between minute 30 and 50 of the hour (his win rate climbs from 51%
    at the open of the hour toward ~77% by the half hour), a lower-timeframe
    market-structure shift against the run -- a close below the low of the
    last `k` bars -> fade it. Stop beyond the hour's extreme (floored);
    `level` = the previous hour's midpoint; his average reward ~1:2."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    hr = _hours(bars, ctx)
    rh, rl, ph, pl, mih = (hr[x].tolist() for x in ("rh", "rl", "ph", "pl", "mih"))
    k = p["k"]
    lo_k = numpy.concatenate(([NAN], F.rolling_min(b["l"], k)[:-1])).tolist()
    hi_k = numpy.concatenate(([NAN], F.rolling_max(b["h"], k)[:-1])).tolist()
    out = {}
    fired = set()
    key = (b["day"] * 100 + b["minute"] // 60).tolist()
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(ph[i]) or key[i] in fired:
            continue
        if not (30 <= mih[i] <= 50) or not _ok(b, i, p):
            continue
        mid = (ph[i] + pl[i]) / 2
        if rh[i] > ph[i] + p["ext"] * A and fin(lo_k[i]) and c[i] < lo_k[i]:
            fired.add(key[i])
            out[i] = (-1, F.stop_dist(-1, c[i], rh[i], A), {"far": F.tgt(-1, c[i], mid)})
        elif rl[i] < pl[i] - p["ext"] * A and fin(hi_k[i]) and c[i] > hi_k[i]:
            fired.add(key[i])
            out[i] = (1, F.stop_dist(1, c[i], rl[i], A), {"far": F.tgt(1, c[i], mid)})
    return {i: v for i, v in out.items() if v[2]["far"] > 0 and fin(v[2]["far"])}


def london_tokyo_fade(bars, ctx, p):
    """#43 (2024). "How I trade London session (84% WR)": bias from where
    price sits in the Tokyo (Asia) range -- in its upper `zone` fraction look
    for sells, lower for buys; trade the London session's third hour (05:00-
    06:00 New York) on a lower-timeframe shift (a close through the last `k`
    bars' low for sells); stop beyond the session's extreme so far; `level`
    = the Tokyo range's midpoint (his mean-reversion target)."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    real = b["L"]["real"]
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "asia")
    th, tl = w["hi"].tolist(), w["lo"].tolist()
    k = p["k"]
    lo_k = numpy.concatenate(([NAN], F.rolling_min(b["l"], k)[:-1])).tolist()
    hi_k = numpy.concatenate(([NAN], F.rolling_max(b["h"], k)[:-1])).tolist()
    swing_h = F.rolling_max(b["h"], 12).tolist()
    swing_l = F.rolling_min(b["l"], 12).tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, fired = day[i], False
        A = a[i]
        if fired or not (A > 0) or not fin(th[i]) or not (5 * 60 <= real[i] < 6 * 60):
            continue
        rng = th[i] - tl[i]
        if not rng > 0 or not _ok(b, i, p):
            continue
        q = (c[i] - tl[i]) / rng
        mid = (th[i] + tl[i]) / 2
        if q >= 1 - p["zone"] and fin(lo_k[i]) and c[i] < lo_k[i]:
            fired = True
            t = F.tgt(-1, c[i], mid)
            if t > 0:
                out[i] = (-1, F.stop_dist(-1, c[i], swing_h[i], A), {"far": t})
        elif q <= p["zone"] and fin(hi_k[i]) and c[i] > hi_k[i]:
            fired = True
            t = F.tgt(1, c[i], mid)
            if t > 0:
                out[i] = (1, F.stop_dist(1, c[i], swing_l[i], A), {"far": t})
    return out


def range_reversal(bars, ctx, p):
    """#18 (2026). "The foolproof strategy": over the last `n` bars (his
    4-5 hours) the market is a RANGE -- the least-squares slope moves less
    than `flat` ATR across the window -- then in its lower third, a sweep of
    the window's low (a low below it, close back inside) followed within 6
    bars by a shift up (a close above the last 3 bars' high) -> long; mirror
    in the upper third. Stop below the sweep; `level` = the range's far
    side."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    n = p["n"]
    cc = b["c"]

    def build():
        m = len(cc)
        slope = numpy.full(m, NAN)
        if m > n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(cc[:-1], n)
            x = numpy.arange(n, dtype=float)
            xc = x - x.mean()
            slope[n:] = (w - w.mean(axis=1, keepdims=True)) @ xc / (xc @ xc)
        return slope
    slope = F.cached(ctx, ("tom_slope", n), build).tolist()
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    hi3 = numpy.concatenate(([NAN], F.rolling_max(b["h"], 3)[:-1])).tolist()
    lo3 = numpy.concatenate(([NAN], F.rolling_min(b["l"], 3)[:-1])).tolist()
    out = {}
    swept = {1: None, -1: None}           # side -> (bar, extreme, top, bot)
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(slope[i]) or not fin(hh[i]):
            continue
        flat = abs(slope[i]) * n < p["flat"] * A
        top, bot = hh[i], ll[i]
        if flat and l[i] < bot < c[i]:
            swept[1] = (i, l[i], top, bot)
        if flat and h[i] > top > c[i]:
            swept[-1] = (i, h[i], top, bot)
        for side in (1, -1):
            s = swept[side]
            if s is None or s[0] == i:
                continue
            j, ext, t0, b0 = s
            if i - j > 6:
                swept[side] = None
                continue
            q = (c[i] - b0) / (t0 - b0) if t0 > b0 else 0.5
            if side == 1 and c[i] > hi3[i] and q <= 0.33 and _ok(b, i, p):
                swept[side] = None
                out.setdefault(i, (1, F.stop_dist(1, c[i], ext, A), {"far": F.tgt(1, c[i], t0)}))
            elif side == -1 and c[i] < lo3[i] and q >= 0.67 and _ok(b, i, p):
                swept[side] = None
                out.setdefault(i, (-1, F.stop_dist(-1, c[i], ext, A), {"far": F.tgt(-1, c[i], b0)}))
    return {i: v for i, v in out.items() if v[2]["far"] > 0 and fin(v[2]["far"])}


SPECS = {
    "tom_hour_extension_fade": (hour_extension_fade, lambda g: {
        "ext": (0.25, 0.5), "k": (2, 3),
        "exit_mode": ("rr_2", "level", "rr_1"), "last_entry_minute": g["last"]}, False, None),
    "tom_london_tokyo_fade": (london_tokyo_fade, lambda g: {
        "zone": (0.25, 0.4), "k": (2, 3),
        "exit_mode": ("level", "rr_2", "rr_1"), "last_entry_minute": g["last"]}, True, None),
    "tom_range_reversal": (range_reversal, lambda g: {
        "n": (24, 48), "flat": (1.0, 2.0),
        "exit_mode": ("level", "rr_2", "rr_1"), "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ()
