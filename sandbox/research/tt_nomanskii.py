"""Nomanskii's TikTok (@nomanskii, "Evans") strategies, as `cfd_tt_families`
families.

Source: 157 posts on the grid (most are travel vlogs), screened by caption
2026-10-04; the 11 trading-rule posts read (captions + descriptions). He
trades QQQ / SPY in the New York session and backtests each rule, then adds
the filters that made it work -- the filters are kept as axes. Map and SKIP
list: `results/tiktok/nomanskii_strategy_notes.md`.

Times are New York wall clock (`real`), so these are `ny_clock` families.
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


def _emit(out, i, side, dist, target=None):
    if not (dist > 0) or not fin(dist):
        return False
    if isinstance(target, dict) and any(not (v > 0) or not fin(v) for v in target.values()):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _ema(bars, ctx, n):
    return F.cached(ctx, ("nmk_ema", n), lambda: F.ema(bars, ctx, n).tolist())


def _rvol_at_time(bars, ctx, days=20):
    """Volume relative to the average volume of the SAME bar-of-day over
    the previous `days` sessions (TradingView's Relative Volume at Time)."""
    def build():
        b = F.base(bars, ctx)
        v = b["v"].astype(float)
        pos = b["pos"]
        hist = {}
        out = numpy.full(len(v), NAN)
        day = b["day"]
        cur_day, buf = None, []
        for i in range(len(v)):
            if day[i] != cur_day:
                for k, x in buf:
                    hist.setdefault(k, []).append(x)
                    if len(hist[k]) > days:
                        hist[k].pop(0)
                buf, cur_day = [], day[i]
            k = int(pos[i])
            h = hist.get(k)
            if h and len(h) >= 5:
                m = sum(h) / len(h)
                out[i] = v[i] / m if m > 0 else NAN
            buf.append((k, v[i]))
        return out.tolist()
    return F.cached(ctx, ("nmk_rvol", days), build)


def _or_levels(bars, ctx, minutes):
    """Per bar: the opening range high/low of the first `minutes` of the
    session, NaN until it has completed."""
    o = F.opening_range(bars, ctx, minutes)
    return o


# --------------------------------------------------------------------------- #
# 1. the 80% rule on yesterday's value area
# --------------------------------------------------------------------------- #

def value_area_80(bars, ctx, p):
    """"I backtested the 80% rule using the FRVP on SPY and QQQ over 239
    trades." Yesterday's value area (70% of volume). Open ABOVE it; a bar
    closes back inside -> short at the close; stop slightly above the high
    of day; target the other side of the value area. Mirror. His filters
    (`filt` 1): the close back inside must come by 11:00 NY, and the target
    must be closer than the stop (else skip); target then extended by
    `ext` x the value-area width. Raw 62% win but lost in 2026; filtered:
    116 trades, 76% win, 29.3R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    prof = F.day_profile(bars, ctx)
    idx = d["day_idx"].tolist()
    real = b["L"]["real"]
    first = b["first"].tolist()
    vah = F.prev_day(bars, ctx, prof["vah"]).tolist()
    val = F.prev_day(bars, ctx, prof["val"]).tolist()
    day_open = d["open"].tolist()
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    done = None
    out = {}
    for i in range(len(c)):
        A = a[i]
        j = idx[i]
        if not (A > 0 and fin(vah[i]) and fin(val[i])) or j == done:
            continue
        if not _ok(b, i, p):
            continue
        if p["filt"] and real[i] > 11 * 60:
            continue
        width = vah[i] - val[i]
        op = day_open[i]
        if op > vah[i] and c[i] < vah[i]:
            side, stop_lvl = -1, rh[i] + 0.05 * A
            tgt = val[i] - p["ext"] * width
        elif op < val[i] and c[i] > val[i]:
            side, stop_lvl = 1, rl[i] - 0.05 * A
            tgt = vah[i] + p["ext"] * width
        else:
            continue
        sd = F.stop_dist(side, c[i], stop_lvl, A)
        td = F.tgt(side, c[i], tgt)
        if p["filt"] and not (fin(td) and abs(side * (c[i] - (val[i] if side == -1 else vah[i]))) < sd):
            done = j
            continue
        _emit(out, i, side, sd, {"far": td})
        done = j
    return out


# --------------------------------------------------------------------------- #
# 2. 30-minute ORB + 20 EMA pullback
# --------------------------------------------------------------------------- #

def orb30_ema_pullback(bars, ctx, p):
    """"I backtested the 30 minute ORB with the 20 EMA on SPY and QQQ." A
    close above the 30-minute range; then a pullback that closes below the
    20 EMA, and a close back above it -> long; stop below the pullback low;
    target the high of day (`level`). His filters (`filt` 1): the pullback
    must stay well above the range (low > range high + 0.25 ATR) and the
    high of day must be no more than 2R away. Mirror. Raw 35% win; filtered
    75% win (half off at 0.5R) or 47% holding to HOD."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    orl = _or_levels(bars, ctx, 30)
    e20 = _ema(bars, ctx, 20)
    d = F.days(bars, ctx)
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    day = b["L"]["day"]
    state = {}                         # day -> [side, phase, pull_extreme]
    done = set()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        ohi, olo = orl["hi"][i], orl["lo"][i]
        if not (A > 0 and fin(ohi) and fin(e20[i])) or day[i] in done:
            continue
        s = state.get(day[i])
        if s is None:
            if c[i] > ohi:
                state[day[i]] = [1, 0, NAN]
            elif c[i] < olo:
                state[day[i]] = [-1, 0, NAN]
            continue
        side, phase, ext = s
        if phase == 0:
            if side * (c[i] - e20[i]) < 0:
                s[1], s[2] = 1, (l[i] if side == 1 else h[i])
            continue
        s[2] = min(ext, l[i]) if side == 1 else max(ext, h[i])
        if side * (c[i] - e20[i]) > 0:
            done.add(day[i])
            if not _ok(b, i, p):
                continue
            hod = rh[i] if side == 1 else rl[i]
            sd = F.stop_dist(side, c[i], s[2], A)
            td = F.tgt(side, c[i], hod)
            if p["filt"]:
                edge = ohi + 0.25 * A if side == 1 else olo - 0.25 * A
                if side * (s[2] - edge) <= 0 or not (fin(td) and td <= 2 * sd):
                    continue
            _emit(out, i, side, sd, {"far": td} if fin(td) else None)
    return out


# --------------------------------------------------------------------------- #
# 3. 15-minute ORB, full-body breakout, with his three filters
# --------------------------------------------------------------------------- #

def orb15_body(bars, ctx, p):
    """"I backtested the 15 minute opening range breakout on QQQ over almost
    two years" (5m, Jan 2025 - Sep 2026). The first candle closing outside
    the 09:30-09:45 range with the wick on the breakout side <= 25% of the
    candle -> enter at its close; stop just past the candle; 1:1; one a
    day. Raw: 432 trades, 51% win. His filters (`filt` 1): the breakout
    closes before 10:00; relative volume at time >= 1.5; the day gapped in
    the trade's direction (open beyond yesterday's close)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    orl = _or_levels(bars, ctx, 15)
    rv = _rvol_at_time(bars, ctx)
    d = F.days(bars, ctx)
    pdc, dop = d["pdc"].tolist(), d["open"].tolist()
    real, day = b["L"]["real"], b["L"]["day"]
    done = set()
    out = {}
    for i in range(len(c)):
        A = a[i]
        ohi, olo = orl["hi"][i], orl["lo"][i]
        if not (A > 0 and fin(ohi)) or day[i] in done:
            continue
        rng = h[i] - l[i]
        if not (rng > 0):
            continue
        side = 1 if c[i] > ohi else -1 if c[i] < olo else 0
        if not side:
            continue
        done.add(day[i])
        wick = (h[i] - c[i]) if side == 1 else (c[i] - l[i])
        if wick > 0.25 * rng or not _ok(b, i, p):
            continue
        if p["filt"]:
            if real[i] + b["bar"] > 10 * 60:
                continue
            if not (fin(rv[i]) and rv[i] >= 1.5):
                continue
            if not (fin(pdc[i]) and side * (dop[i] - pdc[i]) > 0):
                continue
        stop = l[i] if side == 1 else h[i]
        _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
    return out


# --------------------------------------------------------------------------- #
# 4. FVG retest in the first two hours
# --------------------------------------------------------------------------- #

def fvg_first2h(bars, ctx, p):
    """"Backtesting the FVG strategy on QQQ": 5m, first two hours of the
    session; price returns into a fair value gap and a candle closes back
    in the gap's direction -> enter; stop behind the gap's first candle;
    1:1. His filters (`filt` 1): only gaps formed before 10:30, and only
    days that have not swept yesterday's high or low. Raw 42% win; filtered
    63% (50% the year before)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    fv = F.fvg(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl = d["pdh"].tolist(), d["pdl"].tolist()
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    real, since, day = b["L"]["real"], b["L"]["since"], b["L"]["day"]
    gaps = []                          # (side, lo, hi, stop, made_at, day)
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        if since[i] > 120:
            gaps = [g for g in gaps if g[5] == day[i] and False]
            continue
        if fv["bull"][i] and day[i - 2] == day[i]:
            if not p["filt"] or real[i] + b["bar"] <= 10 * 60 + 30:
                gaps.append((1, fv["lo"][i], fv["hi"][i], l[i - 2], i, day[i]))
        if fv["bear"][i] and day[i - 2] == day[i]:
            if not p["filt"] or real[i] + b["bar"] <= 10 * 60 + 30:
                gaps.append((-1, fv["lo"][i], fv["hi"][i], h[i - 2], i, day[i]))
        if not (A > 0) or not _ok(b, i, p):
            continue
        if p["filt"] and fin(pdh[i]) and (rh[i] > pdh[i] or rl[i] < pdl[i]):
            continue
        for g in list(gaps):
            side, glo, ghi, stop, t0, gd = g
            if gd != day[i]:
                gaps.remove(g)
                continue
            if i <= t0 + 1:
                continue
            touched = l[i] <= ghi if side == 1 else h[i] >= glo
            if touched and side * (c[i] - o[i]) > 0:
                gaps.remove(g)
                _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
    return out


# --------------------------------------------------------------------------- #
# 5. trend-setting candle, 50% pullback
# --------------------------------------------------------------------------- #

def trendsetting_pullback(bars, ctx, p):
    """"Momentum pullback strategy turned $100 of risk into $400" (his "no
    ORB breakout" setup): a TREND-SETTING candle -- body >= `body` x ATR,
    closing in its outer 25%, on volume above the prior bar's -- then a
    pullback to its 50% retracement within 6 bars -> enter with the candle's
    direction; stop beyond the candle; his trade 4:1. `sma` 1 also needs
    the 200 SMA within 0.5 ATR of the 50% level (his confluence)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = b["L"]["v"]
    cc = F.base(bars, ctx)["c"]
    s200 = F.cached(ctx, ("nmk_sma", 200), lambda: F.sma(cc, 200).tolist())
    pend = None                       # (side, mid, stop, bar)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if pend is not None:
            side, mid, stop, t0 = pend
            if i - t0 > 6 or side * (c[i] - stop) < 0:
                pend = None
            elif (l[i] <= mid if side == 1 else h[i] >= mid) and _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
                pend = None
                continue
        rng = h[i] - l[i]
        body = abs(c[i] - o[i])
        if rng <= 0 or body < p["body"] * A or v[i] <= v[i - 1]:
            continue
        side = 1 if c[i] > o[i] else -1
        if side == 1 and (h[i] - c[i]) > 0.25 * rng:
            continue
        if side == -1 and (c[i] - l[i]) > 0.25 * rng:
            continue
        mid = (h[i] + l[i]) / 2.0
        if p["sma"] and not (fin(s200[i]) and abs(s200[i] - mid) <= 0.5 * A):
            continue
        pend = (side, mid, l[i] if side == 1 else h[i], i)
    return out


# --------------------------------------------------------------------------- #
# 6. ORB breakout, 50% retracement entry, 9 EMA runner
# --------------------------------------------------------------------------- #

def orb_body_50(bars, ctx, p):
    """"Trading SPY as a beginner. The whole strategy." / "QQQ trended all
    day": nothing before 09:50; a full-body candle (wick on the breakout
    side <= `wick` of its range; his 25%) closes outside the 15-minute range on rising volume; enter
    at the 50% retracement of that candle; stop below the candle (`stop`
    "candle") or below the range ("range"). Half off at 1:1, runner until a
    close back through the 9 EMA (`signal`)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    orl = _or_levels(bars, ctx, 15)
    v = b["L"]["v"]
    real, day = b["L"]["real"], b["L"]["day"]
    pend = None
    done = set()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        ohi, olo = orl["hi"][i], orl["lo"][i]
        if not (A > 0 and fin(ohi)) or day[i] in done:
            pend = None if (pend and pend[4] != day[i]) else pend
            continue
        if pend is not None:
            side, mid, stop, t0, pd = pend
            if pd != day[i] or i - t0 > 8:
                pend = None
            elif (l[i] <= mid if side == 1 else h[i] >= mid) and _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
                done.add(day[i])
                pend = None
                continue
        if real[i] < 9 * 60 + 50 or pend is not None:
            continue
        rng = h[i] - l[i]
        if rng <= 0 or v[i] <= v[i - 1]:
            continue
        side = 1 if c[i] > ohi else -1 if c[i] < olo else 0
        if not side:
            continue
        wick = (h[i] - c[i]) if side == 1 else (c[i] - l[i])
        if wick > p["wick"] * rng or side * (c[i] - o[i]) <= 0:
            continue
        stop = (l[i] if side == 1 else h[i]) if p["stop"] == "candle" else (olo if side == 1 else ohi)
        pend = (side, (h[i] + l[i]) / 2.0, stop, i, day[i])
    return out


def ema9_close_exit(index, bars, ctx, params, side):
    e9 = _ema(bars, ctx, 9)
    c = F.base(bars, ctx)["c"][index]
    return fin(e9[index]) and side * (c - e9[index]) < 0


# --------------------------------------------------------------------------- #
# 7. three green lights + the 9 EMA handrail
# --------------------------------------------------------------------------- #

def three_tf_9ema(bars, ctx, p):
    """"Day trading so simple your grandma could do it": three green lights
    -- the daily, 1h and 15m all making higher highs (here: the last
    completed daily close above its 5-day SMA, the close above the 1h-scale
    and 15m-scale EMAs, i.e. EMA(12 x bars/hour) and EMA(3 x bars/15m)
    rising) -- then a pullback to the 9 EMA and a candle closing back in
    the trend's direction -> enter; stop past the pullback low; 2:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    bar = b["bar"]
    n1h = max(2, int(round(12 * 60 / bar)))
    n15 = max(2, int(round(3 * 15 / bar)))
    e1h, e15, e9 = _ema(bars, ctx, n1h), _ema(bars, ctx, n15), _ema(bars, ctx, 9)
    d = F.days(bars, ctx)
    dc = d["d_close"]
    sma5 = F.sma(numpy.asarray(dc, dtype=float), 5)
    idx = d["day_idx"].tolist()
    lowk = F.rolling_min(b["l"], p["k"]).tolist()
    highk = F.rolling_max(b["h"], p["k"]).tolist()
    out = {}
    for i in range(p["k"], len(c)):
        A = a[i]
        j = idx[i] - 1
        if not (A > 0 and j >= 5 and fin(e1h[i - 1]) and fin(e9[i])) or not _ok(b, i, p):
            continue
        daily = 1 if dc[j] > sma5[j] else -1 if dc[j] < sma5[j] else 0
        h1 = 1 if c[i] > e1h[i] and e1h[i] > e1h[i - 1] else -1 if c[i] < e1h[i] and e1h[i] < e1h[i - 1] else 0
        m15 = 1 if c[i] > e15[i] and e15[i] > e15[i - 1] else -1 if c[i] < e15[i] and e15[i] < e15[i - 1] else 0
        if not (daily == h1 == m15 != 0):
            continue
        side = daily
        touched = l[i] <= e9[i] if side == 1 else h[i] >= e9[i]
        if touched and side * (c[i] - o[i]) > 0 and side * (c[i] - e9[i]) > 0:
            lvl = lowk[i] if side == 1 else highk[i]
            _emit(out, i, side, F.stop_dist(side, c[i], lvl, A))
    return out


SPECS = {
    "nmk_value_area_80": (value_area_80, lambda g: {
        "filt": (0, 1), "ext": (0.0, 0.25), "exit_mode": ("level", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "nmk_orb30_ema_pullback": (orb30_ema_pullback, lambda g: {
        "filt": (0, 1), "exit_mode": ("level", "rr_0.5", "rr_1"),
        "last_entry_minute": g["last"]}, True, None),
    "nmk_orb15_body": (orb15_body, lambda g: {
        "filt": (0, 1), "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "nmk_fvg_first2h": (fvg_first2h, lambda g: {
        "filt": (0, 1), "exit_mode": ("rr_1", "rr_2", "rr_1.5"),
        "last_entry_minute": g["last"]}, True, None),
    "nmk_trendsetting_pullback": (trendsetting_pullback, lambda g: {
        "body": (1.0, 1.5), "sma": (0, 1), "exit_mode": ("rr_4", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
    "nmk_orb_body_50": (orb_body_50, lambda g: {
        "stop": ("candle", "range"), "wick": (0.25, 0.4), "exit_mode": ("signal", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, True, ema9_close_exit),
    "nmk_three_tf_9ema": (three_tf_9ema, lambda g: {
        "k": (3, 6), "exit_mode": ("rr_2", "rr_1", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ("stop",)
