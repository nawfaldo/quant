"""Trading Mythbusters' YouTube (@TradingMythbusters) tests, as
`cfd_tt_families` families.

Source: all 16 videos (2025-26), rules read from the video descriptions
2026-10-03. Each video turns a popular idea into an algorithm and backtests
it on Binance futures, US stocks, CME futures and FX from 1m to 1d; most
fail. Map: `results/tiktok/tradingmythbusters_strategy_notes.md`.

Not built here (already covered by other creators' families): MA crossover,
MACD, RSI divergence, trendline break, head and shoulders, order blocks,
Fibonacci retracement. The OpenClaw video is a workflow, not a rule.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ohlc(bars, ctx):
    b = F.base(bars, ctx)
    return b, b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]


def _ema_arr(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), NAN)
    a = 2.0 / (n + 1)
    v = NAN
    for i, xi in enumerate(x):
        if not fin(xi):
            out[i] = v
            continue
        v = xi if not fin(v) else v + a * (xi - v)
        out[i] = v
    return out


# --------------------------------------------------------------------------- #
# Elliott wave: 5 waves, then a break of the 2-4 line
# --------------------------------------------------------------------------- #

def elliott_24_break(bars, ctx, p):
    """BxnioVonZFo. Confirmed `k`-bar pivots alternating low/high give six
    points 0..5. A 5-wave move UP: 1 > 0, 2 > 0, 3 > 1, 4 > 1 (no overlap),
    5 > 3, wave 3 not the shortest of 1/3/5. After wave 5 is confirmed, the
    line through 2 and 4 (extended) is broken by a close -> short (mirror
    long after a 5-wave move down). Entries refused once price is more than
    `max_run` x (5 - 0) away from wave 5. Stop beyond the wave-5 extreme;
    `level` target = 61.8% retracement of 0 -> 5 (his default TP)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    n = len(c)
    hi_i, lo_i = pv["h_i"], pv["l_i"]
    seq = []                   # alternating (index, price, +1 high / -1 low)
    last_h, last_l = -1, -1
    active = None              # (side, i2, p2, i4, p4, p5, p0)
    out = {}
    for i in range(n):
        new = []
        if hi_i[i] >= 0 and hi_i[i] != last_h:
            last_h = int(hi_i[i])
            new.append((last_h, h[last_h], 1))
        if lo_i[i] >= 0 and lo_i[i] != last_l:
            last_l = int(lo_i[i])
            new.append((last_l, l[last_l], -1))
        for pt in sorted(new):
            if seq and seq[-1][2] == pt[2]:
                # same kind twice: keep the more extreme one
                if (pt[2] == 1 and pt[1] >= seq[-1][1]) or (pt[2] == -1 and pt[1] <= seq[-1][1]):
                    seq[-1] = pt
            else:
                seq.append(pt)
            if len(seq) > 6:
                seq.pop(0)
            if len(seq) == 6:
                P = [s[1] for s in seq]
                d = seq[5][2]              # +1: wave 5 is a high (move up)
                s = d
                w1, w3, w5 = s * (P[1] - P[0]), s * (P[3] - P[2]), s * (P[5] - P[4])
                ok = (w1 > 0 and w3 > 0 and w5 > 0
                      and s * (P[2] - P[0]) > 0 and s * (P[4] - P[1]) > 0
                      and s * (P[5] - P[3]) > 0 and w3 >= min(w1, w5))
                if ok:
                    active = (-d, seq[2][0], P[2], seq[4][0], P[4], P[5], P[0])
        if active is None:
            continue
        side, i2, p2, i4, p4, p5, p0 = active
        A = a[i]
        if not (A > 0) or i4 == i2:
            continue
        line = p4 + (p4 - p2) / (i4 - i2) * (i - i4)
        span = abs(p5 - p0)
        if side * (p5 - c[i]) > 0 or abs(c[i] - p5) > p["max_run"] * span:
            active = None          # the extreme failed or price ran away
            continue
        if side * (c[i] - line) > 0 and side * (c[i - 1] - (line - (p4 - p2) / (i4 - i2))) <= 0:
            tp = p5 + side * 0.618 * span
            out[i] = (side, F.stop_dist(side, c[i], p5, A), {"far": F.tgt(side, c[i], tp)})
            active = None
    return out


# --------------------------------------------------------------------------- #
# Doji + two confirmation candles
# --------------------------------------------------------------------------- #

def doji_confirm(bars, ctx, p):
    """9GVt-psZlEc. A doji (body <= `doji` x range), then two consecutive
    candles in one direction -> enter that way at the next open. "Fixed per
    trade stop": `stop` x ATR. Exits on time / R."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    out = {}
    for i in range(3, len(c)):
        j = i - 2
        rng = h[j] - l[j]
        A = a[i]
        if not (A > 0) or not (rng > 0) or abs(c[j] - o[j]) > p["doji"] * rng:
            continue
        if c[i - 1] > o[i - 1] and c[i] > o[i]:
            out[i] = (1, p["stop"] * A, None)
        elif c[i - 1] < o[i - 1] and c[i] < o[i]:
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# London session body breakout (Jooviers Gems)
# --------------------------------------------------------------------------- #

def london_body_break(bars, ctx, p):
    """K18iaIlkA_w (testing Jooviers Gems' "super easy scalping strategy").
    The high/low of the `win` window; after it, the first candle whose BODY
    closes above the high (both open and close above) -> long, below the low
    -> short. One trade a day; no entries later than `cut` minutes after the
    first bar past the window (the window end, or the cash open on markets
    whose bars start later). Stop at the breakout candle's other end (floored), 2:1 target."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    cw = F.clock_window(bars, ctx, p["win"])
    end = F.WINDOWS[p["win"]][1]
    minute, day = b["L"]["real"], b["L"]["day"]
    done, t0_day, t0 = None, None, 0
    out = {}
    for i in range(len(c)):
        hi, lo, A = cw["hi"][i], cw["lo"][i], a[i]
        if not (fin(hi) and A > 0) or day[i] == done:
            continue
        m = minute[i]
        if m < end:
            continue
        if t0_day != day[i]:               # first tradable bar after the window
            t0_day, t0 = day[i], m
        if m - t0 > p["cut"]:
            continue
        if min(o[i], c[i]) > hi:
            out[i] = (1, F.stop_dist(1, c[i], l[i], A), None)
            done = day[i]
        elif max(o[i], c[i]) < lo:
            out[i] = (-1, F.stop_dist(-1, c[i], h[i], A), None)
            done = day[i]
    return out


# --------------------------------------------------------------------------- #
# SuperTrend + DEMA, Fibonacci Bollinger exit
# --------------------------------------------------------------------------- #

def _dema(bars, ctx, n):
    def build():
        c = F.base(bars, ctx)["c"]
        e1 = _ema_arr(c, n)
        e2 = _ema_arr(e1, n)
        return (2 * e1 - e2).tolist()
    return F.cached(ctx, ("tmb_dema", n), build)


def _fib_bb(bars, ctx, n=20):
    """Fibonacci Bollinger Bands: VWMA(n) +/- 1.618 / 2.618 x stdev(n)."""
    def build():
        b = F.base(bars, ctx)
        c, v = b["c"], b["v"].astype(float)
        out = {"mid": numpy.full(len(c), NAN), "sd": numpy.full(len(c), NAN)}
        if len(c) >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            wc, wv = sliding_window_view(c, n), sliding_window_view(v, n)
            vs = wv.sum(axis=1)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                vw = numpy.where(vs > 0, (wc * wv).sum(axis=1) / vs, wc.mean(axis=1))
            out["mid"][n - 1:] = vw
            out["sd"][n - 1:] = wc.std(axis=1)
        return out
    return F.cached(ctx, ("tmb_fbb", n), build)


def supertrend_dema(bars, ctx, p):
    """qf2moN0UhlI. SuperTrend (10, `mult`) turns up and the close is above
    DEMA(`dema`) -> long (mirror short). Exits: SuperTrend flips back
    (`signal`) or price reaches the Fibonacci Bollinger band (`level` = the
    1.618 band); stop at the SuperTrend line (floored)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    st = F.supertrend(bars, ctx, 10, p["mult"])
    de = _dema(bars, ctx, p["dema"])
    fb = _fib_bb(bars, ctx)
    out = {}
    for i in range(len(c)):
        f, A = st["flip"][i], a[i]
        if not f or not (A > 0) or not fin(de[i]):
            continue
        side = int(f)
        if side * (c[i] - de[i]) <= 0:
            continue
        band = fb["mid"][i] + side * 1.618 * fb["sd"][i]
        out[i] = (side, F.stop_dist(side, c[i], st["line"][i], A), {"far": F.tgt(side, c[i], band)})
    return out


def supertrend_flip_exit(index, bars, ctx, params, side):
    d = F.supertrend(bars, ctx, 10, params["mult"])["dir"][index]
    return d == -side


# --------------------------------------------------------------------------- #
# ADX + DMI + OBV
# --------------------------------------------------------------------------- #

def _obv(bars, ctx, n):
    def build():
        b = F.base(bars, ctx)
        c, v = b["c"], b["v"].astype(float)
        sgn = numpy.sign(numpy.diff(c, prepend=c[0]))
        obv = numpy.cumsum(sgn * v)
        return {"obv": obv, "sig": _ema_arr(obv, n)}
    return F.cached(ctx, ("tmb_obv", n), build)


def adx_dmi_obv(bars, ctx, p):
    """f30GA8PJFjk. +DI crosses above -DI while ADX(14) >= `adx` and OBV is
    above its EMA(`obv`) -> long (mirror short). Exit (`signal`): DI cross
    back or OBV crossing its EMA against the trade. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    d = F.adx(bars, ctx, 14)
    ob = _obv(bars, ctx, p["obv"])
    pdi, mdi, ax = d["pdi"], d["mdi"], d["adx"]
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(ax[i]) and ax[i] >= p["adx"]):
            continue
        up = pdi[i] > mdi[i] and pdi[i - 1] <= mdi[i - 1]
        dn = mdi[i] > pdi[i] and mdi[i - 1] <= pdi[i - 1]
        if up and ob["obv"][i] > ob["sig"][i]:
            out[i] = (1, p["stop"] * A, None)
        elif dn and ob["obv"][i] < ob["sig"][i]:
            out[i] = (-1, p["stop"] * A, None)
    return out


def adx_dmi_obv_exit(index, bars, ctx, params, side):
    d = F.adx(bars, ctx, 14)
    ob = _obv(bars, ctx, params["obv"])
    di = d["pdi"][index] - d["mdi"][index]
    return side * di < 0 or side * (ob["obv"][index] - ob["sig"][index]) < 0


# --------------------------------------------------------------------------- #
# VWAP rejection
# --------------------------------------------------------------------------- #

def vwap_reject(bars, ctx, p):
    """59W3wlo33Is ("VWAP bounce off resistance"). Downtrend (close under
    EMA(`ema`)); the candle's high reaches session VWAP from below and it
    closes back under it as a bearish candle -> short. Mirrored for longs
    (support from above in an uptrend). Stop beyond the candle (floored)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx)
    e = F.ema(bars, ctx, p["ema"])
    out = {}
    for i in range(1, len(c)):
        A, v = a[i], vw[i]
        if not (A > 0 and fin(v) and fin(e[i])):
            continue
        if c[i] < e[i] and c[i - 1] < v and h[i] >= v and c[i] < v and c[i] < o[i]:
            out[i] = (-1, F.stop_dist(-1, c[i], h[i], A), None)
        elif c[i] > e[i] and c[i - 1] > v and l[i] <= v and c[i] > v and c[i] > o[i]:
            out[i] = (1, F.stop_dist(1, c[i], l[i], A), None)
    return out


# --------------------------------------------------------------------------- #
# Heikin Ashi doji + Stochastic
# --------------------------------------------------------------------------- #

def _heikin(bars, ctx):
    def build():
        b = F.base(bars, ctx)
        o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        n = len(c)
        hc = (o + h + l + c) / 4.0
        ho = numpy.empty(n)
        if n:
            ho[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ho[i] = (ho[i - 1] + hc[i - 1]) / 2.0
        hh = numpy.maximum(h, numpy.maximum(ho, hc))
        hl = numpy.minimum(l, numpy.minimum(ho, hc))
        return {"o": ho.tolist(), "c": hc.tolist(), "h": hh.tolist(), "l": hl.tolist()}
    return F.cached(ctx, "tmb_ha", build)


def ha_stoch_doji(bars, ctx, p):
    """q_dOVESpYLI. A Heikin Ashi doji (HA body <= `doji` x HA range) while
    Stochastic %K(14,3) is under `os` (over 100 - `os`), then the next HA
    candle turns green (red) -> long (short): the exhaustion reversal. Stop
    beyond the doji's real low/high (floored)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    ha = _heikin(bars, ctx)
    k = F.stochastic(bars, ctx, 14, 3)
    k = k["k"] if isinstance(k, dict) else k
    out = {}
    for i in range(1, len(c)):
        j = i - 1
        A = a[i]
        rng = ha["h"][j] - ha["l"][j]
        if not (A > 0 and rng > 0 and fin(k[j])):
            continue
        if abs(ha["c"][j] - ha["o"][j]) > p["doji"] * rng:
            continue
        if k[j] <= p["os"] and ha["c"][i] > ha["o"][i]:
            out[i] = (1, F.stop_dist(1, c[i], min(l[j], l[i]), A), None)
        elif k[j] >= 100 - p["os"] and ha["c"][i] < ha["o"][i]:
            out[i] = (-1, F.stop_dist(-1, c[i], max(h[j], h[i]), A), None)
    return out


# --------------------------------------------------------------------------- #
# RSI + Bollinger, exit at the midline
# --------------------------------------------------------------------------- #

def _bb(bars, ctx, n=20, dev=2.0):
    def build():
        c = F.base(bars, ctx)["c"]
        mid = numpy.full(len(c), NAN)
        sd = numpy.full(len(c), NAN)
        if len(c) >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(c, n)
            mid[n - 1:], sd[n - 1:] = w.mean(axis=1), w.std(axis=1)
        return {"mid": mid, "up": mid + dev * sd, "lo": mid - dev * sd}
    return F.cached(ctx, ("tmb_bb", n, dev), build)


def rsi_bb_mid(bars, ctx, p):
    """j2ESnjhT2no. RSI(14) under `os` and the close under the lower
    Bollinger Band (20, 2) -> long; exits at the midline (`level`) "as RSI
    recovers". Mirror short. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, 14)
    bb = _bb(bars, ctx)
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(r[i]) and fin(bb["mid"][i])):
            continue
        if r[i] < p["os"] and c[i] < bb["lo"][i]:
            out[i] = (1, p["stop"] * A, {"far": F.tgt(1, c[i], bb["mid"][i])})
        elif r[i] > 100 - p["os"] and c[i] > bb["up"][i]:
            out[i] = (-1, p["stop"] * A, {"far": F.tgt(-1, c[i], bb["mid"][i])})
    return out


SPECS = {
    "tmb_elliott_24_break": (elliott_24_break, lambda g: {
        "k": (3, 5), "max_run": (0.382, 0.618),
        "exit_mode": ("level", "rr_2", "trail_0.25"), "last_entry_minute": g["last"]}, False, None),
    "tmb_doji_confirm": (doji_confirm, lambda g: {
        "doji": (0.05, 0.1), "stop": (1.5, 2.5),
        "exit_mode": ("rr_1", "rr_2", "time_10"), "last_entry_minute": g["last"]}, False, None),
    "tmb_london_body_break": (london_body_break, lambda g: {
        "win": ("london_h1", "london"), "cut": (60, 180),
        "exit_mode": ("rr_2", "rr_1"), "last_entry_minute": g["last"]}, True, None),
    "tmb_supertrend_dema": (supertrend_dema, lambda g: {
        "mult": (2.0, 3.0), "dema": (100, 200),
        "exit_mode": ("signal", "level", "trail_0.5"), "last_entry_minute": g["last"]}, False,
        supertrend_flip_exit),
    "tmb_adx_dmi_obv": (adx_dmi_obv, lambda g: {
        "adx": (20, 25), "obv": (20, 50), "stop": (2.0,),
        "exit_mode": ("signal", "rr_2", "trail_0.5"), "last_entry_minute": g["last"]}, False,
        adx_dmi_obv_exit),
    "tmb_vwap_reject": (vwap_reject, lambda g: {
        "ema": (50, 200),
        "exit_mode": ("rr_1.5", "rr_2", "time_12"), "last_entry_minute": g["last"]}, False, None),
    "tmb_ha_stoch_doji": (ha_stoch_doji, lambda g: {
        "doji": (0.1, 0.2), "os": (20, 30),
        "exit_mode": ("rr_1", "rr_2", "time_10"), "last_entry_minute": g["last"]}, False, None),
    "tmb_rsi_bb_mid": (rsi_bb_mid, lambda g: {
        "os": (25, 30), "stop": (1.5, 3.0),
        "exit_mode": ("level", "rr_1", "time_20"), "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ("win",)
