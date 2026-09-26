"""The `fastbar` wave: textbook indicators at textbook settings, counted in BARS.

WHY THIS EXISTS. Every lookback in `cfd_families.periods` is a number of
SESSIONS converted to bars, so its shortest RSI is two days and its fastest
average one day, at every bar size. That was deliberate -- "20-day EMA" means
the same history at any timeframe -- and it means the study has never asked the
question a chart trader asks: does RSI(14) on a 5-minute chart, EMA 9/21, or a
20-bar Bollinger band do anything? Those settings are defined in bars and are
meant to shrink with the bar. This module is that question, and nothing else.

So every period here is a FIXED BAR COUNT. Run at 5m, RSI(14) is seventy
minutes; at 15m it is three and a half hours. The wave is intraday-scoped and is
meant for `--bar-minutes 5` and `15`.

WHAT IS SHARED WITH THE REST OF THE STUDY, AND WHAT IS NOT.

    kept     the engine, the live fill model, the cost, the sizing, the
             selection gates, next-bar-open entry, one entry a day, the
             session flatten
    changed  the exit grid (`FASTBAR_EXIT_MODES`): a 0.1 day-range stop, a
             6- and 24-bar clock exit, and a 0.25 day-range trail. The study's
             `trail_1.5` trails one and a half DAILY ranges behind the best
             close, which a 5-minute rule never reaches before the bell

THE DIRECTION AXIS MEANS THE SAME THING IN EVERY FAMILY. Each signal computes
the side its event points to -- an oscillator LEAVING oversold points up, a
close through the upper band points up, a bullish engulfing points up -- and
`follow` trades that side while `fade` trades the opposite. So "fade the
Bollinger break" and "buy the RSI leaving oversold" are both answerable in one
grid, and neither is baked in as the family's opinion.

HOW IT PLUGS IN. `FAMILIES` maps a name to `(signal, axes, keys)`. The
registry in `cfd_families` wraps each one in its `Family`, adds the shared
exit grid and the entry cutoff, and declares the read `fb:<name>`; `build` then
computes only the series those families can reach, into `ctx["fb"]`, keyed by
tuples like `("rsi", 14)`. Arrays are `array('d')` with NaN for "not yet":
8 bytes an element instead of a list's 32, and indexing still returns a plain
float, so a signal pays nothing for it. Every guard here relies on NaN
comparing False -- `nan < x` and `nan > x` are both False, so a missing reading
declines to fire without a single `is None` test.

CAUSAL, ALIGNED, BUILT ONCE -- the three rules `exness_indicators` states, and
they hold here: index `i` reads bars `0..i`, every array is as long as the bar
list, and nothing is windowed inside a signal.
"""
from __future__ import annotations

import math
from array import array
from itertools import combinations

import numpy
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter

from sandbox.research import exness_indicators as ind

TS, O, H, L, C, V = range(6)
NAN = float("nan")

#: The shared grid for this wave; see the module docstring for why it differs.
FASTBAR_EXIT_MODES = ("rr_1", "rr_2", "time_6", "time_24", "trail_0.25")
FASTBAR_STOP_DAY = (0.1, 0.2, 0.4)
FASTBAR_TREND = ("none", "ema_20d")
FASTBAR_VOL = ("none", "calm")

DIRECTION = ("follow", "fade")


# --------------------------------------------------------------------------- #
# numpy helpers. Every one returns a float64 array as long as its input, NaN
# through the warm-up.
# --------------------------------------------------------------------------- #

def _ffill(x):
    """Forward-fill NaN/inf after the first finite value, so a recursive filter
    does not turn one bad division into NaN for the rest of the series."""
    x = numpy.array(x, dtype=float)
    bad = ~numpy.isfinite(x)
    if not bad.any():
        return x
    idx = numpy.where(~bad, numpy.arange(len(x)), 0)
    numpy.maximum.accumulate(idx, out=idx)
    out = x[idx]
    first = numpy.argmax(~bad) if (~bad).any() else len(x)
    out[:first] = numpy.nan
    return out


def _first(x):
    finite = numpy.isfinite(x)
    return int(numpy.argmax(finite)) if finite.any() else len(x)


def ewm(x, alpha, warm):
    """Exponential smoothing seeded on the first finite value; NaN for `warm`-1
    bars after it."""
    x = _ffill(x)
    out = numpy.full(len(x), numpy.nan)
    f = _first(x)
    if f >= len(x):
        return out
    seg = x[f:]
    y, _ = lfilter([alpha], [1.0, alpha - 1.0], seg, zi=[(1.0 - alpha) * seg[0]])
    out[f:] = y
    out[f:f + max(0, warm - 1)] = numpy.nan
    return out


def ema(x, n):
    return ewm(x, 2.0 / (n + 1.0), n)


def wilder(x, n):
    return ewm(x, 1.0 / n, n)


def rolling(x, n, how):
    """Inclusive rolling statistic over the last `n` values."""
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), numpy.nan)
    if len(x) < n:
        return out
    win = sliding_window_view(x, n)
    out[n - 1:] = getattr(numpy, how)(win, axis=1)
    return out


def sma(x, n):
    return rolling(x, n, "mean")


def rsum(x, n):
    return rolling(x, n, "sum")


def rstd(x, n):
    return rolling(x, n, "std")


def shift(x, k):
    out = numpy.full(len(x), numpy.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def wma(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), numpy.nan)
    if len(x) < n:
        return out
    weights = numpy.arange(1, n + 1, dtype=float)
    out[n - 1:] = sliding_window_view(x, n) @ weights / weights.sum()
    return out


def stoch_of(x, hi, lo, n):
    """Where `x` sits in the `n`-bar range of `hi`/`lo`, 0..100."""
    top, bottom = rolling(hi, n, "max"), rolling(lo, n, "min")
    span = top - bottom
    with numpy.errstate(invalid="ignore", divide="ignore"):
        out = 100.0 * (x - bottom) / span
    out[span <= 0] = numpy.nan
    return out


def rsi_of(x, n):
    delta = numpy.diff(numpy.asarray(x, dtype=float), prepend=numpy.nan)
    up, down = wilder(numpy.clip(delta, 0, None), n), wilder(numpy.clip(-delta, 0, None), n)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        out = 100.0 - 100.0 / (1.0 + up / down)
    out[(down == 0) & (up > 0)] = 100.0
    out[(down == 0) & (up == 0)] = 50.0
    out[~numpy.isfinite(up) | ~numpy.isfinite(down)] = numpy.nan
    return out


def cross_events(a, b):
    """+1 on the bar `a` crosses above `b`, -1 below, 0 otherwise."""
    diff = a - b
    prev = shift(diff, 1)
    out = numpy.zeros(len(a))
    out[(prev <= 0) & (diff > 0)] = 1
    out[(prev >= 0) & (diff < 0)] = -1
    return out


class Data:
    """The bar list as numpy columns, plus a memo for shared intermediates."""

    def __init__(self, bars):
        arr = numpy.array([b[:6] for b in bars], dtype=float) if bars else numpy.zeros((0, 6))
        self.n = len(arr)
        self.o, self.h, self.l, self.c = arr[:, O], arr[:, H], arr[:, L], arr[:, C]
        vol = arr[:, V]
        self.has_volume = bool(numpy.nansum(vol) > 0)
        self.v = vol
        self.day = (arr[:, TS] // 86_400).astype(numpy.int64)
        self.bars = bars
        self.memo = {}

    def get(self, key, make):
        if key not in self.memo:
            self.memo[key] = make()
        return self.memo[key]

    @property
    def c1(self):
        return self.get("c1", lambda: shift(self.c, 1))

    def tr(self):
        def make():
            c1 = self.c1
            tr = numpy.fmax(self.h - self.l,
                            numpy.fmax(numpy.abs(self.h - c1), numpy.abs(self.l - c1)))
            tr[0] = self.h[0] - self.l[0]
            return tr
        return self.get("tr", make)

    def atr(self, n):
        return self.get(("atr", n), lambda: wilder(self.tr(), n))

    def ema(self, n):
        return self.get(("ema", n), lambda: ema(self.c, n))

    def sma(self, n):
        return self.get(("sma", n), lambda: sma(self.c, n))

    def std(self, n):
        return self.get(("std", n), lambda: rstd(self.c, n))

    def rsi(self, n):
        return self.get(("rsi", n), lambda: rsi_of(self.c, n))

    def stoch_k(self, n):
        return self.get(("stoch_k", n), lambda: stoch_of(self.c, self.h, self.l, n))

    def cci(self, n):
        def make():
            tp = (self.h + self.l + self.c) / 3.0
            mean = sma(tp, n)
            out = numpy.full(self.n, numpy.nan)
            if self.n >= n:
                win = sliding_window_view(tp, n)
                mad = numpy.abs(win - mean[n - 1:, None]).mean(axis=1)
                with numpy.errstate(invalid="ignore", divide="ignore"):
                    out[n - 1:] = (tp[n - 1:] - mean[n - 1:]) / (0.015 * mad)
            return out
        return self.get(("cci", n), make)

    def vwap(self):
        """Session VWAP and its volume-weighted standard deviation. Equal
        weights on a table with no volume, rather than a division by zero."""
        def make():
            weight = self.v if self.has_volume else numpy.ones(self.n)
            weight = numpy.nan_to_num(weight)
            price = (self.h + self.l + self.c) / 3.0
            start = numpy.r_[True, self.day[1:] != self.day[:-1]]
            group = numpy.cumsum(start) - 1

            def session_cumsum(x):
                total = numpy.cumsum(x)
                base = (total - x)[start]
                return total - base[group]

            cw = session_cumsum(weight)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                mean = session_cumsum(weight * price) / cw
                var = session_cumsum(weight * price * price) / cw - mean * mean
            return mean, numpy.sqrt(numpy.clip(var, 0, None))
        return self.get("vwap", make)

    def adx(self, n):
        def make():
            up = self.h - shift(self.h, 1)
            down = shift(self.l, 1) - self.l
            plus = numpy.where((up > down) & (up > 0), up, 0.0)
            minus = numpy.where((down > up) & (down > 0), down, 0.0)
            tr = wilder(self.tr(), n)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                pdi = 100.0 * wilder(plus, n) / tr
                mdi = 100.0 * wilder(minus, n) / tr
                dx = 100.0 * numpy.abs(pdi - mdi) / (pdi + mdi)
            return pdi, mdi, wilder(dx, n)
        return self.get(("adx", n), make)

    def supertrend(self, period, multiple):
        def make():
            atr = [None if not math.isfinite(a) else a for a in self.atr(period)]
            direction, _ = ind.supertrend(self.bars, atr, multiple)
            return numpy.array([numpy.nan if d is None else d for d in direction])
        return self.get(("st", period, multiple), make)

    def ichimoku(self, scale):
        def make():
            t, k, s = (max(2, round(9 * scale)), max(3, round(26 * scale)),
                       max(4, round(52 * scale)))
            tenkan = (rolling(self.h, t, "max") + rolling(self.l, t, "min")) / 2
            kijun = (rolling(self.h, k, "max") + rolling(self.l, k, "min")) / 2
            span_a = shift((tenkan + kijun) / 2, k)
            span_b = shift((rolling(self.h, s, "max") + rolling(self.l, s, "min")) / 2, k)
            return tenkan, kijun, numpy.fmax(span_a, span_b), numpy.fmin(span_a, span_b)
        return self.get(("ichi", scale), make)

    def heikin(self):
        def make():
            ha_close = (self.o + self.h + self.l + self.c) / 4.0
            ha_open = numpy.empty(self.n)
            if self.n:
                ha_open[0] = (self.o[0] + self.c[0]) / 2.0
                a = 0.5
                # ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2
                ha_open[1:], _ = lfilter([a], [1.0, -a], ha_close[:-1],
                                         zi=[a * ha_open[0]])
            color = numpy.sign(ha_close - ha_open)
            run = numpy.zeros(self.n)
            for i in range(1, self.n):
                run[i] = run[i - 1] + 1 if color[i] == color[i - 1] and color[i] != 0 else 1
            return color, run
        return self.get("heikin", make)

    def obv(self):
        return self.get("obv", lambda: numpy.cumsum(
            numpy.sign(numpy.nan_to_num(self.c - self.c1)) * numpy.nan_to_num(self.v)))

    def force(self, n):
        return self.get(("force", n), lambda: ema(
            numpy.nan_to_num((self.c - self.c1) * self.v), n))

    def mfi(self, n):
        def make():
            tp = (self.h + self.l + self.c) / 3.0
            flow = tp * self.v
            rising = tp > shift(tp, 1)
            pos, neg = rsum(numpy.where(rising, flow, 0.0), n), rsum(numpy.where(~rising, flow, 0.0), n)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                out = 100.0 - 100.0 / (1.0 + pos / neg)
            out[(neg == 0) & (pos > 0)] = 100.0
            out[(pos + neg) == 0] = numpy.nan
            return out
        return self.get(("mfi", n), make)


# --------------------------------------------------------------------------- #
# series. `compute(key, data)` returns one float64 array for one context key.
# --------------------------------------------------------------------------- #

def _ma_kind(d, kind, n):
    c = d.c
    if kind == "ema":
        return ema(c, n)
    if kind == "smma":
        return wilder(c, n)
    if kind == "dema":
        e = ema(c, n)
        return 2 * e - ema(e, n)
    if kind == "zlema":
        lag = (n - 1) // 2
        return ema(c + (c - shift(c, lag)), n)
    if kind == "t3":
        a = 0.7
        e1 = ema(c, n); e2 = ema(e1, n); e3 = ema(e2, n)
        e4 = ema(e3, n); e5 = ema(e4, n); e6 = ema(e5, n)
        c1, c2 = -a ** 3, 3 * a * a + 3 * a ** 3
        c3, c4 = -6 * a * a - 3 * a - 3 * a ** 3, 1 + 3 * a + a ** 3 + 3 * a * a
        return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3
    if kind == "alma":
        offset, sigma = 0.85, 6.0
        m, s = offset * (n - 1), n / sigma
        w = numpy.exp(-((numpy.arange(n) - m) ** 2) / (2 * s * s))
        out = numpy.full(len(c), numpy.nan)
        if len(c) >= n:
            out[n - 1:] = sliding_window_view(c, n) @ w / w.sum()
        return out
    if kind == "mcginley":
        out = numpy.full(len(c), numpy.nan)
        if len(c) < n:
            return out
        md = float(numpy.mean(c[:n]))
        out[n - 1] = md
        for i in range(n, len(c)):
            ratio = c[i] / md if md else 1.0
            md += (c[i] - md) / (0.6 * n * ratio ** 4)
            out[i] = md
        return out
    if kind == "vidya":
        delta = numpy.diff(c, prepend=numpy.nan)
        up = rsum(numpy.clip(delta, 0, None), 9)
        down = rsum(numpy.clip(-delta, 0, None), 9)
        with numpy.errstate(invalid="ignore", divide="ignore"):
            k = numpy.abs((up - down) / (up + down))
        alpha = 2.0 / (n + 1.0) * numpy.nan_to_num(k)
        out = numpy.full(len(c), numpy.nan)
        start = max(n, 10)
        if len(c) <= start:
            return out
        value = float(c[start - 1])
        for i in range(start, len(c)):
            value += alpha[i] * (c[i] - value)
            out[i] = value
        return out
    raise KeyError(kind)


def _candle_pattern(d, pattern, context):
    o, h, l, c = d.o, d.h, d.l, d.c
    body, rng = c - o, h - l
    upper = h - numpy.fmax(o, c)
    lower = numpy.fmin(o, c) - l
    b1, o1, c1 = shift(body, 1), shift(o, 1), shift(c, 1)
    with numpy.errstate(invalid="ignore"):
        bull = numpy.zeros(d.n, dtype=bool)
        bear = numpy.zeros(d.n, dtype=bool)
        reversal = True
        if pattern == "engulfing":
            bull = (b1 < 0) & (body > 0) & (c >= o1) & (o <= c1)
            bear = (b1 > 0) & (body < 0) & (c <= o1) & (o >= c1)
        elif pattern == "hammer":
            small = numpy.abs(body)
            bull = (lower >= 2 * small) & (upper <= 0.3 * rng) & (rng > 0)
            bear = (upper >= 2 * small) & (lower <= 0.3 * rng) & (rng > 0)
        elif pattern == "star":
            b2, r2, r1 = shift(body, 2), shift(rng, 2), shift(rng, 1)
            mid2 = shift(o, 2) + b2 / 2
            bull = (b2 < -0.5 * r2) & (numpy.abs(b1) < 0.3 * r1) & (body > 0) & (c > mid2)
            bear = (b2 > 0.5 * r2) & (numpy.abs(b1) < 0.3 * r1) & (body < 0) & (c < mid2)
        elif pattern == "harami":
            bull = (b1 < 0) & (body > 0) & (o >= c1) & (c <= o1)
            bear = (b1 > 0) & (body < 0) & (o <= c1) & (c >= o1)
        elif pattern == "doji":
            doji = (numpy.abs(body) <= 0.1 * rng) & (rng > 0)
            prior = c1 - shift(c, 4)
            bull = doji & (prior < 0)
            bear = doji & (prior > 0)
        elif pattern == "three":
            reversal = False
            c2, c3 = shift(c, 2), shift(c, 3)
            bull = (body > 0) & (b1 > 0) & (shift(body, 2) > 0) & (c > c1) & (c1 > c2) & (c2 > c3)
            bear = (body < 0) & (b1 < 0) & (shift(body, 2) < 0) & (c < c1) & (c1 < c2) & (c2 < c3)
        elif pattern == "marubozu":
            reversal = False
            big = (numpy.abs(body) >= 0.9 * rng) & (rng > 0)
            bull, bear = big & (body > 0), big & (body < 0)
        elif pattern == "outside":
            outside = (h > shift(h, 1)) & (l < shift(l, 1))
            bull, bear = outside & (body > 0), outside & (body < 0)
        else:
            raise KeyError(pattern)
        # LOCATION. A reversal pattern counts only where it made an n-bar
        # extreme; a continuation pattern only where it closed through one.
        # Without this every hammer in the middle of a range is a signal, which
        # is not the pattern anybody means.
        prior_high = shift(rolling(h, context, "max"), 3 if reversal else 1)
        prior_low = shift(rolling(l, context, "min"), 3 if reversal else 1)
        if reversal:
            recent_low = rolling(l, 3, "min")
            recent_high = rolling(h, 3, "max")
            bull &= recent_low < prior_low
            bear &= recent_high > prior_high
        else:
            bull &= c > prior_high
            bear &= c < prior_low
    return bull.astype(float) - bear.astype(float)


def _zone(d, name):
    """+1 where the oscillator reads oversold, -1 overbought, 0 otherwise.
    Classic settings, fixed -- these are the vocabulary of the combinations,
    not axes of their own."""
    with numpy.errstate(invalid="ignore"):
        if name == "rsi":
            x = d.rsi(14); return (x < 30).astype(float) - (x > 70)
        if name == "rsi2":
            x = d.rsi(2); return (x < 10).astype(float) - (x > 90)
        if name == "stoch":
            x = d.stoch_k(14); return (x < 20).astype(float) - (x > 80)
        if name == "cci":
            x = d.cci(20); return (x < -100).astype(float) - (x > 100)
        if name == "bb":
            m, s = d.sma(20), d.std(20)
            return (d.c < m - 2 * s).astype(float) - (d.c > m + 2 * s)
        if name == "vwap":
            m, s = d.vwap()
            return (d.c < m - 2 * s).astype(float) - (d.c > m + 2 * s)
        if name == "mfi":
            x = d.mfi(14); return (x < 20).astype(float) - (x > 80)
        if name == "crsi":
            x = compute(("crsi",), d); return (x < 10).astype(float) - (x > 90)
    raise KeyError(name)


def _trend_dir(d, kind, bars_):
    """+1 up, -1 down, 0 undecided, for the pullback filter at `bars_` scale."""
    with numpy.errstate(invalid="ignore"):
        if kind == "ema":
            e = d.ema(bars_)
            return (d.c > e).astype(float) - (d.c < e)
        if kind == "supertrend":
            return numpy.nan_to_num(d.supertrend(max(7, bars_ // 10), 3.0))
        if kind == "ichimoku":
            _, _, top, bottom = d.ichimoku(bars_ / 52.0)
            return (d.c > top).astype(float) - (d.c < bottom)
        if kind == "adx":
            pdi, mdi, adx = d.adx(max(7, bars_ // 7))
            strong = adx > 20
            return (strong & (pdi > mdi)).astype(float) - (strong & (mdi > pdi))
    raise KeyError(kind)


def compute(key, d):
    name, args = key[0], key[1:]
    c, h, l, v = d.c, d.h, d.l, d.v
    with numpy.errstate(invalid="ignore", divide="ignore"):
        if name in ("rsi", "ema", "sma", "std", "cci", "mfi", "force"):
            return getattr(d, name)(*args)
        if name == "atr":
            return d.atr(*args)
        if name == "stoch_k":
            return d.stoch_k(*args)
        if name == "stoch_d":
            return sma(d.stoch_k(args[0]), 3)
        if name == "wr":
            return d.stoch_k(args[0]) - 100.0
        if name in ("srsi_k", "srsi_d"):
            n = args[0]
            r = d.rsi(n)
            k = sma(stoch_of(r, r, r, n), 3)
            return k if name == "srsi_k" else sma(k, 3)
        if name == "cmo":
            n = args[0]
            delta = numpy.diff(c, prepend=numpy.nan)
            up, down = rsum(numpy.clip(delta, 0, None), n), rsum(numpy.clip(-delta, 0, None), n)
            return 100.0 * (up - down) / (up + down)
        if name == "uo":
            b = args[0]
            low = numpy.fmin(l, d.c1)
            bp = c - low
            tr = numpy.fmax(h, d.c1) - low
            avg = [rsum(bp, k) / rsum(tr, k) for k in (b, 2 * b, 4 * b)]
            return 100.0 * (4 * avg[0] + 2 * avg[1] + avg[2]) / 7.0
        if name in ("tsi", "tsi_sig"):
            r = args[0]
            s = max(2, round(r / 2))
            m = c - d.c1
            tsi = 100.0 * ema(ema(m, r), s) / ema(ema(numpy.abs(m), r), s)
            return tsi if name == "tsi" else ema(tsi, 7)
        if name == "crsi":
            streak = numpy.zeros(d.n)
            for i in range(1, d.n):
                if c[i] > c[i - 1]:
                    streak[i] = streak[i - 1] + 1 if streak[i - 1] > 0 else 1
                elif c[i] < c[i - 1]:
                    streak[i] = streak[i - 1] - 1 if streak[i - 1] < 0 else -1
            roc = c / d.c1 - 1.0
            rank = numpy.full(d.n, numpy.nan)
            if d.n > 101:
                win = sliding_window_view(roc, 101)
                rank[100:] = 100.0 * (win[:, :-1] < win[:, -1:]).mean(axis=1)
                rank[:101] = numpy.nan
            return (d.rsi(3) + rsi_of(streak, 2) + rank) / 3.0
        if name == "lrsi":
            g = args[0]
            out = numpy.full(d.n, numpy.nan)
            l0 = l1 = l2 = l3 = float(c[0]) if d.n else 0.0
            for i in range(d.n):
                p0, p1, p2 = l0, l1, l2
                l0 = (1 - g) * c[i] + g * l0
                l1 = -g * l0 + p0 + g * l1
                l2 = -g * l1 + p1 + g * l2
                l3 = -g * l2 + p2 + g * l3
                cu = max(l0 - l1, 0) + max(l1 - l2, 0) + max(l2 - l3, 0)
                cd = max(l1 - l0, 0) + max(l2 - l1, 0) + max(l3 - l2, 0)
                if i >= 10 and cu + cd > 0:
                    out[i] = cu / (cu + cd)
            return out
        if name in ("rvi", "rvi_sig"):
            n = args[0]

            def swma(x):
                return (x + 2 * shift(x, 1) + 2 * shift(x, 2) + shift(x, 3)) / 6.0
            rvi = rsum(swma(c - d.o), n) / rsum(swma(h - l), n)
            return rvi if name == "rvi" else swma(rvi)
        if name == "bop":
            rng = h - l
            raw = numpy.where(rng > 0, (c - d.o) / numpy.where(rng > 0, rng, 1), 0.0)
            return sma(raw, args[0])
        if name == "ma":
            return _ma_kind(d, *args)
        if name in ("macd", "macd_sig"):
            f, s, g = args
            line = ema(c, f) - ema(c, s)
            return line if name == "macd" else ema(line, g)
        if name in ("trix", "trix_sig"):
            n = args[0]
            e3 = ema(ema(ema(c, n), n), n)
            trix = 1e4 * (e3 / shift(e3, 1) - 1.0)
            return trix if name == "trix" else ema(trix, 9)
        if name == "roc_atr":
            return (c - shift(c, args[0])) / d.atr(14)
        if name in ("kst", "kst_sig"):
            s = args[0]

            def r(n):
                n = max(1, round(n * s))
                return 100.0 * (c / shift(c, n) - 1.0)

            def m(x, n):
                return sma(x, max(1, round(n * s)))
            kst = (m(r(10), 10) + 2 * m(r(15), 10) + 3 * m(r(20), 10)
                   + 4 * m(r(30), 15))
            return kst if name == "kst" else sma(kst, max(2, round(9 * s)))
        if name in ("ao", "ac"):
            f, s = args
            mid = (h + l) / 2.0
            ao = sma(mid, f) - sma(mid, s)
            return ao if name == "ao" else ao - sma(ao, 5)
        if name == "dpo_z":
            n = args[0]
            dpo = shift(c, n // 2 + 1) - d.sma(n)
            return dpo / d.std(n)
        if name == "coppock":
            s = args[0]
            roc = (100.0 * (c / shift(c, max(1, round(14 * s))) - 1.0)
                   + 100.0 * (c / shift(c, max(1, round(11 * s))) - 1.0))
            return wma(roc, max(2, round(10 * s)))
        if name == "stc":
            cycle = args[0]
            macd = ema(c, 23) - ema(c, 50)
            k1 = _ffill(stoch_of(macd, macd, macd, cycle))
            p1 = ewm(k1, 0.5, 1)
            k2 = _ffill(stoch_of(p1, p1, p1, cycle))
            return ewm(k2, 0.5, 1)
        if name in ("vip", "vim"):
            n = args[0]
            move = numpy.abs(h - shift(l, 1)) if name == "vip" else numpy.abs(l - shift(h, 1))
            return rsum(move, n) / rsum(d.tr(), n)
        if name in ("pdi", "mdi", "adx"):
            pdi, mdi, adx = d.adx(args[0])
            return {"pdi": pdi, "mdi": mdi, "adx": adx}[name]
        if name == "aroon_osc":
            n = args[0]
            out = numpy.full(d.n, numpy.nan)
            if d.n > n:
                win_h = sliding_window_view(h, n + 1)
                win_l = sliding_window_view(l, n + 1)
                up = n - numpy.argmax(win_h[:, ::-1], axis=1)
                down = n - numpy.argmin(win_l[:, ::-1], axis=1)
                out[n:] = 100.0 * (up - down) / n
            return out
        if name in ("tenkan", "kijun", "cloud_top", "cloud_bot"):
            parts = d.ichimoku(args[0])
            return parts[("tenkan", "kijun", "cloud_top", "cloud_bot").index(name)]
        if name == "st":
            return d.supertrend(*args)
        if name == "psar_flip":
            _, flip = ind.parabolic_sar(d.bars, args[0], 0.2)
            return numpy.array(flip, dtype=float)
        if name in ("ha_color", "ha_run"):
            color, run = d.heikin()
            return color if name == "ha_color" else run
        if name == "alligator":
            s = args[0]
            mid = (h + l) / 2.0
            jaw = shift(wilder(mid, max(2, round(13 * s))), max(1, round(8 * s)))
            teeth = shift(wilder(mid, max(2, round(8 * s))), max(1, round(5 * s)))
            lips = shift(wilder(mid, max(2, round(5 * s))), max(1, round(3 * s)))
            up = (lips > teeth) & (teeth > jaw) & (c > lips)
            down = (lips < teeth) & (teeth < jaw) & (c < lips)
            state = up.astype(float) - down
            prev = shift(state, 1)
            return numpy.where(state != prev, state, 0.0)
        if name in ("bull_power", "bear_power"):
            e = d.ema(args[0])
            return h - e if name == "bull_power" else l - e
        if name == "eom":
            n = args[0]
            mid = (h + l) / 2.0
            move = mid - shift(mid, 1)
            ratio = numpy.where(v > 0, move * (h - l) / numpy.where(v > 0, v, 1), 0.0)
            return sma(ratio, n)
        if name == "chaikin":
            s = args[0]
            rng = h - l
            mfm = numpy.where(rng > 0, ((c - l) - (h - c)) / numpy.where(rng > 0, rng, 1), 0.0)
            adl = numpy.cumsum(numpy.nan_to_num(mfm * v))
            return ema(adl, 3 * s) - ema(adl, 10 * s)
        if name in ("kvo", "kvo_sig"):
            s = args[0]
            tp = h + l + c
            vf = numpy.sign(numpy.nan_to_num(tp - shift(tp, 1))) * v
            kvo = ema(vf, max(2, round(34 * s))) - ema(vf, max(3, round(55 * s)))
            return kvo if name == "kvo" else ema(kvo, max(2, round(13 * s)))
        if name == "obv":
            return d.obv()
        if name == "obv_ema":
            return ema(d.obv(), args[0])
        if name in ("vwap", "vwap_sd"):
            mean, sd = d.vwap()
            return mean if name == "vwap" else sd
        if name == "squeeze":
            n, kc = args
            m, s = d.sma(n), d.std(n)
            atr = d.atr(n)
            on = (m + 2 * s < m + kc * atr) & (m - 2 * s > m - kc * atr)
            fired = shift(on.astype(float), 1) == 1
            fired &= ~on
            middle = ((rolling(h, n, "max") + rolling(l, n, "min")) / 2 + m) / 2
            return numpy.where(fired, numpy.sign(c - middle), 0.0)
        if name in ("hh", "ll"):
            n = args[0]
            return shift(rolling(h if name == "hh" else l, n, "max" if name == "hh" else "min"), 1)
        if name == "chop":
            n = args[0]
            span = rolling(h, n, "max") - rolling(l, n, "min")
            return 100.0 * numpy.log10(rsum(d.tr(), n) / span) / math.log10(n)
        if name == "mass_event":
            s = args[0]
            rng = h - l
            e1 = ema(rng, 9)
            mi = rsum(e1 / ema(e1, 9), s)
            top, trigger = 27.0 * s / 25.0, 26.5 * s / 25.0
            slope = numpy.sign(d.ema(9) - shift(d.ema(9), 1))
            out = numpy.zeros(d.n)
            armed = False
            for i in range(d.n):
                x = mi[i]
                if not math.isfinite(x):
                    continue
                if x > top:
                    armed = True
                elif armed and x < trigger:
                    armed = False
                    # A reversal bulge: the event points AGAINST the trend.
                    out[i] = -slope[i] if math.isfinite(slope[i]) else 0.0
            return out
        if name in ("lr_fit", "lr_se"):
            n = args[0]
            fit = numpy.full(d.n, numpy.nan)
            se = numpy.full(d.n, numpy.nan)
            if d.n >= n:
                x = numpy.arange(n, dtype=float) - (n - 1) / 2.0
                sxx = float(x @ x)
                for lo in range(0, d.n - n + 1, 50_000):
                    hi = min(d.n - n + 1, lo + 50_000)
                    win = sliding_window_view(c, n)[lo:hi]
                    mean = win.mean(axis=1)
                    sxy = (win - mean[:, None]) @ x
                    slope = sxy / sxx
                    syy = ((win - mean[:, None]) ** 2).sum(axis=1)
                    resid = numpy.clip(syy - slope * sxy, 0, None) / max(1, n - 2)
                    fit[lo + n - 1:hi + n - 1] = mean + slope * (n - 1) / 2.0
                    se[lo + n - 1:hi + n - 1] = numpy.sqrt(resid)
            return fit if name == "lr_fit" else se
        if name == "candle":
            return _candle_pattern(d, *args)
        if name in ("cam_h3", "cam_l3", "cam_h4", "cam_l4"):
            days, first = numpy.unique(d.day, return_index=True)
            last = numpy.r_[first[1:] - 1, d.n - 1]
            dh = numpy.maximum.reduceat(h, first)
            dl = numpy.minimum.reduceat(l, first)
            dc = c[last]
            width = {"cam_h3": 1.1 / 4, "cam_l3": -1.1 / 4,
                     "cam_h4": 1.1 / 2, "cam_l4": -1.1 / 2}[name]
            level = dc + width * (dh - dl)
            prior = numpy.r_[numpy.nan, level[:-1]]
            return prior[numpy.searchsorted(days, d.day)]
        if name == "zone":
            return _zone(d, args[0])
        if name == "trend_dir":
            return _trend_dir(d, *args)
        if name in ("bull_votes", "bear_votes"):
            stack = d.get("votes", lambda: _voters(d))
            return ((stack > 0).sum(axis=0) if name == "bull_votes"
                    else (stack < 0).sum(axis=0)).astype(float)
        if name == "ema_slope":
            e = d.ema(args[0])
            return numpy.sign(e - shift(e, 1))
        if name == "vol_ratio":
            return v / shift(sma(v, 20), 1)
    raise KeyError(key)


def _voters(d):
    """The ten trend readings `fb_vote` counts, one row each: +1, -1 or 0."""
    def sign(x):
        return numpy.where(numpy.isfinite(x), numpy.sign(x), 0.0)
    with numpy.errstate(invalid="ignore"):
        return numpy.vstack([
            sign(d.ema(9) - d.ema(21)),
            sign(compute(("macd", 12, 26, 9), d) - compute(("macd_sig", 12, 26, 9), d)),
            sign(d.rsi(14) - 50),
            numpy.nan_to_num(d.supertrend(10, 3.0)),
            sign(compute(("vip", 14), d) - compute(("vim", 14), d)),
            sign(compute(("aroon_osc", 25), d)),
            sign(compute(("tsi", 25), d) - compute(("tsi_sig", 25), d)),
            sign(d.c - d.ichimoku(1.0)[1]),
            _psar_side(d),
            d.heikin()[0],
        ])


def _psar_side(d):
    def make():
        _, flip = ind.parabolic_sar(d.bars, 0.02, 0.2)
        side = numpy.zeros(d.n)
        state = 0.0
        for i, f in enumerate(flip):
            if f:
                state = float(f)
            side[i] = state
        return side
    return d.get("psar_side", make)


# --------------------------------------------------------------------------- #
# signal helpers. `i` is the bar index; every read is `array('d')`, NaN-safe.
# --------------------------------------------------------------------------- #

def _late(ctx, bar, params):
    return (not ctx["daily"]
            and bar[TS] % 86_400 // 60 > params["last_entry_minute"])


def _out(side, params):
    if not side:
        return None
    return side if params["direction"] == "follow" else -side


def _leave(series, i, low, high):
    """+1 on the bar an oscillator climbs back above `low`, -1 back below `high`."""
    prev, now = series[i - 1], series[i]
    if prev < low <= now:
        return 1
    if prev > high >= now:
        return -1
    return None


def _cross(a, b, i):
    before, now = a[i - 1] - b[i - 1], a[i] - b[i]
    if before <= 0 < now:
        return 1
    if before >= 0 > now:
        return -1
    return None


def _zero(a, i):
    before, now = a[i - 1], a[i]
    if before <= 0 < now:
        return 1
    if before >= 0 > now:
        return -1
    return None


def _band_break(price, i, upper, lower):
    """+1 on the close that crosses above `upper`, -1 below `lower`."""
    if price[i - 1] <= upper[i - 1] and price[i] > upper[i]:
        return 1
    if price[i - 1] >= lower[i - 1] and price[i] < lower[i]:
        return -1
    return None


def _band_reenter(price, i, upper, lower):
    """+1 on the close back INSIDE from below `lower`, -1 back inside from above."""
    if price[i - 1] < lower[i - 1] and price[i] >= lower[i]:
        return 1
    if price[i - 1] > upper[i - 1] and price[i] <= upper[i]:
        return -1
    return None


def _event(series, i):
    value = series[i]
    return int(value) if value == value and value else None


def _signal(body):
    """Wrap a `(i, f, closes, params) -> side` body into the engine's
    signature: entry cutoff first, warm-up guard, then the direction flip."""
    def signal(index, bars, ctx, params, _state):
        if index < 2 or _late(ctx, bars[index], params):
            return None
        return _out(body(index, ctx["fb"], ctx["fb"]["close"], params), params)
    signal.__name__ = body.__name__.lstrip("_") + "_signal"
    signal.__doc__ = body.__doc__
    return signal


# --------------------------------------------------------------------------- #
# the families
# --------------------------------------------------------------------------- #

FAMILIES = {}


def family(axes, keys):
    def register(body):
        name = "fb_" + body.__name__.lstrip("_")
        FAMILIES[name] = (_signal(body), dict(axes, direction=DIRECTION), keys)
        return body
    return register


# ---- oscillators: the bar they LEAVE an extreme ------------------------------

@family({"period": (2, 5, 9, 14), "threshold": (70.0, 80.0, 90.0)},
        lambda a: {("rsi", n) for n in a["period"]})
def _rsi(i, f, c, p):
    """RSI(n bars) back out of its extreme. RSI(2) at 90/10 is Connors'."""
    return _leave(f[("rsi", p["period"])], i, 100.0 - p["threshold"], p["threshold"])


@family({"period": (5, 9, 14, 21), "zone_edge": (20.0, 30.0)},
        lambda a: {k for n in a["period"] for k in (("stoch_k", n), ("stoch_d", n))})
def _stoch(i, f, c, p):
    """%K crossing %D (3-bar) inside the outer band -- the textbook slow cross."""
    k, dline = f[("stoch_k", p["period"])], f[("stoch_d", p["period"])]
    side = _cross(k, dline, i)
    if side == 1 and k[i - 1] < p["zone_edge"]:
        return 1
    if side == -1 and k[i - 1] > 100.0 - p["zone_edge"]:
        return -1
    return None


@family({"period": (7, 14, 28), "zone_edge": (10.0, 20.0)},
        lambda a: {("wr", n) for n in a["period"]})
def _williams(i, f, c, p):
    """Williams %R leaving -100+band / -band."""
    return _leave(f[("wr", p["period"])], i, -100.0 + p["zone_edge"], -p["zone_edge"])


@family({"period": (7, 14), "zone_edge": (10.0, 20.0)},
        lambda a: {k for n in a["period"] for k in (("srsi_k", n), ("srsi_d", n))})
def _stochrsi(i, f, c, p):
    """Stochastic RSI: %K crossing %D with %K in the outer band."""
    k, dline = f[("srsi_k", p["period"])], f[("srsi_d", p["period"])]
    side = _cross(k, dline, i)
    if side == 1 and k[i - 1] < p["zone_edge"]:
        return 1
    if side == -1 and k[i - 1] > 100.0 - p["zone_edge"]:
        return -1
    return None


@family({"period": (10, 14, 20, 40), "threshold": (100.0, 150.0, 200.0)},
        lambda a: {("cci", n) for n in a["period"]})
def _cci(i, f, c, p):
    """CCI back inside +/-level."""
    return _leave(f[("cci", p["period"])], i, -p["threshold"], p["threshold"])


@family({"period": (9, 14, 20), "threshold": (30.0, 50.0)},
        lambda a: {("cmo", n) for n in a["period"]})
def _cmo(i, f, c, p):
    """Chande momentum oscillator back inside +/-level."""
    return _leave(f[("cmo", p["period"])], i, -p["threshold"], p["threshold"])


@family({"base": (4, 7), "threshold": (25.0, 30.0)},
        lambda a: {("uo", b) for b in a["base"]})
def _ultimate(i, f, c, p):
    """Ultimate oscillator (base, 2x, 4x) back out of level / 100-level."""
    return _leave(f[("uo", p["base"])], i, p["threshold"], 100.0 - p["threshold"])


@family({"period": (13, 25)},
        lambda a: {k for n in a["period"] for k in (("tsi", n), ("tsi_sig", n))})
def _tsi(i, f, c, p):
    """True strength index crossing its 7-bar signal line."""
    return _cross(f[("tsi", p["period"])], f[("tsi_sig", p["period"])], i)


@family({"threshold": (5.0, 10.0, 15.0)}, lambda a: {("crsi",)})
def _crsi(i, f, c, p):
    """Connors RSI (3, 2, 100) back out of level / 100-level."""
    return _leave(f[("crsi",)], i, p["threshold"], 100.0 - p["threshold"])


@family({"gamma": (0.5, 0.65, 0.8), "threshold": (0.15, 0.2)},
        lambda a: {("lrsi", g) for g in a["gamma"]})
def _laguerre(i, f, c, p):
    """Ehlers' Laguerre RSI back out of level / 1-level."""
    return _leave(f[("lrsi", p["gamma"])], i, p["threshold"], 1.0 - p["threshold"])


@family({"period": (10, 20)},
        lambda a: {k for n in a["period"] for k in (("rvi", n), ("rvi_sig", n))})
def _rvi(i, f, c, p):
    """Relative vigor index crossing its signal."""
    return _cross(f[("rvi", p["period"])], f[("rvi_sig", p["period"])], i)


@family({"period": (14, 28), "threshold": (0.1, 0.2)},
        lambda a: {("bop", n) for n in a["period"]})
def _bop(i, f, c, p):
    """Smoothed balance of power crossing INTO +/-level: buyers took over."""
    x = f[("bop", p["period"])]
    if x[i - 1] <= p["threshold"] < x[i]:
        return 1
    if x[i - 1] >= -p["threshold"] > x[i]:
        return -1
    return None


@family({"period": (7, 14), "threshold": (80.0, 90.0)},
        lambda a: {("mfi", n) for n in a["period"]})
def _mfi(i, f, c, p):
    """Money flow index back out of its extreme. Needs real volume."""
    return _leave(f[("mfi", p["period"])], i, 100.0 - p["threshold"], p["threshold"])


# ---- averages ----------------------------------------------------------------

@family({"fast_bars": (5, 9, 13), "slow_bars": (21, 34, 55)},
        lambda a: {("ema", n) for n in (*a["fast_bars"], *a["slow_bars"])})
def _ema_cross(i, f, c, p):
    """EMA(fast) crossing EMA(slow): 9/21, 5/34 and the rest."""
    return _cross(f[("ema", p["fast_bars"])], f[("ema", p["slow_bars"])], i)


MA_ZOO = ("ema", "smma", "dema", "zlema", "t3", "alma", "mcginley", "vidya")


@family({"ma": MA_ZOO, "period": (10, 20, 50)},
        lambda a: {("ma", k, n) for k in a["ma"] for n in a["period"]})
def _ma_zoo(i, f, c, p):
    """The close crossing one average -- the eight the `xma` zoo left out."""
    return _cross(c, f[("ma", p["ma"], p["period"])], i)


MACD_SETS = ((12, 26, 9), (8, 17, 9), (5, 35, 5), (3, 10, 16))


@family({"macd_set": MACD_SETS, "line": ("signal", "zero")},
        lambda a: {k for s in a["macd_set"] for k in (("macd", *s), ("macd_sig", *s))})
def _macd(i, f, c, p):
    """MACD crossing its signal line, or crossing zero."""
    s = p["macd_set"]
    line = f[("macd", *s)]
    return _cross(line, f[("macd_sig", *s)], i) if p["line"] == "signal" else _zero(line, i)


@family({"period": (9, 15, 30), "line": ("signal", "zero")},
        lambda a: {k for n in a["period"] for k in (("trix", n), ("trix_sig", n))})
def _trix(i, f, c, p):
    """TRIX crossing its 9-bar signal, or zero."""
    x = f[("trix", p["period"])]
    return _cross(x, f[("trix_sig", p["period"])], i) if p["line"] == "signal" else _zero(x, i)


@family({"lookback": (3, 6, 12, 24), "threshold_atr": (1.0, 2.0, 3.0)},
        lambda a: {("roc_atr", n) for n in a["lookback"]})
def _roc(i, f, c, p):
    """The n-bar move crossing +/-k bar-ATRs: plain short-horizon momentum."""
    x, k = f[("roc_atr", p["lookback"])], p["threshold_atr"]
    if x[i - 1] <= k < x[i]:
        return 1
    if x[i - 1] >= -k > x[i]:
        return -1
    return None


@family({"scale": (0.5, 1.0)},
        lambda a: {k for s in a["scale"] for k in (("kst", s), ("kst_sig", s))})
def _kst(i, f, c, p):
    """Pring's Know Sure Thing crossing its signal, periods scaled."""
    return _cross(f[("kst", p["scale"])], f[("kst_sig", p["scale"])], i)


@family({"ao_set": ((5, 34), (3, 10)), "mode": ("ao", "ac")},
        lambda a: {(m, *s) for s in a["ao_set"] for m in a["mode"]})
def _awesome(i, f, c, p):
    """Bill Williams' awesome oscillator, or its accelerator, crossing zero."""
    return _zero(f[(p["mode"], *p["ao_set"])], i)


@family({"period": (10, 20, 40), "threshold_z": (1.5, 2.0)},
        lambda a: {("dpo_z", n) for n in a["period"]})
def _dpo(i, f, c, p):
    """Detrended price oscillator back inside +/-z of its window's sigma."""
    return _leave(f[("dpo_z", p["period"])], i, -p["threshold_z"], p["threshold_z"])


@family({"scale": (0.5, 1.0)}, lambda a: {("coppock", s) for s in a["scale"]})
def _coppock(i, f, c, p):
    """The Coppock curve crossing zero."""
    return _zero(f[("coppock", p["scale"])], i)


@family({"cycle": (5, 10, 20)}, lambda a: {("stc", n) for n in a["cycle"]})
def _schaff(i, f, c, p):
    """Schaff trend cycle (23, 50) leaving 25 upward or 75 downward."""
    return _leave(f[("stc", p["cycle"])], i, 25.0, 75.0)


# ---- trend / direction -------------------------------------------------------

@family({"period": (7, 14, 21)},
        lambda a: {k for n in a["period"] for k in (("vip", n), ("vim", n))})
def _vortex(i, f, c, p):
    """VI+ crossing VI-."""
    return _cross(f[("vip", p["period"])], f[("vim", p["period"])], i)


@family({"period": (7, 14), "floor": (20.0, 25.0, 30.0)},
        lambda a: {k for n in a["period"] for k in (("pdi", n), ("mdi", n), ("adx", n))})
def _adx(i, f, c, p):
    """+DI crossing -DI while ADX says there is a trend to cross into."""
    n = p["period"]
    if not f[("adx", n)][i] > p["floor"]:
        return None
    return _cross(f[("pdi", n)], f[("mdi", n)], i)


@family({"period": (14, 25), "threshold": (50.0, 70.0)},
        lambda a: {("aroon_osc", n) for n in a["period"]})
def _aroon(i, f, c, p):
    """Aroon oscillator crossing INTO +/-level."""
    x = f[("aroon_osc", p["period"])]
    if x[i - 1] <= p["threshold"] < x[i]:
        return 1
    if x[i - 1] >= -p["threshold"] > x[i]:
        return -1
    return None


@family({"scale": (0.5, 1.0), "mode": ("tk_cross", "kijun_cross", "cloud_break")},
        lambda a: {(k, s) for s in a["scale"]
                   for k in ("tenkan", "kijun", "cloud_top", "cloud_bot")})
def _ichimoku(i, f, c, p):
    """Ichimoku (9, 26, 52 x scale): tenkan/kijun cross, close/kijun cross, or
    a close through the cloud."""
    s = p["scale"]
    if p["mode"] == "tk_cross":
        return _cross(f[("tenkan", s)], f[("kijun", s)], i)
    if p["mode"] == "kijun_cross":
        return _cross(c, f[("kijun", s)], i)
    return _band_break(c, i, f[("cloud_top", s)], f[("cloud_bot", s)])


@family({"atr_period": (7, 10, 14), "mult": (1.5, 2.0, 3.0)},
        lambda a: {("st", n, m) for n in a["atr_period"] for m in a["mult"]})
def _supertrend(i, f, c, p):
    """Supertrend (ATR in bars) flipping side."""
    x = f[("st", p["atr_period"], p["mult"])]
    if x[i] == x[i] and x[i - 1] == x[i - 1] and x[i] != x[i - 1]:
        return int(x[i])
    return None


@family({"step": (0.01, 0.02, 0.04)}, lambda a: {("psar_flip", s) for s in a["step"]})
def _psar(i, f, c, p):
    """Parabolic SAR flip at a bar-scale acceleration step."""
    return _event(f[("psar_flip", p["step"])], i)


@family({"run": (2, 3, 5)}, lambda a: {("ha_color",), ("ha_run",)})
def _heikin(i, f, c, p):
    """First Heikin-Ashi candle of a new colour after `run` of the other."""
    color, run = f[("ha_color",)], f[("ha_run",)]
    if color[i] and color[i] != color[i - 1] and run[i - 1] >= p["run"]:
        return int(color[i])
    return None


@family({"scale": (0.5, 1.0)}, lambda a: {("alligator", s) for s in a["scale"]})
def _alligator(i, f, c, p):
    """Williams' alligator opening its mouth: lips > teeth > jaw, newly."""
    return _event(f[("alligator", p["scale"])], i)


@family({"period": (13, 26)},
        lambda a: {k for n in a["period"]
                   for k in (("ema", n), ("bull_power", n), ("bear_power", n))})
def _elder_ray(i, f, c, p):
    """Elder ray: rising EMA with bear power negative and turning up (long),
    falling EMA with bull power positive and turning down (short)."""
    n = p["period"]
    e, bull, bear = f[("ema", n)], f[("bull_power", n)], f[("bear_power", n)]
    if e[i] > e[i - 1] and bear[i] < 0 and bear[i] > bear[i - 1] and not bear[i - 1] > bear[i - 2]:
        return 1
    if e[i] < e[i - 1] and bull[i] > 0 and bull[i] < bull[i - 1] and not bull[i - 1] < bull[i - 2]:
        return -1
    return None


# ---- volume ------------------------------------------------------------------

@family({"period": (2, 13)}, lambda a: {("force", n) for n in a["period"]})
def _force(i, f, c, p):
    """Elder's force index crossing zero. Needs real volume."""
    return _zero(f[("force", p["period"])], i)


@family({"period": (14, 28)}, lambda a: {("eom", n) for n in a["period"]})
def _eom(i, f, c, p):
    """Ease of movement crossing zero. Needs real volume."""
    return _zero(f[("eom", p["period"])], i)


@family({"scale": (1, 2)}, lambda a: {("chaikin", s) for s in a["scale"]})
def _chaikin(i, f, c, p):
    """Chaikin oscillator (3/10 EMA of the A/D line) crossing zero."""
    return _zero(f[("chaikin", p["scale"])], i)


@family({"scale": (0.5, 1.0)},
        lambda a: {k for s in a["scale"] for k in (("kvo", s), ("kvo_sig", s))})
def _klinger(i, f, c, p):
    """Klinger volume oscillator crossing its signal."""
    return _cross(f[("kvo", p["scale"])], f[("kvo_sig", p["scale"])], i)


@family({"period": (10, 20, 50)},
        lambda a: {("obv",), *{("obv_ema", n) for n in a["period"]}})
def _obv_ema(i, f, c, p):
    """OBV crossing its own EMA."""
    return _cross(f[("obv",)], f[("obv_ema", p["period"])], i)


# ---- bands and channels ------------------------------------------------------

@family({"k": (1.0, 1.5, 2.0, 2.5), "mode": ("break", "reenter")},
        lambda a: {("vwap",), ("vwap_sd",)})
def _vwap_band(i, f, c, p):
    """Session VWAP +/- k standard deviations: close through, or back in."""
    mean, sd = f[("vwap",)], f[("vwap_sd",)]
    k = p["k"]
    upper = _Shifted(mean, sd, k)
    lower = _Shifted(mean, sd, -k)
    test = _band_break if p["mode"] == "break" else _band_reenter
    return test(c, i, upper, lower)


@family({"period": (10, 20, 50), "k": (1.5, 2.0, 2.5), "mode": ("break", "reenter")},
        lambda a: {k for n in a["period"] for k in (("sma", n), ("std", n))})
def _bollinger(i, f, c, p):
    """Bollinger band (SMA +/- k sigma): close through a band, or back inside."""
    mean, sd = f[("sma", p["period"])], f[("std", p["period"])]
    test = _band_break if p["mode"] == "break" else _band_reenter
    return test(c, i, _Shifted(mean, sd, p["k"]), _Shifted(mean, sd, -p["k"]))


@family({"period": (10, 20, 50), "k": (1.5, 2.0, 2.5)},
        lambda a: {k for n in a["period"] for k in (("ema", n), ("atr", n))})
def _keltner(i, f, c, p):
    """Keltner channel (EMA +/- k ATR, both in bars): close through."""
    mean, atr = f[("ema", p["period"])], f[("atr", p["period"])]
    return _band_break(c, i, _Shifted(mean, atr, p["k"]), _Shifted(mean, atr, -p["k"]))


@family({"period": (10, 20), "kc": (1.0, 1.5, 2.0)},
        lambda a: {("squeeze", n, k) for n in a["period"] for k in a["kc"]})
def _squeeze(i, f, c, p):
    """TTM squeeze: Bollinger back outside Keltner, the side of the momentum."""
    return _event(f[("squeeze", p["period"], p["kc"])], i)


@family({"period": (10, 20, 55)},
        lambda a: {k for n in a["period"] for k in (("hh", n), ("ll", n))})
def _donchian(i, f, c, p):
    """Close through the prior n-bar high or low -- the turtle entry."""
    hh, ll = f[("hh", p["period"])], f[("ll", p["period"])]
    if c[i] > hh[i]:
        return 1
    if c[i] < ll[i]:
        return -1
    return None


@family({"period": (14, 28), "threshold": (38.2, 50.0)},
        lambda a: {("chop", n) for n in a["period"]})
def _chop(i, f, c, p):
    """Choppiness index falling through `level` -- chop resolving into a trend
    -- taken in the direction of the window's own move."""
    x, n = f[("chop", p["period"])], p["period"]
    if not x[i - 1] >= p["threshold"] > x[i] or i < n:
        return None
    move = c[i] - c[i - n]
    return 1 if move > 0 else -1 if move < 0 else None


@family({"sum_bars": (15, 25)}, lambda a: {("mass_event", s) for s in a["sum_bars"]})
def _mass(i, f, c, p):
    """Mass index reversal bulge, against the 9-bar EMA's slope."""
    return _event(f[("mass_event", p["sum_bars"])], i)


@family({"period": (10, 20, 50), "threshold_z": (1.5, 2.0, 2.5, 3.0)},
        lambda a: {k for n in a["period"] for k in (("sma", n), ("std", n))})
def _zscore(i, f, c, p):
    """Close's z-score against its n-bar mean crossing INTO +/-z."""
    mean, sd = f[("sma", p["period"])], f[("std", p["period"])]
    z = p["threshold_z"]
    return _band_break(c, i, _Shifted(mean, sd, z), _Shifted(mean, sd, -z))


@family({"period": (20, 50, 100), "k": (2.0, 2.5)},
        lambda a: {k for n in a["period"] for k in (("lr_fit", n), ("lr_se", n))})
def _linreg_channel(i, f, c, p):
    """Close through a least-squares line +/- k standard errors."""
    fit, se = f[("lr_fit", p["period"])], f[("lr_se", p["period"])]
    return _band_break(c, i, _Shifted(fit, se, p["k"]), _Shifted(fit, se, -p["k"]))


@family({"level": (3, 4)},
        lambda a: {("cam_h3",), ("cam_l3",), ("cam_h4",), ("cam_l4",)})
def _camarilla(i, f, c, p):
    """Close through Camarilla H3/L3 or H4/L4 from yesterday's session. The
    textbook FADES H3 and BREAKS OUT at H4; `direction` covers both."""
    top = f[("cam_h3",)] if p["level"] == 3 else f[("cam_h4",)]
    bottom = f[("cam_l3",)] if p["level"] == 3 else f[("cam_l4",)]
    return _band_break(c, i, top, bottom)


# ---- candles -----------------------------------------------------------------

PATTERNS = ("engulfing", "hammer", "star", "harami", "doji", "three",
            "marubozu", "outside")


@family({"pattern": PATTERNS, "context_bars": (5, 10, 20)},
        lambda a: {("candle", pat, n) for pat in a["pattern"] for n in a["context_bars"]})
def _candle(i, f, c, p):
    """A named candlestick pattern at an n-bar extreme (reversal patterns) or
    closing through one (three soldiers/crows, marubozu)."""
    return _event(f[("candle", p["pattern"], p["context_bars"])], i)


# ---- combinations ------------------------------------------------------------

TREND_FILTERS = ("ema", "supertrend", "ichimoku", "adx")
PULLBACK_TRIGGERS = ("rsi2", "stoch", "cci", "bb", "crsi")


@family({"regime_filter": TREND_FILTERS, "trigger": PULLBACK_TRIGGERS,
         "trend_bars": (50, 100, 200)},
        lambda a: {*{("trend_dir", k, n) for k in a["regime_filter"] for n in a["trend_bars"]},
                   *{("zone", t) for t in a["trigger"]}})
def _trend_pullback(i, f, c, p):
    """Buy the oversold reading inside an uptrend, sell the overbought one
    inside a downtrend: a trend filter at `trend_bars` scale and a classic
    short oscillator as the trigger."""
    trend = f[("trend_dir", p["regime_filter"], p["trend_bars"])][i]
    zone = f[("zone", p["trigger"])][i]
    if trend and trend == zone:
        return int(trend)
    return None


DUAL_POOL = ("rsi", "stoch", "cci", "bb", "vwap", "mfi")
DUAL_PAIRS = tuple(combinations(DUAL_POOL, 2))


@family({"pair": DUAL_PAIRS, "window": (1, 3)},
        lambda a: {("zone", name) for pair in a["pair"] for name in pair})
def _dual_osc(i, f, c, p):
    """Two oscillators in the same extreme, the second within `window` bars."""
    first, second = p["pair"]
    a, b = f[("zone", first)], f[("zone", second)]
    side = a[i]
    if not side:
        return None
    for j in range(max(0, i - p["window"] + 1), i + 1):
        if b[j] == side:
            return int(side)
    return None


@family({"votes": (7, 8, 9, 10)}, lambda a: {("bull_votes",), ("bear_votes",)})
def _vote(i, f, c, p):
    """Ten classic trend readings (EMA 9/21, MACD, RSI 50, supertrend, vortex,
    aroon, TSI, kijun, SAR, Heikin-Ashi); the bar the count reaches `votes`."""
    k = p["votes"]
    bull, bear = f[("bull_votes",)], f[("bear_votes",)]
    if bull[i] >= k > bull[i - 1]:
        return 1
    if bear[i] >= k > bear[i - 1]:
        return -1
    return None


@family({"multiple": (4, 6), "osc": ("force", "stoch")},
        lambda a: {("ema_slope", 13 * m) for m in a["multiple"]}
        | {("force", 2), ("stoch_k", 5), ("high",), ("low",)})
def _triple_screen(i, f, c, p):
    """Elder's triple screen: the slope of a 13 x `multiple` EMA for the tide,
    a pullback on the fast oscillator for the wave, and a close through the
    prior bar's extreme for the ripple."""
    tide = f[("ema_slope", 13 * p["multiple"])][i]
    if not tide or tide != tide:
        return None
    if p["osc"] == "force":
        wave = f[("force", 2)][i - 1]
        pulled = wave < 0 if tide > 0 else wave > 0
    else:
        wave = f[("stoch_k", 5)][i - 1]
        pulled = wave < 30 if tide > 0 else wave > 70
    if not pulled:
        return None
    high, low = f[("high",)], f[("low",)]
    if tide > 0 and c[i] > high[i - 1]:
        return 1
    if tide < 0 and c[i] < low[i - 1]:
        return -1
    return None


@family({"period": (10, 20, 55), "surge": (1.5, 2.0, 3.0)},
        lambda a: {("vol_ratio",), *{k for n in a["period"] for k in (("hh", n), ("ll", n))}})
def _volume_breakout(i, f, c, p):
    """A Donchian break on a bar with `surge` times the prior 20-bar volume."""
    if not f[("vol_ratio",)][i] >= p["surge"]:
        return None
    hh, ll = f[("hh", p["period"])], f[("ll", p["period"])]
    if c[i] > hh[i]:
        return 1
    if c[i] < ll[i]:
        return -1
    return None


class _Shifted:
    """`mean + k * width`, indexed lazily -- a band without a third array."""

    __slots__ = ("mean", "width", "k")

    def __init__(self, mean, width, k):
        self.mean, self.width, self.k = mean, width, k

    def __getitem__(self, i):
        return self.mean[i] + self.k * self.width[i]


#: Families that read the volume column and mean nothing without it.
NEEDS_VOLUME = frozenset({"fb_mfi", "fb_force", "fb_eom", "fb_chaikin",
                          "fb_klinger", "fb_obv_ema", "fb_volume_breakout"})

#: Axes this wave adds whose values are labels, for `CATEGORICAL`.
CATEGORICAL = ("ma", "pattern", "ao_set", "regime_filter", "osc", "line")


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #

def keys_for(names, axes_of):
    """Every context key the named families can reach, given their axes."""
    wanted = set()
    for name in names:
        _signal_fn, _axes, keys = FAMILIES[name]
        wanted |= set(keys(axes_of(name)))
    return wanted


def _pack(values):
    arr = numpy.ascontiguousarray(values, dtype=numpy.float64)
    out = array("d")
    out.frombytes(arr.tobytes())
    return out


class Series(dict):
    """`ctx["fb"]` built ON DEMAND: a series is computed the first time a signal
    reads it and the oldest are dropped past `limit`.

    WHY. Built eagerly, a worker held every series all fifty-three families can
    reach -- 257 arrays, about 0.85 GB at 5m -- although it works through one
    family's cells at a time, and memory is what capped the pool at two or
    three workers. The values are the same `compute(key, data)` either way, so
    nothing a cell reads can differ; only when it is computed does.

    A `dict` subclass with `__missing__`, so a hit is a plain C-level dict
    lookup and the signals pay nothing for the laziness.
    """

    def __init__(self, bars, limit=32):
        super().__init__()
        self.data = Data(bars)
        self.limit = limit
        dict.__setitem__(self, "close", _pack(self.data.c))

    def __missing__(self, key):
        d = self.data
        if key == ("high",):
            value = _pack(d.h)
        elif key == ("low",):
            value = _pack(d.l)
        else:
            value = _pack(compute(key, d))
        if len(self) > self.limit:
            for old in [k for k in self if k != "close"][: len(self) - self.limit]:
                dict.__delitem__(self, old)
        # The memo holds float64 intermediates (EMAs, ATRs, the ADX triple);
        # left alone it regrows everything the eager build held.
        if len(d.memo) > 32:
            d.memo.clear()
        dict.__setitem__(self, key, value)
        return value


#: Off restores the eager build; see `Series`.
LAZY = __import__("os").environ.get("EXNESS_FB_LAZY", "1") == "1"


def build(bars, names):
    """`ctx["fb"]`: every series the named families read, as `array('d')`."""
    if LAZY:
        return Series(bars)
    d = Data(bars)
    wanted = keys_for(names, lambda name: FAMILIES[name][1])
    out = {"close": _pack(d.c)}
    for key in sorted(wanted, key=repr):
        if key == ("high",):
            out[key] = _pack(d.h)
        elif key == ("low",):
            out[key] = _pack(d.l)
        else:
            out[key] = _pack(compute(key, d))
    return out


#: The grid the `swingbar` wave (the same families held for days) uses; the
#: registry lives in `cfd_families`.
SWINGBAR_EXIT_MODES = ("rr_2", "rr_3", "days_2", "days_5", "days_10",
                       "trail_0.5", "trail_1.0")
SWINGBAR_STOP_DAY = (0.5, 1.0, 1.5)
