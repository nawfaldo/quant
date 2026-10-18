"""Flux Charts' TikTok (@fluxchart) strategies, as `cfd_tt_families` families.

Source: 44 posts read 2026-10-03 from TikTok's English auto-captions (28
carry them); on 2026-10-04 a full re-scroll found 690 posts (mostly plugs for
their own indicators), screened by caption, and 24 more read -- the posts on
concepts not yet covered (breaker blocks, BPR, turtle soup, trend channels,
trendline sweeps, the 65% supply/demand + 9 EMA test). Flux Charts sells TradingView indicators the way
LuxAlgo does: a post states a rule, then names the indicator that automates
it. The RULE is kept; each proprietary indicator is replaced by a causal
stand-in named in the family's docstring. Post-to-family map and the SKIP list:
`results/tiktok/fluxchart_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close; structural stops are floored at 0.2 ATR.
A `target` dict feeds the `level*` exits (`level` / `level_far` -> "far"); an
event is only emitted when every level it names is on the trade's side.

Volume. Exness bars carry tick volume, not traded volume, and no buy/sell
split. "Volume delta" (#5, #34) is proxied by the bar's close location times
its tick volume: `v * (2c - h - l) / (h - l)`. That is a guess at who won the
bar, not order flow -- say so before trusting any result built on it.
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
    if isinstance(target, dict):
        if not target or any(not (v > 0) or not fin(v) for v in target.values()):
            return False
    elif target is not None and (not (target > 0) or not fin(target)):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _same_day(b, i, j):
    day = b["L"]["day"]
    return j >= 0 and day[j] == day[i]


def _vol_avg(bars, ctx, n=20):
    b = F.base(bars, ctx)
    return F.cached(ctx, ("fx_vavg", n), lambda: numpy.concatenate(
        ([NAN], F.sma(b["v"], n)[:-1])).tolist())


def _delta(bars, ctx):
    """Close-location x tick volume: +v for a close at the high, -v at the low."""
    def build():
        b = F.base(bars, ctx)
        rng = b["h"] - b["l"]
        with numpy.errstate(invalid="ignore", divide="ignore"):
            loc = numpy.where(rng > 0, (2 * b["c"] - b["h"] - b["l"]) / rng, 0.0)
        return loc * b["v"]
    return F.cached(ctx, "fx_delta", build)


def _bands(bars, ctx, n, mult):
    """Bollinger basis / upper / lower on the close."""
    def build():
        b = F.base(bars, ctx)
        c = b["c"]
        from numpy.lib.stride_tricks import sliding_window_view
        mid = F.sma(c, n)
        sd = numpy.full(len(c), NAN)
        if len(c) >= n:
            sd[n - 1:] = sliding_window_view(c, n).std(axis=1)
        return mid, mid + mult * sd, mid - mult * sd
    return F.cached(ctx, ("fx_bb", n, mult), build)


# =========================================================================== #
# 2026-10 / 2026-09
# =========================================================================== #

def htf_band_reversal(bars, ctx, p):
    """#1 (2026-10-02). Price breaks the HIGHER-timeframe Bollinger lower band,
    then the lower-timeframe bands turn up -> long (mirror at the upper band).
    Stand-ins: the HTF band is a 20-period band over `htf_mult` x as many bars
    (20 x 4 = an 80-bar band = the 20-period band one timeframe up); "bands
    turn up" = the 20-bar basis rises after falling. Their Kalman smoothing is
    dropped. The break must be within the last 10 bars. Stop beyond the
    extreme of those bars; `level` = the HTF basis. (Look-back 10 bars.)"""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    mid, _, _ = _bands(bars, ctx, 20, 2.0)
    hmid, hup, hlo = _bands(bars, ctx, 20 * p["htf_mult"], p["mult"])
    mid, hmid, hup, hlo = mid.tolist(), hmid.tolist(), hup.tolist(), hlo.tolist()
    look = 10
    out = {}
    for i in range(look + 2, len(c)):
        if not (fin(mid[i - 2]) and fin(hlo[i])) or not _ok(b, i, p):
            continue
        if not _same_day(b, i, i - look):
            continue
        turn_up = mid[i] > mid[i - 1] <= mid[i - 2]
        turn_dn = mid[i] < mid[i - 1] >= mid[i - 2]
        lows = l[i - look:i + 1]
        highs = h[i - look:i + 1]
        if turn_up and any(lows[k] < hlo[i - look + k] for k in range(look + 1)):
            lo = min(lows)
            _emit(out, i, 1, F.stop_dist(1, c[i], lo, a[i]),
                  {"far": F.tgt(1, c[i], hmid[i])})
        elif turn_dn and any(highs[k] > hup[i - look + k] for k in range(look + 1)):
            hi = max(highs)
            _emit(out, i, -1, F.stop_dist(-1, c[i], hi, a[i]),
                  {"far": F.tgt(-1, c[i], hmid[i])})
    return out


def equal_sweep_mss(bars, ctx, p):
    """#2 (2026-09-30). Relative EQUAL lows (the last two swing lows within
    `tol` ATR) are swept by a WICK (low below both, close back above); within
    `window` bars a close above the last swing high formed before the sweep
    (the MSS) -> long. `entry` "mss" enters on that close; "fvg" also needs a
    bullish FVG on the MSS leg (their "enter at the FVG"). Stop under the
    sweep low. `level` = the swing high before that one (their "nearest
    swing before the sweep"). Mirror at equal highs. Their claim: 80% win,
    +62% in their own backtester. Single timeframe here (theirs: 15m sweep,
    1m MSS)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    PL, PLp, PH, PHp = (pv[k].tolist() for k in ("l", "l_prev", "h", "h_prev"))
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    win = p["window"]
    out = {}
    armed = {1: None, -1: None}           # side -> (bar, extreme, mss, target)
    for i in range(len(c)):
        A = a[i]
        if not (A > 0):
            continue
        # arm on a wick sweep of equal levels
        if fin(PL[i]) and fin(PLp[i]) and abs(PL[i] - PLp[i]) <= p["tol"] * A:
            eq = min(PL[i], PLp[i])
            if l[i] < eq and c[i] > eq and fin(PH[i]):
                armed[1] = (i, l[i], PH[i], PHp[i])
        if fin(PH[i]) and fin(PHp[i]) and abs(PH[i] - PHp[i]) <= p["tol"] * A:
            eq = max(PH[i], PHp[i])
            if h[i] > eq and c[i] < eq and fin(PL[i]):
                armed[-1] = (i, h[i], PL[i], PLp[i])
        for side in (1, -1):
            st = armed[side]
            if st is None:
                continue
            s0, ext, mss, nxt = st
            if i - s0 > win or not _same_day(b, i, s0):
                armed[side] = None
                continue
            ext = min(ext, l[i]) if side == 1 else max(ext, h[i])
            armed[side] = (s0, ext, mss, nxt)
            if i == s0 or side * (c[i] - mss) <= 0:
                continue
            armed[side] = None
            if p["entry"] == "fvg" and not any(
                    (bull if side == 1 else bear)[j] for j in range(s0 + 1, i + 1)):
                continue
            if _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], ext, A),
                      {"far": F.tgt(side, c[i], nxt)})
    return out


def unicorn(bars, ctx, p):
    """#3 (2026-09-28). ICT Unicorn: a breaker block with an FVG in the same
    zone and direction. Stand-in for the breaker: a bullish break of structure
    (`k` swings) after a LOWER low; the breaker is the last up-close candle
    before that low (the order block the low ran through). A bullish FVG
    formed after the low must overlap it. The first retrace into the zone
    within `window` bars that closes back above its bottom -> long. Stop under
    the zone (floored); `level` = the high of the leg (their "opposite side
    of the range"). Mirror for shorts."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    st = F.structure(bars, ctx, p["k"])
    up, dn = st["up"].tolist(), st["dn"].tolist()
    PL, PLp, PLi = pv["l"].tolist(), pv["l_prev"].tolist(), pv["l_i"].tolist()
    PH, PHp, PHi = pv["h"].tolist(), pv["h_prev"].tolist(), pv["h_i"].tolist()
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    flo, fhi = fv["lo"].tolist(), fv["hi"].tolist()
    win = p["window"]
    out = {}
    zone = None                                # (side, lo, hi, born, target)
    for i in range(len(c)):
        A = a[i]
        if not (A > 0):
            continue
        for side, brk, P, Pp, Pi in ((1, up, PL, PLp, PLi), (-1, dn, PH, PHp, PHi)):
            if not brk[i] or not (fin(P[i]) and fin(Pp[i])):
                continue
            if side * (Pp[i] - P[i]) <= 0:     # needs a lower low / higher high
                continue
            j0 = Pi[i]
            ob = None
            for j in range(j0 - 1, max(j0 - 12, -1), -1):
                if side * (c[j] - o[j]) > 0:
                    ob = (l[j], h[j])
                    break
            if ob is None or not _same_day(b, i, j0):
                continue
            gaps = [(flo[j], fhi[j]) for j in range(j0 + 1, i + 1)
                    if (bull if side == 1 else bear)[j]]
            hit = [(max(ob[0], g0), min(ob[1], g1)) for g0, g1 in gaps
                   if g0 < ob[1] and g1 > ob[0]]
            if not hit:
                continue
            zlo, zhi = ob
            leg = max(h[j0:i + 1]) if side == 1 else min(l[j0:i + 1])
            zone = (side, zlo, zhi, i, leg)
        if zone is None or i == zone[3]:
            continue
        side, zlo, zhi, born, leg = zone
        if i - born > win or not _same_day(b, i, born):
            zone = None
            continue
        if side == 1 and l[i] <= zhi and c[i] > zlo:
            zone = None
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], zlo, A), {"far": F.tgt(1, c[i], leg)})
        elif side == -1 and h[i] >= zlo and c[i] < zhi:
            zone = None
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], zhi, A), {"far": F.tgt(-1, c[i], leg)})
    return out


def sr_retest_low_volume(bars, ctx, p):
    """#4 (2026-09-27). A retest of support (the last `k` swing low, within
    `tol` ATR) on a bar whose tick volume is under `vmult` x its 20-bar mean
    ("low selling pressure"), closing back up -> long; stop the bar's low
    (their "most recent low"); 2R. Mirror at resistance. Stand-ins: their
    logistic-regression S/R levels -> swing pivots; their relative-volume
    oscillator -> tick volume vs its mean; the Fibonacci "golden wave"
    retest is dropped (no causal definition given)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = b["L"]["v"]
    va = _vol_avg(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(va[i]) or v[i] >= p["vmult"] * va[i]:
            continue
        if not _ok(b, i, p):
            continue
        t = p["tol"] * A
        if fin(PL[i]) and l[i] <= PL[i] + t and l[i] >= PL[i] - t and c[i] > PL[i] and c[i] > o[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
        elif fin(PH[i]) and h[i] >= PH[i] - t and h[i] <= PH[i] + t and c[i] < PH[i] and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
    return out


def cvd_zero_cross(bars, ctx, p):
    """#5 (2026-09-26). Cumulative volume delta crosses the zero line, then an
    FVG in that direction within `window` bars confirms -> enter on that FVG's
    bar. CVD = the rolling `n`-bar sum of the close-location delta proxy (see
    the module docstring). Stop beyond the FVG's far edge (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = _delta(bars, ctx)
    n = p["n"]
    cs = numpy.concatenate(([0.0], numpy.cumsum(d)))
    cvd = numpy.full(len(d), NAN)
    cvd[n - 1:] = cs[n:] - cs[:-n]
    cvd = cvd.tolist()
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    flo, fhi = fv["lo"].tolist(), fv["hi"].tolist()
    out = {}
    cross = None                               # (side, bar)
    for i in range(1, len(c)):
        if fin(cvd[i]) and fin(cvd[i - 1]):
            if cvd[i - 1] <= 0 < cvd[i]:
                cross = (1, i)
            elif cvd[i - 1] >= 0 > cvd[i]:
                cross = (-1, i)
        if cross is None:
            continue
        side, at = cross
        if i - at > p["window"] or not _same_day(b, i, at):
            cross = None
            continue
        if side == 1 and bull[i]:
            cross = None
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], flo[i], a[i]), None)
        elif side == -1 and bear[i]:
            cross = None
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], fhi[i], a[i]), None)
    return out


def sweep_reclaim_bias(bars, ctx, p):
    """#6, #9 (2026-09-24, -21). A candle trades through the last `k` swing
    low and closes back above it (sweep + reclaim) -> long, only when the
    trend bias agrees: `bias` "none"; "ema" close above the 50 EMA; "stack"
    50 above 200 as well (their "aligned across every timeframe" -- a
    dashboard of TF biases, stood in for by a fast and a slow EMA). Stop under
    the sweep; `level` = the last swing high (the next opposing swing).
    Mirror for shorts."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    e50 = F.ema(bars, ctx, 50).tolist()
    e200 = F.ema(bars, ctx, 200).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        side = 0
        if fin(PL[i]) and l[i] < PL[i] < c[i]:
            side = 1
        elif fin(PH[i]) and h[i] > PH[i] > c[i]:
            side = -1
        if not side:
            continue
        if p["bias"] != "none":
            if not fin(e50[i]) or side * (c[i] - e50[i]) <= 0:
                continue
            if p["bias"] == "stack" and (not fin(e200[i]) or side * (e50[i] - e200[i]) <= 0):
                continue
        ext = l[i] if side == 1 else h[i]
        lvl = PH[i] if side == 1 else PL[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ext, A), {"far": F.tgt(side, c[i], lvl)})
    return out


def fvg_grade(bars, ctx, p):
    """#8 (2026-09-22). Grade every FVG on four factors and trade only the
    best: (1) the displacement candle's body >= `disp` ATR, (2) its tick
    volume above the 20-bar mean, (3) it sits in the discount (premium) half
    of the 50-bar range for a bullish (bearish) gap, (4) it agrees with the
    `5`-swing structure trend. A gap with >= `min_grade` factors is armed;
    the first retest into it within 12 bars that closes back out of it ->
    enter. Stop beyond the gap's far edge (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = b["L"]["v"]
    va = _vol_avg(bars, ctx)
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    flo, fhi = fv["lo"].tolist(), fv["hi"].tolist()
    tr = F.structure(bars, ctx, 5)["trend"].tolist()
    hh = F.rolling_max(b["h"], 50).tolist()
    ll = F.rolling_min(b["l"], 50).tolist()
    out = {}
    gap = None                                  # (side, lo, hi, born)
    for i in range(2, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if bull[i] or bear[i]:
            side = 1 if bull[i] else -1
            j = i - 1
            score = (abs(c[j] - o[j]) >= p["disp"] * A) + (fin(va[j]) and v[j] > va[j])
            if fin(hh[i]):
                midr = (hh[i] + ll[i]) / 2
                score += side * (midr - (flo[i] + fhi[i]) / 2) > 0
            score += tr[i] == side
            if score >= p["min_grade"]:
                gap = (side, flo[i], fhi[i], i)
                continue
        if gap is None:
            continue
        side, glo, ghi, born = gap
        if i - born > 12 or not _same_day(b, i, born):
            gap = None
            continue
        if side == 1 and l[i] <= ghi and c[i] > ghi:
            gap = None
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], glo, A), None)
        elif side == -1 and h[i] >= glo and c[i] < glo:
            gap = None
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], ghi, A), None)
    return out


def liquidity_grab(bars, ctx, p):
    """#10 (2026-09-19), and #12's Turtle Soup (no caption, same idea). A bar
    breaks the prior `n`-bar high and closes back below it, with an upper
    wick >= `ratio` x its body ("only the biggest bubbles") -> short. Stop
    beyond the wick; `level` = the `n`-bar low (the next opposing level).
    Mirror at lows."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(hh[i]) or not _ok(b, i, p):
            continue
        body = max(abs(c[i] - o[i]), 0.05 * A)
        if h[i] > hh[i] and c[i] < hh[i] and (h[i] - max(o[i], c[i])) >= p["ratio"] * body:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), {"far": F.tgt(-1, c[i], ll[i])})
        elif l[i] < ll[i] and c[i] > ll[i] and (min(o[i], c[i]) - l[i]) >= p["ratio"] * body:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), {"far": F.tgt(1, c[i], hh[i])})
    return out


def sr_poc_rejection(bars, ctx, p):
    """#11 (2026-09-18), #19. A higher-timeframe S/R level (a `k`-bar swing,
    `k` large = a level one timeframe up) within `tol` ATR of the previous
    session's volume POC; price trades into it and a rejection candle closes
    back on the near side -> enter away from it. Stop beyond the zone (the
    bar's extreme, floored); 2R in the video."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    poc = F.prev_day(bars, ctx, F.day_profile(bars, ctx)["poc"]).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(poc[i]) or not _ok(b, i, p):
            continue
        t = p["tol"] * A
        if fin(PH[i]) and abs(PH[i] - poc[i]) <= t and h[i] >= PH[i] and c[i] < PH[i] and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
        elif fin(PL[i]) and abs(PL[i] - poc[i]) <= t and l[i] <= PL[i] and c[i] > PL[i] and c[i] > o[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
    return out


def consolidation_break(bars, ctx, p):
    """#16 (2026-09-13). A tight range -- the last `n` bars span <= `width`
    ATR (their "clean coil") -- and a close outside it -> enter that way. Stop
    at the range's other side (floored); `level` = `expand` x the range's
    height beyond the break (their "the type's average expansion": the
    indicator's per-type history is not reproducible, a multiple of the range
    height is the textbook measured move)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(hh[i]) or not _same_day(b, i, i - n) or not _ok(b, i, p):
            continue
        rng = hh[i] - ll[i]
        if rng > p["width"] * A:
            continue
        if c[i] > hh[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], ll[i], A),
                  {"far": F.tgt(1, c[i], hh[i] + rng)})
        elif c[i] < ll[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], hh[i], A),
                  {"far": F.tgt(-1, c[i], ll[i] - rng)})
    return out


def orb(bars, ctx, p):
    """#20, #36 (2026-09-05, 2026-08-05). The first `or_min` minutes' range;
    `mode` "break": the first close beyond it -> enter; "retest": after that
    close, wait for price to come back to the broken edge and close away from
    it again (#36's ORB Algo). Stop at the range's other side (`far`) or its
    middle (`mid`); 2R in the video. Their claim: 75% win over 100+ trades."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = done = None
    broke = 0
    for i in range(len(c)):
        if day[i] != cur:
            cur, done, broke = day[i], False, 0
        hi, lo = Rh[i], Rl[i]
        if done or not fin(hi):
            continue
        side = 0
        if p["mode"] == "break":
            side = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
        elif not broke:
            broke = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
            continue
        elif broke == 1 and l[i] <= hi and c[i] > hi:
            side = 1
        elif broke == -1 and h[i] >= lo and c[i] < lo:
            side = -1
        if not side:
            continue
        done = True
        if not _ok(b, i, p):
            continue
        ref = (lo if side == 1 else hi) if p["stop"] == "far" else (hi + lo) / 2
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def power_of_three(bars, ctx, p):
    """#22, #24 (2026-08-31, -27). ICT Power of 3: accumulation = the prior
    `n` bars span <= `width` ATR; manipulation = a candle wicks below that
    range and closes back inside it; distribution = the trade the other way
    -> long (mirror above). #22 adds "inside a bullish order block"; stand-in
    `ob` "on": the manipulation wick reaches the low of the last down-close
    candle in the range. Stop under the manipulation wick; `level` = the
    range's far side."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(hh[i]) or not _same_day(b, i, i - n) or not _ok(b, i, p):
            continue
        if hh[i] - ll[i] > p["width"] * A:
            continue
        side = 0
        if l[i] < ll[i] and ll[i] < c[i] < hh[i]:
            side = 1
        elif h[i] > hh[i] and ll[i] < c[i] < hh[i]:
            side = -1
        if not side:
            continue
        if p["ob"] == "on":
            obs = [j for j in range(i - n, i) if side * (c[j] - o[j]) < 0]
            if not obs:
                continue
            j = obs[-1]
            if (side == 1 and l[i] > l[j]) or (side == -1 and h[i] < h[j]):
                continue
        ext = l[i] if side == 1 else h[i]
        far = hh[i] if side == 1 else ll[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ext, A), {"far": F.tgt(side, c[i], far)})
    return out


def trend_fade(bars, ctx, p):
    """#23 (2026-08-28). A trend-strength score from three votes -- RSI(14)
    above 55 / below 45, SuperTrend(10, 3) direction, and the close's distance
    from the `ema_n` EMA beyond 0.5 ATR -- read from -3 to +3. "Candles shift
    from strong teal to neutral grey": the score was +3 on the previous bar
    and is now within +-1 -> short, the fade (mirror from -3). Stop beyond
    the last 6 bars' extreme (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, 14).tolist()
    sd = F.supertrend(bars, ctx, 10, 3.0)["dir"].tolist()
    e = F.ema(bars, ctx, p["ema_n"]).tolist()
    score = [NAN] * len(c)
    for i in range(len(c)):
        if not (fin(r[i]) and fin(e[i]) and a[i] > 0) or not sd[i]:
            continue
        s = (1 if r[i] > 55 else (-1 if r[i] < 45 else 0)) + sd[i]
        dist = (c[i] - e[i]) / a[i]
        s += 1 if dist > 0.5 else (-1 if dist < -0.5 else 0)
        score[i] = s
    out = {}
    for i in range(6, len(c)):
        if not (fin(score[i]) and fin(score[i - 1])) or not _same_day(b, i, i - 6):
            continue
        if not _ok(b, i, p) or abs(score[i]) > 1:
            continue
        if score[i - 1] == 3:
            _emit(out, i, -1, F.stop_dist(-1, c[i], max(h[i - 6:i + 1]), a[i]), None)
        elif score[i - 1] == -3:
            _emit(out, i, 1, F.stop_dist(1, c[i], min(l[i - 6:i + 1]), a[i]), None)
    return out


def fvg_crossfire(bars, ctx, p):
    """#30, #32 (2026-08-15, -12). A bullish and a bearish FVG overlap
    within `max_age` bars of each other: the overlap is the "crossfire" zone
    and its side is the newer gap's ("flips"). Price retests the zone and
    closes back beyond it on that side -> enter. Stop beyond the zone's far
    edge (floored); 2-3R in the video. `poc` "on" (#30) needs the zone within
    1 ATR of the previous session's POC."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    flo, fhi = fv["lo"].tolist(), fv["hi"].tolist()
    poc = F.prev_day(bars, ctx, F.day_profile(bars, ctx)["poc"]).tolist() if p["poc"] == "on" else None
    last = {1: None, -1: None}
    zone = None                                # (side, lo, hi, born)
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        for side, flag in ((1, bull), (-1, bear)):
            if not flag[i]:
                continue
            other = last[-side]
            if other is not None and i - other[2] <= p["max_age"] and _same_day(b, i, other[2]):
                zlo, zhi = max(flo[i], other[0]), min(fhi[i], other[1])
                if zhi > zlo and (poc is None or (fin(poc[i]) and abs((zlo + zhi) / 2 - poc[i]) <= A)):
                    zone = (side, zlo, zhi, i)
            last[side] = (flo[i], fhi[i], i)
        if zone is None or zone[3] == i:
            continue
        side, zlo, zhi, born = zone
        if i - born > p["max_age"] or not _same_day(b, i, born):
            zone = None
            continue
        if side == 1 and l[i] <= zhi and c[i] > zhi:
            zone = None
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], zlo, A), None)
        elif side == -1 and h[i] >= zlo and c[i] < zlo:
            zone = None
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], zhi, A), None)
    return out


def htf_trend_demand_st(bars, ctx, p):
    """#33 (2026-08-08). Higher timeframe in an uptrend -- the last closed
    `htf_min`-minute candle made a higher high AND a higher low than the one
    before (their 4h HH/HL) -- then on this timeframe price tags demand (the
    last 5-bar swing low, within 1 ATR, in the last 10 bars) and SuperTrend
    (10, 3) flips up -> long; stop the recent low (floored); 2R. Mirror for
    downtrends. Their supply/demand-zone indicator -> swing lows; their
    "SuperTrend logistic regression" -> plain SuperTrend."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m = max(2, p["htf_min"] // b["bar"])
    t = F.htf(bars, ctx, m)
    ph, pl, qh, ql = (t[k].tolist() for k in ("ph", "pl", "qh", "ql"))
    flip = F.supertrend(bars, ctx, 10, 3.0)["flip"].tolist()
    pv = F.pivots(bars, ctx, 5)
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    w = 10
    for i in range(w, len(c)):
        A = a[i]
        if not flip[i] or not (A > 0) or not fin(ql[i]) or not _ok(b, i, p):
            continue
        if not _same_day(b, i, i - w):
            continue
        side = flip[i]
        if side == 1:
            if not (ph[i] > qh[i] and pl[i] > ql[i]) or not fin(PL[i]):
                continue
            lo = min(l[i - w:i + 1])
            if abs(lo - PL[i]) > A:
                continue
            _emit(out, i, 1, F.stop_dist(1, c[i], lo, A), None)
        else:
            if not (ph[i] < qh[i] and pl[i] < ql[i]) or not fin(PH[i]):
                continue
            hi = max(h[i - w:i + 1])
            if abs(hi - PH[i]) > A:
                continue
            _emit(out, i, -1, F.stop_dist(-1, c[i], hi, A), None)
    return out


def breakout_delta(bars, ctx, p):
    """#34 (2026-08-07). A close through the last `k` swing high (low) counts
    only when the breakout candle's delta agrees: the close-location proxy
    (2c-h-l)/(h-l) >= `dmin` and tick volume above its 20-bar mean (their
    "if bullish volume is weak, it's a fake-out"). Stop at the last opposite
    swing (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = b["L"]["v"]
    va = _vol_avg(bars, ctx)
    st = F.structure(bars, ctx, p["k"])
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (up[i] or dn[i]) or not (A > 0) or not fin(va[i]) or not _ok(b, i, p):
            continue
        rng = h[i] - l[i]
        if rng <= 0 or v[i] <= va[i]:
            continue
        loc = (2 * c[i] - h[i] - l[i]) / rng
        if up[i] and loc >= p["dmin"] and fin(PL[i]):
            _emit(out, i, 1, F.stop_dist(1, c[i], PL[i], A), None)
        elif dn[i] and -loc >= p["dmin"] and fin(PH[i]):
            _emit(out, i, -1, F.stop_dist(-1, c[i], PH[i], A), None)
    return out


def fvg_osc_divergence(bars, ctx, p):
    """#35 (2026-08-06). An oscillator of FVG activity -- the `n`-bar EMA of
    (bullish gap size - bearish gap size) / ATR -- and price closing above
    the last `k` swing high while the oscillator sits below its value at that
    swing -> short (bearish divergence). Mirror at lows. Stop beyond the
    bar's extreme (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    fv = F.fvg(bars, ctx)
    size = numpy.where(fv["bull"], fv["hi"] - fv["lo"],
                       numpy.where(fv["bear"], -(fv["hi"] - fv["lo"]), 0.0))
    av = F.atr(bars, ctx, 14)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        x = numpy.where(av > 0, size / av, NAN)
    osc = F._ema_arr(x, p["n"]).tolist()
    st = F.structure(bars, ctx, p["k"])
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    PHi, PLi = pv["h_i"].tolist(), pv["l_i"].tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not fin(osc[i]) or not _ok(b, i, p):
            continue
        if up[i] and PHi[i] >= 0 and fin(osc[PHi[i]]) and osc[i] < osc[PHi[i]]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
        elif dn[i] and PLi[i] >= 0 and fin(osc[PLi[i]]) and osc[i] > osc[PLi[i]]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
    return out


def tested_zone(bars, ctx, p):
    """#37, #38, #42 (2026-08-04, -03, 07-29). Trade only levels with a track
    record: every `k`-bar swing is a level; each later touch (within 0.25 ATR,
    counted once until price leaves the zone) is a TEST, and a test that
    closes back on its own side is a HOLD. A level with >= `min_tests` tests
    and a hold rate >= 60% qualifies. `mode` "reject": a qualifying support
    touched and a candle closing back above it -> long, stop under the level
    (floored), `level` = the nearest level above (their "next level up").
    "break" (#42's short): a close through a qualifying level by 0.25 ATR ->
    trade the break, stop back beyond the level. Mirrors."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    Hi, Li = pv["h_i"].tolist(), pv["l_i"].tolist()
    levels = []                       # [price, tests, holds, inside, alive]
    seen = set()
    out = {}
    for i in range(len(c)):
        A = a[i]
        for j in (Hi[i], Li[i]):
            if j >= 0 and j not in seen:
                seen.add(j)
                levels.append([h[j] if j == Hi[i] else l[j], 0, 0, True, True])
        if len(levels) > 40:
            levels = [z for z in levels if z[4]][-40:]
        if not (A > 0):
            continue
        tol = 0.25 * A
        fired = False
        for z in levels:
            if not z[4]:
                continue
            lvl = z[0]
            touch = l[i] <= lvl + tol and h[i] >= lvl - tol
            if not touch:
                z[3] = False
                continue
            if z[3]:
                continue                          # same visit
            z[3] = True
            above = c[i] > lvl                    # which side it holds from
            prev_side = 1 if c[i - 1] > lvl else -1 if i else 0
            held = (prev_side == 1 and above) or (prev_side == -1 and not above)
            qual = z[1] >= p["min_tests"] and z[2] >= 0.6 * z[1]
            z[1] += 1
            z[2] += held
            broke = abs(c[i] - lvl) > tol and not held
            if broke:
                z[4] = False
            if not qual or fired or not _ok(b, i, p):
                continue
            if p["mode"] == "reject" and held:
                side = prev_side
                if side * (c[i] - o[i]) <= 0:
                    continue
            elif p["mode"] == "break" and broke:
                side = -prev_side
            else:
                continue
            if not side:
                continue
            # target: the nearest live level beyond the close, the way the trade goes
            nxt = [y[0] for y in levels if y[4] and side * (y[0] - c[i]) > tol]
            far = min(nxt, key=lambda y: abs(y - c[i])) if nxt else NAN
            fired = _emit(out, i, side, F.stop_dist(side, c[i], lvl - side * tol, A),
                          {"far": F.tgt(side, c[i], far)})
    return out


# =========================================================================== #
# 2026-10-04 re-scroll: the grid holds 690 posts, not 44. 24 more read --
# the strategy posts on concepts no family covered yet.
# =========================================================================== #

def grab_breaker(bars, ctx, p):
    """"Insane / CRAZY / Perfect trades using Breaker Blocks" (2025, 15m) and
    "58% win rate" (BTC 30m). A sell-side liquidity grab (a low under the
    prior `n`-bar low that closes back above it); the last bearish candle
    before the jump is the order block; a close above its high invalidates
    it into a bullish BREAKER [ob low, ob high]. `entry` "form" (#3: "instead
    of waiting for the retest, enter when the breaker forms") or "retest"
    (price comes back to the zone and closes back above it, within 12 bars).
    Stop below the zone (or the grab low, whichever is lower). Mirrors."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    grab = {1: None, -1: None}        # side -> (bar, extreme)
    zone = {1: None, -1: None}        # side -> (zlo, zhi, stop, formed_at)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0) or not fin(hh[i]):
            continue
        if l[i] < ll[i] and c[i] > ll[i]:
            grab[1] = (i, l[i])
        if h[i] > hh[i] and c[i] < hh[i]:
            grab[-1] = (i, h[i])
        for side in (1, -1):
            g = grab[side]
            if g is not None and i - g[0] <= 12 and zone[side] is None:
                # last opposite-colour candle from the grab up to the bar before
                ob = None
                for j in range(i - 1, max(g[0] - 3, 0) - 1, -1):
                    if side * (c[j] - o[j]) < 0:
                        ob = j
                        break
                if ob is not None:
                    edge = h[ob] if side == 1 else l[ob]
                    if side * (c[i] - edge) > 0:
                        zlo, zhi = l[ob], h[ob]
                        stop = min(zlo, g[1]) if side == 1 else max(zhi, g[1])
                        zone[side] = (zlo, zhi, stop, i)
                        grab[side] = None
                        if p["entry"] == "form" and _ok(b, i, p):
                            _emit(out, i, side, F.stop_dist(side, c[i], stop, A), None)
                            zone[side] = None
                        continue
            z = zone[side]
            if z is None or p["entry"] != "retest":
                continue
            zlo, zhi, stop, t0 = z
            if i - t0 > 12 or side * (c[i] - (zlo if side == 1 else zhi)) < 0:
                zone[side] = None
                continue
            touched = l[i] <= zhi if side == 1 else h[i] >= zlo
            if i > t0 and touched and side * (c[i] - o[i]) > 0 and _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], stop, A), None)
                zone[side] = None
    return out


def bpr_sweep(bars, ctx, p):
    """"Balanced Price Range Trading Strategy" (5m after 1h highs/lows) and
    "INSANE BPR strategy" (30m). A higher-timeframe swing (`k`-bar pivot,
    k = 12 ~ an hour on 5m) is swept; then a balanced price range forms:
    an FVG one way overlapping an opposite FVG from the last 20 bars (the
    overlap is the BPR). Price retests the BPR and closes back out of it in
    the sweep's reversal direction -> enter, only with the higher-timeframe
    trend (close vs EMA(`tr_ema`), their "market structure screener").
    Stop at the sweep extreme; their 1:2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    fv = F.fvg(bars, ctx)
    e = F.ema(bars, ctx, p["tr_ema"])
    swept = {1: None, -1: None}      # reversal side -> (bar, extreme)
    bpr = {1: None, -1: None}        # side -> (lo, hi, stop, bar)
    out = {}
    for i in range(3, len(c)):
        A = a[i]
        if not (A > 0) or not fin(e[i]):
            continue
        ph, pl = pv["h"][i], pv["l"][i]
        if fin(ph) and h[i] > ph and c[i] < ph:
            swept[-1] = (i, h[i])
        if fin(pl) and l[i] < pl and c[i] > pl:
            swept[1] = (i, l[i])
        for side in (1, -1):
            s = swept[side]
            if s is not None and i - s[0] <= 24 and bpr[side] is None:
                new = fv["bull"][i] if side == 1 else fv["bear"][i]
                if new:
                    lo_n, hi_n = fv["lo"][i], fv["hi"][i]
                    for j in range(max(2, i - 20), i):
                        if (fv["bear"][j] if side == 1 else fv["bull"][j]):
                            lo_o, hi_o = fv["lo"][j], fv["hi"][j]
                            lo, hi = max(lo_n, lo_o), min(hi_n, hi_o)
                            if hi > lo:
                                bpr[side] = (lo, hi, s[1], i)
                                swept[side] = None
                                break
            z = bpr[side]
            if z is None or i <= z[3]:
                continue
            lo, hi, stop, t0 = z
            if i - t0 > 24:
                bpr[side] = None
                continue
            if side == 1 and l[i] <= hi and c[i] > hi and c[i] > e[i] and _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], stop, A), None)
                bpr[side] = None
            elif side == -1 and h[i] >= lo and c[i] < lo and c[i] < e[i] and _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], stop, A), None)
                bpr[side] = None
    return out


def turtle_soup(bars, ctx, p):
    """"ICT Turtle Soup explained": higher-timeframe liquidity (`liq` "pd" =
    the previous day's high/low, "swing" = the latest 12-bar pivot) is swept
    on the trading timeframe; after the sweep a market structure shift (a
    close through the latest `k`-bar swing the other way, within 12 bars)
    -> enter against the sweep. Stop beyond the swept extreme; `level` =
    the opposite liquidity (their target)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pv12 = F.pivots(bars, ctx, 12)
    pvk = F.pivots(bars, ctx, p["k"])
    sweep = {1: None, -1: None}      # reversal side -> (bar, extreme, target)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if p["liq"] == "pd":
            up_l, dn_l = d["pdh"][i], d["pdl"][i]
        else:
            up_l, dn_l = pv12["h"][i], pv12["l"][i]
        if fin(up_l) and h[i] > up_l and (sweep[-1] is None or h[i] > sweep[-1][1]):
            sweep[-1] = (i, h[i], dn_l)
        if fin(dn_l) and l[i] < dn_l and (sweep[1] is None or l[i] < sweep[1][1]):
            sweep[1] = (i, l[i], up_l)
        for side in (1, -1):
            s = sweep[side]
            if s is None:
                continue
            if i - s[0] > 12:
                sweep[side] = None
                continue
            lvl = pvk["h"][i] if side == 1 else pvk["l"][i]
            if not fin(lvl) or i == s[0]:
                continue
            if side * (c[i] - lvl) > 0 and side * (c[i - 1] - lvl) <= 0 and _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], s[1], A),
                      {"far": F.tgt(side, c[i], s[2])})
                sweep[side] = None
    return out


def channel_retest(bars, ctx, p):
    """"Easy / Overpowered / Insane Trend Channels" (5m/15m). A linear-
    regression channel over `n` bars (centre +/- 2 sd of the residuals);
    slope up = bullish. Price tags the lower line and closes back inside on
    a green candle with tick volume above its 20-bar average (their "buyers
    stepping in" -- a tick-volume proxy, no traded volume here) -> long.
    Stop below the channel line (floored); their 3R. Mirrors."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    va = _vol_avg(bars, ctx)
    v = b["L"]["v"]
    n = p["n"]
    def build():
        cc = b["c"]
        x = numpy.arange(n, dtype=float)
        xm = x.mean()
        sxx = ((x - xm) ** 2).sum()
        mid = numpy.full(len(cc), NAN)
        slope = numpy.full(len(cc), NAN)
        sd = numpy.full(len(cc), NAN)
        from numpy.lib.stride_tricks import sliding_window_view
        if len(cc) >= n:
            W = sliding_window_view(cc, n)
            ym = W.mean(axis=1)
            sl = ((W - ym[:, None]) * (x - xm)).sum(axis=1) / sxx
            fit_end = ym + sl * (n - 1 - xm)
            res = W - (ym[:, None] + sl[:, None] * (x - xm))
            mid[n - 1:], slope[n - 1:], sd[n - 1:] = fit_end, sl, res.std(axis=1)
        return mid.tolist(), slope.tolist(), sd.tolist()
    mid, slope, sd = F.cached(ctx, ("fx_lrc", n), build)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        # channel known at the PREVIOUS bar (so this bar's tag is causal)
        m, s, w = mid[i - 1], slope[i - 1], sd[i - 1]
        if not (A > 0 and fin(m) and fin(va[i]) and w > 0) or not _ok(b, i, p):
            continue
        lo, hi = m + s - 2 * w, m + s + 2 * w
        vol_ok = v[i] > va[i]
        if s > 0 and l[i] <= lo < c[i] and c[i] > o[i] and vol_ok:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
        elif s < 0 and h[i] >= hi > c[i] and c[i] < o[i] and vol_ok:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
    return out


def trendline_sweep_fvg(bars, ctx, p):
    """"Trendline Liquidity Sweeps": a rising trendline through the last two
    rising `k`-bar pivot lows is broken (a low under it) and price reverses
    up leaving a bullish FVG; price retraces into the FVG while in the
    DISCOUNT half of the recent `k`*4-bar range -> buy. Stop at the recent
    low; their 1.5R. Mirrors (falling line through pivot highs)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    fv = F.fvg(bars, ctx)
    w = 4 * p["k"]
    rh = F.rolling_max(b["h"], w).tolist()
    rl = F.rolling_min(b["l"], w).tolist()
    swept = {1: None, -1: None}      # side -> (bar, extreme)
    gap = {1: None, -1: None}        # side -> (lo, hi, stop, bar)
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        # lines from the last two pivots, projected to bar i
        for side, P, Pi, Pp, Ppi in ((1, pv["l"], pv["l_i"], pv["l_prev"], pv["l_prev_i"]),
                                     (-1, pv["h"], pv["h_i"], pv["h_prev"], pv["h_prev_i"])):
            if Pi[i] < 0 or Ppi[i] < 0 or not (fin(P[i]) and fin(Pp[i])) or Pi[i] == Ppi[i]:
                continue
            rising = P[i] > Pp[i]
            if (side == 1) != rising:
                continue
            line = P[i] + (P[i] - Pp[i]) / (Pi[i] - Ppi[i]) * (i - Pi[i])
            if side == 1 and l[i] < line:
                swept[1] = (i, l[i] if swept[1] is None else min(l[i], swept[1][1]))
            if side == -1 and h[i] > line:
                swept[-1] = (i, h[i] if swept[-1] is None else max(h[i], swept[-1][1]))
        for side in (1, -1):
            s = swept[side]
            if s is not None and i - s[0] <= 6 and (fv["bull"][i] if side == 1 else fv["bear"][i]):
                gap[side] = (fv["lo"][i], fv["hi"][i], s[1], i)
                swept[side] = None
            g = gap[side]
            if g is None or i <= g[3]:
                continue
            lo, hi, stop, t0 = g
            if i - t0 > 12:
                gap[side] = None
                continue
            midr = (rh[i] + rl[i]) / 2.0
            if side == 1 and l[i] <= hi and c[i] > lo and c[i] < midr and _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], stop, A), None)
                gap[side] = None
            elif side == -1 and h[i] >= lo and c[i] < hi and c[i] > midr and _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], stop, A), None)
                gap[side] = None
    return out


def demand_9ema(bars, ctx, p):
    """"EASY Supply & Demand strategy with a 65% win rate (backtested)",
    15m, their PF 1.6: price retests a demand zone (the last `k`-bar pivot
    low, +/- 0.25 ATR, standing in for their multi-timeframe zones) and the
    candle CLOSES above the 9 EMA -> long; supply mirror with a close below
    the 9 EMA. Stop beyond the zone."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    e9 = F.ema(bars, ctx, 9)
    touched = {1: -10 ** 9, -1: -10 ** 9}
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(e9[i])):
            continue
        dz, sz = pv["l"][i], pv["h"][i]
        tol = 0.25 * A
        if fin(dz) and l[i] <= dz + tol and c[i] > dz - tol:
            touched[1] = i
        if fin(sz) and h[i] >= sz - tol and c[i] < sz + tol:
            touched[-1] = i
        if i - touched[1] <= 3 and c[i] > e9[i] and c[i - 1] <= e9[i - 1] and _ok(b, i, p):
            _emit(out, i, 1, F.stop_dist(1, c[i], dz - tol, A), None)
        elif i - touched[-1] <= 3 and c[i] < e9[i] and c[i - 1] >= e9[i - 1] and _ok(b, i, p):
            _emit(out, i, -1, F.stop_dist(-1, c[i], sz + tol, A), None)
    return out


RR = ("rr_1", "rr_2", "rr_3")

SPECS = {
    "fx_htf_band_reversal": (htf_band_reversal, lambda g: {
        "htf_mult": (2, 4), "mult": (1.5, 2.0),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_equal_sweep_mss": (equal_sweep_mss, lambda g: {
        "k": (2, 3), "tol": (0.25, 0.5), "window": (12, 24), "entry": ("mss", "fvg"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_unicorn": (unicorn, lambda g: {
        "k": (2, 3), "window": (12, 24),
        "exit_mode": ("rr_2", "rr_3", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_sr_retest_low_volume": (sr_retest_low_volume, lambda g: {
        "k": (5, 10), "tol": (0.1, 0.25), "vmult": (0.8, 1.0),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_cvd_zero_cross": (cvd_zero_cross, lambda g: {
        "n": (20, 50), "window": (3, 6),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "fx_sweep_reclaim_bias": (sweep_reclaim_bias, lambda g: {
        "k": (3, 5), "bias": ("none", "ema", "stack"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_fvg_grade": (fvg_grade, lambda g: {
        "min_grade": (3, 4), "disp": (1.0, 1.5),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "fx_liquidity_grab": (liquidity_grab, lambda g: {
        "n": (10, 20), "ratio": (1.0, 2.0, 3.0),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_sr_poc_rejection": (sr_poc_rejection, lambda g: {
        "k": (10, 20), "tol": (0.5, 1.0),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "fx_consolidation_break": (consolidation_break, lambda g: {
        "n": (6, 12), "width": (2.0, 3.0),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_orb": (orb, lambda g: {
        "or_min": (15, 30), "mode": ("break", "retest"), "stop": ("far", "mid"),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_power_of_three": (power_of_three, lambda g: {
        "n": (6, 12), "width": (2.0, 3.0), "ob": ("off", "on"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_trend_fade": (trend_fade, lambda g: {
        "ema_n": (20, 50),
        "exit_mode": ("rr_1", "rr_2", "time_4"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_fvg_crossfire": (fvg_crossfire, lambda g: {
        "max_age": (10, 30), "poc": ("off", "on"),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_htf_trend_demand_st": (htf_trend_demand_st, lambda g: {
        "htf_min": (60, 240),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "fx_breakout_delta": (breakout_delta, lambda g: {
        "k": (3, 5), "dmin": (0.3, 0.6),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_fvg_osc_divergence": (fvg_osc_divergence, lambda g: {
        "n": (10, 20), "k": (3, 5),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "fx_tested_zone": (tested_zone, lambda g: {
        "k": (5, 10), "min_tests": (2, 4, 10), "mode": ("reject", "break"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_grab_breaker": (grab_breaker, lambda g: {
        "n": (10, 20), "entry": ("form", "retest"),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_bpr_sweep": (bpr_sweep, lambda g: {
        "k": (6, 12), "tr_ema": (50, 200),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_turtle_soup": (turtle_soup, lambda g: {
        "liq": ("pd", "swing"), "k": (2, 3),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_channel_retest": (channel_retest, lambda g: {
        "n": (50, 100),
        "exit_mode": ("rr_2", "rr_3", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_trendline_sweep_fvg": (trendline_sweep_fvg, lambda g: {
        "k": (3, 5),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "fx_demand_9ema": (demand_9ema, lambda g: {
        "k": (5, 10),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("entry", "bias", "mode", "stop", "ob", "poc", "liq")
