"""Yo Forex's TikTok (@yo.forex3) indicator tests, as `cfd_tt_families`
families.

Source: 250 posts on the grid, screened by caption 2026-10-04: ~20-second
music clips running somebody else's TradingView indicator on a chart, plus
broker / "comment 101" promos. Only the OTT post states settings (OTT
period 4, percent 2, take each buy/sell at 1:2.5-1:3). The other four are
public TradingView scripts he tests by their own buy/sell signals, coded here
from the published formulas. UT Bot, HalfTrend and zero-lag MA already exist
(`tt_tradingdata2`). Map: `results/tiktok/yoforex3_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

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


def _emit_signals(bars, ctx, p, sig, stop_line):
    """Entries on `sig` (+1/-1), stop at `stop_line` (floored at 0.2 ATR,
    or `stop_atr` x ATR when the line is missing)."""
    b = F.base(bars, ctx)
    c = b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    out = {}
    for i in numpy.flatnonzero(sig).tolist():
        A = a[i]
        if not (A > 0) or mins[i] > last:
            continue
        side = int(sig[i])
        lvl = stop_line[i] if stop_line is not None else NAN
        dist = F.stop_dist(side, c[i], lvl, A) if fin(lvl) and side * (c[i] - lvl) > 0 else p.get("stop_atr", 1.5) * A
        if dist > 0 and fin(dist):
            out[i] = (side, dist, None)
    return out


# --------------------------------------------------------------------------- #
# OTT -- Optimized Trend Tracker (Anil Ozeksi)
# --------------------------------------------------------------------------- #

def _ott(bars, ctx, n, pct):
    def build():
        c = F.base(bars, ctx)["c"]
        m = len(c)
        d = numpy.diff(c, prepend=c[0])
        ud = numpy.convolve(numpy.maximum(d, 0), numpy.ones(9))[:m]
        dd = numpy.convolve(numpy.maximum(-d, 0), numpy.ones(9))[:m]
        cmo = numpy.where(ud + dd > 0, (ud - dd) / numpy.where(ud + dd > 0, ud + dd, 1), 0.0)
        alpha = 2.0 / (n + 1.0)
        mav = numpy.empty(m)
        ls = numpy.empty(m)
        ss = numpy.empty(m)
        ott = numpy.empty(m)
        acc = 0.0
        dr = 1
        for i in range(m):
            k = alpha * abs(cmo[i])
            acc = k * c[i] + (1 - k) * acc if i else c[0]
            mav[i] = acc
            fark = acc * pct * 0.01
            lo, hi = acc - fark, acc + fark
            if i:
                lo = max(lo, ls[i - 1]) if acc > ls[i - 1] else lo
                hi = min(hi, ss[i - 1]) if acc < ss[i - 1] else hi
                if dr == -1 and acc > ss[i - 1]:
                    dr = 1
                elif dr == 1 and acc < ls[i - 1]:
                    dr = -1
            ls[i], ss[i] = lo, hi
            mt = lo if dr == 1 else hi
            ott[i] = mt * (200 + pct) / 200 if acc > mt else mt * (200 - pct) / 200
        ref = numpy.concatenate(([NAN, NAN], ott[:-2]))        # OTT[2], the plotted line
        sig = numpy.zeros(m, dtype=numpy.int8)
        warm = 30
        for i in range(max(warm, 3), m):
            if mav[i] > ref[i] and mav[i - 1] <= ref[i - 1]:
                sig[i] = 1
            elif mav[i] < ref[i] and mav[i - 1] >= ref[i - 1]:
                sig[i] = -1
        return {"sig": sig, "line": ref}
    return F.cached(ctx, ("yf_ott", n, pct), build)


def ott_signal(bars, ctx, p):
    """"Master Optimized Trend Tracker": TradingView's OTT with OTT period 4
    and OTT percent 2 (his settings); buy when the variable MA crosses above
    the OTT line, sell below; stop at the OTT line (floored); his 1:2.5-1:3."""
    o = _ott(bars, ctx, p["len"], p["pct"])
    return _emit_signals(bars, ctx, p, o["sig"], o["line"])


def ott_exit(index, bars, ctx, params, side):
    return int(_ott(bars, ctx, params["len"], params["pct"])["sig"][index]) == -side


# --------------------------------------------------------------------------- #
# Range Filter Buy and Sell (DonovanWall / guikroth 5m version)
# --------------------------------------------------------------------------- #

def _range_filter(bars, ctx, per, mult):
    def build():
        c = F.base(bars, ctx)["c"]
        m = len(c)
        ar = numpy.abs(numpy.diff(c, prepend=c[0]))
        rng = _ema_arr(_ema_arr(ar, per), per * 2 - 1) * mult
        filt = numpy.full(m, NAN)
        sig = numpy.zeros(m, dtype=numpy.int8)
        f = c[0]
        up = dn = 0
        cond = 0
        for i in range(1, m):
            r = rng[i]
            if not fin(r):
                filt[i] = f
                continue
            prev = f
            f = max(prev, c[i] - r) if c[i] > prev else min(prev, c[i] + r)
            if f > prev:
                up, dn = up + 1, 0
            elif f < prev:
                up, dn = 0, dn + 1
            filt[i] = f
            longc = c[i] > f and up > 0 and c[i] != c[i - 1]
            shortc = c[i] < f and dn > 0 and c[i] != c[i - 1]
            new = 1 if longc else -1 if shortc else cond
            if longc and cond == -1:
                sig[i] = 1
            elif shortc and cond == 1:
                sig[i] = -1
            cond = new
        return {"sig": sig, "line": filt}
    return F.cached(ctx, ("yf_rf", per, mult), build)


def range_filter(bars, ctx, p):
    """"Testing Range Filter Buy Sell Indicator on 5 Min Chart": the Range
    Filter (sampling period `per`, range multiplier `mult`; the 5m script's
    defaults 100 / 3); buy on the first close above a rising filter after a
    sell state, sell mirror; stop at the filter (floored)."""
    o = _range_filter(bars, ctx, p["per"], p["mult"])
    return _emit_signals(bars, ctx, p, o["sig"], o["line"])


def range_filter_exit(index, bars, ctx, params, side):
    return int(_range_filter(bars, ctx, params["per"], params["mult"])["sig"][index]) == -side


# --------------------------------------------------------------------------- #
# AlphaTrend (KivancOzbilgic)
# --------------------------------------------------------------------------- #

def _alphatrend(bars, ctx, coeff, use_rsi, n=14):
    def build():
        b = F.base(bars, ctx)
        h, l, c, v = b["h"], b["l"], b["c"], b["v"]
        m = len(c)
        prev = numpy.concatenate(([c[0]], c[:-1]))
        tr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
        atr = F.sma(tr, n)
        if use_rsi:
            osc = F.rsi(bars, ctx, n)
        else:
            tp = (h + l + c) / 3.0
            dtp = numpy.diff(tp, prepend=tp[0])
            pos = numpy.convolve(numpy.where(dtp > 0, tp * v, 0.0), numpy.ones(n))[:m]
            neg = numpy.convolve(numpy.where(dtp < 0, tp * v, 0.0), numpy.ones(n))[:m]
            osc = numpy.where(neg > 0, 100 - 100 / (1 + pos / numpy.where(neg > 0, neg, 1)), 100.0)
            osc[:n] = NAN
        at = numpy.full(m, NAN)
        acc = 0.0
        for i in range(m):
            if not (fin(atr[i]) and fin(osc[i])):
                continue
            upt, dnt = l[i] - atr[i] * coeff, h[i] + atr[i] * coeff
            if osc[i] >= 50:
                acc = acc if (acc and upt < acc) else upt
            else:
                acc = acc if (acc and dnt > acc) else dnt
            at[i] = acc
        ref = numpy.concatenate(([NAN, NAN], at[:-2]))
        sig = numpy.zeros(m, dtype=numpy.int8)
        last = 0
        for i in range(1, m):
            if not (fin(at[i]) and fin(ref[i]) and fin(at[i - 1]) and fin(ref[i - 1])):
                continue
            # the script's filtered signals alternate buy / sell
            if at[i] > ref[i] and at[i - 1] <= ref[i - 1] and last != 1:
                sig[i] = last = 1
            elif at[i] < ref[i] and at[i - 1] >= ref[i - 1] and last != -1:
                sig[i] = last = -1
        return {"sig": sig, "line": ref}
    return F.cached(ctx, ("yf_at", coeff, use_rsi), build)


def alphatrend(bars, ctx, p):
    """"AlphaTrend Indicator Test": AlphaTrend (ATR 14 x `coeff` off the
    bar's low/high, ratcheting while MFI(14) >= 50 -- `rsi` 1 = the script's
    no-volume-data option, RSI(14)); buy when AlphaTrend crosses above its
    2-bar lag, sell below (alternating); stop at the lagged line (floored)."""
    o = _alphatrend(bars, ctx, p["coeff"], p["rsi"])
    return _emit_signals(bars, ctx, p, o["sig"], o["line"])


def alphatrend_exit(index, bars, ctx, params, side):
    return int(_alphatrend(bars, ctx, params["coeff"], params["rsi"])["sig"][index]) == -side


# --------------------------------------------------------------------------- #
# QQE MOD (Mihkel00)
# --------------------------------------------------------------------------- #

def _qqe_line(c, rsi_n, sf, qqe):
    r = numpy.asarray(c, dtype=float)
    rsima = _ema_arr(r, sf)
    wil = rsi_n * 2 - 1
    atr_r = numpy.abs(numpy.diff(rsima, prepend=NAN))
    dar = _ema_arr(_ema_arr(atr_r, wil), wil) * qqe
    m = len(r)
    lb = numpy.zeros(m)
    sb = numpy.zeros(m)
    tl = numpy.full(m, NAN)
    t = 1
    for i in range(1, m):
        x, x1 = rsima[i], rsima[i - 1]
        if not (fin(x) and fin(x1) and fin(dar[i])):
            continue
        nl, ns = x - dar[i], x + dar[i]
        lb[i] = max(lb[i - 1], nl) if (x1 > lb[i - 1] and x > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (x1 < sb[i - 1] and x < sb[i - 1]) and sb[i - 1] else ns
        if x > sb[i - 1] and x1 <= sb[i - 1]:
            t = 1
        elif x < lb[i - 1] and x1 >= lb[i - 1]:
            t = -1
        tl[i] = lb[i] if t == 1 else sb[i]
    return rsima, tl


def _qqe_mod(bars, ctx, thr):
    def build():
        r = F.rsi(bars, ctx, 6)
        rsima, tl = _qqe_line(r, 6, 5, 3.0)
        rsima2, _ = _qqe_line(r, 6, 5, 1.61)
        x = tl - 50
        basis = F.sma(numpy.nan_to_num(x, nan=0.0), 50)
        from numpy.lib.stride_tricks import sliding_window_view
        dev = numpy.full(len(x), NAN)
        if len(x) >= 50:
            dev[49:] = sliding_window_view(numpy.nan_to_num(x, nan=0.0), 50).std(axis=1) * 0.35
        up, lo = basis + dev, basis - dev
        blue = (rsima2 - 50 > thr) & (rsima - 50 > up)
        red = (rsima2 - 50 < -thr) & (rsima - 50 < lo)
        col = numpy.where(blue, 1, numpy.where(red, -1, 0)).astype(numpy.int8)
        sig = numpy.zeros(len(col), dtype=numpy.int8)
        sig[1:] = numpy.where((col[1:] != 0) & (col[1:] != col[:-1]), col[1:], 0)
        sig[:120] = 0
        return {"sig": sig, "col": col}
    return F.cached(ctx, ("yf_qqe", thr), build)


def qqe_mod(bars, ctx, p):
    """"Testing Smart QQE Mod Indicator": QQE MOD (RSI 6, smoothing 5, QQE
    factors 3 and 1.61, Bollinger 50 x 0.35 on the slow line); the first
    blue histogram bar (both QQEs above 50 past the threshold `thr` and the
    band) -> buy, first red -> sell. Stop `stop_atr` x ATR."""
    o = _qqe_mod(bars, ctx, p["thr"])
    return _emit_signals(bars, ctx, p, o["sig"], None)


def qqe_exit(index, bars, ctx, params, side):
    return int(_qqe_mod(bars, ctx, params["thr"])["col"][index]) == -side


# --------------------------------------------------------------------------- #
# Double Supertrend with ADX
# --------------------------------------------------------------------------- #

def double_st_adx(bars, ctx, p):
    """"Double Supertrend with ADX -- Entry Logic Tested": a fast
    SuperTrend(10, `fast`) and a slow SuperTrend(10, 3); buy on the bar both
    point up after they did not, while ADX(14) >= `adx`; sell mirror. Stop at
    the slow line (floored)."""
    fast = F.supertrend(bars, ctx, 10, p["fast"])
    slow = F.supertrend(bars, ctx, 10, 3.0)
    ax = F.adx(bars, ctx, 14)["adx"]
    fd, sd = fast["dir"], slow["dir"]
    both = numpy.where((fd == 1) & (sd == 1), 1, numpy.where((fd == -1) & (sd == -1), -1, 0))
    sig = numpy.zeros(len(both), dtype=numpy.int8)
    ok = numpy.nan_to_num(ax, nan=0.0) >= p["adx"]
    sig[1:] = numpy.where((both[1:] != 0) & (both[1:] != both[:-1]) & ok[1:], both[1:], 0)
    return _emit_signals(bars, ctx, p, sig, slow["line"])


def double_st_exit(index, bars, ctx, params, side):
    return int(F.supertrend(bars, ctx, 10, params["fast"])["dir"][index]) == -side


SPECS = {
    "yf_ott_signal": (ott_signal, lambda g: {
        "len": (2, 4), "pct": (1.4, 2.0), "exit_mode": ("rr_2.5", "rr_3", "signal"),
        "last_entry_minute": g["last"]}, False, ott_exit),
    "yf_range_filter": (range_filter, lambda g: {
        "per": (50, 100), "mult": (2.0, 3.0), "exit_mode": ("signal", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, range_filter_exit),
    "yf_alphatrend": (alphatrend, lambda g: {
        "coeff": (1.0, 1.5), "rsi": (0, 1), "exit_mode": ("signal", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, alphatrend_exit),
    "yf_qqe_mod": (qqe_mod, lambda g: {
        "thr": (3.0, 6.0), "stop_atr": (1.5, 2.5), "exit_mode": ("signal", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, qqe_exit),
    "yf_double_st_adx": (double_st_adx, lambda g: {
        "fast": (1.0, 2.0), "adx": (20, 25), "exit_mode": ("signal", "rr_2", "rr_1.5"),
        "last_entry_minute": g["last"]}, False, double_st_exit),
}

CATEGORICAL = ()
