"""Jade Tan's TikTok (@jadetantrade) rule, as a `cfd_tt_families` family.

Source: 284 posts on the grid (Bahasa; mostly the team's paid "Altrade"
auto-trading product and a $500 challenge diary), screened by caption
2026-10-04; 3 read. Map: `results/tiktok/jadetantrade_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _wma(x, n):
    n = max(1, int(n))
    out = numpy.full(len(x), NAN)
    if len(x) >= n:
        w = numpy.arange(1, n + 1, dtype=float)
        from numpy.lib.stride_tricks import sliding_window_view
        out[n - 1:] = sliding_window_view(numpy.nan_to_num(x, nan=0.0), n) @ w / w.sum()
        bad = numpy.isnan(x)
        if bad.any():
            cb = numpy.convolve(bad.astype(float), numpy.ones(n))[:len(x)]
            out[cb > 0] = NAN
    return out


def _hull(bars, ctx, n, thma):
    def build():
        c = F.base(bars, ctx)["c"]
        if thma:     # THMA: WMA(3*WMA(n/3) - WMA(n/2) - WMA(n), n)
            raw = 3 * _wma(c, n // 3) - _wma(c, n // 2) - _wma(c, n)
            h = _wma(raw, n)
        else:        # HMA: WMA(2*WMA(n/2) - WMA(n), sqrt(n))
            raw = 2 * _wma(c, n // 2) - _wma(c, n)
            h = _wma(raw, int(round(math.sqrt(n))))
        lag = numpy.concatenate(([NAN, NAN], h[:-2]))
        up = h > lag
        sig = numpy.zeros(len(c), dtype=numpy.int8)
        ok = numpy.isfinite(h) & numpy.isfinite(lag)
        okp = numpy.concatenate(([False], ok[:-1]))
        upp = numpy.concatenate(([False], up[:-1]))
        sig[ok & okp & up & ~upp] = 1
        sig[ok & okp & ~up & upp] = -1
        return {"sig": sig, "up": numpy.where(ok, numpy.where(up, 1, -1), 0)}
    return F.cached(ctx, ("jt_hull", n, thma), build)


def hull_suite(bars, ctx, p):
    """"Hull Suite Strategy by DashTrader" (BTCUSDT 1h, Hull Variation set
    to THMA, other settings default -- length 55): the Hull line against
    its 2-bar lag -- turning up -> long, down -> short (stop and reverse,
    `signal` exit). His backtest: +94.51% before commission, -23.01% with
    0.07% commission. Stop `stop_atr` x ATR."""
    o = _hull(bars, ctx, p["len"], p["thma"])
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    out = {}
    for i in numpy.flatnonzero(o["sig"]).tolist():
        A = a[i]
        if A > 0 and mins[i] <= last:
            out[i] = (int(o["sig"][i]), p["stop_atr"] * A, None)
    return out


def hull_exit(index, bars, ctx, params, side):
    return int(_hull(bars, ctx, params["len"], params["thma"])["up"][index]) == -side


SPECS = {
    "jt_hull_suite": (hull_suite, lambda g: {
        "len": (55, 80), "thma": (1, 0), "stop_atr": (2.0, 3.0),
        "exit_mode": ("signal", "rr_2", "trail_0.5"),
        "last_entry_minute": g["last"]}, False, hull_exit),
}

CATEGORICAL = ()
