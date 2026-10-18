"""CodeTrading's YouTube (@CodeTradingCafe) strategies, as `cfd_tt_families`
families.

Source: 350 videos listed 2026-10-03; 60 English strategy videos screened by
description, 34 read from the captions (incl. 9 recent uploads only titled in
Indonesian). He codes each rule in Python (backtesting.py) on EURUSD / gold /
stocks and grids the ATR stop and the TP:SL ratio. Map:
`results/tiktok/codetrading_strategy_notes.md`.

"Trend" in his code is usually "the last `back` candles all fully above (or
below) the moving average" (open AND close on one side) -- `_side_run`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research import tt_tradingrush as TR

NAN = float("nan")
fin = math.isfinite


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ohlc(bars, ctx):
    b = F.base(bars, ctx)
    return b, b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]


def _side_run(bars, ctx, line, back, key):
    """+1 where the last `back` candles (incl. this one) are fully above
    `line` (open and close), -1 fully below, else 0."""
    def build():
        b = F.base(bars, ctx)
        lo = numpy.minimum(b["o"], b["c"])
        hi = numpy.maximum(b["o"], b["c"])
        with numpy.errstate(invalid="ignore"):
            line_a = numpy.asarray(line, dtype=float)
            up = (lo > line_a).astype(float)
            dn = (hi < line_a).astype(float)
        out = numpy.zeros(len(lo), dtype=numpy.int8)
        if len(lo) >= back:
            cu = numpy.convolve(up, numpy.ones(back), "valid")
            cd = numpy.convolve(dn, numpy.ones(back), "valid")
            out[back - 1:][cu == back] = 1
            out[back - 1:][cd == back] = -1
        return out.tolist()
    return F.cached(ctx, ("ct_run",) + key + (back,), build)


def _ema(bars, ctx, n):
    return F.ema(bars, ctx, n)


def _bb(bars, ctx, n, dev):
    def build():
        c = F.base(bars, ctx)["c"]
        mid = numpy.full(len(c), NAN)
        sd = numpy.full(len(c), NAN)
        if len(c) >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(c, n)
            mid[n - 1:], sd[n - 1:] = w.mean(axis=1), w.std(axis=1)
        return {"mid": mid.tolist(), "up": (mid + dev * sd).tolist(), "lo": (mid - dev * sd).tolist()}
    return F.cached(ctx, ("ct_bb", n, dev), build)


def _rsi(bars, ctx, n):
    return F.rsi(bars, ctx, n).tolist()


# --------------------------------------------------------------------------- #
# 1. Hull 16/65 + RSI + daily linear regression (CoinQuant clone)
# --------------------------------------------------------------------------- #

def _hma(c, n):
    return TR._wma(2 * TR._wma(c, n // 2) - TR._wma(c, n), int(math.sqrt(n)))


def _daily_linreg_ok(bars, ctx, n):
    """Per bar: +1 if the last completed session closed above the value of a
    least-squares line fitted to the last `n` session closes, -1 below."""
    def build():
        d = F.days(bars, ctx)
        dc = d["d_close"]
        k = len(dc)
        sig = numpy.zeros(k)
        x = numpy.arange(n, dtype=float)
        for j in range(n - 1, k):
            y = dc[j - n + 1:j + 1]
            if not numpy.isfinite(y).all():
                continue
            sl, ic = numpy.polyfit(x, y, 1)
            sig[j] = 1 if y[-1] > ic + sl * (n - 1) else -1
        idx = d["day_idx"]
        out = numpy.zeros(len(idx))
        ok = idx >= 1
        out[ok] = sig[idx[ok] - 1]
        return out.tolist()
    return F.cached(ctx, ("ct_lr", n), build)


def _hull_pair(bars, ctx):
    def build():
        c = F.base(bars, ctx)["c"]
        return _hma(c, 16).tolist(), _hma(c, 65).tolist()
    return F.cached(ctx, "ct_hull", build)


def hull_rsi_linreg(bars, ctx, p):
    """b6W3GsDZ_Kg / aOIRo4Q7qZE (4H, long only in the video). HMA 16 crosses
    above HMA 65, RSI 14 above `rsi`, and price above the daily linear-
    regression curve (`lr` sessions) -> long; exit when HMA 16 crosses back
    below ("the crossover is the stop", `signal`). Mirrored for shorts. The
    engine still needs a stop: `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    f, s = _hull_pair(bars, ctx)
    r = _rsi(bars, ctx, 14)
    lr = _daily_linreg_ok(bars, ctx, p["lr"])
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(f[i - 1]) and fin(s[i - 1]) and fin(r[i])):
            continue
        if f[i] > s[i] and f[i - 1] <= s[i - 1] and r[i] > p["rsi"] and lr[i] > 0:
            out[i] = (1, p["stop"] * A, None)
        elif f[i] < s[i] and f[i - 1] >= s[i - 1] and r[i] < 100 - p["rsi"] and lr[i] < 0:
            out[i] = (-1, p["stop"] * A, None)
    return out


def hull_exit(index, bars, ctx, params, side):
    f, s = _hull_pair(bars, ctx)
    return fin(f[index]) and side * (f[index] - s[index]) < 0


# --------------------------------------------------------------------------- #
# 2. Pin bar + EMA 50/200 (+ swing-level filter)
# --------------------------------------------------------------------------- #

def pin_bar_trend(bars, ctx, p):
    """z6OMf-HB5qE, XDJidg6s2DU (gold 1H). Pin bar: the long wick >= 2x the
    body, body < 33% of the range, the short wick < the body. Uptrend = EMA50
    > EMA200 and close > EMA50 -> bullish pins only (mirror). `sr` > 0 also
    needs the pin's wick within `sr` x ATR of the latest confirmed 5-bar
    swing low (support) / high (resistance); 0 = off. Stop `stop` x ATR
    beyond the pin; 2R; closed after 50 bars."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e50, e200 = _ema(bars, ctx, 50), _ema(bars, ctx, 200)
    pv = F.pivots(bars, ctx, 5)
    out = {}
    for i in range(len(c)):
        A = a[i]
        rng = h[i] - l[i]
        if not (A > 0 and rng > 0 and fin(e200[i])):
            continue
        bd = abs(c[i] - o[i])
        up_w = h[i] - max(o[i], c[i])
        lo_w = min(o[i], c[i]) - l[i]
        if bd >= 0.33 * rng or bd <= 0:
            continue
        if lo_w >= 2 * bd and up_w < bd and e50[i] > e200[i] and c[i] > e50[i]:
            if p["sr"] > 0 and not (fin(pv["l"][i]) and abs(l[i] - pv["l"][i]) <= p["sr"] * A):
                continue
            out[i] = (1, c[i] - l[i] + p["stop"] * A, None)
        elif up_w >= 2 * bd and lo_w < bd and e50[i] < e200[i] and c[i] < e50[i]:
            if p["sr"] > 0 and not (fin(pv["h"][i]) and abs(h[i] - pv["h"][i]) <= p["sr"] * A):
                continue
            out[i] = (-1, h[i] - c[i] + p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 3. MACD + EMA trend pullback
# --------------------------------------------------------------------------- #

def macd_ema_pullback(bars, ctx, p):
    """Ik0ILD0gPHA. Uptrend: the last 5 candles fully above EMA(`ema`). A
    retracement: the MACD histogram dipped below -`thr` x ATR within the last
    7 candles. Trigger: MACD crosses above its signal with both below zero ->
    long at the close. Mirror short. His FX: 5.5%/yr, Sharpe 1.45. Stop
    `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e = _ema(bars, ctx, p["ema"])
    tr = _side_run(bars, ctx, e, 5, ("ema", p["ema"]))
    m = F.macd(bars, ctx)
    ln, sg, hs = m["macd"], m["signal"], m["hist"]
    out = {}
    for i in range(8, len(c)):
        A = a[i]
        if not (A > 0) or not tr[i] or not (fin(ln[i - 1]) and fin(sg[i - 1])):
            continue
        thr = p["thr"] * A
        if tr[i] > 0 and ln[i] > sg[i] and ln[i - 1] <= sg[i - 1] and ln[i] < 0 and sg[i] < 0:
            if min(hs[i - 7:i + 1]) < -thr:
                out[i] = (1, p["stop"] * A, None)
        elif tr[i] < 0 and ln[i] < sg[i] and ln[i - 1] >= sg[i - 1] and ln[i] > 0 and sg[i] > 0:
            if max(hs[i - 7:i + 1]) > thr:
                out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 4. VWAP trend + Bollinger + RSI scalp
# --------------------------------------------------------------------------- #

def vwap_bb_rsi(bars, ctx, p):
    """RbQaARxEW9o (5m; "~300% over 3 years, Sharpe 1.65"). The candles of the
    last `back` minutes fully above session VWAP = uptrend; a close below the lower
    BB(14, 2) with RSI(16) < 45 -> long (mirror: above the upper band, RSI >
    55). `back` is in minutes (his 15 five-minute candles = 75). SL `stop` x ATR(7)-ish (the 14-bar ATR here), TP = ratio x SL."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx)
    back = max(2, round(p["back"] / b["bar"]))   # `back` is in minutes (his 15 x 5m)
    tr = _side_run(bars, ctx, vw, back, ("vwap",))
    bb = _bb(bars, ctx, 14, 2.0)
    r = _rsi(bars, ctx, 16)
    out = {}
    for i in range(1, len(c)):
        A, t = a[i], tr[i - 1]          # the trend run ends on the candle before
        if not (A > 0 and t and fin(bb["lo"][i]) and fin(r[i])):
            continue
        if t > 0 and c[i] < bb["lo"][i] and r[i] < 45:
            out[i] = (1, p["stop"] * A, None)
        elif t < 0 and c[i] > bb["up"][i] and r[i] > 55:
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 5. RSI(2) extreme with the 200 EMA
# --------------------------------------------------------------------------- #

def rsi_scalp_ema(bars, ctx, p):
    """MzEX4XumtEE ("250% in 3 years"). Close above EMA 200 and RSI(`n`)
    below `lvl` -> long at the close; below EMA 200 and RSI above 100-`lvl`
    -> short. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e = _ema(bars, ctx, 200)
    r = _rsi(bars, ctx, p["n"])
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(e[i]) and fin(r[i])):
            continue
        if c[i] > e[i] and r[i] < p["lvl"]:
            out[i] = (1, p["stop"] * A, None)
        elif c[i] < e[i] and r[i] > 100 - p["lvl"]:
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 6. RSI(3) + ADX(5) + engulfing confirmation
# --------------------------------------------------------------------------- #

def rsi_adx_engulf(bars, ctx, p):
    """NOBV08Im56Y (5m scalp "advertised as profitable"). Trend: the last
    `back` candles fully above EMA 50. Signal candle: RSI(3) < 20 and ADX(5)
    > 30. The NEXT candle is a bullish engulfing -> long at its close (mirror
    short). Stop at the lowest low of the last 3 candles (floored)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e = _ema(bars, ctx, 50)
    tr = _side_run(bars, ctx, e, p["back"], ("ema", 50))
    r = _rsi(bars, ctx, 3)
    ax = F.adx(bars, ctx, 5)["adx"]
    out = {}
    for i in range(3, len(c)):
        j = i - 1
        A = a[i]
        if not (A > 0 and fin(r[j]) and fin(ax[j]) and ax[j] > p["adx"]):
            continue
        if tr[j] > 0 and r[j] < 20 and c[i] > o[i] and c[j] < o[j] and c[i] >= o[j] and o[i] <= c[j]:
            out[i] = (1, F.stop_dist(1, c[i], min(l[i - 2:i + 1]), A), None)
        elif tr[j] < 0 and r[j] > 80 and c[i] < o[i] and c[j] > o[j] and c[i] <= o[j] and o[i] >= c[j]:
            out[i] = (-1, F.stop_dist(-1, c[i], max(h[i - 2:i + 1]), A), None)
    return out


# --------------------------------------------------------------------------- #
# 7. three EMAs aligned + a candle through the fast EMA (+ no tail)
# --------------------------------------------------------------------------- #

def ema3_fast_cross(bars, ctx, p):
    """NoNucP_IVB0 / ybmep_u5MeU / ZoCpme03sVI (EURUSD 15m). EMA fast > mid >
    slow (`fast`, 2x, 3x... here 50/100/150 scaled by `fast`) with all three
    slopes positive over 10 candles = uptrend; a candle opening below and
    closing above the fast EMA -> long (mirror). `tail` < 1: the candle's
    lower wick must be <= `tail` x its range ("no or very short tail");
    1 = off. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    n1 = p["fast"]
    e1, e2, e3 = _ema(bars, ctx, n1), _ema(bars, ctx, 2 * n1), _ema(bars, ctx, 3 * n1)
    out = {}
    for i in range(10, len(c)):
        A = a[i]
        rng = h[i] - l[i]
        if not (A > 0 and rng > 0 and fin(e3[i - 10])):
            continue
        up = e1[i] > e2[i] > e3[i] and e1[i] > e1[i - 10] and e2[i] > e2[i - 10] and e3[i] > e3[i - 10]
        dn = e1[i] < e2[i] < e3[i] and e1[i] < e1[i - 10] and e2[i] < e2[i - 10] and e3[i] < e3[i - 10]
        if up and o[i] < e1[i] < c[i] and (min(o[i], c[i]) - l[i]) <= p["tail"] * rng:
            out[i] = (1, p["stop"] * A, None)
        elif dn and o[i] > e1[i] > c[i] and (h[i] - max(o[i], c[i])) <= p["tail"] * rng:
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 8. Alligator order broken by a close through the teeth
# --------------------------------------------------------------------------- #

def alligator_break(bars, ctx, p):
    """4rbGvaiD9Wk ("the best Gator strategy" claim). Lips > teeth > jaw for
    the last `back` candles (uptrend), then a candle closes below the teeth
    -> SHORT (the trend is broken); mirror long. Stop beyond the last 10-bar
    swing (floored)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    jaw, teeth, lips = (x.tolist() for x in TR._alligator(bars, ctx))
    k = p["back"]
    hi10 = F.rolling_max(b["h"], 10).tolist()
    lo10 = F.rolling_min(b["l"], 10).tolist()
    out = {}
    for i in range(k + 1, len(c)):
        A = a[i]
        if not (A > 0 and fin(jaw[i - k - 1])):
            continue
        rng = range(i - k, i)
        if c[i] < teeth[i] and c[i - 1] >= teeth[i - 1] and all(lips[j] > teeth[j] > jaw[j] for j in rng):
            out[i] = (-1, F.stop_dist(-1, c[i], hi10[i], A), None)
        elif c[i] > teeth[i] and c[i - 1] <= teeth[i - 1] and all(lips[j] < teeth[j] < jaw[j] for j in rng):
            out[i] = (1, F.stop_dist(1, c[i], lo10[i], A), None)
    return out


# --------------------------------------------------------------------------- #
# 9. trend + close outside the Bollinger band against it (Rayner Teo)
# --------------------------------------------------------------------------- #

def trend_bb_dip(bars, ctx, p):
    """wPOPRu8587s / f6Nh4guZPRs (Rayner Teo's BB trend strategy, daily and
    4H) and IfoZaCGTJ_Q (EMA + BB). The last 6 candles fully above the
    `ma` EMA = uptrend; a close below the lower BB(20, `dev`) -> long (mirror
    short). Exit (`signal`): RSI(2) above `rsi_x` for longs (Rayner: 50; his
    optimised 65), below 100 - `rsi_x` for shorts. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e = _ema(bars, ctx, p["ma"])
    tr = _side_run(bars, ctx, e, 6, ("ema", p["ma"]))
    bb = _bb(bars, ctx, 20, p["dev"])
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and tr[i] and fin(bb["lo"][i])):
            continue
        if tr[i] > 0 and c[i] < bb["lo"][i]:
            out[i] = (1, p["stop"] * A, None)
        elif tr[i] < 0 and c[i] > bb["up"][i]:
            out[i] = (-1, p["stop"] * A, None)
    return out


def rsi2_exit(index, bars, ctx, params, side):
    r = _rsi(bars, ctx, 2)[index]
    x = params["rsi_x"]
    return fin(r) and (r > x if side == 1 else r < 100 - x)


# --------------------------------------------------------------------------- #
# 10. Ichimoku cloud retracement
# --------------------------------------------------------------------------- #

def ichimoku_retrace(bars, ctx, p):
    """zRVkam1pVrs (4H, "~40%/yr on some assets"). Trend: the last 6 candles
    fully above EMA(`ema`). At least `share` of the previous 10 candles
    fully above the cloud; the current candle OPENS inside the cloud and
    closes above it -> long. Mirror short. Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    e = _ema(bars, ctx, p["ema"])
    tr = _side_run(bars, ctx, e, 6, ("ema", p["ema"]))
    _, _, sa, sb = TR._ichimoku(bars, ctx)
    top = numpy.fmax(sa, sb).tolist()
    bot = numpy.fmin(sa, sb).tolist()
    need = p["share"]
    out = {}
    for i in range(11, len(c)):
        A = a[i]
        if not (A > 0 and tr[i] and fin(top[i]) and fin(top[i - 10])):
            continue
        if tr[i] > 0 and bot[i] <= o[i] <= top[i] and c[i] > top[i]:
            if sum(min(o[j], c[j]) > top[j] for j in range(i - 10, i)) >= need:
                out[i] = (1, p["stop"] * A, None)
        elif tr[i] < 0 and bot[i] <= o[i] <= top[i] and c[i] < bot[i]:
            if sum(max(o[j], c[j]) < bot[j] for j in range(i - 10, i)) >= need:
                out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 11. VWAP side + candle colour, ATR trailing stop
# --------------------------------------------------------------------------- #

def vwap_candle(bars, ctx, p):
    """e9IDuLESYv8 (15m; "~100%/yr vs buy and hold -52%, 6,072 trades"). A
    green candle closing above session VWAP -> long; a red one closing below
    -> short. Exits on an ATR trailing stop. `gap` requires the close to be
    at least `gap` x ATR beyond VWAP (0 = his rule)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx)
    out = {}
    for i in range(1, len(c)):
        A, v = a[i], vw[i]
        if not (A > 0 and fin(v)):
            continue
        if c[i] > o[i] and c[i] > v + p["gap"] * A and not (c[i - 1] > o[i - 1] and c[i - 1] > v + p["gap"] * A):
            out[i] = (1, p["stop"] * A, None)
        elif c[i] < o[i] and c[i] < v - p["gap"] * A and not (c[i - 1] < o[i - 1] and c[i - 1] < v - p["gap"] * A):
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 12. New York session breakout after a green day
# --------------------------------------------------------------------------- #

def session_break_prev_day(bars, ctx, p):
    """cTJ0Qbz0eAI (a fellow YouTuber's yen/gold system, "positive over 9
    years"). Previous session green -> after the first `hours` of today's
    session, a buy stop at today's running high: the first close above it
    -> long (mirror after a red day). Out at the session end (engine) or
    the wide stop (`stop` x ATR; his 150 pips)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    since = b["L"]["since"]
    rh, rl = d["run_h"], d["run_l"]
    pdo, pdc = d["pdo"], d["pdc"]
    day = b["L"]["day"]
    lvl_day, lvl_h, lvl_l, done = None, NAN, NAN, None
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(pdo[i])) or day[i] == done:
            continue
        if since[i] < p["hours"] * 60:
            continue
        if lvl_day != day[i]:
            # the level: the session's high/low BEFORE this bar
            lvl_day, lvl_h, lvl_l = day[i], (rh[i - 1] if day[i - 1] == day[i] else NAN), \
                (rl[i - 1] if day[i - 1] == day[i] else NAN)
        if not fin(lvl_h):
            continue
        if pdc[i] > pdo[i] and c[i] > lvl_h:
            out[i] = (1, p["stop"] * A, None)
            done = day[i]
        elif pdc[i] < pdo[i] and c[i] < lvl_l:
            out[i] = (-1, p["stop"] * A, None)
            done = day[i]
    return out


# --------------------------------------------------------------------------- #
# 13. Michael Harris' rising-lows pattern
# --------------------------------------------------------------------------- #

def harris_pattern(bars, ctx, p):
    """H23GLHD__yY (from Harris' book; QQQ/SPY/DAX long, "107%, 70% win").
    Four candles with rising lows, each high inside the next candle's range
    (as he codes it: h0 > h1 > l0 > l2, h2 > l1, l1 > h3 > l2 > l3) -> long at
    the next open; mirrored for shorts. Stop `stop` x ATR (his fixed %)."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    out = {}
    for i in range(3, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        if (h[i] > h[i - 1] > l[i] > l[i - 2] and h[i - 2] > l[i - 1]
                and l[i - 1] > h[i - 3] > l[i - 2] > l[i - 3]):
            out[i] = (1, p["stop"] * A, None)
        elif (l[i] < l[i - 1] < h[i] < h[i - 2] and l[i - 2] < h[i - 1]
                and h[i - 1] < l[i - 3] < h[i - 2] < h[i - 3]):
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 14. trend + a new short-term low (MA pullback)
# --------------------------------------------------------------------------- #

def ma_new_low(bars, ctx, p):
    """7oNYZCMMHPA ("two rules"). Close above the `ma` SMA = uptrend; the
    candle's low below the lows of the previous `back` candles -> long
    (mirror: a new high in a downtrend -> short). Stop `stop` x ATR."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    s = F.cached(ctx, ("ct_sma", p["ma"]), lambda: F.sma(b["c"], p["ma"]).tolist())
    k = p["back"]
    lo_k = TR._shift(F.rolling_min(b["l"], k), 1).tolist()
    hi_k = TR._shift(F.rolling_max(b["h"], k), 1).tolist()
    out = {}
    for i in range(k + 1, len(c)):
        A = a[i]
        if not (A > 0 and fin(s[i]) and fin(lo_k[i])):
            continue
        if c[i] > s[i] and l[i] < lo_k[i]:
            out[i] = (1, p["stop"] * A, None)
        elif c[i] < s[i] and h[i] > hi_k[i]:
            out[i] = (-1, p["stop"] * A, None)
    return out


# --------------------------------------------------------------------------- #
# 15. Hoffman inventory retracement bar
# --------------------------------------------------------------------------- #

def hoffman_irb(bars, ctx, p):
    """QE6nfBBD_AI / OLsKawK3c-s. Uptrend: the 20 SMA rising by at least
    `slope` x ATR over 5 candles (his stand-in for "45 degrees"). An IRB:
    the upper wick >= 45% of the candle's range. A later close above the
    IRB's high (within 5 candles) -> long. Mirror. Stop at the IRB's low."""
    b, o, h, l, c = _ohlc(bars, ctx)
    a = _atr(bars, ctx)
    s = F.cached(ctx, ("ct_sma", 20), lambda: F.sma(b["c"], 20).tolist())
    pend = None                      # (side, level, stop, expires)
    out = {}
    for i in range(6, len(c)):
        A = a[i]
        if not (A > 0 and fin(s[i - 5])):
            continue
        if pend is not None:
            side, lvl, st, exp = pend
            if i > exp:
                pend = None
            elif side * (c[i] - lvl) > 0:
                out[i] = (side, F.stop_dist(side, c[i], st, A), None)
                pend = None
                continue
        rng = h[i] - l[i]
        if not (rng > 0):
            continue
        slope = (s[i] - s[i - 5]) / A
        if slope >= p["slope"] and h[i] - max(o[i], c[i]) >= 0.45 * rng:
            pend = (1, h[i], l[i], i + 5)
        elif slope <= -p["slope"] and min(o[i], c[i]) - l[i] >= 0.45 * rng:
            pend = (-1, l[i], h[i], i + 5)
    return out


EXITS = ("rr_1", "rr_2", "time_20")

SPECS = {
    "ct_hull_rsi_linreg": (hull_rsi_linreg, lambda g: {
        "rsi": (50, 52), "lr": (50, 100), "stop": (3.0,),
        "exit_mode": ("signal", "trail_0.5"), "last_entry_minute": g["last"]}, False, hull_exit),
    "ct_pin_bar_trend": (pin_bar_trend, lambda g: {
        "sr": (0, 0.5), "stop": (0.5, 1.0),
        "exit_mode": ("rr_2", "time_50", "rr_1"), "last_entry_minute": g["last"]}, False, None),
    "ct_macd_ema_pullback": (macd_ema_pullback, lambda g: {
        "ema": (100, 200), "thr": (0.05, 0.15), "stop": (1.5,),
        "exit_mode": EXITS, "last_entry_minute": g["last"]}, False, None),
    "ct_vwap_bb_rsi": (vwap_bb_rsi, lambda g: {
        "back": (75, 150), "stop": (1.2, 2.0),
        "exit_mode": ("rr_1.5", "rr_1", "time_12"), "last_entry_minute": g["last"]}, False, None),
    "ct_rsi_scalp_ema": (rsi_scalp_ema, lambda g: {
        "n": (2, 3), "lvl": (10, 20), "stop": (1.5,),
        "exit_mode": EXITS, "last_entry_minute": g["last"]}, False, None),
    "ct_rsi_adx_engulf": (rsi_adx_engulf, lambda g: {
        "back": (3, 6), "adx": (25, 30),
        "exit_mode": ("rr_1", "rr_1.5", "time_12"), "last_entry_minute": g["last"]}, False, None),
    "ct_ema3_fast_cross": (ema3_fast_cross, lambda g: {
        "fast": (20, 50), "tail": (1.0, 0.1), "stop": (1.5,),
        "exit_mode": EXITS, "last_entry_minute": g["last"]}, False, None),
    "ct_alligator_break": (alligator_break, lambda g: {
        "back": (5, 10),
        "exit_mode": ("rr_1", "rr_2", "trail_0.25"), "last_entry_minute": g["last"]}, False, None),
    "ct_trend_bb_dip": (trend_bb_dip, lambda g: {
        "ma": (150, 200), "dev": (2.0, 2.5), "rsi_x": (50, 65), "stop": (2.0,),
        "exit_mode": ("signal", "rr_1", "rr_2"), "last_entry_minute": g["last"]}, False, rsi2_exit),
    "ct_ichimoku_retrace": (ichimoku_retrace, lambda g: {
        "ema": (100, 200), "share": (6, 8), "stop": (1.5,),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"), "last_entry_minute": g["last"]}, False, None),
    "ct_vwap_candle": (vwap_candle, lambda g: {
        "gap": (0.0, 0.25), "stop": (1.5,),
        "exit_mode": ("trail_0.25", "trail_0.5", "rr_2"), "last_entry_minute": g["last"]}, False, None),
    "ct_session_break_prev_day": (session_break_prev_day, lambda g: {
        "hours": (2, 4), "stop": (2.0, 4.0),
        "exit_mode": ("rr_10", "rr_2"), "last_entry_minute": g["last"]}, False, None),
    "ct_harris_pattern": (harris_pattern, lambda g: {
        "stop": (1.0, 2.0),
        "exit_mode": ("rr_1", "time_5", "time_10"), "last_entry_minute": g["last"]}, False, None),
    "ct_ma_new_low": (ma_new_low, lambda g: {
        "ma": (100, 200), "back": (7, 15), "stop": (1.5,),
        "exit_mode": EXITS, "last_entry_minute": g["last"]}, False, None),
    "ct_hoffman_irb": (hoffman_irb, lambda g: {
        "slope": (0.5, 1.0),
        "exit_mode": ("rr_1", "rr_2", "trail_0.25"), "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
