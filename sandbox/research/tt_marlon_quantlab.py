"""marlon_quantlab's TikTok (@marlon_quantlab) tested rules, as
`cfd_tt_families` families.

Source: 41 posts on the grid (each tests one idea on 6-7 years of NASDAQ
data: SMT, silver bullet, FVGs, golden cross, VWAP, fibs...), screened by
caption 2026-10-04; 10 read. Most of his tests come back negative (silver
bullet PF 0.80, best FVG setup -0.20R after costs, fading big moves worse
than random). Two rules he reports as working are coded. Map:
`results/tiktok/marlon_quantlab_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def open_candle_ema(bars, ctx, p):
    """"Can 5min predict the direction of the ENTIRE day?" / "Can one candle
    predict the price?" (NASDAQ, 7 years): the first 5-minute candle after
    the New York open closes above the 12 EMA -> long, below -> short; held
    for the day (+1,600%, 54.9% win, 33% max drawdown), or with a stop and a
    trail that only starts once in profit (946% at 1% risk, 19% drawdown).
    Stop `stop_atr` x ATR. On bars over 5 minutes the first bar is the
    candle."""
    b = F.base(bars, ctx)
    c = b["L"]["c"]
    e = F.ema(bars, ctx, 12).tolist()
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, mins, day = b["L"]["real"], b["L"]["minute"], b["L"]["day"]
    open_m = 9 * 60 + 30
    last = p.get("last_entry_minute", 10 ** 6)
    done = set()
    out = {}
    for i in range(len(c)):
        if day[i] in done or not (open_m <= real[i] < open_m + max(5, b["bar"])):
            continue
        if real[i] + b["bar"] < open_m + 5:
            continue                 # 1m bars: wait for the 5-minute close
        done.add(day[i])
        A = a[i]
        if not (A > 0 and fin(e[i])) or mins[i] > last or c[i] == e[i]:
            continue
        out[i] = (1 if c[i] > e[i] else -1, p["stop_atr"] * A, None)
    return out


def big_move_cont(bars, ctx, p):
    """"Does a big move tend to keep going or reverse?" / "I tested trading
    AGAINST the trend": a bar whose close-to-close move exceeds `big` x ATR
    (he found continuation past 1.5 ATR, stronger with size) and, with
    `vol` 1, volume above 2x its 20-bar average (+0.34R over the next 30
    minutes in-sample, weaker out of sample) -> trade WITH the move, out
    after ~30 minutes (`time_`)."""
    b = F.base(bars, ctx)
    c, v = b["c"], b["v"]
    a = F.atr(bars, ctx, 14)
    a_prev = numpy.concatenate(([NAN], a[:-1]))
    mv = numpy.concatenate(([0.0], numpy.diff(c)))
    va = numpy.concatenate(([NAN], F.sma(v, 20)[:-1]))
    big = numpy.abs(mv) > p["big"] * a_prev
    if p["vol"]:
        big &= v > 2.0 * va
    sig = numpy.where(big, numpy.sign(mv), 0).astype(numpy.int8)
    mins = b["L"]["minute"]
    last = p.get("last_entry_minute", 10 ** 6)
    out = {}
    for i in numpy.flatnonzero(sig).tolist():
        A = a[i]
        if A > 0 and mins[i] <= last:
            out[i] = (int(sig[i]), p["stop_atr"] * A, None)
    return out


SPECS = {
    "mql_open_candle_ema": (open_candle_ema, lambda g: {
        "stop_atr": (1.0, 2.0), "exit_mode": ("days_1", "trail_0.25", "rr_2"),
        "last_entry_minute": g["last"]}, True, None),
    "mql_big_move_cont": (big_move_cont, lambda g: {
        "big": (1.5, 2.5), "vol": (1, 0), "stop_atr": (1.0, 1.5),
        "exit_mode": ("time_6", "time_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
