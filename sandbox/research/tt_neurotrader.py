"""neurotrader's YouTube (@neurotrader888) strategies, as `cfd_tt_families`
families.

Source: all 23 videos on the channel (2022-2025), read 2026-10-03 from the
English captions. neurotrader develops on hourly BTC with permutation tests;
most videos are indicator research (visibility graphs, permutation entropy,
reversibility, VSA). The videos that end in a trading RULE are built here.
Map and SKIP list: `results/tiktok/neurotrader_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close. His systems are always-in-the-market
stop-and-reverse; here each signal is one trade, closed by its own reverse
signal (`signal` exit), an ATR stop, or the session close.
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


def _lagged_channel(bars, ctx, n):
    """Highest / lowest CLOSE of the previous `n` bars (his close-only
    Donchian, lagged one bar)."""
    def build():
        c = _base(bars, ctx)["c"]
        hh = numpy.concatenate(([NAN], F.rolling_max(c, n)[:-1]))
        ll = numpy.concatenate(([NAN], F.rolling_min(c, n)[:-1]))
        return hh, ll
    return F.cached(ctx, ("nt_ch", n), build)


def donchian_close(bars, ctx, p):
    """#22, #1, #4 (2022-2025). Close-only Donchian: a close above the
    highest close of the previous `n` bars -> long, below the lowest -> short
    (stop-and-reverse in his version; here the reverse breakout is the
    `signal` exit). `after` "loss" (#4, the Turtles' rule): take a breakout
    only if the previous breakout's trade -- from its close to this one --
    LOST; his runs test found winners follow losers. Stop `stop` ATR. His
    BTC 1h: profitable at nearly every look-back (PF ~1.05-1.08, before
    costs); the loss filter improved it."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    hh, ll = _lagged_channel(bars, ctx, p["n"])
    hh, ll = hh.tolist(), ll.tolist()
    mult = float(p["stop"][4:])
    out = {}
    pos, entry = 0, NAN
    for i in range(len(c)):
        if not fin(hh[i]):
            continue
        side = 1 if c[i] > hh[i] else (-1 if c[i] < ll[i] else 0)
        if not side or side == pos:
            continue
        lost = pos != 0 and fin(entry) and pos * (c[i] - entry) < 0
        pos, entry = side, c[i]
        if p["after"] == "loss" and not lost:
            continue
        if a[i] > 0 and _ok(b, i, p) and i not in out:
            out[i] = (side, mult * a[i], None)
    return out


def donchian_close_exit(index, bars, ctx, params, side):
    hh, ll = _lagged_channel(bars, ctx, params["n"])
    c = _base(bars, ctx)["c"][index]
    return (side == 1 and c < ll[index]) or (side == -1 and c > hh[index])


def trendline_break(bars, ctx, p):
    """#5 (2023). Trendlines refit on every bar over the previous `n` closes
    (not the current one): the resistance line is the least-squares slope
    through those closes lifted to touch the highest of them (his "minimum
    distance line above every price", approximated); projected one bar
    forward. A close above it -> long, below the support line -> short. His
    trade: 3 ATR stop and target, 12-bar max hold (`time_12`). His BTC 1h:
    PF 1.035 always-in; 50% win with the 3 ATR bracket before the ML
    filter."""
    b = _base(bars, ctx)
    c = b["c"]
    n = p["n"]
    a = _atr(bars, ctx)

    def build():
        m = len(c)
        res = numpy.full(m, NAN)
        sup = numpy.full(m, NAN)
        if m <= n:
            return res, sup
        from numpy.lib.stride_tricks import sliding_window_view
        w = sliding_window_view(c[:-1], n)           # window ending at i-1
        x = numpy.arange(n, dtype=float)
        xc = x - x.mean()
        slope = (w - w.mean(axis=1, keepdims=True)) @ xc / (xc @ xc)
        base = w - slope[:, None] * x
        res[n:] = base.max(axis=1) + slope * n
        sup[n:] = base.min(axis=1) + slope * n
        return res, sup
    res, sup = F.cached(ctx, ("nt_tl", n), build)
    res, sup = res.tolist(), sup.tolist()
    cl = c.tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(1, len(cl)):
        if not (fin(res[i]) and fin(res[i - 1])) or not (a[i] > 0) or not _ok(b, i, p):
            continue
        if cl[i] > res[i] and cl[i - 1] <= res[i - 1]:
            out[i] = (1, mult * a[i], None)
        elif cl[i] < sup[i] and cl[i - 1] >= sup[i - 1]:
            out[i] = (-1, mult * a[i], None)
    return out


def _hawkes(bars, ctx, kappa, look):
    """Hawkes process of the normalised range (log range / long ATR of log
    prices), with rolling 5% / 95% quantile thresholds over `look` bars."""
    def build():
        b = _base(bars, ctx)
        lh, ll, lc = numpy.log(b["h"]), numpy.log(b["l"]), numpy.log(b["c"])
        prev = numpy.concatenate(([lc[0]], lc[:-1]))
        tr = numpy.maximum(lh - ll, numpy.maximum(abs(lh - prev), abs(ll - prev)))
        an = 336
        atr_l = F.sma(tr, an)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            nr = (lh - ll) / atr_l
        alpha = math.exp(-kappa)
        hk = numpy.full(len(nr), NAN)
        acc = NAN
        for i, v in enumerate(nr):
            if not fin(v):
                continue
            acc = v if not fin(acc) else acc * alpha + v
            hk[i] = acc * kappa
        from numpy.lib.stride_tricks import sliding_window_view
        q05 = numpy.full(len(hk), NAN)
        q95 = numpy.full(len(hk), NAN)
        if len(hk) > look:
            w = sliding_window_view(numpy.nan_to_num(hk, nan=0.0), look)
            q05[look - 1:] = numpy.quantile(w, 0.05, axis=1)
            q95[look - 1:] = numpy.quantile(w, 0.95, axis=1)
            bad = ~numpy.isfinite(hk)
            q05[bad] = q95[bad] = NAN
        return hk, q05, q95
    return F.cached(ctx, ("nt_hk", kappa, look), build)


def hawkes_vol(bars, ctx, p):
    """#11 (2023). Volatility self-excitation: the Hawkes process (decay
    `kappa`) of the normalised range crosses ABOVE its rolling 95% quantile
    after having been below its 5% quantile -> trade in the direction price
    moved since it was last below (long if up); held until the process falls
    back below the 5% quantile (`signal`). Stop `stop` ATR. His BTC 1h: PF
    1.07, 58% long win rate."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    hk, q05, q95 = _hawkes(bars, ctx, p["kappa"], 168)
    hk, q05, q95 = hk.tolist(), q05.tolist(), q95.tolist()
    mult = float(p["stop"][4:])
    out = {}
    last_below = -1
    for i in range(1, len(c)):
        if not (fin(hk[i]) and fin(q05[i]) and fin(hk[i - 1]) and fin(q95[i - 1])):
            continue
        if hk[i] < q05[i]:
            last_below = i
            continue
        if last_below >= 0 and hk[i] > q95[i] and hk[i - 1] <= q95[i - 1]:
            move = c[i] - c[last_below]
            side = 1 if move > 0 else (-1 if move < 0 else 0)
            if side and a[i] > 0 and _ok(b, i, p):
                out[i] = (side, mult * a[i], None)
    return out


def hawkes_exit(index, bars, ctx, params, side):
    hk, q05, _ = _hawkes(bars, ctx, params["kappa"], 168)
    return fin(hk[index]) and fin(q05[index]) and hk[index] < q05[index]


def profile_levels(bars, ctx, p):
    """#12 (2025). Support/resistance from the market profile: a histogram of
    the last 168 closes (bins ~ `bw` ATR wide; his Gaussian KDE with an ATR
    bandwidth, here a smoothed histogram), its prominent peaks are the
    levels. Trend-following rule: a close crossing UP through a level ->
    long, DOWN through one -> short. Stop `stop` ATR; `signal`-free, rr exits.
    Levels refreshed every 4 bars to keep the cost down."""
    b = _base(bars, ctx)
    c = b["c"]
    a = F.atr(bars, ctx, 14)
    look = 168
    bw = p["bw"]

    def build():
        m = len(c)
        lv = [None] * m
        cur = []
        for i in range(look, m):
            if (i - look) % 4 == 0 and a[i] > 0:
                w = c[i - look:i]
                lo, hi = w.min(), w.max()
                nb = int(max(5, min(80, (hi - lo) / (bw * a[i]))))
                hist, edges = numpy.histogram(w, bins=nb, range=(lo, hi))
                sm = numpy.convolve(hist, [1, 2, 1], "same") / 4.0
                peaks = [k for k in range(1, nb - 1) if sm[k] > sm[k - 1] and sm[k] >= sm[k + 1]
                         and sm[k] >= 0.25 * sm.max()]
                cur = [(edges[k] + edges[k + 1]) / 2 for k in peaks]
            lv[i] = cur
        return lv
    lv = F.cached(ctx, ("nt_prof", bw), build)
    cl = c.tolist()
    al = a.tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(1, len(cl)):
        L = lv[i]
        if not L or not (al[i] > 0) or not _ok(b, i, p):
            continue
        up = any(cl[i - 1] <= x < cl[i] for x in L)
        dn = any(cl[i] < x <= cl[i - 1] for x in L)
        if up and not dn:
            out[i] = (1, mult * al[i], None)
        elif dn and not up:
            out[i] = (-1, mult * al[i], None)
    return out


def ma_bounce(bars, ctx, p):
    """#20 (2022). "Do moving averages work as support?" Bands at the `n` MA
    +- 0.5 ATR: price above the MA comes down into the bands and leaves them
    through the TOP (a bounce) -> long; mirror below the MA. His BTC 1h
    study measured bounce vs penetration frequencies against a random
    baseline; here the bounce is traded. Stop `stop` ATR."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    m = F.sma(b["c"], p["n"]).tolist()
    mult = float(p["stop"][4:])
    out = {}
    state = 0          # +1 entered bands from above, -1 from below
    for i in range(1, len(c)):
        if not (fin(m[i]) and a[i] > 0):
            continue
        up_b, lo_b = m[i] + 0.5 * a[i], m[i] - 0.5 * a[i]
        inside = lo_b <= c[i] <= up_b
        if inside:
            if state == 0 and fin(m[i - 1]):
                state = 1 if c[i - 1] > m[i - 1] + 0.5 * a[i - 1] else (-1 if c[i - 1] < m[i - 1] - 0.5 * a[i - 1] else 0)
            continue
        if state == 1 and c[i] > up_b and _ok(b, i, p):
            out[i] = (1, mult * a[i], None)
        elif state == -1 and c[i] < lo_b and _ok(b, i, p):
            out[i] = (-1, mult * a[i], None)
        state = 0
    return out


def flag(bars, ctx, p):
    """#16, #17 (2023). Bull flag / pennant: a pole from a `k`-bar swing low
    to the highest close since (at least `pole` ATR tall); then a pullback
    that retraces no more than half the pole; the first close above the
    flag's high (the highest close since the pole tip... i.e. the tip itself)
    after at least 3 bars of flag -> long. Mirror for bear flags. His test
    held the position for a fixed number of bars after confirmation
    (`time_N`). Stop below the flag's low (floored)."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    PL, PLi, PH, PHi = (pv[x].tolist() for x in ("l", "l_i", "h", "h_i"))
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        for side, P, Pi in ((1, PL, PLi), (-1, PH, PHi)):
            j = Pi[i]
            if j < 0 or i - j < 5 or i - j > 60:
                continue
            seg = c[j:i]                      # pole + flag, excluding today
            if side == 1:
                t = j + int(numpy.argmax(seg))
                tip, base = c[t], P[i]
                pole = tip - base
                if t >= i - 3 or pole < p["pole"] * A:
                    continue
                fl = min(c[t:i])
                if tip - fl > 0.5 * pole or not (c[i] > tip >= c[i - 1]):
                    continue
                out.setdefault(i, (1, F.stop_dist(1, c[i], fl, A), None))
            else:
                t = j + int(numpy.argmin(seg))
                tip, base = c[t], P[i]
                pole = base - tip
                if t >= i - 3 or pole < p["pole"] * A:
                    continue
                fh = max(c[t:i])
                if fh - tip > 0.5 * pole or not (c[i] < tip <= c[i - 1]):
                    continue
                out.setdefault(i, (-1, F.stop_dist(-1, c[i], fh, A), None))
    return out


def head_shoulders(bars, ctx, p):
    """#15 (2023). Head and shoulders from `k`-bar swings: three swing highs
    with the middle one highest (head); balance -- both shoulders above the
    midpoint of their shoulder/armpit; symmetry -- neither shoulder-to-head
    span more than 2.5x the other. His early confirmation: a close below the
    midpoint between the right shoulder and its armpit -> short. Inverse
    pattern -> long. Held a fixed number of bars (`time_N`) as in his test.
    Stop above the right shoulder (floored)."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    Hi, Li = pv["h_i"].tolist(), pv["l_i"].tolist()
    highs, lows = [], []
    active = []                     # (side, rs, trigger level, stop level)
    out = {}

    def consider(side, ext, opp, px, oppx):
        if len(ext) < 3:
            return
        ls, hd, rs = ext[-3:]
        sgn = -side                                         # +1 for tops
        if not (sgn * (px[hd] - px[ls]) > 0 and sgn * (px[hd] - px[rs]) > 0):
            return
        arm_l = [j for j in opp[-6:] if ls < j < hd]
        arm_r = [j for j in opp[-6:] if hd < j < rs]
        if not arm_l or not arm_r:
            return
        mid_l = (px[ls] + oppx[arm_l[-1]]) / 2
        mid_r = (px[rs] + oppx[arm_r[-1]]) / 2
        if not (sgn * (px[ls] - mid_r) > 0 and sgn * (px[rs] - mid_l) > 0):
            return
        d1, d2 = hd - ls, rs - hd
        if max(d1, d2) > 2.5 * max(1, min(d1, d2)):
            return
        active.append((side, rs, mid_r, px[rs]))

    for i in range(len(c)):
        if Hi[i] >= 0 and (not highs or highs[-1] != Hi[i]):
            highs.append(Hi[i])
            consider(-1, highs, lows, h, l)
        if Li[i] >= 0 and (not lows or lows[-1] != Li[i]):
            lows.append(Li[i])
            consider(1, lows, highs, l, h)
        if not active:
            continue
        A = a[i]
        keep = []
        for side, rs, trig, ext in active:
            if i - rs > 30:
                continue
            if side == -1 and c[i] < trig <= c[i - 1]:
                if A > 0 and _ok(b, i, p):
                    out.setdefault(i, (-1, F.stop_dist(-1, c[i], ext, A), None))
                continue
            if side == 1 and c[i] > trig >= c[i - 1]:
                if A > 0 and _ok(b, i, p):
                    out.setdefault(i, (1, F.stop_dist(1, c[i], ext, A), None))
                continue
            keep.append((side, rs, trig, ext))
        active = keep
    return out

ATR_STOPS = ("atr_2", "atr_3")

SPECS = {
    "nt_donchian_close": (donchian_close, lambda g: {
        "n": (12, 24, 48), "after": ("any", "loss"), "stop": ATR_STOPS,
        "exit_mode": ("signal", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, donchian_close_exit),
    "nt_trendline_break": (trendline_break, lambda g: {
        "n": (24, 72), "stop": ATR_STOPS,
        "exit_mode": ("rr_1", "time_12", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "nt_hawkes_vol": (hawkes_vol, lambda g: {
        "kappa": (0.1, 0.3), "stop": ATR_STOPS,
        "exit_mode": ("signal", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, hawkes_exit),
    "nt_profile_levels": (profile_levels, lambda g: {
        "bw": (0.5, 1.0), "stop": ATR_STOPS,
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "nt_ma_bounce": (ma_bounce, lambda g: {
        "n": (24, 72), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_1", "rr_2", "time_12"),
        "last_entry_minute": g["last"]}, False, None),
    "nt_flag": (flag, lambda g: {
        "k": (3, 5), "pole": (2.0, 4.0),
        "exit_mode": ("time_6", "time_12", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "nt_head_shoulders": (head_shoulders, lambda g: {
        "k": (3, 5),
        "exit_mode": ("time_6", "time_12", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("after", "stop")
