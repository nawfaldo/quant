"""Quantitativo's Substack (quantitativo.com) single-market rules, as
`cfd_tt_families` families.

Source: the post archive (sitemap, ~50 posts) read 2026-10-03. Most posts are
cross-sectional stock books (stat-arb, network momentum, learning-to-rank)
that a one-symbol engine cannot express; the daily single-market rules are
built here. Map: `results/tiktok/quantitativo_strategy_notes.md`.

THE HOLDING PERIOD IS CUT TO ONE SESSION. Every rule here is a daily signal
held for days ("exit on the next open after a close above yesterday's
high"). This study's cells are flat by the session close, so the signal is
read on yesterday's completed session, the entry is the next session's first
bar, and the exits are the close (`days_1`) or their own exit rule firing
intraday (`signal`: a bar closing above the previous session's high).

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _daily(bars, ctx):
    """Per-session OHLC arrays (completed sessions) and the bar->session map."""
    d = F.days(bars, ctx)
    return d, d["d_open"], d["d_hi"], d["d_lo"], d["d_close"]


def _first_bar_events(bars, ctx, p, ok_long, ok_short=None):
    """Enter on the first bar of session j+1 where `ok_*[j]` is True (j is the
    completed session). Stop `stop` x the 14-bar ATR."""
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    d = F.days(bars, ctx)
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(first)):
        if not first[i] or not (a[i] > 0):
            continue
        if b["L"]["minute"][i] > p.get("last_entry_minute", 10 ** 6):
            continue
        j = idx[i] - 1
        if j < 0:
            continue
        if ok_long[j]:
            out[i] = (1, mult * a[i], None)
        elif ok_short is not None and ok_short[j]:
            out[i] = (-1, mult * a[i], None)
    return out


def _sma(x, n):
    return F.sma(numpy.asarray(x, dtype=float), n)


def above_prev_high_exit(index, bars, ctx, params, side):
    """Their exit: a close above yesterday's high (mirror for shorts)."""
    d = F.days(bars, ctx)
    c = F.base(bars, ctx)["c"][index]
    if side == 1:
        return fin(d["pdh"][index]) and c > d["pdh"][index]
    return fin(d["pdl"][index]) and c < d["pdl"][index]


def ibs_band(bars, ctx, p):
    """"A mean-reversion strategy with 2.11 Sharpe" (QQQ). Lower band = the
    10-day high minus 2.5 x the 25-day mean of (high - low); yesterday's
    close below the band AND its IBS ((c-l)/(h-l)) under 0.3 -> long. `trend`
    filter: `sma` 300 needs the close above the 300-day SMA (their regime /
    dynamic-stop versions); 0 = off. Their QQQ: Sharpe 1.83-2.25, 15-20%
    exposure."""
    d, o, h, l, c = _daily(bars, ctx)
    rng = h - l
    band = F.rolling_max(h, 10) - 2.5 * _sma(rng, 25)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        ibs = numpy.where(rng > 0, (c - l) / rng, 0.5)
        ok = (c < band) & (ibs < 0.3)
        if p["sma"]:
            ok &= c > _sma(c, p["sma"])
    return _first_bar_events(bars, ctx, p, ok)


def rsi2_dip(bars, ctx, p):
    """"The Holy Grail still works" / "Trading the mean-reversion curve":
    yesterday's 2-period RSI (daily closes) below `lo` (5 ... 30 in their
    portfolio of six) with the close above its 200-day SMA -> long. Their
    stocks book: 64% win, +0.40% a trade."""
    d, o, h, l, c = _daily(bars, ctx)
    diff = numpy.diff(c, prepend=c[0])
    up, dn = numpy.maximum(diff, 0), numpy.maximum(-diff, 0)
    au, ad = F._ema_arr(up, 3), F._ema_arr(dn, 3)        # Wilder(2) = EMA(3)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        r = numpy.where(ad > 0, 100 - 100 / (1 + au / ad), 100.0)
        ok = (r < p["lo"]) & (c > _sma(c, 200))
    return _first_bar_events(bars, ctx, p, ok)


def dv2(bars, ctx, p):
    """"A different indicator": David Varadi's DV2 -- the 2-day mean of
    close / ((high+low)/2), percent-ranked over the last 252 days; yesterday
    below `lo` (their 10) with the close above the 200-day SMA -> long. Their
    S&P book: 65.8% win, Sharpe 1.01."""
    d, o, h, l, c = _daily(bars, ctx)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        x = _sma(c / ((h + l) / 2.0), 2)
    rank = numpy.full(len(c), NAN)
    for j in range(253, len(c)):
        w = x[j - 252:j]
        rank[j] = 100.0 * (w < x[j]).mean()
    with numpy.errstate(invalid="ignore"):
        ok = (rank < p["lo"]) & (c > _sma(c, 200))
    return _first_bar_events(bars, ctx, p, ok)


def turnaround(bars, ctx, p):
    """"Turnaround Tuesdays on steroids" (TQQQ): today is Tuesday (or
    Wednesday, `days` "tue_wed") and the last `n` daily closes each fell ->
    long at the open; their exit a close above yesterday's high. Their TQQQ:
    71% win, Sharpe 1.74, ~15 trades a year."""
    d, o, h, l, c = _daily(bars, ctx)
    wd = numpy.array([d["weekday"][s] for s in d["d_start"]])
    n = p["n"]
    falls = numpy.ones(len(c), dtype=bool)
    for k in range(n):
        prev = numpy.full(len(c), NAN)
        prev[k + 1:] = c[:len(c) - k - 1]
        cur = numpy.full(len(c), NAN)
        cur[k:] = c[:len(c) - k]
        with numpy.errstate(invalid="ignore"):
            falls &= cur < prev
    # signal day j is the completed session; the entry session is j+1
    nxt = numpy.full(len(c), -1)
    nxt[:-1] = wd[1:]
    days_ok = (nxt == 1) | ((nxt == 2) & (p["days"] == "tue_wed"))
    # the last session has no known successor weekday yet: use the calendar
    if len(c):
        nxt_last = (wd[-1] + 1) % 7 if wd[-1] < 4 else 0
        days_ok[-1] = nxt_last == 1 or (nxt_last == 2 and p["days"] == "tue_wed")
    return _first_bar_events(bars, ctx, p, falls & days_ok)


def gap_down(bars, ctx, p):
    """"Mind the gap" (S&P stocks): today opens BELOW yesterday's close by at
    least `gap` x the 20-day mean daily range, with the open above the
    200-day SMA -> buy the open (theirs: sell the next open; here the close).
    Their book: 55.6% win, +0.11% a trade, Sharpe 1.66 after 10 bps."""
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    d, o, h, l, c = _daily(bars, ctx)
    rng = _sma(h - l, 20)
    sma = _sma(c, 200)
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    op = b["L"]["o"]
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(first)):
        j = idx[i] - 1
        if not first[i] or j < 0 or not (a[i] > 0):
            continue
        if not (fin(rng[j]) and fin(sma[j])):
            continue
        if op[i] < c[j] - p["gap"] * rng[j] and op[i] > sma[j]:
            out[i] = (1, mult * a[i], None)
    return out


def qp_rarity(bars, ctx, p):
    """"A mean-reversion strategy from first principles": QP = how rare
    the last 3-day move is among all 3-day moves of the past 5 years (the
    percentile scaled by the share of moves on that side of zero, 0-100,
    lower = rarer). A 3-day DECLINE with QP under `lo` (their 15) and the
    close above the 200-day SMA -> long; a 3-day surge with QP < `lo` below
    the SMA -> short (their short book). Their S&P book: 64% win, Sharpe
    1.10."""
    d, o, h, l, c = _daily(bars, ctx)
    r3 = numpy.full(len(c), NAN)
    r3[3:] = c[3:] / c[:-3] - 1
    qp = numpy.full(len(c), NAN)
    look = 5 * 252
    for j in range(60, len(c)):
        w = r3[max(3, j - look):j]
        w = w[numpy.isfinite(w)]
        if len(w) < 50 or not fin(r3[j]):
            continue
        if r3[j] < 0:
            neg = (w < 0).mean()
            qp[j] = 100.0 * (w < r3[j]).mean() / neg if neg > 0 else NAN
        else:
            pos = (w > 0).mean()
            qp[j] = 100.0 * (w > r3[j]).mean() / pos if pos > 0 else NAN
    sma = _sma(c, 200)
    with numpy.errstate(invalid="ignore"):
        ok_l = (r3 < 0) & (qp < p["lo"]) & (c > sma)
        ok_s = (r3 > 0) & (qp < p["lo"]) & (c < sma)
    return _first_bar_events(bars, ctx, p, ok_l, ok_s)


EXITS = ("days_1", "signal", "rr_1")
STOPS = ("atr_2", "atr_4")

SPECS = {
    "qt_ibs_band": (ibs_band, lambda g: {
        "sma": (0, 300), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
    "qt_rsi2_dip": (rsi2_dip, lambda g: {
        "lo": (5, 15, 30), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
    "qt_dv2": (dv2, lambda g: {
        "lo": (10, 25), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
    "qt_turnaround": (turnaround, lambda g: {
        "n": (1, 2), "days": ("tue", "tue_wed"), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
    "qt_gap_down": (gap_down, lambda g: {
        "gap": (0.1, 0.25), "stop": STOPS, "exit_mode": ("days_1", "signal", "rr_1"),
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
    "qt_qp_rarity": (qp_rarity, lambda g: {
        "lo": (15, 30), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, above_prev_high_exit),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "days")
