"""Shared price features for the TikTok-creator families (`cfd_tt_families`).

Every function reads the session bar list and the context `cfd_tt_families`
builds, and caches its result on that context, so the ~50 families that share
a swing, an FVG or an opening range compute it once per worker.

CAUSAL BY CONSTRUCTION. An array entry `x[i]` uses bars `0..i` only:
  - a swing pivot needs `k` bars on its right, so it is CONFIRMED at `j + k`
    and only appears in `last_high[i]` from `i = j + k` on;
  - a clock window (the 08:00 candle, the Asian range) appears on a bar only
    once the window's last minute has passed;
  - day-level values are the PREVIOUS session's, never today's close.

Arrays are float64 with NaN for "not known yet"; comparisons with NaN are
False, so a signal that reads one simply declines to fire.

Clocks. `minute` is the session clock `cfd_families` uses (shifted +6h for
the Asian indices). `real` is New York wall-clock minutes, which is what the
videos' "8am candle", "London session" and "silver bullet" mean.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy

TS, O, H, L, C, V = range(6)
NAN = float("nan")


def cached(ctx, key, build):
    hit = ctx.get(key)
    if hit is None:
        hit = build()
        ctx[key] = hit
    return hit


# --------------------------------------------------------------------------- #
# base arrays
# --------------------------------------------------------------------------- #

def base(bars, ctx):
    """OHLCV, clocks and day bookkeeping as numpy arrays, plus list copies
    (`L`) for the loop-heavy families -- list indexing is several times faster
    than numpy scalar indexing inside a Python loop."""
    def build():
        a = numpy.asarray(bars, dtype=float)
        n = len(a)
        spec = ctx["cfg"]
        shift = int(spec["shift_hours"])
        opened, closed = spec["session"]
        ts = a[:, TS].astype(numpy.int64)
        day = numpy.asarray(ctx["day"], dtype=numpy.int64)
        minute = numpy.asarray(ctx["minute"], dtype=numpy.int64)
        real = (minute - shift * 60) % 1440
        first = numpy.ones(n, dtype=bool)
        first[1:] = day[1:] != day[:-1]
        starts = numpy.flatnonzero(first)
        dstart = numpy.repeat(starts, numpy.diff(numpy.append(starts, n)))
        pos = numpy.arange(n) - dstart
        since = (minute - opened) % 1440
        vol = a[:, V].copy()
        if not numpy.isfinite(vol).any() or numpy.nanmax(vol) <= 0:
            vol[:] = 1.0
        vol[~numpy.isfinite(vol) | (vol <= 0)] = 1.0
        out = {"o": a[:, O], "h": a[:, H], "l": a[:, L], "c": a[:, C],
               "v": vol, "ts": ts, "day": day, "minute": minute, "real": real,
               "first": first, "dstart": dstart, "pos": pos, "since": since,
               "n": n, "shift": shift, "open_min": opened,
               "close_min": closed,
               "bar": int(ctx.get("bar_minutes") or 30)}
        out["L"] = {k: out[k].tolist() for k in
                    ("o", "h", "l", "c", "v", "day", "minute", "real",
                     "since", "pos", "dstart")}
        return out
    return cached(ctx, "ttf_base", build)


def atr(bars, ctx, n=14):
    def build():
        b = base(bars, ctx)
        h, l, c = b["h"], b["l"], b["c"]
        prev = numpy.empty_like(c)
        prev[0] = c[0]
        prev[1:] = c[:-1]
        tr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
        out = numpy.full(len(c), NAN)
        if len(c) >= n:
            alpha = 1.0 / n
            acc = tr[:n].mean()
            out[n - 1] = acc
            for i in range(n, len(c)):
                acc += alpha * (tr[i] - acc)
                out[i] = acc
        return out
    return cached(ctx, ("ttf_atr", n), build)


def ema(bars, ctx, n, src="c"):
    def build():
        x = base(bars, ctx)[src]
        out = numpy.empty_like(x)
        alpha = 2.0 / (n + 1.0)
        acc = x[0]
        for i in range(len(x)):
            acc += alpha * (x[i] - acc)
            out[i] = acc
        out[:min(n, len(x))] = NAN
        return out
    return cached(ctx, ("ttf_ema", n, src), build)


def sma(values, n):
    out = numpy.full(len(values), NAN)
    if len(values) >= n:
        csum = numpy.cumsum(numpy.insert(values, 0, 0.0))
        out[n - 1:] = (csum[n:] - csum[:-n]) / n
    return out


def rsi(bars, ctx, n=14):
    def build():
        c = base(bars, ctx)["c"]
        d = numpy.diff(c, prepend=c[0])
        up, dn = numpy.maximum(d, 0), numpy.maximum(-d, 0)
        out = numpy.full(len(c), NAN)
        if len(c) <= n:
            return out
        au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
        for i in range(n + 1, len(c)):
            au += (up[i] - au) / n
            ad += (dn[i] - ad) / n
            out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
        return out
    return cached(ctx, ("ttf_rsi", n), build)


def stochastic(bars, ctx, n=14, smooth=3):
    def build():
        b = base(bars, ctx)
        hh = rolling_max(b["h"], n)
        ll = rolling_min(b["l"], n)
        k = numpy.where(hh > ll, 100.0 * (b["c"] - ll) / (hh - ll), 50.0)
        return sma(k, smooth)
    return cached(ctx, ("ttf_stoch", n, smooth), build)


def rolling_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = numpy.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def rolling_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = numpy.full(len(x), NAN)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def body(bars, ctx):
    b = base(bars, ctx)
    return cached(ctx, "ttf_body", lambda: abs(b["c"] - b["o"]))


def vwap(bars, ctx):
    """Session-anchored VWAP on the typical price."""
    def build():
        b = base(bars, ctx)
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        pv, vv = tp * b["v"], b["v"]
        out = numpy.empty_like(tp)
        acc_pv = acc_v = 0.0
        first = b["first"]
        for i in range(b["n"]):
            if first[i]:
                acc_pv = acc_v = 0.0
            acc_pv += pv[i]
            acc_v += vv[i]
            out[i] = acc_pv / acc_v
        return out
    return cached(ctx, "ttf_vwap", build)


def _ema_arr(x, n):
    """EMA of an array, NaN-tolerant: starts at the first finite value."""
    out = numpy.full(len(x), NAN)
    alpha = 2.0 / (n + 1.0)
    acc = NAN
    for i in range(len(x)):
        v = x[i]
        if not math.isfinite(v):
            continue
        acc = v if not math.isfinite(acc) else acc + alpha * (v - acc)
        out[i] = acc
    return out


def smi(bars, ctx, k=7, d=2, sig=2):
    """TradingView's Stochastic Momentum Index: the close's distance from the
    middle of the `k`-bar range, double-EMA smoothed over `d`, as a percent of
    half the (smoothed) range; `signal` is its `sig`-EMA."""
    def build():
        b = base(bars, ctx)
        hh, ll = rolling_max(b["h"], k), rolling_min(b["l"], k)
        rel = b["c"] - (hh + ll) / 2.0
        rng = hh - ll
        ar = _ema_arr(_ema_arr(rel, d), d)
        ad = _ema_arr(_ema_arr(rng, d), d)
        val = numpy.where(ad > 0, 100.0 * ar / (ad / 2.0), 0.0)
        val[~numpy.isfinite(ar)] = NAN
        return {"smi": val, "signal": _ema_arr(val, sig)}
    return cached(ctx, ("ttf_smi", k, d, sig), build)


def halftrend(bars, ctx, amplitude=2, deviation=2):
    """The HalfTrend indicator (everget's, which BigBeluga's engine wraps).

    `trend[i]` is 0 up / 1 down; `flip[i]` is +1 on the bar it turns up, -1
    on the bar it turns down; `line[i]` is the HalfTrend line (the stop the
    indicator draws). Uses bars `0..i` only."""
    def build():
        b = base(bars, ctx)
        h, l, c, n = b["h"], b["l"], b["c"], b["n"]
        hp = rolling_max(h, amplitude)
        lp = rolling_min(l, amplitude)
        hma = sma(h, amplitude)
        lma = sma(l, amplitude)
        trend = numpy.zeros(n, dtype=numpy.int8)
        flip = numpy.zeros(n, dtype=numpy.int8)
        line = numpy.full(n, NAN)
        t, nxt = 0, 0
        max_low = l[0]
        min_high = h[0]
        up = dn = NAN
        for i in range(1, n):
            if not (math.isfinite(hp[i]) and math.isfinite(hma[i])):
                trend[i] = t
                continue
            if nxt == 1:
                max_low = max(lp[i], max_low)
                if hma[i] < max_low and c[i] < l[i - 1]:
                    t, nxt, min_high = 1, 0, hp[i]
            else:
                min_high = min(hp[i], min_high)
                if lma[i] > min_high and c[i] > h[i - 1]:
                    t, nxt, max_low = 0, 1, lp[i]
            prev = trend[i - 1]
            if t == 0:
                up = (dn if math.isfinite(dn) else max_low) if prev != 0 else max(max_low, up if math.isfinite(up) else max_low)
                line[i] = up
                if prev == 1:
                    flip[i] = 1
            else:
                dn = (up if math.isfinite(up) else min_high) if prev != 1 else min(min_high, dn if math.isfinite(dn) else min_high)
                line[i] = dn
                if prev == 0:
                    flip[i] = -1
            trend[i] = t
        return {"trend": trend, "flip": flip, "line": line}
    return cached(ctx, ("ttf_ht", amplitude, deviation), build)


def macd(bars, ctx, fast=12, slow=26, sig=9):
    """MACD line, signal line and histogram on the close."""
    def build():
        c = base(bars, ctx)["c"]
        line = _ema_arr(c, fast) - _ema_arr(c, slow)
        line[:slow] = NAN
        signal = _ema_arr(line, sig)
        return {"macd": line, "signal": signal, "hist": line - signal}
    return cached(ctx, ("ttf_macd", fast, slow, sig), build)


def adx(bars, ctx, n=14):
    """Wilder's ADX with +DI / -DI."""
    def build():
        b = base(bars, ctx)
        h, l, c, m = b["h"], b["l"], b["c"], b["n"]
        up = numpy.zeros(m)
        dn = numpy.zeros(m)
        up[1:] = h[1:] - h[:-1]
        dn[1:] = l[:-1] - l[1:]
        pdm = numpy.where((up > dn) & (up > 0), up, 0.0)
        mdm = numpy.where((dn > up) & (dn > 0), dn, 0.0)
        prev = numpy.concatenate(([c[0]], c[:-1]))
        tr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
        pdi = numpy.full(m, NAN)
        mdi = numpy.full(m, NAN)
        out = numpy.full(m, NAN)
        if m <= 2 * n:
            return {"adx": out, "pdi": pdi, "mdi": mdi}
        st, sp, sm = tr[1:n + 1].sum(), pdm[1:n + 1].sum(), mdm[1:n + 1].sum()
        dx = numpy.full(m, NAN)
        for i in range(n, m):
            if i > n:
                st += tr[i] - st / n
                sp += pdm[i] - sp / n
                sm += mdm[i] - sm / n
            if st > 0:
                pdi[i] = 100 * sp / st
                mdi[i] = 100 * sm / st
                tot = pdi[i] + mdi[i]
                dx[i] = 0.0 if tot == 0 else 100 * abs(pdi[i] - mdi[i]) / tot
        acc = numpy.nanmean(dx[n:2 * n])
        out[2 * n - 1] = acc
        for i in range(2 * n, m):
            acc += (dx[i] - acc) / n
            out[i] = acc
        return {"adx": out, "pdi": pdi, "mdi": mdi}
    return cached(ctx, ("ttf_adx", n), build)


def ut_bot(bars, ctx, key=1.0, n=10):
    """UT Bot Alerts (QuantNomad): an ATR trailing stop `key` x ATR(`n`)
    behind the close; `sig[i]` is +1 when the close crosses above it (buy),
    -1 below (sell)."""
    def build():
        b = base(bars, ctx)
        c = b["c"]
        a = atr(bars, ctx, n)
        m = b["n"]
        stop = numpy.full(m, NAN)
        sig = numpy.zeros(m, dtype=numpy.int8)
        prev = NAN
        for i in range(m):
            if not math.isfinite(a[i]):
                continue
            loss = key * a[i]
            if not math.isfinite(prev):
                cur = c[i] - loss
            elif c[i] > prev and c[i - 1] > prev:
                cur = max(prev, c[i] - loss)
            elif c[i] < prev and c[i - 1] < prev:
                cur = min(prev, c[i] + loss)
            elif c[i] > prev:
                cur = c[i] - loss
            else:
                cur = c[i] + loss
            if math.isfinite(prev) and i > 0:
                if c[i - 1] <= prev and c[i] > cur:
                    sig[i] = 1
                elif c[i - 1] >= prev and c[i] < cur:
                    sig[i] = -1
            stop[i] = cur
            prev = cur
        return {"stop": stop, "sig": sig}
    return cached(ctx, ("ttf_ut", key, n), build)


def squeeze(bars, ctx, n=20, bb=2.0, kc=1.5):
    """LazyBear's Squeeze Momentum: `on[i]` while the Bollinger Bands sit
    inside the Keltner Channel; `val[i]` the linear-regression momentum of
    the close against the mid of the `n`-bar range and SMA."""
    def build():
        b = base(bars, ctx)
        h, l, c, m = b["h"], b["l"], b["c"], b["n"]
        from numpy.lib.stride_tricks import sliding_window_view
        basis = sma(c, n)
        dev = numpy.full(m, NAN)
        if m >= n:
            dev[n - 1:] = sliding_window_view(c, n).std(axis=1)
        prev = numpy.concatenate(([c[0]], c[:-1]))
        tr = numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))
        rng = sma(tr, n)
        on = ((basis - bb * dev) > (basis - kc * rng)) & ((basis + bb * dev) < (basis + kc * rng))
        mid = (rolling_max(h, n) + rolling_min(l, n)) / 2.0
        x = c - (mid + basis) / 2.0
        val = numpy.full(m, NAN)
        if m >= 2 * n:
            t = numpy.arange(n, dtype=float)
            tc = t - t.mean()
            w = sliding_window_view(x, n)
            slope = (w * tc).sum(axis=1) / (tc * tc).sum()
            inter = w.mean(axis=1) - slope * t.mean()
            val[n - 1:] = inter + slope * (n - 1)
        return {"on": on, "val": val}
    return cached(ctx, ("ttf_sqz", n, bb, kc), build)


def zlema_trend(bars, ctx, n=70, mult=1.2):
    """AlgoAlpha's Zero Lag Trend Signals: ZLEMA(`n`) with a band of `mult`
    x the highest ATR(`n`) over `3n` bars; `trend` turns +1 when the close
    crosses above ZLEMA + band, -1 below ZLEMA - band; `flip` marks it."""
    def build():
        b = base(bars, ctx)
        c, m = b["c"], b["n"]
        lag = (n - 1) // 2
        src = c.copy()
        src[lag:] = c[lag:] + (c[lag:] - c[:-lag] if lag else 0)
        src[:lag] = NAN
        z = _ema_arr(src, n)
        vol = rolling_max(numpy.nan_to_num(atr(bars, ctx, n), nan=0.0), 3 * n) * mult
        trend = numpy.zeros(m, dtype=numpy.int8)
        flip = numpy.zeros(m, dtype=numpy.int8)
        t = 0
        for i in range(1, m):
            if not (math.isfinite(z[i]) and math.isfinite(vol[i]) and vol[i] > 0):
                trend[i] = t
                continue
            if c[i] > z[i] + vol[i] and c[i - 1] <= z[i - 1] + vol[i - 1]:
                if t != 1:
                    flip[i] = 1
                t = 1
            elif c[i] < z[i] - vol[i] and c[i - 1] >= z[i - 1] - vol[i - 1]:
                if t != -1:
                    flip[i] = -1
                t = -1
            trend[i] = t
        return {"z": z, "trend": trend, "flip": flip}
    return cached(ctx, ("ttf_zl", n, mult), build)


def cci(bars, ctx, n=20):
    """Commodity Channel Index on the typical price."""
    def build():
        b = base(bars, ctx)
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        out = numpy.full(b["n"], NAN)
        if b["n"] >= n:
            from numpy.lib.stride_tricks import sliding_window_view
            w = sliding_window_view(tp, n)
            mean = w.mean(axis=1)
            mad = numpy.abs(w - mean[:, None]).mean(axis=1)
            with numpy.errstate(invalid="ignore", divide="ignore"):
                out[n - 1:] = numpy.where(mad > 0, (tp[n - 1:] - mean) / (0.015 * mad), 0.0)
        return out
    return cached(ctx, ("ttf_cci", n), build)


def vix_fix(bars, ctx, pd=22, bbl=20, mult=2.0, lb=50, ph=0.85):
    """CM Williams VIX Fix: `wvf` = (highest close of `pd` - low) / that
    close x 100; `green[i]` when it reaches its upper Bollinger band or
    `ph` x its `lb`-bar high (the indicator's bottom flash). `top` is the
    inverted version (Aaron Stone's), flashing at tops."""
    def build():
        b = base(bars, ctx)
        c, h, l, m = b["c"], b["h"], b["l"], b["n"]
        from numpy.lib.stride_tricks import sliding_window_view

        def flash(x):
            mid = sma(x, bbl)
            sd = numpy.full(m, NAN)
            if m >= bbl:
                sd[bbl - 1:] = sliding_window_view(x, bbl).std(axis=1)
            upper = mid + mult * sd
            rh = rolling_max(numpy.nan_to_num(x, nan=0.0), lb) * ph
            return (x >= upper) | (x >= rh)

        hc = rolling_max(c, pd)
        wvf = (hc - l) / hc * 100.0
        lc = rolling_min(c, pd)
        inv = (h - lc) / lc * 100.0
        return {"green": flash(wvf) & numpy.isfinite(wvf),
                "top": flash(inv) & numpy.isfinite(inv)}
    return cached(ctx, ("ttf_wvf", pd, bbl, mult, lb, ph), build)


# --------------------------------------------------------------------------- #
# swings and structure
# --------------------------------------------------------------------------- #

def pivots(bars, ctx, k):
    """Most recent CONFIRMED swing high/low as of each bar.

    A pivot high at `j` is the strict maximum of `h[j-k .. j+k]` (ties on the
    left allowed); it is known from bar `j + k`. Returns arrays of the latest
    and the one-before-latest pivot price and index, for highs and lows.
    """
    def build():
        b = base(bars, ctx)
        h, l, n = b["h"], b["l"], b["n"]
        from numpy.lib.stride_tricks import sliding_window_view
        is_ph = numpy.zeros(n, dtype=bool)
        is_pl = numpy.zeros(n, dtype=bool)
        if n > 2 * k:
            wh = sliding_window_view(h, 2 * k + 1)
            wl = sliding_window_view(l, 2 * k + 1)
            centre_h = h[k:n - k]
            centre_l = l[k:n - k]
            right_h = wh[:, k + 1:].max(axis=1)
            right_l = wl[:, k + 1:].min(axis=1)
            left_h = wh[:, :k].max(axis=1)
            left_l = wl[:, :k].min(axis=1)
            is_ph[k:n - k] = (centre_h > right_h) & (centre_h >= left_h)
            is_pl[k:n - k] = (centre_l < right_l) & (centre_l <= left_l)
        out = {}
        for name, flags, px in (("h", is_ph, h), ("l", is_pl, l)):
            last = numpy.full(n, NAN)
            last_i = numpy.full(n, -1, dtype=numpy.int64)
            prev = numpy.full(n, NAN)
            prev_i = numpy.full(n, -1, dtype=numpy.int64)
            cur, cur_i, pv, pv_i = NAN, -1, NAN, -1
            conf = numpy.flatnonzero(flags) + k
            ptr = 0
            for i in range(n):
                while ptr < len(conf) and conf[ptr] <= i:
                    pv, pv_i = cur, cur_i
                    cur_i = conf[ptr] - k
                    cur = px[cur_i]
                    ptr += 1
                last[i], last_i[i], prev[i], prev_i[i] = cur, cur_i, pv, pv_i
            out[name] = last
            out[name + "_i"] = last_i
            out[name + "_prev"] = prev
            out[name + "_prev_i"] = prev_i
        return out
    return cached(ctx, ("ttf_piv", k), build)


def structure(bars, ctx, k):
    """Break-of-structure events and trend state on `k`-bar swings.

    `up[i]` is True on the first close above the latest confirmed swing high
    (each swing breaks once), `dn[i]` likewise below the latest swing low.
    `trend[i]` is +1/-1 after the most recent break, 0 before any.
    `choch_up[i]` is an up-break while the trend was -1 (a change of
    character); `n_up[i]` counts consecutive same-direction up-breaks.
    """
    def build():
        b = base(bars, ctx)
        p = pivots(bars, ctx, k)
        c = b["c"]
        n = b["n"]
        up = numpy.zeros(n, dtype=bool)
        dn = numpy.zeros(n, dtype=bool)
        chu = numpy.zeros(n, dtype=bool)
        chd = numpy.zeros(n, dtype=bool)
        trend = numpy.zeros(n, dtype=numpy.int8)
        run = numpy.zeros(n, dtype=numpy.int16)
        broken_h = broken_l = -1
        t = 0
        r = 0
        ph, ph_i, pl, pl_i = p["h"], p["h_i"], p["l"], p["l_i"]
        for i in range(n):
            if ph_i[i] >= 0 and ph_i[i] != broken_h and c[i] > ph[i]:
                up[i] = True
                broken_h = ph_i[i]
                chu[i] = t == -1
                r = r + 1 if t == 1 else 1
                t = 1
            elif pl_i[i] >= 0 and pl_i[i] != broken_l and c[i] < pl[i]:
                dn[i] = True
                broken_l = pl_i[i]
                chd[i] = t == 1
                r = r + 1 if t == -1 else 1
                t = -1
            trend[i] = t
            run[i] = r
        return {"up": up, "dn": dn, "choch_up": chu, "choch_dn": chd,
                "trend": trend, "run": run}
    return cached(ctx, ("ttf_struct", k), build)


def fvg(bars, ctx):
    """Three-bar fair value gaps ending at each bar.

    Bullish at `i`: `l[i] > h[i-2]`, gap `(h[i-2], l[i])`. Bearish at `i`:
    `h[i] < l[i-2]`, gap `(h[i], l[i-2])`. Size in price units.
    """
    def build():
        b = base(bars, ctx)
        h, l, n = b["h"], b["l"], b["n"]
        bull = numpy.zeros(n, dtype=bool)
        bear = numpy.zeros(n, dtype=bool)
        lo = numpy.full(n, NAN)
        hi = numpy.full(n, NAN)
        if n > 2:
            bull[2:] = l[2:] > h[:-2]
            bear[2:] = h[2:] < l[:-2]
            lo[2:] = numpy.where(bull[2:], h[:-2], numpy.where(bear[2:], h[2:], NAN))
            hi[2:] = numpy.where(bull[2:], l[2:], numpy.where(bear[2:], l[:-2], NAN))
        return {"bull": bull, "bear": bear, "lo": lo, "hi": hi}
    return cached(ctx, "ttf_fvg", build)


# --------------------------------------------------------------------------- #
# session and day levels
# --------------------------------------------------------------------------- #

def days(bars, ctx):
    """Per bar: previous session's OHLC, today's open, today's running
    high/low through the bar, and the overnight (between-session) range."""
    def build():
        b = base(bars, ctx)
        n = b["n"]
        day, first = b["day"], b["first"]
        o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        starts = numpy.flatnonzero(first)
        ends = numpy.append(starts[1:], n)
        dh = numpy.array([h[s:e].max() for s, e in zip(starts, ends)])
        dl = numpy.array([l[s:e].min() for s, e in zip(starts, ends)])
        do = o[starts]
        dc = c[ends - 1]
        idx = numpy.cumsum(first) - 1          # day ordinal per bar
        prev = idx - 1
        ok = prev >= 0
        out = {name: numpy.full(n, NAN) for name in
               ("pdh", "pdl", "pdo", "pdc", "ppdh", "ppdl", "on_h", "on_l")}
        out["pdh"][ok] = dh[prev[ok]]
        out["pdl"][ok] = dl[prev[ok]]
        out["pdo"][ok] = do[prev[ok]]
        out["pdc"][ok] = dc[prev[ok]]
        ok2 = prev >= 1
        out["ppdh"][ok2] = dh[prev[ok2] - 1]
        out["ppdl"][ok2] = dl[prev[ok2] - 1]
        out["open"] = do[idx]
        # running high/low of the day through each bar
        rh = numpy.empty(n)
        rl = numpy.empty(n)
        for s, e in zip(starts, ends):
            rh[s:e] = numpy.maximum.accumulate(h[s:e])
            rl[s:e] = numpy.minimum.accumulate(l[s:e])
        out["run_h"], out["run_l"] = rh, rl
        # the overnight range: every full-series bar strictly between the
        # previous session's last bar and this session's first bar
        full = ctx.get("tt_full")
        if full is not None and len(full["ts"]):
            fts = full["ts"]
            ts = b["ts"]
            on_h = numpy.full(len(starts), NAN)
            on_l = numpy.full(len(starts), NAN)
            for d in range(1, len(starts)):
                a0 = numpy.searchsorted(fts, ts[ends[d - 1] - 1], side="right")
                a1 = numpy.searchsorted(fts, ts[starts[d]], side="left")
                if a1 > a0:
                    on_h[d] = full["h"][a0:a1].max()
                    on_l[d] = full["l"][a0:a1].min()
            out["on_h"] = on_h[idx]
            out["on_l"] = on_l[idx]
        # weekday of the session (0 = Monday), from the first bar's NY date
        wd = numpy.array([datetime.fromtimestamp(int(b["ts"][s]) - b["shift"] * 3600,
                                                 tz=timezone.utc).weekday()
                          for s in starts])
        out["weekday"] = wd[idx]
        out["day_idx"] = idx
        out["d_hi"], out["d_lo"], out["d_open"], out["d_close"] = dh, dl, do, dc
        out["d_start"], out["d_end"] = starts, ends
        return out
    return cached(ctx, "ttf_days", build)


#: New York clock windows `(start, end)` in minutes; `end < start` wraps past
#: midnight and belongs to the date on which it ENDS.
WINDOWS = {
    "asia": (19 * 60, 3 * 60),
    "sydney": (17 * 60, 2 * 60),
    "london": (3 * 60, 8 * 60),
    "h8": (8 * 60, 9 * 60),         # the 08:00 one-hour candle
    "m8": (8 * 60, 8 * 60 + 15),    # the 08:00 fifteen-minute candle
    "midnight": (0, 5),             # the 00:00 open
    "london_open": (3 * 60, 3 * 60 + 5),
    "h0_4": (0, 4 * 60),            # the day's first 4-hour candle
}


def clock_window(bars, ctx, name):
    """Per bar: high/low/open/close of the named New York window on the bar's
    New York date, or NaN until that window has fully closed."""
    def build():
        b = base(bars, ctx)
        n = b["n"]
        out = {k: numpy.full(n, NAN) for k in ("hi", "lo", "op", "cl")}
        full = ctx.get("tt_full")
        if full is None or not len(full["ts"]):
            return out
        start, end = WINDOWS[name]
        shift = b["shift"] * 3600
        fts = full["ts"] - shift                   # real New York seconds
        fmin = (fts // 60) % 1440
        fdate = fts // 86400
        if start < end:
            inside = (fmin >= start) & (fmin < end)
            key = fdate
        else:
            inside = (fmin >= start) | (fmin < end)
            key = fdate + (fmin >= start)
        sel = numpy.flatnonzero(inside)
        if not len(sel):
            return out
        keys = key[sel]
        change = numpy.flatnonzero(numpy.diff(keys)) + 1
        grp_s = numpy.insert(change, 0, 0)
        grp_e = numpy.append(change, len(sel))
        table = {}
        fh, fl, fo, fc = full["h"], full["l"], full["o"], full["c"]
        for s, e in zip(grp_s, grp_e):
            rows = sel[s:e]
            end_sec = (int(keys[s]) * 86400 + end * 60)
            table[int(keys[s])] = (fh[rows].max(), fl[rows].min(),
                                   fo[rows[0]], fc[rows[-1]], end_sec)
        bts = b["ts"] - shift
        bdate = bts // 86400
        for i in range(n):
            row = table.get(int(bdate[i]))
            if row is not None and bts[i] >= row[4]:
                out["hi"][i], out["lo"][i], out["op"][i], out["cl"][i] = row[:4]
        return out
    return cached(ctx, ("ttf_win", name), build)


def opening_range(bars, ctx, minutes):
    """High/low of the first `minutes` of each session (at least one bar),
    known on the bars AFTER it closes; `done[i]` marks those bars."""
    def build():
        b = base(bars, ctx)
        n = b["n"]
        since, bar = b["since"], b["bar"]
        inside = since < max(minutes, bar)
        hi = numpy.full(n, NAN)
        lo = numpy.full(n, NAN)
        d = days(bars, ctx)
        h, l = b["h"], b["l"]
        for s, e in zip(d["d_start"], d["d_end"]):
            m = inside[s:e]
            if not m.any() or m.all():
                continue
            last = s + int(numpy.flatnonzero(m).max())
            hh, ll = h[s:last + 1].max(), l[s:last + 1].min()
            hi[last + 1:e] = hh
            lo[last + 1:e] = ll
        return {"hi": hi, "lo": lo, "done": numpy.isfinite(hi)}
    return cached(ctx, ("ttf_or", minutes), build)


def day_profile(bars, ctx, bins=30, share=0.70):
    """Per day: volume profile POC, VAH, VAL of the session's own bars
    (typical price, volume-weighted). Arrays over days, not bars."""
    def build():
        b = base(bars, ctx)
        d = days(bars, ctx)
        h, l, c, v = b["h"], b["l"], b["c"], b["v"]
        nd = len(d["d_start"])
        poc = numpy.full(nd, NAN)
        vah = numpy.full(nd, NAN)
        val = numpy.full(nd, NAN)
        for j, (s, e) in enumerate(zip(d["d_start"], d["d_end"])):
            lo, hi = l[s:e].min(), h[s:e].max()
            if not hi > lo:
                continue
            tp = (h[s:e] + l[s:e] + c[s:e]) / 3.0
            hist, edges = numpy.histogram(tp, bins=bins, range=(lo, hi),
                                          weights=v[s:e])
            top = int(hist.argmax())
            total = hist.sum()
            a, z, acc = top, top, hist[top]
            while acc < share * total and (a > 0 or z < bins - 1):
                left = hist[a - 1] if a > 0 else -1
                right = hist[z + 1] if z < bins - 1 else -1
                if right >= left:
                    z += 1
                    acc += hist[z]
                else:
                    a -= 1
                    acc += hist[a]
            poc[j] = (edges[top] + edges[top + 1]) / 2
            val[j], vah[j] = edges[a], edges[z + 1]
        return {"poc": poc, "vah": vah, "val": val}
    return cached(ctx, ("ttf_prof", bins, share), build)


def prev_day(bars, ctx, arr, lag=1):
    """Map a per-day array onto bars as the value `lag` sessions back."""
    d = days(bars, ctx)
    idx = d["day_idx"] - lag
    out = numpy.full(len(idx), NAN)
    ok = idx >= 0
    out[ok] = arr[idx[ok]]
    return out


def htf(bars, ctx, m):
    """Session-aligned higher-timeframe candles of `m` bars.

    `group[i]` is the HTF candle bar `i` belongs to; `closed[i]` is True on
    the last bar of each HTF candle, where its OHLC (`ho`, `hh`, `hl`, `hc`)
    becomes known. `prev_*[i]` is the most recent CLOSED HTF candle at bar
    `i` (inclusive of a candle closing on `i`)."""
    def build():
        b = base(bars, ctx)
        n = b["n"]
        pos, o, h, l, c = b["pos"], b["o"], b["h"], b["l"], b["c"]
        group = b["dstart"] * 0 + (numpy.cumsum(b["first"]) * 100000 + pos // m)
        closed = numpy.zeros(n, dtype=bool)
        closed[:-1] = group[1:] != group[:-1]
        closed[-1] = True
        keys = {k: numpy.full(n, NAN) for k in
                ("po", "ph", "pl", "pc", "qo", "qh", "ql", "qc")}
        cur_o = cur_h = cur_l = None
        last = prev2 = None
        for i in range(n):
            if i == 0 or group[i] != group[i - 1]:
                cur_o, cur_h, cur_l = o[i], h[i], l[i]
            else:
                cur_h = max(cur_h, h[i])
                cur_l = min(cur_l, l[i])
            if closed[i]:
                prev2 = last
                last = (cur_o, cur_h, cur_l, c[i])
            if last is not None:
                keys["po"][i], keys["ph"][i], keys["pl"][i], keys["pc"][i] = last
            if prev2 is not None:
                keys["qo"][i], keys["qh"][i], keys["ql"][i], keys["qc"][i] = prev2
        keys["closed"] = closed
        keys["group"] = group
        return keys
    return cached(ctx, ("ttf_htf", m), build)


# --------------------------------------------------------------------------- #
# helpers for signals
# --------------------------------------------------------------------------- #

def stop_dist(side, price, level, atr_v, floor=0.2):
    """Distance from `price` to a structural stop `level` on the losing side,
    floored at `floor` x ATR so a sweep that closed at its own extreme does
    not produce a stop of nothing (and a position sized to the margin cap)."""
    if not (atr_v > 0) or not math.isfinite(level):
        return NAN
    d = side * (price - level)
    return max(d, floor * atr_v)


def tgt(side, price, level):
    """Distance to a target level in the trade's direction, or NaN."""
    if not math.isfinite(level):
        return NAN
    d = side * (level - price)
    return d if d > 0 else NAN
