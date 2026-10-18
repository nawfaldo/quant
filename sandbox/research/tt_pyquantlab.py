"""PyQuantLab's TikTok (@pyquantlab) strategies, as `cfd_tt_families`
families.

Source: 212 posts on the grid (each a strategy from his 250-strategy
Backtrader / VectorBT library; codes and ebooks sold, but the articles on
pyquantlab.com carry the full code free), screened by caption 2026-10-04;
8 rules taken from the posts and their articles. Thresholds written for
daily crypto bars (5% stddev, 1% ROC, 2% trail) are rescaled to relative
ones here. His exits are ATR(7) x 3 trailing stops -> `trail_` exits with a
`stop_atr` initial stop. Map: `results/tiktok/pyquantlab_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _signals(bars, ctx, p, sig, mult=None):
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    k = p.get("stop_atr", 3.0) if mult is None else mult
    out = {}
    for i in numpy.flatnonzero(sig).tolist():
        A = a[i]
        if A > 0 and mins[i] <= last:
            out[i] = (int(sig[i]), k * A, None)
    return out


def _sig(L, S):
    return numpy.where(L, 1, numpy.where(S, -1, 0)).astype(numpy.int8)


def _std(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = numpy.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).std(axis=1, ddof=1)
    return out


def _bb(bars, ctx, n=20, dev=2.0):
    def build():
        c = F.base(bars, ctx)["c"]
        mid = F.sma(c, n)
        sd = _std(c, n)
        return mid, mid + dev * sd, mid - dev * sd
    return F.cached(ctx, ("pql_bb", n, dev), build)


# --------------------------------------------------------------------------- #

def ao_twin_msb(bars, ctx, p):
    """"AO Twin Peaks + Market Structure Break + ATR trailing stop": AO =
    SMA(median, 7) - SMA(median, 30); two AO troughs where price makes a
    lower low but AO a higher low ARM a long; enter when the close breaks
    above the last confirmed swing high (`swing`-bar pivots, confirmed
    `swing` bars later). Mirror. Exit: ATR(7) x 3 trail."""
    b = F.base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    med = (h + l) / 2.0
    ao = F.sma(med, 7) - F.sma(med, 30)
    pv = F.pivots(bars, ctx, p["swing"])
    sh, sl = pv["h"], pv["l"]
    m = len(c)
    sig = numpy.zeros(m, dtype=numpy.int8)
    pk, tr = [], []
    bull = bear = False
    for i in range(2, m):
        a0, a1, a2 = ao[i - 2], ao[i - 1], ao[i]
        if fin(a0) and fin(a1) and fin(a2):
            if a0 < a1 > a2:
                pk = (pk + [(a1, h[i - 1])])[-2:]
            if a0 > a1 < a2:
                tr = (tr + [(a1, l[i - 1])])[-2:]
        if len(pk) == 2 and pk[1][1] > pk[0][1] and pk[1][0] < pk[0][0]:
            bear, bull, pk = True, False, []
        if len(tr) == 2 and tr[1][1] < tr[0][1] and tr[1][0] > tr[0][0]:
            bull, bear, tr = True, False, []
        if bull and fin(sh[i - 1]) and c[i] > sh[i - 1] >= c[i - 1]:
            sig[i], bull = 1, False
        elif bear and fin(sl[i - 1]) and c[i] < sl[i - 1] <= c[i - 1]:
            sig[i], bear = -1, False
    return _signals(bars, ctx, p, sig)


def squeeze_atr_wake(bars, ctx, p):
    """"Beating the False Breakout -- why directional filters matter": quiet
    = stddev(close, 30) at or below its lowest of the previous 30 bars;
    wake-up = ATR(7) crosses above its 30-bar SMA; direction from `dirf`
    0 = ROC(30) sign (his reference), 1 = close vs SMA(50) (his winner).
    Exit: ATR(14) x 3 trail."""
    b = F.base(bars, ctx)
    c = b["c"]
    sd = _std(c, 30)
    lo_prev = numpy.concatenate(([NAN], F.rolling_min(numpy.nan_to_num(sd, nan=numpy.inf), 30)[:-1]))
    quiet = sd <= lo_prev
    af = F.atr(bars, ctx, 7)
    asl = F.sma(numpy.nan_to_num(af, nan=0.0), 30)
    wake = numpy.zeros(len(c), dtype=bool)
    wake[1:] = (af[1:] > asl[1:]) & (af[:-1] <= asl[:-1])
    if p["dirf"]:
        d = numpy.sign(c - F.sma(c, 50))
    else:
        d = numpy.sign(c - numpy.concatenate((numpy.full(30, NAN), c[:-30])))
    ok = quiet & wake
    return _signals(bars, ctx, p, _sig(ok & (d > 0), ok & (d < 0)))


def intraday_vol_break(bars, ctx, p):
    """"An Intraday Volatility Breakout Strategy" (5m): a channel of the
    last `hours` hours' high/low; compression = ATR(14) below 0.8 x its
    12-bar SMA on the PREVIOUS bar; then a close beyond the previous bar's
    channel -> trade the break. Exit: ATR trail."""
    b = F.base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    n = max(2, int(round(p["hours"] * 60 / b["bar"])))
    up = numpy.concatenate(([NAN], F.rolling_max(h, n)[:-1]))
    dn = numpy.concatenate(([NAN], F.rolling_min(l, n)[:-1]))
    prev = numpy.concatenate(([c[0]], c[:-1]))
    trr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
    at = F.sma(trr, 14)
    comp = at < 0.8 * F.sma(numpy.nan_to_num(at, nan=0.0), 12)
    cprev = numpy.concatenate(([False], comp[:-1]))
    return _signals(bars, ctx, p, _sig(cprev & (c > up), cprev & (c < dn)))


def _rsi(bars, ctx, n=14):
    return F.rsi(bars, ctx, n)


def bb_mr_adx_rsi(bars, ctx, p):
    """"Enhancing Bollinger Bands mean-reversion with ADX and RSI": no new
    entries while ADX(14) >= `adx`; close below the lower BB(20, 2) with
    RSI(14) < 30 -> long; above the upper band with RSI > 70 -> short. Exit
    at the middle band (`signal`)."""
    c = F.base(bars, ctx)["c"]
    mid, up, lo = _bb(bars, ctx)
    r = _rsi(bars, ctx)
    ax = numpy.nan_to_num(F.adx(bars, ctx, 14)["adx"], nan=100.0)
    calm = ax < p["adx"]
    return _signals(bars, ctx, p, _sig(calm & (c < lo) & (r < 30), calm & (c > up) & (r > 70)))


def bb_mr_exit(index, bars, ctx, params, side):
    c = F.base(bars, ctx)["c"][index]
    mid = _bb(bars, ctx)[0][index]
    return (side == 1 and c > mid) or (side == -1 and c < mid)


def rsi_mr_adx(bars, ctx, p):
    """"Enhancing RSI mean-reversion with ATR and ADX": RSI(14) crosses
    below 30 -> long, above 70 -> short, skipped while the market is
    trending AND volatile (ADX >= `adx` and ATR/close above its 100-bar
    median). Exit at the opposite RSI extreme (`signal`)."""
    b = F.base(bars, ctx)
    c = b["c"]
    r = _rsi(bars, ctx)
    ax = numpy.nan_to_num(F.adx(bars, ctx, 14)["adx"], nan=100.0)
    ar = F.atr(bars, ctx, 14) / c

    def med():
        from numpy.lib.stride_tricks import sliding_window_view
        out = numpy.full(len(ar), NAN)
        if len(ar) >= 100:
            out[99:] = numpy.nanmedian(sliding_window_view(ar, 100), axis=1)
        return out
    md = F.cached(ctx, "pql_atr_med", med)
    skip = (ax >= p["adx"]) & (ar > md)
    L = numpy.zeros(len(c), dtype=bool)
    S = numpy.zeros(len(c), dtype=bool)
    L[1:] = (r[1:] < 30) & (r[:-1] >= 30)
    S[1:] = (r[1:] > 70) & (r[:-1] <= 70)
    return _signals(bars, ctx, p, _sig(L & ~skip, S & ~skip))


def rsi_mr_exit(index, bars, ctx, params, side):
    r = _rsi(bars, ctx)[index]
    return (side == 1 and r > 70) or (side == -1 and r < 30)


def momentum_ignition(bars, ctx, p):
    """"Momentum Ignition -- breakouts from consolidation": consolidating =
    stddev(close, 30)/close below `cons` x its 500-bar median (his 5% of
    price is a daily-bar number); ROC(7) beyond its 30-bar mean +- 1 sd;
    close on the same side of SMA(30) -> trade it. Exit: ATR(7) x 3 trail."""
    b = F.base(bars, ctx)
    c = b["c"]
    rel = _std(c, 30) / c

    def med():
        from numpy.lib.stride_tricks import sliding_window_view
        out = numpy.full(len(rel), NAN)
        if len(rel) >= 500:
            out[499:] = numpy.nanmedian(sliding_window_view(rel, 500)[:, ::5], axis=1)
        return out
    md = F.cached(ctx, "pql_rel_med", med)
    roc = numpy.concatenate((numpy.full(7, NAN), c[7:] / c[:-7] - 1))
    rm = F.sma(numpy.nan_to_num(roc, nan=0.0), 30)
    rs = _std(numpy.nan_to_num(roc, nan=0.0), 30)
    tr = F.sma(c, 30)
    cons = rel < p["cons"] * md
    return _signals(bars, ctx, p, _sig(cons & (roc > rm + rs) & (c > tr), cons & (roc < rm - rs) & (c < tr)))


def rvol_spike(bars, ctx, p):
    """"Relative Volume Spike Momentum with trailing stops": on the
    PREVIOUS bar, volume >= `rvol` x its 7-bar SMA and ROC(7) above +k
    (below -k) -> long (short); k = `roc_atr` x ATR(14)/close (his 1% is a
    daily-crypto number). Exit: his 2% trail -> `trail_`."""
    b = F.base(bars, ctx)
    c, v = b["c"], b["v"]
    vs = F.sma(v, 7)
    rv = v / numpy.where(vs > 0, vs, numpy.nan)
    roc = numpy.concatenate((numpy.full(7, NAN), c[7:] / c[:-7] - 1))
    k = p["roc_atr"] * F.atr(bars, ctx, 14) / c
    L = (rv >= p["rvol"]) & (roc > k)
    S = (rv >= p["rvol"]) & (roc < -k)
    Lp = numpy.concatenate(([False], L[:-1]))
    Sp = numpy.concatenate(([False], S[:-1]))
    first = lambda x: x & ~numpy.concatenate(([False], x[:-1]))
    return _signals(bars, ctx, p, _sig(first(Lp), first(Sp)))


def _slope(bars, ctx):
    def build():
        s = F.sma(F.base(bars, ctx)["c"], 20)
        return numpy.concatenate((numpy.full(5, NAN), s[5:] - s[:-5]))
    return F.cached(ctx, "pql_slope", build)


def sma_slope_flip(bars, ctx, p):
    """"SMA slope flip": long when the SMA(20) slope over 5 bars turns
    positive, exit when it turns negative (`signal`), 1% trailing stop
    (`trail_`). `short` 1 adds the mirror short."""
    sl = numpy.nan_to_num(_slope(bars, ctx), nan=0.0)
    L = numpy.zeros(len(sl), dtype=bool)
    S = numpy.zeros(len(sl), dtype=bool)
    L[1:] = (sl[1:] > 0) & (sl[:-1] <= 0)
    if p["short"]:
        S[1:] = (sl[1:] < 0) & (sl[:-1] >= 0)
    return _signals(bars, ctx, p, _sig(L, S))


def slope_exit(index, bars, ctx, params, side):
    s = _slope(bars, ctx)[index]
    return fin(s) and side * s < 0


TRAIL = ("trail_0.25", "trail_0.5", "rr_2")

SPECS = {
    "pql_ao_twin_msb": (ao_twin_msb, lambda g: {
        "swing": (5, 7), "stop_atr": (2.0, 3.0), "exit_mode": TRAIL,
        "last_entry_minute": g["last"]}, False, None),
    "pql_squeeze_atr_wake": (squeeze_atr_wake, lambda g: {
        "dirf": (1, 0), "stop_atr": (2.0, 3.0), "exit_mode": TRAIL,
        "last_entry_minute": g["last"]}, False, None),
    "pql_intraday_vol_break": (intraday_vol_break, lambda g: {
        "hours": (2, 3), "stop_atr": (2.0, 3.0), "exit_mode": TRAIL,
        "last_entry_minute": g["last"]}, False, None),
    "pql_bb_mr_adx_rsi": (bb_mr_adx_rsi, lambda g: {
        "adx": (20, 25), "stop_atr": (2.0, 3.0), "exit_mode": ("signal", "rr_1", "time_12"),
        "last_entry_minute": g["last"]}, False, bb_mr_exit),
    "pql_rsi_mr_adx": (rsi_mr_adx, lambda g: {
        "adx": (20, 25), "stop_atr": (2.0, 3.0), "exit_mode": ("signal", "rr_1", "time_12"),
        "last_entry_minute": g["last"]}, False, rsi_mr_exit),
    "pql_momentum_ignition": (momentum_ignition, lambda g: {
        "cons": (0.75, 1.0), "stop_atr": (2.0, 3.0), "exit_mode": TRAIL,
        "last_entry_minute": g["last"]}, False, None),
    "pql_rvol_spike": (rvol_spike, lambda g: {
        "rvol": (2.0, 3.0), "roc_atr": (1.0, 2.0), "stop_atr": (2.0,), "exit_mode": TRAIL,
        "last_entry_minute": g["last"]}, False, None),
    "pql_sma_slope_flip": (sma_slope_flip, lambda g: {
        "short": (0, 1), "stop_atr": (1.5, 3.0), "exit_mode": ("signal", "trail_0.25", "trail_0.5"),
        "last_entry_minute": g["last"]}, False, slope_exit),
}

CATEGORICAL = ()
