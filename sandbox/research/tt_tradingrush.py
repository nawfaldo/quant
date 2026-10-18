"""Trading Rush's YouTube (@TradingRush) strategies, as `cfd_tt_families`
families.

Source: the channel's 204 videos, listed 2026-10-03; the ~80 strategy tests
("I tested X 100 / 200 TIMES") read from YouTube's English captions. Map, win
rates and SKIP list: `results/tiktok/tradingrush_strategy_notes.md` (kept with
the TikTok notes so every creator's map lives in one folder).

ONE FRAMEWORK, MANY ENTRIES. Every Trading Rush test uses the same frame:
the 200-period EMA picks the side (long only above it, short only below), the
indicator gives the entry at the signal candle's close, the stop goes beyond
the pullback / swing (or at the indicator's own line where the video says so),
and the target is 1.5x the stop ("the best ratio for trend strategies"
according to his 300-trade reward-risk test). Here: `ema` is the trend average's
length (200 his; 100 a faster check), the swing stop is the extreme of the
last `SWING` bars (floored at 0.2 ATR), and `exit_mode` offers rr_1 / rr_1.5 /
rr_2. His tests ran on 30m (EURJPY, then XAUUSD) -- the study's 30m is the
match.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`; entries fill at the next bar's open
(his "closing price of the candle"), one trade a day, flat by the close.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite
SWING = 10            # bars back for "the pullback" / "the swing low"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _base(bars, ctx):
    return F.base(bars, ctx)


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _ema(x, n):
    return F._ema_arr(numpy.asarray(x, dtype=float), n)


def _rma(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), NAN)
    acc = NAN
    k = 0
    for i, v in enumerate(x):
        if not fin(v):
            continue
        if not fin(acc):
            k += 1
            if k == n:
                acc = numpy.nanmean(x[max(0, i - n + 1):i + 1])
                out[i] = acc
            continue
        acc += (v - acc) / n
        out[i] = acc
    return out


def _wma(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), NAN)
    if len(x) >= n:
        from numpy.lib.stride_tricks import sliding_window_view
        w = numpy.arange(1, n + 1, dtype=float)
        out[n - 1:] = sliding_window_view(x, n) @ w / w.sum()
    return out


def _lag(x, k=1):
    """`x` delayed by `k` bars; the first `k` slots are NaN (False for bools)."""
    x = numpy.asarray(x)
    out = numpy.zeros(len(x), dtype=bool) if x.dtype == bool else numpy.full(len(x), NAN)
    if 0 < k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def _shift(x, k):
    out = numpy.full(len(x), NAN)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def _cross_up(a, b):
    """True at i where a crosses above b (a[i-1] <= b[i-1], a[i] > b[i])."""
    a, b = numpy.asarray(a, dtype=float), numpy.broadcast_to(numpy.asarray(b, dtype=float), (len(a),))
    out = numpy.zeros(len(a), dtype=bool)
    with numpy.errstate(invalid="ignore"):
        out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _cross_dn(a, b):
    a, b = numpy.asarray(a, dtype=float), numpy.broadcast_to(numpy.asarray(b, dtype=float), (len(a),))
    out = numpy.zeros(len(a), dtype=bool)
    with numpy.errstate(invalid="ignore"):
        out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


def _psar(bars, ctx, step=0.02, cap=0.2):
    """Parabolic SAR: `sar[i]` and `up[i]` (True = dots below price)."""
    def build():
        b = _base(bars, ctx)
        h, l, n = b["h"], b["l"], b["n"]
        sar = numpy.full(n, NAN)
        up = numpy.zeros(n, dtype=bool)
        if n < 3:
            return sar, up
        bull = True
        af = step
        ep = h[0]
        s = l[0]
        for i in range(1, n):
            s = s + af * (ep - s)
            if bull:
                s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
                if l[i] < s:
                    bull, s, ep, af = False, ep, l[i], step
                elif h[i] > ep:
                    ep, af = h[i], min(af + step, cap)
            else:
                s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
                if h[i] > s:
                    bull, s, ep, af = True, ep, h[i], step
                elif l[i] < ep:
                    ep, af = l[i], min(af + step, cap)
            sar[i], up[i] = s, bull
        return sar, up
    return F.cached(ctx, ("tr_psar", step, cap), build)


def _alligator(bars, ctx):
    """Bill Williams' Alligator on hl2: jaw SMMA(13)>>8, teeth SMMA(8)>>5,
    lips SMMA(5)>>3 (the forward shifts read past values only)."""
    def build():
        b = _base(bars, ctx)
        hl2 = (b["h"] + b["l"]) / 2.0
        return (_shift(_rma(hl2, 13), 8), _shift(_rma(hl2, 8), 5), _shift(_rma(hl2, 5), 3))
    return F.cached(ctx, "tr_gator", build)


def _ichimoku(bars, ctx):
    """Tenkan (9), kijun (26), and the cloud as it is DRAWN at each bar
    (spans computed 26 bars earlier)."""
    def build():
        b = _base(bars, ctx)
        h, l = b["h"], b["l"]
        mid = lambda n: (F.rolling_max(h, n) + F.rolling_min(l, n)) / 2.0
        ten, kij = mid(9), mid(26)
        a = _shift((ten + kij) / 2.0, 26)
        bb = _shift(mid(52), 26)
        return ten, kij, a, bb
    return F.cached(ctx, "tr_ichi", build)


# --------------------------------------------------------------------------- #
# the framework
# --------------------------------------------------------------------------- #

def _trend_events(bars, ctx, p, long_sig, short_sig, stop_long=None, stop_short=None,
                  extra_long=None, extra_short=None):
    """Long where `long_sig` fires above the `ema` EMA, short where
    `short_sig` fires below it. Stop: the given level arrays, else the swing
    extreme of the last `SWING` bars; floored at 0.2 ATR."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    e = F.ema(bars, ctx, p["ema"]).tolist()
    sw_lo = F.rolling_min(b["l"], SWING).tolist()
    sw_hi = F.rolling_max(b["h"], SWING).tolist()
    out = {}
    for i in numpy.flatnonzero(numpy.asarray(long_sig) | numpy.asarray(short_sig)):
        i = int(i)
        A = a[i]
        if not (A > 0) or not fin(e[i]) or not _ok(b, i, p) or i in out:
            continue
        if long_sig[i] and c[i] > e[i] and (extra_long is None or extra_long[i]):
            lvl = stop_long[i] if stop_long is not None else sw_lo[i]
            d = F.stop_dist(1, c[i], lvl, A)
            if d > 0 and fin(d):
                out[i] = (1, d, None)
        elif short_sig[i] and c[i] < e[i] and (extra_short is None or extra_short[i]):
            lvl = stop_short[i] if stop_short is not None else sw_hi[i]
            d = F.stop_dist(-1, c[i], lvl, A)
            if d > 0 and fin(d):
                out[i] = (-1, d, None)
    return out


def _arr(b, k):
    return b[k]


# --------------------------------------------------------------------------- #
# families (newest test first)
# --------------------------------------------------------------------------- #

def connors_rsi(bars, ctx, p):
    """#9 (2025-12). Connors RSI(3, 2, 100) leaves the extreme zone -- crosses
    back above `lo` (his 10) -> long above the 200 EMA; back below 100-`lo`
    -> short. Swing stop. His: 39% at 1.5R (mostly good market)."""
    b = _base(bars, ctx)
    c = b["c"]

    def build():
        def rsi_of(x, n):
            d = numpy.diff(x, prepend=x[0])
            up, dn = _rma(numpy.maximum(d, 0), n), _rma(numpy.maximum(-d, 0), n)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                return numpy.where(dn > 0, 100 - 100 / (1 + up / dn), 100.0)
        streak = numpy.zeros(len(c))
        for i in range(1, len(c)):
            if c[i] > c[i - 1]:
                streak[i] = streak[i - 1] + 1 if streak[i - 1] > 0 else 1
            elif c[i] < c[i - 1]:
                streak[i] = streak[i - 1] - 1 if streak[i - 1] < 0 else -1
        roc = numpy.diff(c, prepend=c[0]) / numpy.where(c > 0, c, 1)
        rank = numpy.full(len(c), NAN)
        for i in range(100, len(c)):
            w = roc[i - 100:i]
            rank[i] = 100.0 * (w < roc[i]).mean()
        return (rsi_of(c, 3) + rsi_of(streak, 2) + rank) / 3.0
    crsi = F.cached(ctx, "tr_crsi", build)
    lo = p["lo"]
    return _trend_events(bars, ctx, p, _cross_up(crsi, lo), _cross_dn(crsi, 100 - lo))


def golden_cross(bars, ctx, p):
    """#10, #180 (SMA), #139/#140 (WMA), #133 (DEMA). The 50 crosses the 200
    with price beyond both -> enter; stop beyond the cross (the 200 average,
    floored). `ma` sma / wma / dema. His: SMA 42%, WMA 48%; 76% in his
    extremely good market but only 78 trades in 20 years."""
    b = _base(bars, ctx)
    c = b["c"]

    def build():
        if p["ma"] == "sma":
            return F.sma(c, 50), F.sma(c, 200)
        if p["ma"] == "wma":
            return _wma(c, 50), _wma(c, 200)
        e50, e200 = _ema(c, 50), _ema(c, 200)
        return 2 * e50 - _ema(e50, 50), 2 * e200 - _ema(e200, 200)
    f, s = F.cached(ctx, ("tr_gc", p["ma"]), build)
    up = _cross_up(f, s) & (c > f) & (c > s)
    dn = _cross_dn(f, s) & (c < f) & (c < s)
    return _trend_events(bars, ctx, p, up, dn, s, s)


def donchian(bars, ctx, p):
    """#11, #182. Donchian(`n`): the upper band turns up again (a new `n`-bar
    high after it had gone flat) above the 200 EMA -> long; stop below the
    lower band. Mirror short. His: the 2nd-highest win rate of the first
    series; ~60% in trending markets."""
    b = _base(bars, ctx)
    n = p["n"]
    up_b = F.rolling_max(b["h"], n)
    lo_b = F.rolling_min(b["l"], n)
    flat_u = numpy.zeros(len(up_b), dtype=bool)
    flat_l = numpy.zeros(len(up_b), dtype=bool)
    flat_u[2:] = up_b[1:-1] <= up_b[:-2]
    flat_l[2:] = lo_b[1:-1] >= lo_b[:-2]
    lu = numpy.zeros(len(up_b), dtype=bool)
    ls = numpy.zeros(len(up_b), dtype=bool)
    lu[1:] = (up_b[1:] > up_b[:-1]) & flat_u[1:]
    ls[1:] = (lo_b[1:] < lo_b[:-1]) & flat_l[1:]
    return _trend_events(bars, ctx, p, lu, ls, lo_b, up_b)


def vortex(bars, ctx, p):
    """#12, #185. Vortex(14): VI+ crosses above VI- above the 200 EMA ->
    long; stop at the Parabolic SAR. His: 46%."""
    b = _base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    n = 14
    vmp = numpy.abs(h - _lag(l, 1))
    vmm = numpy.abs(l - _lag(h, 1))
    tr = numpy.maximum(h - l, numpy.maximum(abs(h - _lag(c, 1)), abs(l - _lag(c, 1))))
    vmp[0] = vmm[0] = tr[0] = NAN
    s = lambda x: F.sma(numpy.nan_to_num(x), n)
    vip, vim = s(vmp) / s(tr), s(vmm) / s(tr)
    sar, _ = _psar(bars, ctx)
    return _trend_events(bars, ctx, p, _cross_up(vip, vim), _cross_dn(vip, vim), sar, sar)


def aroon(bars, ctx, p):
    """#14, #189. Aroon(14): Aroon-up reaches 100 while Aroon-down is at 0
    (the lines fully apart) for the first time -> long above the 200 EMA;
    stop at the Parabolic SAR. His record: 64% in the extremely good market,
    40% in the mostly good one."""
    b = _base(bars, ctx)
    h, l = b["h"], b["l"]
    n = 14

    def build():
        from numpy.lib.stride_tricks import sliding_window_view
        up = numpy.full(len(h), NAN)
        dn = numpy.full(len(h), NAN)
        if len(h) > n:
            wh = sliding_window_view(h, n + 1)
            wl = sliding_window_view(l, n + 1)
            up[n:] = 100.0 * wh.argmax(axis=1) / n
            dn[n:] = 100.0 * wl.argmin(axis=1) / n
        return up, dn
    up, dn = F.cached(ctx, "tr_aroon", build)
    full_u = (up >= 100) & (dn <= 0)
    full_d = (dn >= 100) & (up <= 0)
    lu = full_u & ~_lag(full_u, 1)
    ld = full_d & ~_lag(full_d, 1)
    sar, _ = _psar(bars, ctx)
    return _trend_events(bars, ctx, p, lu, ld, sar, sar)


def supertrend(bars, ctx, p):
    """#15, #191. SuperTrend(10, `mult`) flips up above the 200 EMA -> long;
    stop at the SuperTrend line. His: 46%."""
    st = F.supertrend(bars, ctx, 10, p["mult"])
    flip, line = st["flip"], st["line"]
    return _trend_events(bars, ctx, p, flip == 1, flip == -1, line, line)


def mfi(bars, ctx, p):
    """#16, #194. Money Flow Index(14) crosses back above `lo` (the creator's
    10; platforms default 20) -> long above the 200 EMA; mirror at 100-`lo`.
    Swing stop. His: 42% (and almost never fires at 10)."""
    b = _base(bars, ctx)

    def build():
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        mf = tp * b["v"]
        d = numpy.diff(tp, prepend=tp[0])
        pos = F.sma(numpy.where(d > 0, mf, 0.0), 14)
        neg = F.sma(numpy.where(d < 0, mf, 0.0), 14)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            return numpy.where(neg > 0, 100 - 100 / (1 + pos / neg), 100.0)
    m = F.cached(ctx, "tr_mfi", build)
    lo = p["lo"]
    return _trend_events(bars, ctx, p, _cross_up(m, lo), _cross_dn(m, 100 - lo))


def psar_alligator(bars, ctx, p):
    """#17, #197. The Parabolic SAR dots flip below price, the Alligator is
    stacked up (lips > teeth > jaw) and the candle is above the 200 EMA and
    clear of the jaw -> long; stop at the SAR. His: 43% -> 52% good market."""
    b = _base(bars, ctx)
    sar, up = _psar(bars, ctx)
    jaw, teeth, lips = _alligator(bars, ctx)
    flip_up = up & ~_lag(up, 1)
    flip_dn = ~up & _lag(up, 1)
    with numpy.errstate(invalid="ignore"):
        stk_u = (lips > teeth) & (teeth > jaw) & (b["l"] > jaw)
        stk_d = (lips < teeth) & (teeth < jaw) & (b["h"] < jaw)
    return _trend_events(bars, ctx, p, flip_up, flip_dn, sar, sar, stk_u, stk_d)


def alligator(bars, ctx, p):
    """#19. Lips cross above the jaw, the close above all three lines and
    the 200 EMA -> long; stop on the other side of the lines (the lowest of
    them, floored). His: 50%; 53% good market."""
    b = _base(bars, ctx)
    c = b["c"]
    jaw, teeth, lips = _alligator(bars, ctx)
    with numpy.errstate(invalid="ignore"):
        lo_line = numpy.fmin(numpy.fmin(jaw, teeth), lips)
        hi_line = numpy.fmax(numpy.fmax(jaw, teeth), lips)
        lu = _cross_up(lips, jaw) & (c > hi_line)
        ld = _cross_dn(lips, jaw) & (c < lo_line)
    return _trend_events(bars, ctx, p, lu, ld, lo_line, hi_line)


def fractal(bars, ctx, p):
    """#18 (reversal arrow), #162 (breakout). Bill Williams fractals (2
    bars each side, known 2 bars later). `mode` "reversal": a down-fractal
    (swing low) confirms while the Alligator is stacked up -> long at the
    confirming close (his 38%); "breakout": a close above the latest
    up-fractal's high in the trend (his 48%). Swing stop."""
    b = _base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    n = len(c)
    jaw, teeth, lips = _alligator(bars, ctx)
    upf = numpy.zeros(n, dtype=bool)        # fractal high at j, known at j+2
    dnf = numpy.zeros(n, dtype=bool)
    for j in range(2, n - 2):
        if h[j] > max(h[j - 2], h[j - 1], h[j + 1], h[j + 2]):
            upf[j + 2] = True
        if l[j] < min(l[j - 2], l[j - 1], l[j + 1], l[j + 2]):
            dnf[j + 2] = True
    with numpy.errstate(invalid="ignore"):
        gu = (lips > teeth) & (teeth > jaw)
        gd = (lips < teeth) & (teeth < jaw)
    if p["mode"] == "reversal":
        return _trend_events(bars, ctx, p, dnf & gu, upf & gd)
    last_h = numpy.full(n, NAN)
    last_l = numpy.full(n, NAN)
    ch = cl = NAN
    for i in range(n):
        if upf[i]:
            ch = h[i - 2]
        if dnf[i]:
            cl = l[i - 2]
        last_h[i], last_l[i] = ch, cl
    return _trend_events(bars, ctx, p, _cross_up(c, last_h), _cross_dn(c, last_l))


def ma_cross(bars, ctx, p):
    """#20, #187, #203 (9/21), #110/#111 (McGinley ~ a double-length EMA).
    The `fast` EMA crosses the 21 EMA -> enter with the 200 EMA; swing stop.
    His: 48% (9/21), 43% McGinley."""
    b = _base(bars, ctx)
    c = b["c"]
    f = F.ema(bars, ctx, p["fast"])
    s = F.ema(bars, ctx, 21)
    return _trend_events(bars, ctx, p, _cross_up(f, s), _cross_dn(f, s))


def ichimoku(bars, ctx, p):
    """#21. Tenkan crosses above kijun ABOVE a green cloud, the close above
    both lines, above the 200 EMA -> long; stop below the kijun. `confirm`
    "beepboop" (#144): instead of the tenkan cross, the first positive MACD
    histogram bar with price above the kijun and the kijun above a green
    cloud. His: 53% (one of the best); beep-boop combo below that."""
    b = _base(bars, ctx)
    c = b["c"]
    ten, kij, sa, sb = _ichimoku(bars, ctx)
    with numpy.errstate(invalid="ignore"):
        green, red = sa > sb, sa < sb
        top, bot = numpy.fmax(sa, sb), numpy.fmin(sa, sb)
        if p["confirm"] == "cross":
            lu = _cross_up(ten, kij) & green & (ten > top) & (c > ten) & (c > kij)
            ld = _cross_dn(ten, kij) & red & (ten < bot) & (c < ten) & (c < kij)
        else:
            hist = F.macd(bars, ctx)["hist"]
            lu = _cross_up(hist, 0.0) & green & (kij > top) & (c > kij)
            ld = _cross_dn(hist, 0.0) & red & (kij < bot) & (c < kij)
    return _trend_events(bars, ctx, p, lu, ld, kij, kij)


def oscillator_rebound(bars, ctx, p):
    """#22 (CCI), #131/#132 (Williams %R), #170 (Stochastic), #178 (RSI).
    The oscillator comes back OUT of its oversold zone in an uptrend (above
    the 200 EMA) -> long at that close; mirror at overbought. Swing stop.
    CCI(20) -100/+100; %R(14) -80/-20; Stoch(14,3) %K 20/80; RSI(14) 30/70.
    His: CCI 36%, %R 44%, Stoch 41%, RSI 49%."""
    b = _base(bars, ctx)
    osc = p["osc"]
    if osc == "cci":
        x, lo, hi = F.cci(bars, ctx, 20), -100.0, 100.0
    elif osc == "willr":
        hh, ll = F.rolling_max(b["h"], 14), F.rolling_min(b["l"], 14)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            x = numpy.where(hh > ll, -100 * (hh - b["c"]) / (hh - ll), -50.0)
        lo, hi = -80.0, -20.0
    elif osc == "stoch":
        x, lo, hi = F.stochastic(bars, ctx, 14, 3), 20.0, 80.0
    else:
        x, lo, hi = F.rsi(bars, ctx, 14), 30.0, 70.0
    return _trend_events(bars, ctx, p, _cross_up(x, lo), _cross_dn(x, hi))


def bb_breakout(bars, ctx, p):
    """#23, #125/#126 (%B > 1 is the same trigger), #142 (with beep-boop).
    The FIRST candle to close above the upper Bollinger Band(20, `dev`) above
    the 200 EMA -> long; swing stop. His record: 76% in the extremely good
    market, 42% in the bad one; %B 100-trade test ~breakeven."""
    b = _base(bars, ctx)
    c = b["c"]
    mid = F.sma(c, 20)
    from numpy.lib.stride_tricks import sliding_window_view
    sd = numpy.full(len(c), NAN)
    if len(c) >= 20:
        sd[19:] = sliding_window_view(c, 20).std(axis=1)
    up, lo = mid + p["dev"] * sd, mid - p["dev"] * sd
    with numpy.errstate(invalid="ignore"):
        above, below = c > up, c < lo
    lu = above & ~_lag(above, 1)
    ld = below & ~_lag(below, 1)
    return _trend_events(bars, ctx, p, lu, ld)


def macd(bars, ctx, p):
    """#24, #204, #104 (BTC), #149 (zero-lag), #171 (with Stochastic). His
    best: MACD crosses above its signal BELOW the zero line, the candle above
    the 200 EMA -> long; swing stop; 1.5R. `variant` "zerolag" uses
    zero-lag EMAs (his worst, 33%); "stoch" needs %K under 20 within the
    last 10 bars and drops the zero-line rule (#171, 53%). His: 60-62%."""
    b = _base(bars, ctx)
    c = b["c"]
    v = p["variant"]
    if v == "zerolag":
        def zl(n):
            e1 = _ema(c, n)
            return 2 * e1 - _ema(e1, n)
        line = zl(12) - zl(26)
        sig = _ema(line, 9)
        sig = 2 * sig - _ema(sig, 9)
    else:
        m = F.macd(bars, ctx)
        line, sig = m["macd"], m["signal"]
    with numpy.errstate(invalid="ignore"):
        if v == "stoch":
            k = F.stochastic(bars, ctx, 14, 3)
            os_ = F.rolling_min(numpy.nan_to_num(k, nan=50.0), 10) < 20
            ob = F.rolling_max(numpy.nan_to_num(k, nan=50.0), 10) > 80
            lu = _cross_up(line, sig) & os_
            ld = _cross_dn(line, sig) & ob
        else:
            lu = _cross_up(line, sig) & (line < 0)
            ld = _cross_dn(line, sig) & (line > 0)
    return _trend_events(bars, ctx, p, lu, ld)


def trend_breakout(bars, ctx, p):
    """#43 (2025). Above the 200 EMA, after a pullback, a close above the
    last `k`-bar swing high -> long. `stop` "level": just below the broken
    swing high (his 52%); "swing": below the pullback low (his 40%)."""
    b = _base(bars, ctx)
    c = b["c"]
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"], pv["l"]
    lu = _cross_up(c, ph)
    ld = _cross_dn(c, pl)
    if p["stop"] == "level":
        return _trend_events(bars, ctx, p, lu, ld, ph, pl)
    return _trend_events(bars, ctx, p, lu, ld)


def livermore_pivot(bars, ctx, p):
    """#93 (2022). Livermore's pivotal point, made mechanical with the
    SuperTrend: each SuperTrend flip marks a reversal; the reversal's extreme
    is the pivotal point; a later close through it in the 200 EMA's direction
    -> enter; stop beyond the pivot. His: 41%."""
    b = _base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    flip = F.supertrend(bars, ctx, 10, 3.0)["flip"]
    n = len(c)
    piv_h = numpy.full(n, NAN)            # high made before a flip down
    piv_l = numpy.full(n, NAN)
    cur_h = cur_l = NAN
    run_h, run_l = -numpy.inf, numpy.inf
    for i in range(n):
        run_h, run_l = max(run_h, h[i]), min(run_l, l[i])
        if flip[i] == -1:
            cur_h, run_h, run_l = run_h, h[i], l[i]
        elif flip[i] == 1:
            cur_l, run_h, run_l = run_l, h[i], l[i]
        piv_h[i], piv_l[i] = cur_h, cur_l
    return _trend_events(bars, ctx, p, _cross_up(c, piv_h), _cross_dn(c, piv_l), piv_h, piv_l)


def channel_break(bars, ctx, p):
    """#108/#109/#136 (MA channel: SMA20 of highs and of lows), #135
    (Keltner: EMA20 +- 2 ATR(10)). A candle that OPENS AND CLOSES above the
    channel, above the 200 EMA -> long; swing stop. His: MA channel 49%
    (58% for 75 trades, then 15 losses), Keltner ~break-even with a 17-loss
    streak."""
    b = _base(bars, ctx)
    o, c = b["o"], b["c"]
    if p["channel"] == "ma":
        up, lo = F.sma(b["h"], 20), F.sma(b["l"], 20)
    else:
        mid = F.ema(bars, ctx, 20)
        a10 = F.atr(bars, ctx, 10)
        up, lo = mid + 2 * a10, mid - 2 * a10
    with numpy.errstate(invalid="ignore"):
        au = (o > up) & (c > up)
        bd = (o < lo) & (c < lo)
    return _trend_events(bars, ctx, p, au & ~_lag(au, 1), bd & ~_lag(bd, 1))


def rvi(bars, ctx, p):
    """#112/#113. Relative Vigor Index(10): RVI crosses its signal; `zone`
    "below": long crosses below the zero line (deep pullback, his 40%);
    "above": above zero (strong trend, his 45%). 200 EMA side; swing stop."""
    b = _base(bars, ctx)
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    w = lambda x: (x + 2 * _lag(x, 1) + 2 * _lag(x, 2) + _lag(x, 3)) / 6.0
    num, den = F.sma(numpy.nan_to_num(w(c - o)), 10), F.sma(numpy.nan_to_num(w(h - l)), 10)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        r = numpy.where(den != 0, num / den, NAN)
    r[:4] = NAN
    sig = w(numpy.nan_to_num(r))
    sig[:8] = NAN
    with numpy.errstate(invalid="ignore"):
        if p["zone"] == "below":
            lu, ld = _cross_up(r, sig) & (r < 0), _cross_dn(r, sig) & (r > 0)
        else:
            lu, ld = _cross_up(r, sig) & (r > 0), _cross_dn(r, sig) & (r < 0)
    return _trend_events(bars, ctx, p, lu, ld)


def zero_cross(bars, ctx, p):
    """#121/#122 (PPO), #127/#128 (TRIX 18), #130 (CMO 9), #158 (Awesome
    Oscillator), #153 (Chaikin oscillator 3/10), #166 (Chaikin Money Flow
    20). The oscillator crosses its ZERO line in the 200 EMA's direction ->
    enter; swing stop. His: PPO late signals, TRIX ~MACD-like, CMO tested,
    AO zero-line below 45%, Chaikin osc 47%, CMF 43%."""
    b = _base(bars, ctx)
    o, h, l, c, v = b["o"], b["h"], b["l"], b["c"], b["v"]
    osc = p["osc"]

    def build():
        if osc == "ppo":
            e1, e2 = _ema(c, 12), _ema(c, 26)
            return 100 * (e1 - e2) / e2
        if osc == "trix":
            t = _ema(_ema(_ema(numpy.log(c), 18), 18), 18)
            return 10000 * numpy.diff(t, prepend=NAN)
        if osc == "cmo":
            d = numpy.diff(c, prepend=c[0])
            su, sd = F.sma(numpy.maximum(d, 0), 9) * 9, F.sma(numpy.maximum(-d, 0), 9) * 9
            with numpy.errstate(invalid="ignore", divide="ignore"):
                return 100 * (su - sd) / (su + sd)
        if osc == "ao":
            hl2 = (h + l) / 2
            return F.sma(hl2, 5) - F.sma(hl2, 34)
        rng = h - l
        with numpy.errstate(invalid="ignore", divide="ignore"):
            mfm = numpy.where(rng > 0, ((c - l) - (h - c)) / rng, 0.0)
        if osc == "chaikin":
            adl = numpy.cumsum(mfm * v)
            return _ema(adl, 3) - _ema(adl, 10)
        return F.sma(mfm * v, 20) / F.sma(v, 20)                    # cmf
    x = F.cached(ctx, ("tr_osc", osc), build)
    return _trend_events(bars, ctx, p, _cross_up(x, 0.0), _cross_dn(x, 0.0))


def awesome_turn(bars, ctx, p):
    """#147 (2021). Awesome Oscillator BELOW zero rising `n` bars in a row
    (his 3: fewer false signals in ranges) -> long above the 200 EMA; mirror.
    Swing stop. His: 45%, better than the zero-line version."""
    b = _base(bars, ctx)
    hl2 = (b["h"] + b["l"]) / 2
    ao = F.sma(hl2, 5) - F.sma(hl2, 34)
    n = p["n"]
    d = numpy.diff(ao, prepend=NAN)
    with numpy.errstate(invalid="ignore"):
        rise = numpy.ones(len(ao), dtype=bool)
        fall = numpy.ones(len(ao), dtype=bool)
        for k in range(n):
            rise &= (_lag(d, k) if k else d) > 0
            fall &= (_lag(d, k) if k else d) < 0
        rise_p = _lag(rise, 1)
        fall_p = _lag(fall, 1)
        lu = rise & ~rise_p & (ao < 0)
        ld = fall & ~fall_p & (ao > 0)
    lu[:n + 1] = ld[:n + 1] = False
    return _trend_events(bars, ctx, p, lu, ld)


def hma_cross(bars, ctx, p):
    """#143. Hull MA(`n`) crosses the 200 SMA, the candle not on or between
    them -> enter; stop beyond the 200 SMA (floored). His: 42%."""
    b = _base(bars, ctx)
    c, h, l = b["c"], b["h"], b["l"]
    n = p["n"]
    hma = _wma(2 * _wma(c, n // 2) - _wma(c, n), int(math.sqrt(n)))
    s = F.sma(c, 200)
    with numpy.errstate(invalid="ignore"):
        lu = _cross_up(hma, s) & (l > numpy.fmax(hma, s))
        ld = _cross_dn(hma, s) & (h < numpy.fmin(hma, s))
    return _trend_events(bars, ctx, p, lu, ld, s, s)


def schaff(bars, ctx, p):
    """#154. Schaff Trend Cycle(10, 23, 50) crosses above `lo` (his 25) ->
    long above the 200 EMA; below 100-`lo` -> short. Swing stop. His: 50%."""
    b = _base(bars, ctx)
    c = b["c"]

    def build():
        m = _ema(c, 23) - _ema(c, 50)
        def stoch_smooth(x):
            lo_, hi_ = F.rolling_min(numpy.nan_to_num(x, nan=0.0), 10), F.rolling_max(numpy.nan_to_num(x, nan=0.0), 10)
            out = numpy.full(len(x), NAN)
            prev = NAN
            for i in range(len(x)):
                if not (fin(x[i]) and fin(lo_[i])):
                    continue
                k = 100 * (x[i] - lo_[i]) / (hi_[i] - lo_[i]) if hi_[i] > lo_[i] else (prev if fin(prev) else 50.0)
                prev = k if not fin(prev) else prev + 0.5 * (k - prev)
                out[i] = prev
            return out
        return stoch_smooth(stoch_smooth(m))
    stc = F.cached(ctx, "tr_stc", build)
    lo = p["lo"]
    return _trend_events(bars, ctx, p, _cross_up(stc, lo), _cross_dn(stc, 100 - lo))


def kst(bars, ctx, p):
    """#156. Know Sure Thing (ROC 10/15/20/30, smoothed 10/10/10/15, weights
    1-4, signal SMA 9) crosses above its signal BELOW zero -> long above the
    200 EMA; mirror. Swing stop. His: 52%."""
    b = _base(bars, ctx)
    c = b["c"]

    def build():
        def roc(n):
            out = numpy.full(len(c), NAN)
            out[n:] = 100 * (c[n:] / c[:-n] - 1)
            return out
        k = (F.sma(numpy.nan_to_num(roc(10)), 10) + 2 * F.sma(numpy.nan_to_num(roc(15)), 10)
             + 3 * F.sma(numpy.nan_to_num(roc(20)), 10) + 4 * F.sma(numpy.nan_to_num(roc(30)), 15))
        k[:45] = NAN
        return k, F.sma(numpy.nan_to_num(k), 9)
    k, s = F.cached(ctx, "tr_kst", build)
    with numpy.errstate(invalid="ignore"):
        return _trend_events(bars, ctx, p, _cross_up(k, s) & (k < 0), _cross_dn(k, s) & (k > 0))


def fisher(bars, ctx, p):
    """#123/#124. Fisher Transform(`n`) crosses above its trigger (its own
    previous value) BELOW zero -> long above the 200 EMA; mirror. Swing
    stop. His: fast, accurate early, gave it back; below the MACD."""
    b = _base(bars, ctx)
    n = p["n"]

    def build():
        hl2 = (b["h"] + b["l"]) / 2
        hi, lo = F.rolling_max(hl2, n), F.rolling_min(hl2, n)
        val = 0.0
        fish = numpy.full(len(hl2), NAN)
        prev = 0.0
        for i in range(len(hl2)):
            if not fin(hi[i]):
                continue
            r = (hl2[i] - lo[i]) / (hi[i] - lo[i]) if hi[i] > lo[i] else 0.5
            val = 0.66 * (r - 0.5) + 0.67 * val
            val = max(min(val, 0.999), -0.999)
            prev = 0.5 * math.log((1 + val) / (1 - val)) + 0.5 * prev
            fish[i] = prev
        return fish
    f = F.cached(ctx, ("tr_fish", n), build)
    trig = _shift(f, 1)
    with numpy.errstate(invalid="ignore"):
        return _trend_events(bars, ctx, p, _cross_up(f, trig) & (f < 0), _cross_dn(f, trig) & (f > 0))


def beep_boop(bars, ctx, p):
    """#164/#165 (his own indicator), #114 (with SuperTrend), #119 (with
    the Alligator). Beep Boop: the first MACD-histogram bar on the trade's
    side (hist crosses zero) while price is beyond the `fast` EMA (his 50,
    the short-term trend) and the 200 EMA. `filter` "supertrend" also needs
    SuperTrend(10,3) on the same side; "alligator" a stacked Alligator with
    the candle clear of all lines. Swing stop. His: 48% alone."""
    b = _base(bars, ctx)
    c, h, l = b["c"], b["h"], b["l"]
    hist = F.macd(bars, ctx)["hist"]
    e = F.ema(bars, ctx, p["fast"])
    with numpy.errstate(invalid="ignore"):
        lu = _cross_up(hist, 0.0) & (c > e)
        ld = _cross_dn(hist, 0.0) & (c < e)
        if p["filter"] == "supertrend":
            d = F.supertrend(bars, ctx, 10, 3.0)["dir"]
            lu &= d == 1
            ld &= d == -1
        elif p["filter"] == "alligator":
            jaw, teeth, lips = _alligator(bars, ctx)
            hi_line = numpy.fmax(numpy.fmax(jaw, teeth), lips)
            lo_line = numpy.fmin(numpy.fmin(jaw, teeth), lips)
            lu &= (lips > teeth) & (teeth > jaw) & (l > hi_line)
            ld &= (lips < teeth) & (teeth < jaw) & (h < lo_line)
    return _trend_events(bars, ctx, p, lu, ld)


def rsi_momentum(bars, ctx, p):
    """#146 (SuperTrend), #117/#118 (Ichimoku cloud), #150 (ADX). RSI(14)
    crosses ABOVE 70 (buying pressure, not overbought) in an uptrend -> long;
    below 30 in a downtrend -> short. `filter`: "supertrend" (SuperTrend on
    the same side; stop at its line), "cloud" (candle above a green cloud),
    "adx" (ADX(14) above 25). Plus the 200 EMA. His: 45%, 49%, 36%."""
    b = _base(bars, ctx)
    c = b["c"]
    r = F.rsi(bars, ctx, 14)
    lu, ld = _cross_up(r, 70.0), _cross_dn(r, 30.0)
    stop_l = stop_s = None
    with numpy.errstate(invalid="ignore"):
        if p["filter"] == "supertrend":
            st = F.supertrend(bars, ctx, 10, 3.0)
            lu &= st["dir"] == 1
            ld &= st["dir"] == -1
            stop_l = stop_s = st["line"]
        elif p["filter"] == "cloud":
            _, _, sa, sb = _ichimoku(bars, ctx)
            lu &= (sa > sb) & (b["l"] > numpy.fmax(sa, sb))
            ld &= (sa < sb) & (b["h"] < numpy.fmin(sa, sb))
        else:
            ax = F.adx(bars, ctx, 14)["adx"]
            lu &= ax > 25
            ld &= ax > 25
    return _trend_events(bars, ctx, p, lu, ld, stop_l, stop_s)


def engulfing(bars, ctx, p):
    """#174, #176. A bullish (bearish) engulfing candle -- body engulfs the
    previous opposite body -- with the 200 EMA -> enter. He measured the next
    1-2 candles' direction (bullish 54%, bearish 56%); `time_1` / `time_2`
    reproduce that, rr exits the usual frame. Swing stop."""
    b = _base(bars, ctx)
    o, c = b["o"], b["c"]
    po, pc = _lag(o, 1), _lag(c, 1)
    bull = (c > o) & (pc < po) & (c >= po) & (o <= pc)
    bear = (c < o) & (pc > po) & (c <= po) & (o >= pc)
    bull[0] = bear[0] = False
    return _trend_events(bars, ctx, p, bull, bear)


def extreme_level(bars, ctx, p):
    """#67 (2024). "Extreme price levels": the biggest reversal points on the
    daily chart -- here the highest high / lowest low of the previous
    `days` sessions. When price reaches one and prints a rejection candle
    (a wick through it of at least its body, closing back inside) -> trade
    the reversal; stop beyond the extreme (floored). No 200 EMA filter (a
    reversal setup). His: 76% over 100 trades."""
    b = _base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    dh, dl = d["d_hi"], d["d_lo"]
    nd = len(dh)
    k = p["days"]
    xh = numpy.full(nd, NAN)
    xl = numpy.full(nd, NAN)
    for j in range(k, nd):
        xh[j] = dh[j - k:j].max()
        xl[j] = dl[j - k:j].min()
    idx = d["day_idx"]
    top, bot = xh[idx].tolist(), xl[idx].tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        body = abs(c[i] - o[i])
        if fin(top[i]) and h[i] >= top[i] > c[i] and h[i] - max(o[i], c[i]) >= body:
            out[i] = (-1, F.stop_dist(-1, c[i], h[i], A), None)
        elif fin(bot[i]) and l[i] <= bot[i] < c[i] and min(o[i], c[i]) - l[i] >= body:
            out[i] = (1, F.stop_dist(1, c[i], l[i], A), None)
    return out


def darvas(bars, ctx, p):
    """#85 (2023). Nicolas Darvas' box: a new `n`-bar high that then holds
    for 3 bars (the box top) while the low of those bars sets the box bottom;
    a close above the box top -> long (with the 200 EMA); stop at the box
    bottom. His: 45% at 5:1 on closes (73% at 1.5:1), 27% with highs/lows."""
    b = _base(bars, ctx)
    h, l, c = b["h"], b["l"], b["c"]
    n = p["n"]
    hh = F.rolling_max(h, n)
    top = numpy.full(len(c), NAN)
    bot = numpy.full(len(c), NAN)
    t = bb_ = NAN
    for i in range(n + 3, len(c)):
        j = i - 3
        if h[j] >= hh[j] and h[j + 1:i + 1].max() <= h[j]:
            t, bb_ = h[j], l[j:i + 1].min()
        top[i], bot[i] = t, bb_
    lu = _cross_up(c, top)
    no = numpy.zeros(len(c), dtype=bool)
    return _trend_events(bars, ctx, p, lu, no, bot, None)


def range_edge(bars, ctx, p):
    """#83 (2022). In a sideways market -- the last two `k`-bar swing highs
    within `tol` ATR of each other and likewise the last two swing lows --
    take longs in the bottom ~third of the range and shorts in the top third
    on `osc`: "macd" (cross below / above zero), "rsi" (RSI crosses its
    14-SMA below / above 50), "pctb" (%B crosses back above 0 / below 1).
    Stop just outside the range (floored); his target 0.5:1 (`rr_0.5`), so
    the target stays inside the range. No 200 EMA. His: MACD 78%, RSI 70%,
    %B 60% at 0.5:1 (break-even 67%)."""
    b = _base(bars, ctx)
    c = b["c"]
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    H1, H0, L1, L0 = pv["h"], pv["h_prev"], pv["l"], pv["l_prev"]
    osc = p["osc"]
    with numpy.errstate(invalid="ignore"):
        if osc == "macd":
            m = F.macd(bars, ctx)
            lu = _cross_up(m["macd"], m["signal"]) & (m["macd"] < 0)
            ld = _cross_dn(m["macd"], m["signal"]) & (m["macd"] > 0)
        elif osc == "rsi":
            r = F.rsi(bars, ctx, 14)
            rs = F.sma(numpy.nan_to_num(r, nan=50.0), 14)
            lu = _cross_up(r, rs) & (r < 50)
            ld = _cross_dn(r, rs) & (r > 50)
        else:
            mid = F.sma(c, 20)
            from numpy.lib.stride_tricks import sliding_window_view
            sd = numpy.full(len(c), NAN)
            if len(c) >= 20:
                sd[19:] = sliding_window_view(c, 20).std(axis=1)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                pb = (c - (mid - 2 * sd)) / (4 * sd)
            lu, ld = _cross_up(pb, 0.0), _cross_dn(pb, 1.0)
    out = {}
    for i in numpy.flatnonzero(lu | ld):
        i = int(i)
        A = a[i]
        if not (A > 0) or not _ok(b, i, p) or not (fin(H0[i]) and fin(L0[i])):
            continue
        if abs(H1[i] - H0[i]) > p["tol"] * A or abs(L1[i] - L0[i]) > p["tol"] * A:
            continue
        top, bot = max(H1[i], H0[i]), min(L1[i], L0[i])
        if not top > bot or not (bot <= c[i] <= top):
            continue
        q = (c[i] - bot) / (top - bot)
        if lu[i] and q <= 0.35:
            out[i] = (1, F.stop_dist(1, c[i], bot - 0.1 * A, A), None)
        elif ld[i] and q >= 0.65:
            out[i] = (-1, F.stop_dist(-1, c[i], top + 0.1 * A, A), None)
    return out


RR = ("rr_1", "rr_1.5", "rr_2")


def _spec(extra):
    return lambda g: {**extra, "ema": (100, 200), "exit_mode": RR,
                      "last_entry_minute": g["last"]}


SPECS = {
    "tr_connors_rsi": (connors_rsi, _spec({"lo": (10, 20)}), False, None),
    "tr_golden_cross": (golden_cross, _spec({"ma": ("sma", "wma", "dema")}), False, None),
    "tr_donchian": (donchian, _spec({"n": (20, 40)}), False, None),
    "tr_vortex": (vortex, _spec({}), False, None),
    "tr_aroon": (aroon, _spec({}), False, None),
    "tr_supertrend": (supertrend, _spec({"mult": (2.0, 3.0)}), False, None),
    "tr_mfi": (mfi, _spec({"lo": (10, 20)}), False, None),
    "tr_psar_alligator": (psar_alligator, _spec({}), False, None),
    "tr_alligator": (alligator, _spec({}), False, None),
    "tr_fractal": (fractal, _spec({"mode": ("reversal", "breakout")}), False, None),
    "tr_ma_cross": (ma_cross, _spec({"fast": (5, 9)}), False, None),
    "tr_ichimoku": (ichimoku, _spec({"confirm": ("cross", "beepboop")}), False, None),
    "tr_oscillator_rebound": (oscillator_rebound, _spec({"osc": ("rsi", "cci", "willr", "stoch")}), False, None),
    "tr_bb_breakout": (bb_breakout, _spec({"dev": (2.0, 2.5)}), False, None),
    "tr_macd": (macd, _spec({"variant": ("zero", "zerolag", "stoch")}), False, None),
    "tr_trend_breakout": (trend_breakout, _spec({"k": (3, 5), "stop": ("level", "swing")}), False, None),
    "tr_livermore_pivot": (livermore_pivot, _spec({}), False, None),
    "tr_channel_break": (channel_break, _spec({"channel": ("ma", "keltner")}), False, None),
    "tr_rvi": (rvi, _spec({"zone": ("below", "above")}), False, None),
    "tr_zero_cross": (zero_cross, _spec({"osc": ("ppo", "trix", "cmo", "ao", "chaikin", "cmf")}), False, None),
    "tr_awesome_turn": (awesome_turn, _spec({"n": (2, 3)}), False, None),
    "tr_hma_cross": (hma_cross, _spec({"n": (50, 100)}), False, None),
    "tr_schaff": (schaff, _spec({"lo": (25, 10)}), False, None),
    "tr_kst": (kst, _spec({}), False, None),
    "tr_fisher": (fisher, _spec({"n": (9, 14)}), False, None),
    "tr_beep_boop": (beep_boop, _spec({"fast": (50, 34), "filter": ("none", "supertrend", "alligator")}), False, None),
    "tr_rsi_momentum": (rsi_momentum, _spec({"filter": ("supertrend", "cloud", "adx")}), False, None),
    "tr_engulfing": (engulfing, lambda g: {"ema": (100, 200), "exit_mode": ("time_1", "time_2", "rr_1.5"),
                                           "last_entry_minute": g["last"]}, False, None),
    "tr_extreme_level": (extreme_level, lambda g: {"days": (5, 20), "exit_mode": RR,
                                                   "last_entry_minute": g["last"]}, False, None),
    "tr_range_edge": (range_edge, lambda g: {"k": (3, 5), "tol": (1.0, 2.0), "osc": ("macd", "rsi", "pctb"),
                                             "exit_mode": ("rr_0.5", "rr_1", "rr_1.5"),
                                             "last_entry_minute": g["last"]}, False, None),
    "tr_darvas": (darvas, lambda g: {"n": (20, 50), "ema": (100, 200),
                                     "exit_mode": ("rr_1.5", "rr_3", "rr_5"),
                                     "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("ma", "mode", "confirm", "osc", "variant", "stop", "channel", "zone", "filter")
