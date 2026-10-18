"""Thomas Skinner's TikTok (@deltatrendtrading) strategies, as
`cfd_tt_families` families.

Source: 29 posts read 2026-10-03; a 2026-10-04 re-scroll found 419 (options
education, guru commentary, Monte Carlo / prop-firm maths), screened by
caption, 11 more read. The same person runs @quantpad. Beyond his own rule,
three are other traders' strategies he coded and tested on camera (TJR's
daily bias, Powell Trades, Alorah Ziva's volume-profile levels). Map and SKIP list:
`results/tiktok/deltatrendtrading_strategy_notes.md`.

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


def _vol_high(bars, ctx):
    """True where the 20-bar realized volatility of log returns sits above
    its own 100-bar median (the stand-in for "elevated VIX")."""
    def build():
        c = F.base(bars, ctx)["c"]
        r = numpy.diff(numpy.log(c), prepend=numpy.log(c[0]))
        from numpy.lib.stride_tricks import sliding_window_view
        rv = numpy.full(len(c), NAN)
        if len(c) >= 20:
            rv[19:] = sliding_window_view(r, 20).std(axis=1)
        med = numpy.full(len(c), NAN)
        if len(c) >= 120:
            med[118:] = numpy.median(sliding_window_view(rv[19:], 100), axis=1)
        return (rv > med).tolist()
    return F.cached(ctx, "dt_vol_high", build)


def rsi_dip_vol(bars, ctx, p):
    """#24 (2026-07-26). "A basic RSI mean-reversion strategy": RSI(`n`)
    under `lo` with the close under its 20-bar mean -> long. Their feature
    study: deeper stretch and elevated (implied and realized) volatility both
    helped; buying while extended above the long trend hurt. `vol` "high"
    takes only bars where realized vol is above its median (their VIX sizing,
    as a filter); "any" takes all. Stop `stop` ATR; `signal` exits when RSI
    is back over 50."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, p["n"]).tolist()
    m = F.sma(b["c"], 20).tolist()
    hi = _vol_high(bars, ctx) if p["vol"] == "high" else None
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(c)):
        if not (fin(r[i]) and fin(m[i])) or r[i] >= p["lo"] or c[i] >= m[i]:
            continue
        if hi is not None and not hi[i]:
            continue
        if _ok(b, i, p):
            _emit(out, i, 1, mult * a[i], None)
    return out


def rsi_dip_exit(index, bars, ctx, params, side):
    return F.rsi(bars, ctx, params["n"])[index] > 50


# =========================================================================== #
# 2026-10-04: influencer strategies he coded (re-scroll)
# =========================================================================== #

def tjr_session_bias(bars, ctx, p):
    """"I coded TJR's daily bias methodology" (ES, 71.4% of 227 state
    transitions right). Sessions are classified; a London session that is a
    MANIPULATION-REVERSAL -- it takes out the Asia low (high) and then
    closes above the Asia high (below the Asia low) -- forecasts expansion /
    continuation for New York. Trade that forecast at the New York open
    (first bar after `start` minutes into the session): long after a bullish
    reversal, short after a bearish one. Stop beyond London's extreme
    (floored); out at the close or the bracket."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    asia = F.clock_window(bars, ctx, "asia")
    lon = F.clock_window(bars, ctx, "london")
    since, day = b["L"]["since"], b["L"]["day"]
    done = None
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or day[i] == done or since[i] < p["start"] or not _ok(b, i, p):
            continue
        ah, al, lh, ll, lc = asia["hi"][i], asia["lo"][i], lon["hi"][i], lon["lo"][i], lon["cl"][i]
        if not all(fin(x) for x in (ah, al, lh, ll, lc)):
            continue
        done = day[i]
        if ll < al and lc > ah:
            _emit(out, i, 1, F.stop_dist(1, c[i], ll, A), None)
        elif lh > ah and lc < al:
            _emit(out, i, -1, F.stop_dist(-1, c[i], lh, A), None)
    return out


def powell_rb_fvg(bars, ctx, p):
    """"Powell trades' strategy, coded and tested" (Pine): a tap into the
    previous day's REJECTION BLOCK (the wick zone above the prior day's body
    for a bearish block -- `wick` of the way up it -- below it for bullish)
    sets the day's bias. The leg from the tap's extreme to the running
    swing the other way is the fib; a 5m FVG in the bias direction beyond
    the 0.5 level (premium half for shorts, discount for longs) is entered
    on its first tap. Stop beyond the FVG's far side; target the nearest
    3-bar swing the other way (`level`); his tests liked low RR (1:1.5)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    fv = F.fvg(bars, ctx)
    pv = F.pivots(bars, ctx, 3)
    day = b["L"]["day"]
    bias, ext, cur = 0, NAN, None
    lo_run = hi_run = NAN
    gaps = []                         # (lo, hi, made_at)
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        if day[i] != cur:
            bias, ext, cur, gaps = 0, NAN, day[i], []
            lo_run, hi_run = l[i], h[i]
        lo_run, hi_run = min(lo_run, l[i]), max(hi_run, h[i])
        if not (A > 0):
            continue
        pdh, pdl, pdo, pdc = d["pdh"][i], d["pdl"][i], d["pdo"][i], d["pdc"][i]
        if not fin(pdh):
            continue
        top_body, bot_body = max(pdo, pdc), min(pdo, pdc)
        if bias == 0:
            if pdh > top_body and h[i] >= top_body + p["wick"] * (pdh - top_body):
                bias, ext = -1, h[i]
            elif bot_body > pdl and l[i] <= bot_body - p["wick"] * (bot_body - pdl):
                bias, ext = 1, l[i]
            continue
        ext = max(ext, h[i]) if bias == -1 else min(ext, l[i])
        far = lo_run if bias == -1 else hi_run
        mid = (ext + far) / 2.0
        if fv["bear"][i] if bias == -1 else fv["bull"][i]:
            g_lo, g_hi = fv["lo"][i], fv["hi"][i]
            if (bias == -1 and g_lo >= mid) or (bias == 1 and g_hi <= mid):
                gaps.append((g_lo, g_hi, i))
        for g in list(gaps):
            g_lo, g_hi, t0 = g
            if i <= t0:
                continue
            if (bias == -1 and h[i] >= g_lo) or (bias == 1 and l[i] <= g_hi):
                gaps.remove(g)
                if not _ok(b, i, p):
                    continue
                stop = g_hi if bias == -1 else g_lo
                tgt = pv["l"][i] if bias == -1 else pv["h"][i]
                _emit(out, i, bias, F.stop_dist(bias, c[i], stop, A),
                      {"far": F.tgt(bias, c[i], tgt)})
    return out


def _session_troughs(bars, ctx, bins, smooth):
    """Per day: low-volume-node prices (local minima of the session's
    smoothed tick-volume-at-price histogram on the typical price)."""
    def build():
        b = F.base(bars, ctx)
        d = F.days(bars, ctx)
        h, l, c, v = b["h"], b["l"], b["c"], b["v"]
        out = []
        k = numpy.ones(smooth) / smooth
        for s, e in zip(d["d_start"], d["d_end"]):
            if e <= s:
                out.append([])
                continue
            lo, hi = l[s:e].min(), h[s:e].max()
            if not (hi > lo):
                out.append([])
                continue
            tp = (h[s:e] + l[s:e] + c[s:e]) / 3.0
            idx = numpy.clip(((tp - lo) / (hi - lo) * bins).astype(int), 0, bins - 1)
            hist = numpy.bincount(idx, weights=v[s:e], minlength=bins)
            sm = numpy.convolve(hist, k, "same")
            out.append([lo + (j + 0.5) * (hi - lo) / bins for j in range(1, bins - 1)
                        if sm[j] < sm[j - 1] and sm[j] <= sm[j + 1]])
        return out
    return F.cached(ctx, ("dt_troughs", bins, smooth), build)


def vp_trough_break(bars, ctx, p):
    """"I coded @Alorah Ziva's strategy ... then optimized it": levels at the
    TROUGHS of the previous session's volume profile (a smoothed histogram's
    local minima; tick volume here), BREAKOUTS only (his tuned version,
    Sharpe 0.15 -> ~5x OOS). Conviction: a bar with range >= `conv` x ATR
    closes beyond the level and the next `confirm` bars stay beyond it ->
    enter the break. Stop back across the level (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    idx = d["day_idx"].tolist()
    T = _session_troughs(bars, ctx, 30, 3)
    pending = []                      # (side, level, trigger bar)
    used = set()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        j = idx[i] - 1
        if not (A > 0) or j < 0:
            pending = []
            continue
        for g in list(pending):
            side, lvl, t0 = g
            if side * (c[i] - lvl) <= 0:
                pending.remove(g)
            elif i - t0 >= p["confirm"]:
                pending.remove(g)
                if _ok(b, i, p):
                    _emit(out, i, side, F.stop_dist(side, c[i], lvl, A), None)
        if h[i] - l[i] < p["conv"] * A:
            continue
        for lvl in T[j]:
            key = (idx[i], round(lvl, 8))
            if key in used:
                continue
            if c[i - 1] <= lvl < c[i]:
                used.add(key)
                pending.append((1, lvl, i))
            elif c[i - 1] >= lvl > c[i]:
                used.add(key)
                pending.append((-1, lvl, i))
    return out


SPECS = {
    "dt_tjr_session_bias": (tjr_session_bias, lambda g: {
        "start": (0, 30), "exit_mode": ("days_1", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, True, None),
    "dt_powell_rb_fvg": (powell_rb_fvg, lambda g: {
        "wick": (0.25, 0.5), "exit_mode": ("level", "rr_1.5", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "dt_vp_trough_break": (vp_trough_break, lambda g: {
        "conv": (1.0, 1.5), "confirm": (1, 2), "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "dt_rsi_dip_vol": (rsi_dip_vol, lambda g: {
        "n": (2, 14), "lo": (10, 30), "vol": ("any", "high"),
        "stop": ("atr_1", "atr_2"), "exit_mode": ("signal", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, rsi_dip_exit),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("vol", "stop")
