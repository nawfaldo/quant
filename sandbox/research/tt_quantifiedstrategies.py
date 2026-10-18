"""QuantifiedStrategies.com (Oddmund Groette) free rules, as
`cfd_tt_families` families.

Source: the 314 articles linked from "200 Trading Strategies (Free)", read
2026-10-03 in Chrome. Round-up articles hide their rules from non-members
("you find the trading rules at the bottom of the article"); only articles
that print the rules were used. Most are daily close-to-close rules on SPY /
QQQ / ETFs: here the daily features come from completed sessions and the
entry is the next session's first bar (as in `tt_quantitativo`). Map:
`results/tiktok/quantifiedstrategies_strategy_notes.md`.

Already covered elsewhere (not rebuilt): RSI(2) dip + QS exit (qt_rsi2_dip),
Turnaround Tuesday (qt_turnaround), IBS band (qt_ibs_band), ORB, London
breakout, MA crosses, Bollinger, SuperTrend, Ichimoku.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research import tt_quantitativo as Q

NAN = float("nan")
fin = math.isfinite


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _daily_rsi(c, n):
    diff = numpy.diff(c, prepend=c[0])
    up, dn = numpy.maximum(diff, 0), numpy.maximum(-diff, 0)
    au, ad = F._ema_arr(up, 2 * n - 1), F._ema_arr(dn, 2 * n - 1)   # Wilder(n)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        return numpy.where(ad > 0, 100 - 100 / (1 + au / ad), 100.0)


def _dd(bars, ctx):
    """Daily arrays plus common derived series, cached."""
    def build():
        d, o, h, l, c = Q._daily(bars, ctx)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            ibs = numpy.where(h > l, (c - l) / (h - l), 0.5)
        tr = numpy.maximum(h - l, numpy.abs(h - numpy.roll(c, 1)))
        tr = numpy.maximum(tr, numpy.abs(l - numpy.roll(c, 1)))
        tr[0] = h[0] - l[0]
        return {"o": o, "h": h, "l": l, "c": c, "ibs": ibs,
                "sma5": Q._sma(c, 5), "sma200": Q._sma(c, 200),
                "rsi2": _daily_rsi(c, 2), "rsi5": _daily_rsi(c, 5), "tr": tr}
    return F.cached(ctx, "qs_dd", build)


def _prev_day_value(bars, ctx, arr, index):
    """`arr` (per session) for the last completed session at bar `index`."""
    j = F.days(bars, ctx)["day_idx"][index] - 1
    return arr[j] if j >= 0 else NAN


def _exit_close_above_sma5(index, bars, ctx, params, side):
    """Connors' exit: the last completed close above its 5-day SMA (taken at
    the next session's first bar)."""
    D = _dd(bars, ctx)
    c, s = _prev_day_value(bars, ctx, D["c"], index), _prev_day_value(bars, ctx, D["sma5"], index)
    return fin(s) and (c > s if side == 1 else c < s)


# --------------------------------------------------------------------------- #
# Larry Connors
# --------------------------------------------------------------------------- #

def connors_3day_hl(bars, ctx, p):
    """larry-connors-3-day-high-low-method. Close > SMA200, close < SMA5, and
    three days in a row of a lower high AND lower low -> long at the close
    (here: next session's first bar). Exit: close above SMA5."""
    D = _dd(bars, ctx)
    h, l, c = D["h"], D["l"], D["c"]
    k = p["days"]
    lhll = (h < numpy.roll(h, 1)) & (l < numpy.roll(l, 1))
    lhll[0] = False
    run = numpy.convolve(lhll.astype(float), numpy.ones(k), "full")[:len(c)] >= k
    with numpy.errstate(invalid="ignore"):
        ok = run & (c > D["sma200"]) & (c < D["sma5"])
    return Q._first_bar_events(bars, ctx, p, ok)


def connors_r3(bars, ctx, p):
    """larry-connors-r3-strategy. Close > SMA200; RSI(2) drops three days in
    a row, the first drop from below 60; today's RSI(2) below `lo` -> long.
    Exit: RSI(2) above 70."""
    D = _dd(bars, ctx)
    r, c = D["rsi2"], D["c"]
    ok = numpy.zeros(len(c), dtype=bool)
    for j in range(3, len(c)):
        if (r[j] < r[j - 1] < r[j - 2] < r[j - 3] and r[j - 3] < 60 and r[j] < p["lo"]
                and fin(D["sma200"][j]) and c[j] > D["sma200"][j]):
            ok[j] = True
    return Q._first_bar_events(bars, ctx, p, ok)


def _exit_rsi2_70(index, bars, ctx, params, side):
    r = _prev_day_value(bars, ctx, _dd(bars, ctx)["rsi2"], index)
    return fin(r) and (r > 70 if side == 1 else r < 30)


def connors_4of5_down(bars, ctx, p):
    """larry-connors-multiple-days-up-and-multiple-days-down-strategy. Close
    > SMA200, close < SMA5, and at least `n` down closes in the last 5 days
    -> long. Exit: close above SMA5."""
    D = _dd(bars, ctx)
    c = D["c"]
    down = numpy.zeros(len(c))
    down[1:] = c[1:] < c[:-1]
    cnt = numpy.convolve(down, numpy.ones(5), "full")[:len(c)]
    with numpy.errstate(invalid="ignore"):
        ok = (cnt >= p["n"]) & (c > D["sma200"]) & (c < D["sma5"])
    return Q._first_bar_events(bars, ctx, p, ok)


# --------------------------------------------------------------------------- #
# IBS / RSI / CCI / WVF dips
# --------------------------------------------------------------------------- #

def ibs_rsi5(bars, ctx, p):
    """nasdaq-qqq-mean-reversion. IBS below 0.25 and RSI(5) below `lo` ->
    long; exit on the QS exit (a close above yesterday's high)."""
    D = _dd(bars, ctx)
    with numpy.errstate(invalid="ignore"):
        ok = (D["ibs"] < 0.25) & (D["rsi5"] < p["lo"])
    return Q._first_bar_events(bars, ctx, p, ok)


def cci_dip(bars, ctx, p):
    """cci-trading-strategy. CCI(`n`) of the daily bars below -`lvl` -> long;
    exit when the close exceeds the prior day's high (QS exit). Profit
    factor ~1.8 on SPY."""
    D = _dd(bars, ctx)
    n = p["n"]
    tp = (D["h"] + D["l"] + D["c"]) / 3.0
    m = Q._sma(tp, n)
    md = numpy.full(len(tp), NAN)
    for j in range(n - 1, len(tp)):
        w = tp[j - n + 1:j + 1]
        md[j] = numpy.mean(numpy.abs(w - w.mean()))
    with numpy.errstate(invalid="ignore", divide="ignore"):
        cci = (tp - m) / (0.015 * md)
        ok = cci < -p["lvl"]
    return Q._first_bar_events(bars, ctx, p, ok)


def vix_fix_rank(bars, ctx, p):
    """williamsvixfix. WVF = (highest close of 22 days - low) / that close;
    its percent rank over the last `look` days above 98 -> long. Exit: a
    close above the previous close."""
    D = _dd(bars, ctx)
    c, l = D["c"], D["l"]
    hc = F.rolling_max(c, 22)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        w = (hc - l) / hc * 100.0
    k = p["look"]
    ok = numpy.zeros(len(c), dtype=bool)
    for j in range(k, len(c)):
        win = w[j - k:j]
        if fin(w[j]) and numpy.isfinite(win).all():
            ok[j] = (win < w[j]).mean() * 100.0 > 98.0
    return Q._first_bar_events(bars, ctx, p, ok)


def _exit_up_close(index, bars, ctx, params, side):
    """Exit once the latest completed session closed above the one before."""
    D = _dd(bars, ctx)
    j = F.days(bars, ctx)["day_idx"][index] - 1
    if j < 1:
        return False
    return (D["c"][j] > D["c"][j - 1]) if side == 1 else (D["c"][j] < D["c"][j - 1])


def rsi2_overbought(bars, ctx, p):
    """what-happens-when-stock-markets-are-overbought. RSI(2) above `hi` ->
    BUY (strength persists short-term in their test); out after N days."""
    D = _dd(bars, ctx)
    with numpy.errstate(invalid="ignore"):
        ok = D["rsi2"] > p["hi"]
    return Q._first_bar_events(bars, ctx, p, ok)


def inside_day(bars, ctx, p):
    """inside-day-trading-strategy. Today is an inside day and yesterday's
    high was below the day before's close -> long; exit on a close above
    yesterday's high (QS exit). `rsi` < 100 adds their RSI(2) filter."""
    D = _dd(bars, ctx)
    h, l, c = D["h"], D["l"], D["c"]
    ok = numpy.zeros(len(c), dtype=bool)
    for j in range(2, len(c)):
        ok[j] = (h[j] < h[j - 1] and l[j] > l[j - 1] and h[j - 1] < c[j - 2]
                 and D["rsi2"][j] < p["rsi"])
    return Q._first_bar_events(bars, ctx, p, ok)


# --------------------------------------------------------------------------- #
# gap after a weak day (needs today's open)
# --------------------------------------------------------------------------- #

def gap_ibs_rsi(bars, ctx, p):
    """gap-trading-strategies. Yesterday's IBS <= 0.25 and RSI(5) <= 45 and
    today opens below yesterday's close by at least `gap` x ATR -> buy the
    open. (Their third condition is not printed; a gap down is the article's
    subject.) Exit: a close above the previous close."""
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    D = _dd(bars, ctx)
    idx = F.days(bars, ctx)["day_idx"].tolist()
    first, o = b["first"].tolist(), b["L"]["o"]
    out = {}
    for i in range(len(first)):
        if not first[i] or not (a[i] > 0):
            continue
        j = idx[i] - 1
        if j < 0:
            continue
        if D["ibs"][j] <= 0.25 and D["rsi5"][j] <= 45 and D["c"][j] - o[i] >= p["gap"] * a[i]:
            out[i] = (1, p["stop_atr"] * a[i], None)
    return out


# --------------------------------------------------------------------------- #
# trend: 350-day SMA +/- an ATR band (their sugar system)
# --------------------------------------------------------------------------- #

def sma_atr_band(bars, ctx, p):
    """sugar-trading-strategy. A close above the `n`-day SMA plus the 7-day
    ATR -> long; below the SMA minus the ATR -> short (first close beyond
    the band). Exit (`signal`): a close beyond the opposite band."""
    D = _dd(bars, ctx)
    c = D["c"]
    s = Q._sma(c, p["n"])
    at = Q._sma(D["tr"], 7)
    up, dn = s + at, s - at
    ok_l = numpy.zeros(len(c), dtype=bool)
    ok_s = numpy.zeros(len(c), dtype=bool)
    with numpy.errstate(invalid="ignore"):
        ok_l[1:] = (c[1:] > up[1:]) & ~(c[:-1] > up[:-1])
        ok_s[1:] = (c[1:] < dn[1:]) & ~(c[:-1] < dn[:-1])
    return Q._first_bar_events(bars, ctx, p, ok_l, ok_s)


def _exit_band(index, bars, ctx, params, side):
    D = _dd(bars, ctx)
    j = F.days(bars, ctx)["day_idx"][index] - 1
    if j < 0:
        return False
    s = Q._sma(D["c"], params["n"])[j]
    at = Q._sma(D["tr"], 7)[j]
    if not fin(s + at):
        return False
    return D["c"][j] < s - at if side == 1 else D["c"][j] > s + at


# --------------------------------------------------------------------------- #
# intraday: open = low / last-hour extreme
# --------------------------------------------------------------------------- #

def open_low(bars, ctx, p):
    """ohl-trading-strategy. If the session's low over its first `m` minutes
    never trades below the open (Open = Low) -> long on the next bar; Open =
    High -> short. Out at the session close (`days_1`) or the bracket."""
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    o, h, l = b["L"]["o"], b["L"]["h"], b["L"]["l"]
    since, day, bar = b["L"]["since"], b["L"]["day"], b["bar"]
    out = {}
    op, lo, hi, cur, done = NAN, NAN, NAN, None, None
    tol = p["tol"]
    for i in range(len(o)):
        if day[i] != cur:
            cur, op, lo, hi = day[i], o[i], l[i], h[i]
        else:
            lo, hi = min(lo, l[i]), max(hi, h[i])
        end = since[i] + bar
        if end < p["m"] or done == day[i] or i + 1 >= len(o) or day[i + 1] != day[i]:
            continue
        A = a[i]
        if not (A > 0):
            continue
        if end >= p["m"] and end - bar < p["m"]:           # the window's last bar
            if op - lo <= tol * A and hi - op > tol * A:
                out[i + 1] = (1, p["stop_atr"] * A, None)
            elif hi - op <= tol * A and op - lo > tol * A:
                out[i + 1] = (-1, p["stop_atr"] * A, None)
            done = day[i]
    return out


def last_hour_extreme(bars, ctx, p):
    """spy-sets-low-or-high-in-the-last-hour. If the session's low was set
    in its last hour -> long at the next session's open, out by midday
    (`time_N`); a high set in the last hour -> short (their weaker side)."""
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    l, h = b["l"], b["h"]
    since, bar = b["since"], b["bar"]
    starts, ends = d["d_start"], d["d_end"]
    first = b["first"].tolist()
    idx = d["day_idx"].tolist()
    long_ok = numpy.zeros(len(starts), dtype=bool)
    short_ok = numpy.zeros(len(starts), dtype=bool)
    for j, (s0, e0) in enumerate(zip(starts, ends)):
        seg = slice(s0, e0)                           # d_end is exclusive
        span = since[seg]
        if not len(span):
            continue
        last = span >= span.max() + bar - 60          # bars in the final hour
        if last.all():
            continue
        lw, hw = l[seg], h[seg]
        long_ok[j] = lw[last].min() <= lw[~last].min()
        short_ok[j] = hw[last].max() >= hw[~last].max()
    out = {}
    for i in range(len(first)):
        if not first[i] or not (a[i] > 0):
            continue
        j = idx[i] - 1
        if j < 0:
            continue
        if long_ok[j]:
            out[i] = (1, p["stop_atr"] * a[i], None)
        elif short_ok[j] and p["side"] == "both":
            out[i] = (-1, p["stop_atr"] * a[i], None)
    return out


STOPS = ("atr_2", "atr_4")
EXITS = ("signal", "days_1", "rr_1")

SPECS = {
    "qs_connors_3day_hl": (connors_3day_hl, lambda g: {
        "days": (2, 3), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, _exit_close_above_sma5),
    "qs_connors_r3": (connors_r3, lambda g: {
        "lo": (10, 20), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, _exit_rsi2_70),
    "qs_connors_4of5_down": (connors_4of5_down, lambda g: {
        "n": (3, 4), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, _exit_close_above_sma5),
    "qs_ibs_rsi5": (ibs_rsi5, lambda g: {
        "lo": (25, 35), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, Q.above_prev_high_exit),
    "qs_cci_dip": (cci_dip, lambda g: {
        "n": (9, 14), "lvl": (90, 150), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, Q.above_prev_high_exit),
    "qs_vix_fix_rank": (vix_fix_rank, lambda g: {
        "look": (10, 50), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, _exit_up_close),
    "qs_rsi2_overbought": (rsi2_overbought, lambda g: {
        "hi": (90, 95), "stop": STOPS, "exit_mode": ("days_1", "days_5", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
    "qs_inside_day": (inside_day, lambda g: {
        "rsi": (100, 50), "stop": STOPS, "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, Q.above_prev_high_exit),
    "qs_gap_ibs_rsi": (gap_ibs_rsi, lambda g: {
        "gap": (0.0, 0.25), "stop_atr": (2.0, 4.0), "exit_mode": EXITS,
        "last_entry_minute": g["last"]}, False, _exit_up_close),
    "qs_sma_atr_band": (sma_atr_band, lambda g: {
        "n": (200, 350), "stop": STOPS, "exit_mode": ("signal", "days_15", "trail_2.5"),
        "last_entry_minute": g["last"]}, False, _exit_band),
    "qs_open_low": (open_low, lambda g: {
        "m": (30, 60), "tol": (0.02, 0.05), "stop_atr": (1.0, 2.0),
        "exit_mode": ("days_1", "rr_1", "rr_2"), "last_entry_minute": g["last"]}, False, None),
    "qs_last_hour_extreme": (last_hour_extreme, lambda g: {
        "side": ("long", "both"), "stop_atr": (1.0, 2.0),
        "exit_mode": ("time_4", "time_6", "days_1"), "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ("stop", "side")
