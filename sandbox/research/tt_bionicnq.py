"""BionicNQ's YouTube (@BionicNQ) strategies, as `cfd_tt_families` families.

Source: 82 videos listed 2026-10-03 (ICT concepts on NQ); the model and
concept videos (12) read from the English captions; the rest are trade
recaps and live backtests of the same model. Map:
`results/tiktok/bionicnq_strategy_notes.md`.

THE BNQ MODEL: a higher-timeframe PDA (a 15m/30m/1h fair value gap), a stop
hunt (turtle soup) into it, entry on the INVERSION of a lower-timeframe FVG
with displacement (closing through it within about four candles), targeting
engineered external liquidity (the last swing high). SMT with ES is his
"cherry on top" -- a second market is not available to a one-symbol cell.

Shape and conventions are `tt_luxalgo`'s.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _base(bars, ctx):
    return F.base(bars, ctx)


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _htf_fvgs(bars, ctx, m):
    """Bullish / bearish FVGs on session-aligned `m`-bar candles, known from
    the bar that closes the third candle. Returns per-bar lists of the most
    recent (lo, hi) bullish and bearish zones (NaN when none)."""
    def build():
        b = _base(bars, ctx)
        n = b["n"]
        t = F.htf(bars, ctx, m)
        closed, grp = t["closed"], t["group"]
        h, l = b["h"], b["l"]
        cand = []            # (hi, lo, close_bar) of closed HTF candles, same session
        bu_lo = numpy.full(n, NAN)
        bu_hi = numpy.full(n, NAN)
        be_lo = numpy.full(n, NAN)
        be_hi = numpy.full(n, NAN)
        start = 0
        cur = (NAN, NAN, NAN, NAN)
        day = b["day"]
        for i in range(n):
            if i and day[i] != day[i - 1]:
                cand = []
            if closed[i]:
                s = i
                while s > 0 and grp[s - 1] == grp[i]:
                    s -= 1
                cand.append((h[s:i + 1].max(), l[s:i + 1].min()))
                if len(cand) >= 3:
                    (h0, l0), _, (h2, l2) = cand[-3:]
                    c0, c1, c2, c3 = cur
                    if l2 > h0:
                        c0, c1 = h0, l2
                    if h2 < l0:
                        c2, c3 = h2, l0
                    cur = (c0, c1, c2, c3)
            bu_lo[i], bu_hi[i], be_lo[i], be_hi[i] = cur
        return bu_lo, bu_hi, be_lo, be_hi
    return F.cached(ctx, ("bnq_hfvg", m), build)


def ifvg_model(bars, ctx, p):
    """#43, #82, #70, #31, #56 (2025-26). A sweep of the last `k`-bar swing
    low (low below it, the stop hunt); within the next 8 bars a bearish FVG
    (formed after the swing high before the sweep) is INVERTED: a candle
    closes above the gap's top with a body >= `disp` ATR, within 4 bars of
    the gap's creation or of the sweep -> long. `pda` "hN" also needs the
    sweep low inside the latest bullish FVG of N-bar candles (his 15m/30m/1h
    PDA: h3 / h6 on 5m bars); "off" drops it. Stop under the sweep low (floored);
    `level` = the last swing high (his engineered-liquidity target). Mirror
    short."""
    b = _base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    flo, fhi = fv["lo"].tolist(), fv["hi"].tolist()
    if p["pda"] != "off":
        bu_lo, bu_hi, be_lo, be_hi = (x.tolist() for x in _htf_fvgs(bars, ctx, int(p["pda"][1:])))
    day = b["L"]["day"]
    out = {}
    sweep = {1: None, -1: None}       # side -> (bar, extreme, target)
    gaps = {1: [], -1: []}            # opposing gaps awaiting inversion: (bar, lo, hi)
    for i in range(len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if i and day[i] != day[i - 1]:
            sweep = {1: None, -1: None}
            gaps = {1: [], -1: []}
        if bear[i]:
            gaps[1] = (gaps[1] + [(i, flo[i], fhi[i])])[-4:]
        if bull[i]:
            gaps[-1] = (gaps[-1] + [(i, flo[i], fhi[i])])[-4:]
        if fin(PL[i]) and l[i] < PL[i] and fin(PH[i]):
            ok_pda = p["pda"] == "off" or (fin(bu_lo[i]) and bu_lo[i] - 0.25 * A <= l[i] <= bu_hi[i] + 0.25 * A)
            if ok_pda:
                sweep[1] = (i, l[i], PH[i])
        if fin(PH[i]) and h[i] > PH[i] and fin(PL[i]):
            ok_pda = p["pda"] == "off" or (fin(be_lo[i]) and be_lo[i] - 0.25 * A <= h[i] <= be_hi[i] + 0.25 * A)
            if ok_pda:
                sweep[-1] = (i, h[i], PL[i])
        for side in (1, -1):
            s = sweep[side]
            if s is None:
                continue
            j, ext, tgt = s
            ext = min(ext, l[i]) if side == 1 else max(ext, h[i])
            sweep[side] = (j, ext, tgt)
            if i - j > 8:
                sweep[side] = None
                continue
            body = side * (c[i] - o[i])
            if body < p["disp"] * A:
                continue
            hit = [g for g in gaps[side] if min(i - g[0], i - j) <= 4 and i > g[0]
                   and (c[i] > g[2] if side == 1 else c[i] < g[1])]
            if not hit or not _ok(b, i, p):
                continue
            sweep[side] = None
            t = F.tgt(side, c[i], tgt)
            if t > 0 and fin(t):
                out.setdefault(i, (side, F.stop_dist(side, c[i], ext, A), {"far": t}))
    return out


def stacked_po3(bars, ctx, p):
    """#4, #78, #67 (2025-26). Stacked Power of 3: at each key open (09:30,
    09:45, 10:00 New York) the open price is equilibrium; price runs BELOW it
    (a time discount, the manipulation) by at least `run` ATR within the
    interval's first 15 minutes, then a candle closes back above that open
    with displacement -> long (distribution toward the day's draw); mirror
    above. Stop beyond the manipulation extreme; `level` = the session's high
    so far for longs (low for shorts)."""
    b = _base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    real = b["L"]["real"]
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    run_h, run_l = d["run_h"].tolist(), d["run_l"].tolist()
    day = b["L"]["day"]
    bar = b["bar"]
    keys = (9 * 60 + 30, 9 * 60 + 45, 10 * 60)
    out = {}
    cur = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, fired, state = day[i], False, {}
        A = a[i]
        if fired or not (A > 0):
            continue
        for kt in keys:
            if real[i] == kt:
                state[kt] = [o[i], h[i], l[i]]
            elif kt in state and kt < real[i] < kt + 15 + bar:
                st = state[kt]
                st[1], st[2] = max(st[1], h[i]), min(st[2], l[i])
        for kt, (op, hi, lo) in list(state.items()):
            if not (kt < real[i] <= kt + 30) or not _ok(b, i, p):
                continue
            if lo < op - p["run"] * A and c[i] > op and c[i] - o[i] > 0.5 * A:
                t = F.tgt(1, c[i], run_h[i - 1] if i else NAN)
                if t > 0 and fin(t):
                    fired = True
                    out[i] = (1, F.stop_dist(1, c[i], lo, A), {"far": t})
                    break
            if hi > op + p["run"] * A and c[i] < op and o[i] - c[i] > 0.5 * A:
                t = F.tgt(-1, c[i], run_l[i - 1] if i else NAN)
                if t > 0 and fin(t):
                    fired = True
                    out[i] = (-1, F.stop_dist(-1, c[i], hi, A), {"far": t})
                    break
    return out


SPECS = {
    "bnq_ifvg_model": (ifvg_model, lambda g: {
        "k": (3, 5), "disp": (0.3, 0.6), "pda": ("off", "h3", "h6"),
        "exit_mode": ("level", "rr_2", "rr_3"), "last_entry_minute": g["last"]}, False, None),
    "bnq_stacked_po3": (stacked_po3, lambda g: {
        "run": (0.25, 0.5),
        "exit_mode": ("level", "rr_2", "rr_1"), "last_entry_minute": g["last"]}, True, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("pda",)
