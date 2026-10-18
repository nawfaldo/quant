"""MoneyballAustin's TikTok (@moneyballaustin) rule, as a `cfd_tt_families`
family.

Source: 278 posts on the grid, screened by caption 2026-10-04 (lifestyle,
psychology, prop-firm talk, discretionary supply & demand with COT and
fundamentals); 3 backtest posts read. One mechanical rule is stated. Map:
`results/tiktok/moneyballaustin_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _stoch_rsi(bars, ctx, n=14, k=3):
    def build():
        r = F.rsi(bars, ctx, 14)
        lo = F.rolling_min(r, n)
        hi = F.rolling_max(r, n)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            raw = numpy.where(hi > lo, 100.0 * (r - lo) / (hi - lo), 50.0)
        return F.sma(raw, k).tolist()
    return F.cached(ctx, ("mab_srsi", n, k), build)


def _bb(bars, ctx, n, dev):
    def build():
        c = F.base(bars, ctx)["c"]
        mid = numpy.full(len(c), NAN)
        sd = numpy.full(len(c), NAN)
        if len(c) >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(c, n)
            mid[n - 1:], sd[n - 1:] = w.mean(axis=1), w.std(axis=1)
        return (mid + dev * sd).tolist(), (mid - dev * sd).tolist()
    return F.cached(ctx, ("mab_bb", n, dev), build)


def bb_stochrsi(bars, ctx, p):
    """"BEST forex trading strategy backtest: Bollinger Bands + Stochastic
    RSI" (his 54-trade test: 21 wins / 33 losses at 2:1, +9%). A WHOLE
    candle below the lower Bollinger Band (its high under the band) with
    Stochastic RSI oversold (< `os`) -> buy; stop below that candle's low;
    2:1. Mirror short (whole candle above the upper band, Stoch RSI >
    100 - `os`)."""
    b = F.base(bars, ctx)
    h, l, c = b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    up, lo = _bb(bars, ctx, 20, p["dev"])
    s = _stoch_rsi(bars, ctx)
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(lo[i]) and fin(s[i])):
            continue
        if b["L"]["minute"][i] > p.get("last_entry_minute", 10 ** 6):
            continue
        if h[i] < lo[i] and s[i] < p["os"]:
            out[i] = (1, F.stop_dist(1, c[i], l[i], A), None)
        elif l[i] > up[i] and s[i] > 100 - p["os"]:
            out[i] = (-1, F.stop_dist(-1, c[i], h[i], A), None)
    return out


SPECS = {
    "mab_bb_stochrsi": (bb_stochrsi, lambda g: {
        "dev": (2.0, 2.5), "os": (10, 20),
        "exit_mode": ("rr_2", "rr_1", "time_12"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
