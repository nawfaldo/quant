"""Quant Tactics' TikTok (@quanttactic) strategies, as `cfd_tt_families`
families.

Source: all 44 posts on the grid, read 2026-10-04 (32 unique strategy videos
from their English captions; the rest are repeats and Freqtrade tutorials).
Every strategy is coded in Freqtrade and backtested on 8 crypto pairs, most
over May 2020 - 2026 at several timeframes ("4h won" is the usual finding).
Indicator settings are as they state them. Map and SKIP list:
`results/tiktok/quanttactic_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open.
Their "exit on X" rules are `signal` exits; their ATR stops are the stop.
Their Freqtrade runs are mostly long-only spot; here both sides are taken
where the video gives the mirror, long-only otherwise (`side` axis absent).
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


def _emit(out, i, side, dist, target=None):
    if not (dist > 0) or not fin(dist):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _ema(bars, ctx, n):
    return F.cached(ctx, ("qtt_ema", n), lambda: F.ema(bars, ctx, n).tolist())


def _rsi(bars, ctx, n=14):
    return F.cached(ctx, ("qtt_rsi", n), lambda: F.rsi(bars, ctx, n).tolist())


def _macd(bars, ctx):
    def build():
        m = F.macd(bars, ctx)
        return m["macd"].tolist(), m["signal"].tolist()
    return F.cached(ctx, "qtt_macd", build)


def _bb(bars, ctx, n, dev):
    def build():
        c = F.base(bars, ctx)["c"]
        mid = numpy.full(len(c), NAN)
        sd = numpy.full(len(c), NAN)
        if len(c) >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(c, n)
            mid[n - 1:], sd[n - 1:] = w.mean(axis=1), w.std(axis=1)
        return mid.tolist(), (mid + dev * sd).tolist(), (mid - dev * sd).tolist()
    return F.cached(ctx, ("qtt_bb", n, dev), build)


def _vol_avg(bars, ctx, n=20):
    b = F.base(bars, ctx)
    return F.cached(ctx, ("qtt_vavg", n), lambda: F.sma(b["v"].astype(float), n).tolist())


def _xup(a, b, i):
    return fin(a[i - 1]) and fin(b[i - 1]) and a[i] > b[i] and a[i - 1] <= b[i - 1]


def _xdn(a, b, i):
    return fin(a[i - 1]) and fin(b[i - 1]) and a[i] < b[i] and a[i - 1] >= b[i - 1]


def _ema_arr(x, n):
    return F._ema_arr(numpy.asarray(x, dtype=float), n)


# --------------------------------------------------------------------------- #
# the "I tested X for 6 years" series (exits are signals, ATR safety stops)
# --------------------------------------------------------------------------- #

def triple_ema(bars, ctx, p):
    """"I tested a triple EMA strategy for 6 years... 3714%" (4h best).
    Price above EMA200 and EMA55; EMA34 crosses above EMA55 -> long (mirror
    short). Exit: the 34 crosses back (`signal`). ATR stop `stop` x ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    e34, e55, e200 = _ema(bars, ctx, 34), _ema(bars, ctx, 55), _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(e200[i])) or not _ok(b, i, p):
            continue
        if _xup(e34, e55, i) and c[i] > e55[i] and c[i] > e200[i]:
            _emit(out, i, 1, p["stop"] * A)
        elif _xdn(e34, e55, i) and c[i] < e55[i] and c[i] < e200[i]:
            _emit(out, i, -1, p["stop"] * A)
    return out


def triple_ema_exit(index, bars, ctx, params, side):
    e34, e55 = _ema(bars, ctx, 34), _ema(bars, ctx, 55)
    return fin(e55[index]) and side * (e34[index] - e55[index]) < 0


def bos_second(bars, ctx, p):
    """"I tested Smart Money Concepts for 6 years... 4,900%" / "Smart Money
    BOS strategy: here's the truth" (4h best; 15m -81%). The first bullish
    break of structure sets the structure; the SECOND consecutive one is the
    entry, only above EMA200 (mirror short). Stop 2 x ATR. Exit: a close
    through the latest swing the other way (CHoCH) -- `signal`."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    st = F.structure(bars, ctx, p["k"])
    e200 = _ema(bars, ctx, 200)
    up, dn, run = st["up"], st["dn"], st["run"]
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(e200[i])) or not _ok(b, i, p):
            continue
        if up[i] and run[i] == 2 and c[i] > e200[i]:
            _emit(out, i, 1, 2.0 * A)
        elif dn[i] and run[i] == 2 and c[i] < e200[i]:
            _emit(out, i, -1, 2.0 * A)
    return out


def bos_exit(index, bars, ctx, params, side):
    pv = F.pivots(bars, ctx, params["k"])
    c = F.base(bars, ctx)["c"][index]
    lvl = pv["l"][index] if side == 1 else pv["h"][index]
    return fin(lvl) and side * (c - lvl) < 0


def _force(bars, ctx, n):
    def build():
        b = F.base(bars, ctx)
        c, v = b["c"], b["v"].astype(float)
        raw = numpy.concatenate(([0.0], numpy.diff(c))) * v
        return _ema_arr(raw, n).tolist()
    return F.cached(ctx, ("qtt_force", n), build)


def bb_force(bars, ctx, p):
    """"Bollinger Bands + Force Index for 6 years" (long only, BB 40 / 2.5,
    Force Index EMA 55). A close above the upper band while above EMA200 ->
    long. Exit: Force Index crosses below zero (`signal`). ATR stop."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    mid, up, lo = _bb(bars, ctx, 40, p["dev"])
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(up[i]) and fin(e200[i])) or not _ok(b, i, p):
            continue
        if c[i] > up[i] and c[i - 1] <= up[i - 1] and c[i] > e200[i]:
            _emit(out, i, 1, p["stop"] * A)
    return out


def bb_force_exit(index, bars, ctx, params, side):
    f = _force(bars, ctx, 55)
    return index > 0 and fin(f[index - 1]) and f[index] < 0 <= f[index - 1]


def vwap_rsi_vol(bars, ctx, p):
    """"VWAP + MACD for 6 years: 3,856%" (as described: long only). Price
    crosses above VWAP, RSI above 50, volume above `vmult` x its 20-bar average, price
    above EMA200 -> long. Exit: two consecutive closes below VWAP (`signal`).
    Stop 3 x ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx).tolist()
    r = _rsi(bars, ctx)
    v, va = b["L"]["v"], _vol_avg(bars, ctx)
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(vw[i]) and fin(vw[i - 1]) and fin(e200[i]) and fin(va[i])):
            continue
        if c[i] > vw[i] and c[i - 1] <= vw[i - 1] and r[i] > 50 and v[i] > p["vmult"] * va[i] \
                and c[i] > e200[i] and _ok(b, i, p):
            _emit(out, i, 1, 3.0 * A)
    return out


def vwap_two_close_exit(index, bars, ctx, params, side):
    if index < 1:
        return False
    vw = F.vwap(bars, ctx)
    c = F.base(bars, ctx)["c"]
    return c[index] < vw[index] and c[index - 1] < vw[index - 1]


def supertrend_ema200(bars, ctx, p):
    """"Supertrend + EMA200 for 6 years (long AND short): 2,529%". SuperTrend
    length 8, multiplier 4: a flip up above EMA200 -> long, a flip down below
    it -> short. Exit: the SuperTrend flips back (`signal`). Stop 1.5 ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    st = F.supertrend(bars, ctx, 8, p["mult"])
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(len(c)):
        A, f = a[i], st["flip"][i]
        if not (A > 0 and f and fin(e200[i])) or not _ok(b, i, p):
            continue
        if f > 0 and c[i] > e200[i]:
            _emit(out, i, 1, 1.5 * A)
        elif f < 0 and c[i] < e200[i]:
            _emit(out, i, -1, 1.5 * A)
    return out


def supertrend_exit(index, bars, ctx, params, side):
    return F.supertrend(bars, ctx, 8, params["mult"])["dir"][index] == -side


def ema_macd(bars, ctx, p):
    """"EMA + MACD for 6 years: 2311%" (long only). Price above EMA200; the
    MACD crossed above its signal within the last 3 bars and is above zero
    -> long. Exit: MACD crosses back below the signal, or a close below
    EMA200 (`signal`). Stop 1.5 ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m, s = _macd(bars, ctx)
    e200 = _ema(bars, ctx, 200)
    last_x = -10 ** 9
    out = {}
    for i in range(1, len(c)):
        if _xup(m, s, i):
            last_x = i
        A = a[i]
        if not (A > 0 and fin(e200[i]) and fin(m[i])) or not _ok(b, i, p):
            continue
        if i - last_x <= p["within"] and m[i] > 0 and m[i] > s[i] and c[i] > e200[i]:
            _emit(out, i, 1, 1.5 * A)
            last_x = -10 ** 9
    return out


def ema_macd_exit(index, bars, ctx, params, side):
    m, s = _macd(bars, ctx)
    e200 = _ema(bars, ctx, 200)
    c = F.base(bars, ctx)["c"][index]
    return (fin(m[index]) and m[index] < s[index]) or (fin(e200[index]) and c < e200[index])


def rsi_ema_retest(bars, ctx, p):
    """"I backtested RSI for 6 years (6,047%)". RSI(14) with a 9-EMA of RSI.
    Entry A: RSI crosses above its EMA while the EMA is above 50. Entry B
    (the retest): RSI pulls back into 44-55 while its EMA holds above 50.
    Price above EMA200. Exit: EMA20 crosses below EMA50 (`signal`). Stop
    1.5 ATR. Long only."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = _rsi(bars, ctx)
    re = F.cached(ctx, "qtt_rsi_ema9", lambda: _ema_arr(r, 9).tolist())
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(re[i]) and fin(e200[i])) or not _ok(b, i, p) or c[i] <= e200[i]:
            continue
        cross = _xup(r, re, i) and re[i] > 50
        retest = p["retest"] and 44 <= r[i] <= 55 and not (44 <= r[i - 1] <= 55) and re[i] > 50
        if cross or retest:
            _emit(out, i, 1, 1.5 * A)
    return out


def ema20_50_exit(index, bars, ctx, params, side):
    e20, e50 = _ema(bars, ctx, 20), _ema(bars, ctx, 50)
    return fin(e50[index]) and side * (e20[index] - e50[index]) < 0


def bb_breakout_vol(bars, ctx, p):
    """"Bollinger Band breakout for 6 years: 5,277%" (12% max DD). BB(20, 1):
    a close above the upper band, above EMA200, volume above its 20-bar
    average -> long. Exit: a close back below the middle band (`signal`).
    Stop 2 x ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    mid, up, lo = _bb(bars, ctx, 20, p["dev"])
    v, va = b["L"]["v"], _vol_avg(bars, ctx)
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(up[i]) and fin(e200[i]) and fin(va[i])) or not _ok(b, i, p):
            continue
        if c[i] > up[i] and c[i] > e200[i] and v[i] > va[i]:
            _emit(out, i, 1, 2.0 * A)
    return out


def bb_mid_exit(index, bars, ctx, params, side):
    mid = _bb(bars, ctx, 20, params["dev"])[0]
    c = F.base(bars, ctx)["c"][index]
    return fin(mid[index]) and side * (c - mid[index]) < 0


# --------------------------------------------------------------------------- #
# the "made N% profit (full tutorial)" series (fixed R targets)
# --------------------------------------------------------------------------- #

def rsi_ema_smooth(bars, ctx, p):
    """"RSI trading strategy made 314%". An EMA of RSI crosses above 65 with
    price above EMA200 -> long (below 35 under EMA200 -> short). Stop just
    beyond the signal candle (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = _rsi(bars, ctx)
    sm = F.cached(ctx, ("qtt_rsi_sm", p["smooth"]), lambda: _ema_arr(r, p["smooth"]).tolist())
    e200 = _ema(bars, ctx, 200)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(sm[i - 1]) and fin(e200[i])) or not _ok(b, i, p):
            continue
        if sm[i] > 65 >= sm[i - 1] and c[i] > e200[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A))
        elif sm[i] < 35 <= sm[i - 1] and c[i] < e200[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A))
    return out


def mr_bb_htf_rsi(bars, ctx, p):
    """"Mean reversion strategy explained & backtested - 179%". 1h chart:
    a close below the lower BB(20, 2) -> long if the higher-timeframe RSI is
    above 55 (they use the 4h RSI as a trend filter); above the upper band
    -> short if it is below 45. HTF RSI here = RSI(14 x `htf`) on the
    trading bars. Stop `stop` x ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    mid, up, lo = _bb(bars, ctx, 20, 2.0)
    rh = _rsi(bars, ctx, 14 * p["htf"])
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(lo[i]) and fin(rh[i])) or not _ok(b, i, p):
            continue
        if c[i] < lo[i] and rh[i] > 55:
            _emit(out, i, 1, p["stop"] * A)
        elif c[i] > up[i] and rh[i] < 45:
            _emit(out, i, -1, p["stop"] * A)
    return out


def _cmf(bars, ctx, n=20):
    def build():
        b = F.base(bars, ctx)
        h, l, c, v = b["h"], b["l"], b["c"], b["v"].astype(float)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            mfm = numpy.where(h > l, ((c - l) - (h - c)) / (h - l), 0.0)
        num = F.sma(mfm * v, n)
        den = F.sma(v, n)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            return numpy.where(den > 0, num / den, NAN).tolist()
    return F.cached(ctx, ("qtt_cmf", n), build)


def cmf_ema(bars, ctx, p):
    """"Chaikin Money Flow + EMA... 325%" / "CMF strategy made 316%". CMF(20)
    crosses above +`lvl` (0.05 / 0.07 "to cut false signals") with the close
    above EMA200 -> long; below -`lvl` under EMA200 -> short. Stop = signal
    close -/+ 2 x ATR; 2:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    cm = _cmf(bars, ctx)
    e200 = _ema(bars, ctx, 200)
    lv = p["lvl"]
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(cm[i - 1]) and fin(e200[i])) or not _ok(b, i, p):
            continue
        if cm[i] > lv >= cm[i - 1] and c[i] > e200[i]:
            _emit(out, i, 1, 2.0 * A)
        elif cm[i] < -lv <= cm[i - 1] and c[i] < e200[i]:
            _emit(out, i, -1, 2.0 * A)
    return out


def st_macd_rsi(bars, ctx, p):
    """"Supertrend + MACD + RSI... 153%". Close above the SuperTrend
    (10, `mult`), RSI above 50, MACD above its signal, all three newly true
    on this bar -> long; mirror short. Stop at the latest 5-bar swing low /
    high (floored); 1.5:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    st = F.supertrend(bars, ctx, 10, p["mult"])
    r = _rsi(bars, ctx)
    m, s = _macd(bars, ctx)
    pv = F.pivots(bars, ctx, 5)
    d = st["dir"]
    out = {}
    prev = 0
    for i in range(len(c)):
        A = a[i]
        cond = 0
        if fin(r[i]) and fin(m[i]):
            if d[i] > 0 and r[i] > 50 and m[i] > s[i]:
                cond = 1
            elif d[i] < 0 and r[i] < 50 and m[i] < s[i]:
                cond = -1
        if cond and cond != prev and A > 0 and _ok(b, i, p):
            lvl = pv["l"][i] if cond == 1 else pv["h"][i]
            _emit(out, i, cond, F.stop_dist(cond, c[i], lvl, A))
        prev = cond
    return out


def vwap_macd(bars, ctx, p):
    """"Master VWAP & MACD strategy - 191%" (1h). A close above VWAP and the
    MACD crossing above its signal (both within this bar or the last) ->
    long; mirror short. Stop 2.5 x ATR beyond the signal candle's low /
    high; 3:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx).tolist()
    m, s = _macd(bars, ctx)
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        if not (A > 0 and fin(vw[i]) and fin(m[i - 1])) or not _ok(b, i, p):
            continue
        xu = _xup(m, s, i) or _xup(m, s, i - 1)
        xd = _xdn(m, s, i) or _xdn(m, s, i - 1)
        if c[i] > vw[i] and c[i - 1] <= vw[i - 1] and xu:
            _emit(out, i, 1, c[i] - l[i] + p["atr_k"] * A)
        elif c[i] < vw[i] and c[i - 1] >= vw[i - 1] and xd:
            _emit(out, i, -1, h[i] - c[i] + p["atr_k"] * A)
    return out


def _ao(bars, ctx):
    def build():
        b = F.base(bars, ctx)
        hl2 = (b["h"] + b["l"]) / 2.0
        return (F.sma(hl2, 5) - F.sma(hl2, 34)).tolist()
    return F.cached(ctx, "qtt_ao", build)


def ao_macd_rsi(bars, ctx, p):
    """"Awesome Oscillator strategy made 189%". AO crosses above zero, MACD
    above its signal, RSI above 50 -- each fresh within `within` bars ->
    long; mirror short. Stop below the signal candle's low (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    ao = _ao(bars, ctx)
    m, s = _macd(bars, ctx)
    r = _rsi(bars, ctx)
    w = p["within"]
    lx = {1: [-10 ** 9] * 3, -1: [-10 ** 9] * 3}
    out = {}
    for i in range(1, len(c)):
        if fin(ao[i - 1]):
            if ao[i] > 0 >= ao[i - 1]:
                lx[1][0] = i
            if ao[i] < 0 <= ao[i - 1]:
                lx[-1][0] = i
        if _xup(m, s, i):
            lx[1][1] = i
        if _xdn(m, s, i):
            lx[-1][1] = i
        if fin(r[i - 1]):
            if r[i] > 50 >= r[i - 1]:
                lx[1][2] = i
            if r[i] < 50 <= r[i - 1]:
                lx[-1][2] = i
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        for side in (1, -1):
            t = lx[side]
            if max(t) == i and i - min(t) <= w:
                lvl = l[i] if side == 1 else h[i]
                _emit(out, i, side, F.stop_dist(side, c[i], lvl, A))
                lx[side] = [-10 ** 9] * 3
    return out


def williams_rsi_macd(bars, ctx, p):
    """"Williams %R indicator made 305%". %R(14) drops below -80 then rises
    back above -`up_to` (their -40), with RSI above 55 and MACD above its
    signal -> long; mirror (above -20 then back below -60, RSI < 45, MACD
    below) short. Stop below the signal candle (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    hh = F.rolling_max(b["h"], 14).tolist()
    ll = F.rolling_min(b["l"], 14).tolist()
    r = _rsi(bars, ctx)
    m, s = _macd(bars, ctx)
    armed = {1: False, -1: False}
    out = {}
    for i in range(len(c)):
        if not (fin(hh[i]) and hh[i] > ll[i]):
            continue
        wr = -100.0 * (hh[i] - c[i]) / (hh[i] - ll[i])
        if wr < -80:
            armed[1] = True
        if wr > -20:
            armed[-1] = True
        A = a[i]
        if not (A > 0 and fin(r[i]) and fin(m[i])) or not _ok(b, i, p):
            continue
        if armed[1] and wr > -p["up_to"] and r[i] > 55 and m[i] > s[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A))
            armed[1] = False
        elif armed[-1] and wr < -(100 - p["up_to"]) and r[i] < 45 and m[i] < s[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A))
            armed[-1] = False
    return out


def _alma(c, n=9, offset=0.85, sigma=6.0):
    m = offset * (n - 1)
    s = n / sigma
    w = numpy.exp(-((numpy.arange(n) - m) ** 2) / (2 * s * s))
    w /= w.sum()
    out = numpy.full(len(c), NAN)
    if len(c) >= n:
        from numpy.lib.stride_tricks import sliding_window_view
        out[n - 1:] = sliding_window_view(c, n) @ w
    return out


def alma_macd(bars, ctx, p):
    """"ALMA + MACD strategy" (BCH 1h, walk-forward). A close above the ALMA
    (`n`, 0.85, 6) and the MACD crossing above its signal -> long; mirror
    short. Stop 3 x ATR; 2.5:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    al = F.cached(ctx, ("qtt_alma", p["n"]), lambda: _alma(b["c"], p["n"]).tolist())
    m, s = _macd(bars, ctx)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(al[i])) or not _ok(b, i, p):
            continue
        if c[i] > al[i] and _xup(m, s, i):
            _emit(out, i, 1, 3.0 * A)
        elif c[i] < al[i] and _xdn(m, s, i):
            _emit(out, i, -1, 3.0 * A)
    return out


SIG = ("signal", "trail_0.5")

SPECS = {
    "qtt_triple_ema": (triple_ema, lambda g: {
        "stop": (2.0, 3.0), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, triple_ema_exit),
    "qtt_bos_second": (bos_second, lambda g: {
        "k": (3, 5), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, bos_exit),
    "qtt_bb_force": (bb_force, lambda g: {
        "dev": (2.0, 2.5), "stop": (2.0, 3.0), "exit_mode": SIG,
        "last_entry_minute": g["last"]}, False, bb_force_exit),
    "qtt_vwap_rsi_vol": (vwap_rsi_vol, lambda g: {
        "vmult": (1.0, 1.3), "exit_mode": ("signal", "rr_2", "trail_0.5"), "last_entry_minute": g["last"]},
        False, vwap_two_close_exit),
    "qtt_supertrend_ema200": (supertrend_ema200, lambda g: {
        "mult": (3.0, 4.0), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, supertrend_exit),
    "qtt_ema_macd": (ema_macd, lambda g: {
        "within": (1, 3), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, ema_macd_exit),
    "qtt_rsi_ema_retest": (rsi_ema_retest, lambda g: {
        "retest": (0, 1), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, ema20_50_exit),
    "qtt_bb_breakout_vol": (bb_breakout_vol, lambda g: {
        "dev": (1.0, 2.0), "exit_mode": SIG, "last_entry_minute": g["last"]},
        False, bb_mid_exit),
    "qtt_rsi_ema_smooth": (rsi_ema_smooth, lambda g: {
        "smooth": (5, 10), "exit_mode": ("rr_1.5", "rr_2", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_mr_bb_htf_rsi": (mr_bb_htf_rsi, lambda g: {
        "htf": (2, 4), "stop": (1.5, 2.5), "exit_mode": ("rr_1", "rr_2", "time_12"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_cmf_ema": (cmf_ema, lambda g: {
        "lvl": (0.05, 0.07), "exit_mode": ("rr_2", "rr_1.5", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_st_macd_rsi": (st_macd_rsi, lambda g: {
        "mult": (2.0, 3.0), "exit_mode": ("rr_1.5", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_vwap_macd": (vwap_macd, lambda g: {
        "atr_k": (1.5, 2.5), "exit_mode": ("rr_3", "rr_2", "rr_1.5"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_ao_macd_rsi": (ao_macd_rsi, lambda g: {
        "within": (2, 5), "exit_mode": ("rr_1.5", "rr_2", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_williams_rsi_macd": (williams_rsi_macd, lambda g: {
        "up_to": (40, 50), "exit_mode": ("rr_1.5", "rr_2", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "qtt_alma_macd": (alma_macd, lambda g: {
        "n": (9, 21), "exit_mode": ("rr_2.5", "rr_1.5", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
