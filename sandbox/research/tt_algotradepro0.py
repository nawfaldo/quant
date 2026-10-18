"""AlgoTrade Pro's TikTok (@algotradepro0) strategies, as `cfd_tt_families`
families.

Source: 37 posts on the grid (full-length YouTube cuts of his "testing
indicators / strategies" series), screened by caption 2026-10-04; 15
strategy posts read. His standard risk model: stop on the ATR(14) band
`stop_atr` x ATR from the entry, take profit 1:1.5 (some episodes: half off
at 1:1 then an ATR trail -> `trail_` exits). Proprietary or unpublished
scripts (Purple Cloud, Super Trend Fusion + MMI, Range Identifier, Gold
Scalper, Super Ichi) are skipped. Map:
`results/tiktok/algotradepro0_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research import tt_tradingrush as TR

NAN = float("nan")
fin = math.isfinite


def _ema_arr(x, n):
    out = numpy.full(len(x), NAN)
    alpha = 2.0 / (n + 1.0)
    acc = NAN
    for i, v in enumerate(x):
        if not fin(v):
            continue
        acc = v if not fin(acc) else acc + alpha * (v - acc)
        out[i] = acc
    return out


def _signals(bars, ctx, p, sig):
    """Entries on `sig` (+1/-1) with his ATR-band stop."""
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    out = {}
    for i in numpy.flatnonzero(sig).tolist():
        A = a[i]
        if A > 0 and mins[i] <= last:
            out[i] = (int(sig[i]), p["stop_atr"] * A, None)
    return out


def _first(cond_l, cond_s):
    """+1 on the first bar `cond_l` holds, -1 on the first `cond_s` bar."""
    l = numpy.asarray(cond_l, dtype=bool)
    s = numpy.asarray(cond_s, dtype=bool)
    sig = numpy.zeros(len(l), dtype=numpy.int8)
    sig[1:][l[1:] & ~l[:-1]] = 1
    sig[1:][s[1:] & ~s[:-1]] = -1
    return sig


def st_macd_adx(bars, ctx, p):
    """"NEW Crypto Scalping Strategy | SuperTrend + MACD + ADX" (5m, a
    subscriber's rules): SuperTrend(10, 3) buy flip, MACD(34, 52, 21)
    histogram above zero, ADX(14) above `adx` -> long; sell mirror. Stop
    2 x ATR band, 1:1.5."""
    st = F.supertrend(bars, ctx, 10, 3.0)
    hist = F.macd(bars, ctx, 34, 52, 21)["hist"]
    ax = numpy.nan_to_num(F.adx(bars, ctx, 14)["adx"], nan=0.0)
    fl = st["flip"]
    sig = numpy.where((fl == 1) & (hist > 0) & (ax > p["adx"]), 1,
                      numpy.where((fl == -1) & (hist < 0) & (ax > p["adx"]), -1, 0)).astype(numpy.int8)
    return _signals(bars, ctx, p, sig)


def alligator_ema200(bars, ctx, p):
    """"Williams Alligator EXPOSED | 4,000+ trades": lips cross above BOTH
    teeth and jaw with the close above all three lines, and the close above
    the 200 EMA -> long; mirror short. Stop 2 x ATR band, 1:1.5."""
    jaw, teeth, lips = TR._alligator(bars, ctx)
    c = F.base(bars, ctx)["c"]
    e = F.ema(bars, ctx, 200)
    top = numpy.fmax(teeth, jaw)
    bot = numpy.fmin(teeth, jaw)
    ok = numpy.isfinite(jaw) & numpy.isfinite(e)
    up = ok & (lips > top) & (c > numpy.fmax(top, lips))
    dn = ok & (lips < bot) & (c < numpy.fmin(bot, lips))
    lu = numpy.zeros(len(c), dtype=bool)
    ld = numpy.zeros(len(c), dtype=bool)
    lu[1:] = (lips[1:] > top[1:]) & ~(lips[:-1] > top[:-1])
    ld[1:] = (lips[1:] < bot[1:]) & ~(lips[:-1] < bot[:-1])
    sig = numpy.where(up & lu & (c > e), 1, numpy.where(dn & ld & (c < e), -1, 0)).astype(numpy.int8)
    return _signals(bars, ctx, p, sig)


def _zl_macd(bars, ctx, f, s, g):
    def build():
        c = F.base(bars, ctx)["c"]

        def zl(x, n):
            e1 = _ema_arr(x, n)
            return 2 * e1 - _ema_arr(e1, n)
        line = zl(c, f) - zl(c, s)
        return line - zl(line, g)
    return F.cached(ctx, ("atp_zlmacd", f, s, g), build)


def zl_macd_ema200(bars, ctx, p):
    """"Zero Lag MACD vs Standard MACD" (34 / 52 / 21, histogram only): the
    histogram turns green (crosses above zero) with the close above the 200
    EMA -> long; red below -> short. `zl` 0 = the standard MACD, his
    comparison leg. Stop 2 x ATR band; his half at 1:1 + ATR trail."""
    hist = _zl_macd(bars, ctx, 34, 52, 21) if p["zl"] else F.macd(bars, ctx, 34, 52, 21)["hist"]
    c = F.base(bars, ctx)["c"]
    e = F.ema(bars, ctx, 200)
    h = numpy.nan_to_num(hist, nan=0.0)
    sig = numpy.zeros(len(c), dtype=numpy.int8)
    up = (h[1:] > 0) & (h[:-1] <= 0) & (c[1:] > e[1:])
    dn = (h[1:] < 0) & (h[:-1] >= 0) & (c[1:] < e[1:])
    sig[1:] = numpy.where(up, 1, numpy.where(dn, -1, 0))
    return _signals(bars, ctx, p, sig)


def _gauss(x, per, poles=4):
    """DonovanWall's N-pole Gaussian filter (no lag reduction)."""
    beta = (1 - math.cos(4 * math.asin(1) / per)) / (1.414 ** (2.0 / poles) - 1)
    al = -beta + math.sqrt(beta * beta + 2 * beta)
    q = 1 - al
    out = numpy.full(len(x), NAN)
    f1 = f2 = f3 = f4 = None
    for i, v in enumerate(x):
        if not fin(v):
            continue
        if f1 is None:
            f1 = f2 = f3 = f4 = v
        f = al ** 4 * v + 4 * q * f1 - 6 * q ** 2 * f2 + 4 * q ** 3 * f3 - q ** 4 * f4
        f4, f3, f2, f1 = f3, f2, f1, f
        out[i] = f
    return out


def gaussian_kijun_vap(bars, ctx, p):
    """The "+156% gold scalping" series (5m): trigger = Gaussian Channel
    (DonovanWall, 4 poles, period 144, multiplier 1.414 on hlc3) turning
    long -- the filter rising and the close above the upper band; trend
    filter = close above the 120-period Kijun-sen; volume filter = Volume
    Accumulation Percentage (`vlen`) above `vap`. Mirror for shorts. Stop
    3 x ATR; his 30% at 1:0.75 then an ATR(3) trail."""
    def build():
        b = F.base(bars, ctx)
        h, l, c = b["h"], b["l"], b["c"]
        prev = numpy.concatenate(([c[0]], c[:-1]))
        tr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
        filt = _gauss((h + l + c) / 3.0, 144)
        ftr = _gauss(tr, 144)
        return filt, filt + 1.414 * ftr, filt - 1.414 * ftr
    filt, hb, lb = F.cached(ctx, "atp_gauss", build)
    b = F.base(bars, ctx)
    h, l, c, v = b["h"], b["l"], b["c"], b["v"]
    kij = (F.rolling_max(h, 120) + F.rolling_min(l, 120)) / 2.0
    rng = numpy.where(h > l, h - l, numpy.nan)
    xt = numpy.nan_to_num(((c - l) - (h - c)) / rng, nan=0.0) * v
    n = p["vlen"]
    vap = numpy.full(len(c), NAN)
    if len(c) >= n:
        cs = numpy.cumsum(numpy.insert(xt, 0, 0.0))
        cv = numpy.cumsum(numpy.insert(v, 0, 0.0))
        vap[n - 1:] = 100.0 * (cs[n:] - cs[:-n]) / numpy.maximum(cv[n:] - cv[:-n], 1e-12)
    rising = numpy.zeros(len(c), dtype=bool)
    rising[1:] = filt[1:] > filt[:-1]
    long_c = rising & (c > hb)
    short_c = ~rising & (c < lb)
    trig = _first(long_c, short_c)
    sig = numpy.where((trig == 1) & (c > kij) & (vap > p["vap"]), 1,
                      numpy.where((trig == -1) & (c < kij) & (vap < -p["vap"]), -1, 0)).astype(numpy.int8)
    sig[:300] = 0
    return _signals(bars, ctx, p, sig)


SPECS = {
    "atp_st_macd_adx": (st_macd_adx, lambda g: {
        "adx": (20, 25), "stop_atr": (2.0, 3.0), "exit_mode": ("rr_1.5", "rr_1", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "atp_alligator_ema200": (alligator_ema200, lambda g: {
        "stop_atr": (2.0, 3.0), "exit_mode": ("rr_1.5", "rr_1", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "atp_zl_macd_ema200": (zl_macd_ema200, lambda g: {
        "zl": (1, 0), "stop_atr": (2.0, 3.0), "exit_mode": ("rr_1.5", "rr_1", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "atp_gaussian_kijun_vap": (gaussian_kijun_vap, lambda g: {
        "vlen": (10, 20), "vap": (5.0, 10.0), "stop_atr": (3.0,),
        "exit_mode": ("trail_0.25", "rr_0.75", "rr_1.5"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
