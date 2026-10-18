"""setup4alpha's Substack (setup4alpha.substack.com) single-market rules, as
`cfd_tt_families` families.

Source: the ~80-post sitemap, read 2026-10-03. Most strategy posts put their
rules behind the paywall (S&P 500 mean reversion 73.7% win, Bollinger, the
small-cap breakout, ETF rotation); the rules published in the free text are
built here. Map: `results/tiktok/setup4alpha_strategy_notes.md`.

Like `tt_quantitativo`, these are DAILY signals: read on the completed
session, entered at the next session's first bar, out at the close or on
their own exit rule (`signal`).
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research.tt_quantitativo import _daily, _first_bar_events, _sma

NAN = float("nan")
fin = math.isfinite


def close_below_low(bars, ctx, p):
    """"Tested 5 buy-the-dip patterns on SPY": `pattern` "below_low": the
    daily close under the previous day's low -> buy; "engulf": that plus a
    high above the previous high (a bearish engulfing range, bought); exit on
    the first close above the previous close (`signal`). Their SPY 2000-2026:
    below-low 61.1% win, CAGR 3.7%; engulfing 66.5%, CAGR 1.5% (low
    exposure)."""
    d, o, h, l, c = _daily(bars, ctx)
    pl, ph = numpy.full(len(c), NAN), numpy.full(len(c), NAN)
    pl[1:], ph[1:] = l[:-1], h[:-1]
    with numpy.errstate(invalid="ignore"):
        ok = c < pl
        if p["pattern"] == "engulf":
            ok &= h > ph
        if p["sma"]:
            ok &= c > _sma(c, p["sma"])
    return _first_bar_events(bars, ctx, p, ok)


def up_close_exit(index, bars, ctx, params, side):
    """Their exit: a close above the previous close (here: the session's
    running close above yesterday's close)."""
    d = F.days(bars, ctx)
    return F.base(bars, ctx)["c"][index] > d["pdc"][index]


def gap_below_low(bars, ctx, p):
    """"The simplest gap-down reversal": the session OPENS below the
    previous session's low -> buy the open; hold until a green day (close
    above the open) -- here `signal`: the first bar closing above the
    session open, else the close. Their SPY: 21% exposure, MAR similar to
    buy-and-hold, lower return."""
    b = F.base(bars, ctx)
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    d = F.days(bars, ctx)
    pdl = d["pdl"].tolist()
    first = b["first"].tolist()
    op = b["L"]["o"]
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(first)):
        if first[i] and fin(pdl[i]) and a[i] > 0 and op[i] < pdl[i] - p["buf"] * a[i]:
            if b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6):
                out[i] = (1, mult * a[i], None)
    return out


def green_exit(index, bars, ctx, params, side):
    d = F.days(bars, ctx)
    return F.base(bars, ctx)["c"][index] > d["open"][index]


def rsi3_dip(bars, ctx, p):
    """"We tried mean reversion on gold": daily RSI(3) < 30 AND RSI(2) < 20
    -> buy; their exit RSI(3) > 80 (here the session close or 1R). `sma`
    200/500 trend filters were tried (theirs: none beat buy-and-hold; it made
    money mainly in gold uptrends)."""
    d, o, h, l, c = _daily(bars, ctx)

    def rsi(n):
        diff = numpy.diff(c, prepend=c[0])
        au = F._ema_arr(numpy.maximum(diff, 0), 2 * n - 1)
        ad = F._ema_arr(numpy.maximum(-diff, 0), 2 * n - 1)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            return numpy.where(ad > 0, 100 - 100 / (1 + au / ad), 100.0)
    with numpy.errstate(invalid="ignore"):
        ok = (rsi(3) < 30) & (rsi(2) < 20)
        if p["sma"]:
            ok &= c > _sma(c, p["sma"])
    return _first_bar_events(bars, ctx, p, ok)


STOPS = ("atr_2", "atr_4")

SPECS = {
    "s4a_close_below_low": (close_below_low, lambda g: {
        "pattern": ("below_low", "engulf"), "sma": (0, 200), "stop": STOPS,
        "exit_mode": ("days_1", "signal", "rr_1"),
        "last_entry_minute": g["last"]}, False, up_close_exit),
    "s4a_gap_below_low": (gap_below_low, lambda g: {
        "buf": (0.0, 0.25), "stop": STOPS,
        "exit_mode": ("days_1", "signal", "rr_1"),
        "last_entry_minute": g["last"]}, False, green_exit),
    "s4a_rsi3_dip": (rsi3_dip, lambda g: {
        "sma": (0, 200), "stop": STOPS,
        "exit_mode": ("days_1", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("pattern", "stop")
