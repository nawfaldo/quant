"""QuantCrawler's TikTok (@quantcrawler) strategies, as `cfd_tt_families`
families.

Source: 26 posts read 2026-10-03; on 2026-10-04 a full re-scroll found 400
(nearly all AI-prompt tips, penny-stock "challenges" and promos for their
order router), screened by caption, and 13 trading posts read. Three rules
are stated (the ORB re-statement is already `fx_orb`). Map and SKIP list:
`results/tiktok/quantcrawler_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _lists(bars, ctx):
    b = F.base(bars, ctx)
    Lb = b["L"]
    return b, Lb["o"], Lb["h"], Lb["l"], Lb["c"]


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _emit(out, i, side, dist, target):
    if not (dist > 0) or not fin(dist):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def ema_cross(bars, ctx, p):
    """#4, #9, #10, #14, #21 (2026-09-24..10-01), the example every Strategy
    Architect promo builds: the 9 EMA crosses the 21 -> enter that way, a
    20-tick stop and a 40-tick target. Ticks mean nothing across CFDs, so
    the stop is `stop` ATR and the target its 2R (`rr_2`); `signal` exits on
    the opposite cross. `side` "long" or "both"."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    f = F.ema(bars, ctx, p["fast"]).tolist()
    s = F.ema(bars, ctx, 21).tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(1, len(c)):
        if not (fin(f[i - 1]) and fin(s[i - 1])) or not _ok(b, i, p):
            continue
        if f[i - 1] <= s[i - 1] and f[i] > s[i]:
            _emit(out, i, 1, mult * a[i], None)
        elif p["side"] == "both" and f[i - 1] >= s[i - 1] and f[i] < s[i]:
            _emit(out, i, -1, mult * a[i], None)
    return out


def ema_cross_exit(index, bars, ctx, params, side):
    f = F.ema(bars, ctx, params["fast"])
    s = F.ema(bars, ctx, 21)
    return (side == 1 and f[index] < s[index]) or (side == -1 and f[index] > s[index])


def momentum_pick(bars, ctx, p):
    """#2..#26, the "$100 a day" series (2026-09-21..10-02). Their engine
    ranks ~500 US stocks and buys the top one at the open; every pick so far
    shared the same profile, kept here as a filter on yesterday's session:
    up over the last 20 AND 60 sessions, yesterday's volume >= `vol_x` x its
    20-session median, yesterday's close 0-6% above its 20-session average,
    RSI(14) of the daily closes in 50-70 (their picks: 1-4%, 57-60, widened
    so a single CFD fires more than a handful of times a year) -> long on the session's first bar,
    held to the close (`days_1`) or to an ATR target. Single-instrument: it
    fires when THIS symbol would have qualified, not when it ranked first.
    Stop `stop` x the 14-bar ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    dc = d["d_close"]
    starts, ends = d["d_start"], d["d_end"]
    dv = numpy.array([b["v"][s:e].sum() for s, e in zip(starts, ends)])
    nd = len(dc)
    ok_day = numpy.zeros(nd, dtype=bool)
    # RSI of daily closes (Wilder, 14)
    rsi = numpy.full(nd, NAN)
    if nd > 15:
        diff = numpy.diff(dc)
        up, dn = numpy.maximum(diff, 0), numpy.maximum(-diff, 0)
        au, ad = up[:14].mean(), dn[:14].mean()
        for j in range(15, nd):
            au += (up[j - 1] - au) / 14
            ad += (dn[j - 1] - ad) / 14
            rsi[j] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    for j in range(60, nd):
        avg20 = dc[j - 19:j + 1].mean()
        med_v = numpy.median(dv[j - 20:j])
        ok_day[j] = (dc[j] > dc[j - 20] and dc[j] > dc[j - 60]
                     and med_v > 0 and dv[j] >= p["vol_x"] * med_v
                     and avg20 < dc[j] <= 1.06 * avg20
                     and 50 <= rsi[j] <= 70)
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(c)):
        j = idx[i] - 1
        if not first[i] or j < 0 or not ok_day[j] or not _ok(b, i, p):
            continue
        _emit(out, i, 1, mult * a[i], None)
    return out


def session_level_fade(bars, ctx, p):
    """2026-09/10 posts ("Asia High Rejection: 10 APEX evaluations", "QC
    Radar: London High short", "live-account rejection trades"): resting
    limit orders at the UNTESTED Asia and London session highs/lows -- sell
    the first touch of the high, buy the first touch of the low -- at a
    fixed 1:2 (his $500 stop / $1,000 target). `mode` "touch" fills at the
    level (entered on the touching bar's close, the nearest the engine can
    do); "reject" also needs that bar to close back inside. Stop `stop_atr` x
    ATR beyond the level."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    wins = [F.clock_window(bars, ctx, w) for w in ("asia", "london")]
    day = b["L"]["day"]
    used = set()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        for wi, w in enumerate(wins):
            hi, lo = w["hi"][i], w["lo"][i]
            if not fin(hi):
                continue
            for side, lvl in ((-1, hi), (1, lo)):
                key = (day[i], wi, side)
                if key in used:
                    continue
                hit = h[i] >= lvl if side == -1 else l[i] <= lvl
                if not hit:
                    continue
                used.add(key)                 # first touch only: tested now
                if p["mode"] == "reject" and side * (c[i] - lvl) <= 0:
                    continue
                d = side * (c[i] - lvl) + p["stop_atr"] * A
                _emit(out, i, side, d, None)
    return out


SPECS = {
    "qc_session_level_fade": (session_level_fade, lambda g: {
        "mode": ("touch", "reject"), "stop_atr": (0.5, 1.0),
        "exit_mode": ("rr_2", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "qc_ema_cross": (ema_cross, lambda g: {
        "fast": (5, 9), "side": ("long", "both"), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_2", "signal", "days_1"),
        "last_entry_minute": g["last"]}, False, ema_cross_exit),
    "qc_momentum_pick": (momentum_pick, lambda g: {
        "vol_x": (1.0, 1.2, 1.5), "stop": ("atr_2", "atr_4"),
        "exit_mode": ("days_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "side", "mode")
