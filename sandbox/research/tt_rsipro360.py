"""RSI Pro's TikTok (@rsipro.360) strategies, as `cfd_tt_families` families.

Source: all 17 posts on the grid read 2026-10-04 (8-9 minute indicator-combo
lessons with explicit entry rules). His risk rule is nearly always the same:
stop just beyond the recent swing (`swing` bars, floored), take profit at
1:2 -- rr exits here. RSI divergence, RSI + Fibonacci golden zone and RSI +
order blocks are covered by existing families; the BigBeluga two-pole
oscillator is closed-source; the RSI + Alligator post had no transcript.
Map: `results/tiktok/rsipro360_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _ema_arr(x, n):
    return F._ema_arr(numpy.asarray(x, dtype=float), n)


def _rsi_arr(x, n):
    x = numpy.asarray(x, dtype=float)
    d = numpy.diff(x, prepend=x[0])
    up, dn = numpy.maximum(d, 0), numpy.maximum(-d, 0)
    out = numpy.full(len(x), NAN)
    if len(x) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(x)):
        au += (up[i] - au) / n
        ad += (dn[i] - ad) / n
        out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def _rsi(bars, ctx, n):
    return F.cached(ctx, ("rp_rsi", n), lambda: _rsi_arr(F.base(bars, ctx)["c"], n))


def _hist(bars, ctx, f, s, g=9):
    return F.macd(bars, ctx, f, s, g)["hist"]


def _swing_signals(bars, ctx, p, sig):
    """Entries on `sig` (+1/-1); stop beyond the `swing`-bar extreme
    (floored at 0.2 ATR)."""
    b = F.base(bars, ctx)
    c = b["L"]["c"]
    k = p.get("swing", 10)
    lo = F.cached(ctx, ("rp_lo", k), lambda: F.rolling_min(b["l"], k).tolist())
    hi = F.cached(ctx, ("rp_hi", k), lambda: F.rolling_max(b["h"], k).tolist())
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    out = {}
    for i in numpy.flatnonzero(sig).tolist():
        A = a[i]
        if not (A > 0) or mins[i] > last:
            continue
        side = int(sig[i])
        lvl = lo[i] if side == 1 else hi[i]
        if not fin(lvl):
            continue
        dist = F.stop_dist(side, c[i], lvl, A)
        if dist > 0 and fin(dist):
            out[i] = (side, dist, None)
    return out


def _cross_up(a, b):
    a = numpy.asarray(a, dtype=float)
    b = numpy.broadcast_to(numpy.asarray(b, dtype=float), a.shape)
    out = numpy.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def _cross_dn(a, b):
    a = numpy.asarray(a, dtype=float)
    b = numpy.broadcast_to(numpy.asarray(b, dtype=float), a.shape)
    out = numpy.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return out


def _recent(ev, win):
    """True at i when `ev` fired within the last `win` bars (inclusive)."""
    ev = numpy.asarray(ev, dtype=bool)
    idx = numpy.where(ev, numpy.arange(len(ev)), -10 ** 9)
    last = numpy.maximum.accumulate(idx)
    return numpy.arange(len(ev)) - last <= win


def _sig(long_c, short_c):
    return numpy.where(long_c, 1, numpy.where(short_c, -1, 0)).astype(numpy.int8)


# --------------------------------------------------------------------------- #

def triple_macd(bars, ctx, p):
    """"Triple MACD Scalping" (1m): bottom MACD(30, 150) histogram above
    zero = uptrend; middle MACD(12, 20) prints a weakening bar (smaller than
    the one before); top MACD(4, 7) prints its FIRST bar below zero, and the
    next bar is larger -> long at the next candle. Mirror."""
    slow = _hist(bars, ctx, 30, 150)
    mid = _hist(bars, ctx, 12, 20)
    top = _hist(bars, ctx, 4, 7)
    m = len(slow)
    L = numpy.zeros(m, dtype=bool)
    S = numpy.zeros(m, dtype=bool)
    i = numpy.arange(2, m)
    wk = numpy.abs(mid[i - 1]) < numpy.abs(mid[i - 2])          # weakening at the first opposite bar
    first_dn = (top[i - 1] < 0) & (top[i - 2] >= 0)
    first_up = (top[i - 1] > 0) & (top[i - 2] <= 0)
    grow_dn = (top[i] < top[i - 1])                            # second bar larger (further below 0)
    grow_up = (top[i] > top[i - 1])
    L[i] = (slow[i] > 0) & wk & first_dn & grow_dn
    S[i] = (slow[i] < 0) & wk & first_up & grow_up
    return _swing_signals(bars, ctx, p, _sig(L, S))


def rsi_macd_ema(bars, ctx, p):
    """"RSI + MACD + EMA scalping": EMA9 crosses above EMA50, RSI(14) rises
    back above 30, MACD(12, 26, 9) crosses above its signal with the
    histogram above zero -- all within `win` bars -- then a candle CLOSES
    above both EMAs -> long. Mirror (RSI back below 70)."""
    b = F.base(bars, ctx)
    c = b["c"]
    e9, e50 = F.ema(bars, ctx, 9), F.ema(bars, ctx, 50)
    r = _rsi(bars, ctx, 14)
    m = F.macd(bars, ctx, 12, 26, 9)
    w = p["win"]
    L = (_recent(_cross_up(e9, e50), w) & (e9 > e50) & _recent(_cross_up(r, 30), w) & (r > 30)
         & _recent(_cross_up(m["macd"], m["signal"]), w) & (m["hist"] > 0) & (c > numpy.fmax(e9, e50)))
    S = (_recent(_cross_dn(e9, e50), w) & (e9 < e50) & _recent(_cross_dn(r, 70), w) & (r < 70)
         & _recent(_cross_dn(m["macd"], m["signal"]), w) & (m["hist"] < 0) & (c < numpy.fmin(e9, e50)))
    first = lambda x: x & ~numpy.concatenate(([False], x[:-1]))
    return _swing_signals(bars, ctx, p, _sig(first(L), first(S)))


def double_rsi(bars, ctx, p):
    """"Double RSI scalping": slow RSI(21) above 50 = uptrend; the fast
    RSI(5) dips below 50 (the pullback) and then crosses back above the slow
    RSI -> long at the next open. Mirror."""
    fast, slow = _rsi(bars, ctx, 5), _rsi(bars, ctx, 21)
    w = p["win"]
    L = (slow > 50) & _cross_up(fast, slow) & _recent(fast < 50, w)
    S = (slow < 50) & _cross_dn(fast, slow) & _recent(fast > 50, w)
    return _swing_signals(bars, ctx, p, _sig(L, S))


def ribbon_rsi(bars, ctx, p):
    """"RSI + MA Ribbon 5-minute scalping": EMA34 of the high, close and low
    all above SMA55 (uptrend); price pulls back to the EMA34-of-close but
    does not close below the EMA34-of-low; RSI(14) between 50 and 70; a
    candle closes back above the EMA34-of-close -> long (his exit: RSI above
    70 -> `signal`). Mirror."""
    b = F.base(bars, ctx)
    c, h, l = b["c"], b["h"], b["l"]
    eh, ec, el = F.ema(bars, ctx, 34, "h"), F.ema(bars, ctx, 34, "c"), F.ema(bars, ctx, 34, "l")
    s55 = F.sma(c, 55)
    r = _rsi(bars, ctx, 14)
    up = (el > s55) & (ec > s55) & (eh > s55)
    dn = (el < s55) & (ec < s55) & (eh < s55)
    held_l = _recent(l <= ec, p["win"]) & ~_recent(c < el, p["win"])
    held_s = _recent(h >= ec, p["win"]) & ~_recent(c > eh, p["win"])
    L = up & held_l & _cross_up(c, ec) & (r > 50) & (r < 70)
    S = dn & held_s & _cross_dn(c, ec) & (r < 50) & (r > 30)
    return _swing_signals(bars, ctx, p, _sig(L, S))


def ribbon_exit(index, bars, ctx, params, side):
    r = _rsi(bars, ctx, 14)[index]
    return (side == 1 and r > 70) or (side == -1 and r < 30)


def three_ema_rsi(bars, ctx, p):
    """"5-Minute RSI & EMA scalping": EMA25 > EMA50 > EMA100; a pullback
    touches the 25 or 50 EMA without closing below the 100; RSI(14) stays
    above 40 through it; RSI crosses back above 50 with price back at/above
    the 25 EMA -> long. His stop: below the 50 EMA (`swing` 0) or the swing;
    take profit 2x. Mirror (RSI below 60, back below 50)."""
    b = F.base(bars, ctx)
    c, h, l = b["c"], b["h"], b["l"]
    e25, e50, e100 = F.ema(bars, ctx, 25), F.ema(bars, ctx, 50), F.ema(bars, ctx, 100)
    r = _rsi(bars, ctx, 14)
    w = p["win"]
    up = (e25 > e50) & (e50 > e100)
    dn = (e25 < e50) & (e50 < e100)
    L = up & _recent(l <= e25, w) & ~_recent(c < e100, w) & ~_recent(r < 40, w) & _cross_up(r, 50) & (c >= e25)
    S = dn & _recent(h >= e25, w) & ~_recent(c > e100, w) & ~_recent(r > 60, w) & _cross_dn(r, 50) & (c <= e25)
    sig = _sig(L, S)
    if p["stop_ema"]:
        a = F.atr(bars, ctx, 14)
        mins = b["L"]["minute"]
        last = p.get("last_entry_minute", 10 ** 6)
        out = {}
        for i in numpy.flatnonzero(sig).tolist():
            side = int(sig[i])
            if not (a[i] > 0) or mins[i] > last or side * (c[i] - e50[i]) <= 0:
                continue
            out[i] = (side, F.stop_dist(side, c[i], e50[i], a[i]), None)
        return out
    return _swing_signals(bars, ctx, p, sig)


def rsi_double_macd(bars, ctx, p):
    """"RSI + Double MACD scalping" (1m): slow MACD(34, 144) histogram above
    zero (uptrend); fast MACD(13, 21) histogram below zero (the pullback);
    RSI(9) drops below 30 and crosses back above it -> long. Mirror (70)."""
    slow, fast = _hist(bars, ctx, 34, 144), _hist(bars, ctx, 13, 21)
    r = _rsi(bars, ctx, 9)
    L = (slow > 0) & (fast < 0) & _cross_up(r, 30)
    S = (slow < 0) & (fast > 0) & _cross_dn(r, 70)
    return _swing_signals(bars, ctx, p, _sig(L, S))


def _mfi(bars, ctx, n=14):
    def build():
        b = F.base(bars, ctx)
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        mf = tp * b["v"]
        d = numpy.diff(tp, prepend=tp[0])
        m = len(tp)
        pos = numpy.convolve(numpy.where(d > 0, mf, 0.0), numpy.ones(n))[:m]
        neg = numpy.convolve(numpy.where(d < 0, mf, 0.0), numpy.ones(n))[:m]
        out = numpy.where(neg > 0, 100 - 100 / (1 + pos / numpy.where(neg > 0, neg, 1)), 100.0)
        out[:n] = NAN
        return out
    return F.cached(ctx, ("rp_mfi", n), build)


def rsi_mfi_candle(bars, ctx, p):
    """"RSI + MFI": MFI(14) above 80, RSI(14) above 70, and a strong green
    candle (body >= 60% of its range, upper wick <= `wick` of the range) ->
    long. Mirror (MFI < 20, RSI < 30, strong red candle)."""
    b = F.base(bars, ctx)
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    rng = numpy.where(h > l, h - l, numpy.nan)
    body = numpy.abs(c - o) / rng
    uw = (h - numpy.maximum(o, c)) / rng
    lw = (numpy.minimum(o, c) - l) / rng
    mf, r = _mfi(bars, ctx), _rsi(bars, ctx, 14)
    L = (mf > 80) & (r > 70) & (c > o) & (body >= 0.6) & (uw <= p["wick"])
    S = (mf < 20) & (r < 30) & (c < o) & (body >= 0.6) & (lw <= p["wick"])
    return _swing_signals(bars, ctx, p, _sig(L, S))


def stoch_rsi_macd(bars, ctx, p):
    """"RSI + MACD + Stochastic": Stochastic(14, 3) %K and %D both in the
    oversold zone (< 20, within `win` bars), RSI(14) above 50, MACD crosses
    above its signal with the histogram rising -> long. Mirror."""
    k = F.stochastic(bars, ctx, 14, 3)
    d = F.sma(numpy.nan_to_num(k, nan=50.0), 3)
    r = _rsi(bars, ctx, 14)
    m = F.macd(bars, ctx, 12, 26, 9)
    hs = m["hist"]
    rising = numpy.zeros(len(hs), dtype=bool)
    rising[1:] = hs[1:] > hs[:-1]
    w = p["win"]
    L = _recent((k < 20) & (d < 20), w) & (r > 50) & _cross_up(m["macd"], m["signal"]) & rising
    S = _recent((k > 80) & (d > 80), w) & (r < 50) & _cross_dn(m["macd"], m["signal"]) & ~rising
    return _swing_signals(bars, ctx, p, _sig(L, S))


def _impulse(bars, ctx, n=34, sig=9):
    def build():
        b = F.base(bars, ctx)
        h, l, c = b["h"], b["l"], b["c"]

        def smma(x):
            out = numpy.full(len(x), NAN)
            acc = NAN
            for i, v in enumerate(x):
                acc = (x[:n].mean() if i == n - 1 else NAN) if not fin(acc) else (acc * (n - 1) + v) / n
                out[i] = acc
            return out
        src = (h + l + c) / 3.0
        e1 = _ema_arr(src, n)
        e2 = _ema_arr(e1, n)
        mi = e1 + (e1 - e2)
        hi, lo = smma(h), smma(l)
        md = numpy.where(mi > hi, mi - hi, numpy.where(mi < lo, mi - lo, 0.0))
        sb = F.sma(md, sig)
        return md, sb
    return F.cached(ctx, ("rp_impulse", n, sig), build)


def impulse_macd_rsi(bars, ctx, p):
    """"Impulse MACD + RSI" (LazyBear's Impulse MACD, 34 / 9): the impulse
    line crosses above its signal line while not flat (non-zero) and RSI(14)
    is above 50 -> long. Mirror."""
    md, sb = _impulse(bars, ctx)
    r = _rsi(bars, ctx, 14)
    L = _cross_up(md, sb) & (md != 0) & (r > 50)
    S = _cross_dn(md, sb) & (md != 0) & (r < 50)
    return _swing_signals(bars, ctx, p, _sig(L, S))


def st_rsi(bars, ctx, p):
    """"RSI + Supertrend": SuperTrend(10, 3) flips green with RSI(14) above
    50 (`rising` 1 = and rising) -> long at the close; mirror. 1h / 4h is his
    advice."""
    st = F.supertrend(bars, ctx, 10, 3.0)
    r = _rsi(bars, ctx, 14)
    ru = numpy.zeros(len(r), dtype=bool)
    ru[1:] = r[1:] > r[:-1]
    rs = ru if p["rising"] else numpy.ones(len(r), dtype=bool)
    fs = ~ru if p["rising"] else numpy.ones(len(r), dtype=bool)
    L = (st["flip"] == 1) & (r > 50) & rs
    S = (st["flip"] == -1) & (r < 50) & fs
    return _swing_signals(bars, ctx, p, _sig(L, S))


def vwap_rsi_break(bars, ctx, p):
    """"RSI + VWAP -- fake breakouts exposed": a strong candle (body >= 60%
    of its range) closes from below to above the session VWAP while RSI(14)
    crosses above 50 (same bar or the one before) -> long at the close; stop
    below the recent swing low, 1:2. Mirror."""
    b = F.base(bars, ctx)
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    vw = F.vwap(bars, ctx)
    vw = vw["vwap"] if isinstance(vw, dict) else vw
    r = _rsi(bars, ctx, 14)
    rng = numpy.where(h > l, h - l, numpy.nan)
    strong = numpy.abs(c - o) / rng >= 0.6
    L = _cross_up(c, vw) & (c > o) & strong & _recent(_cross_up(r, 50), 1) & (r > 50)
    S = _cross_dn(c, vw) & (c < o) & strong & _recent(_cross_dn(r, 50), 1) & (r < 50)
    return _swing_signals(bars, ctx, p, _sig(L, S))


RR = ("rr_2", "rr_1", "rr_1.5")

SPECS = {
    "rp_triple_macd": (triple_macd, lambda g: {
        "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_rsi_macd_ema": (rsi_macd_ema, lambda g: {
        "win": (8, 15), "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_double_rsi": (double_rsi, lambda g: {
        "win": (5, 10), "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_ribbon_rsi": (ribbon_rsi, lambda g: {
        "win": (3, 6), "swing": (5, 10), "exit_mode": ("signal", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, ribbon_exit),
    "rp_three_ema_rsi": (three_ema_rsi, lambda g: {
        "win": (5, 10), "stop_ema": (1, 0), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_rsi_double_macd": (rsi_double_macd, lambda g: {
        "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_rsi_mfi_candle": (rsi_mfi_candle, lambda g: {
        "wick": (0.1, 0.2), "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_stoch_rsi_macd": (stoch_rsi_macd, lambda g: {
        "win": (3, 6), "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_impulse_macd_rsi": (impulse_macd_rsi, lambda g: {
        "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_st_rsi": (st_rsi, lambda g: {
        "rising": (1, 0), "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "rp_vwap_rsi_break": (vwap_rsi_break, lambda g: {
        "swing": (5, 10), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
