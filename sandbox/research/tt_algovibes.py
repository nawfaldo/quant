"""Algovibes' YouTube (@Algovibes, "WiseQuant") strategies, as
`cfd_tt_families` families.

Source: 331 videos listed 2026-10-03; the ~78 that state or test a rule were
read from the English captions (titles show machine-translated for this
login; the audio is English). Two eras: 2021-2024 "I backtested X's strategy
in Python" (Rayner Teo, Data Trader, TradePro, ChatGPT, crypto intraday
rules) and 2025-2026 mass studies (millions of configurations, mostly
negative after costs / multiple-testing correction). Map and SKIP list:
`results/tiktok/algovibes_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s. His NQ tests use a 1 ATR stop and
a 2 ATR target ("same stop, same target"); crypto tests use % stops, here
ATR multiples.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research.tt_quantitativo import _daily, _first_bar_events, _sma

NAN = float("nan")
fin = math.isfinite


def _base(bars, ctx):
    return F.base(bars, ctx)


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def _cross_up(a, b):
    a = numpy.asarray(a, dtype=float)
    b = numpy.broadcast_to(numpy.asarray(b, dtype=float), (len(a),))
    out = numpy.zeros(len(a), dtype=bool)
    with numpy.errstate(invalid="ignore"):
        out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return out


def _cross_dn(a, b):
    a = numpy.asarray(a, dtype=float)
    b = numpy.broadcast_to(numpy.asarray(b, dtype=float), (len(a),))
    out = numpy.zeros(len(a), dtype=bool)
    with numpy.errstate(invalid="ignore"):
        out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return out


def _signals_to_events(bars, ctx, p, lu, ld, mult=None, stop_l=None, stop_s=None, target=None):
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    mult = mult if mult is not None else float(p.get("stop", "atr_1")[4:])
    out = {}
    for i in numpy.flatnonzero(numpy.asarray(lu) | numpy.asarray(ld)):
        i = int(i)
        A = a[i]
        if not (A > 0) or not _ok(b, i, p) or i in out:
            continue
        side = 1 if lu[i] else -1
        lvl = (stop_l if side == 1 else stop_s)
        d = F.stop_dist(side, c[i], lvl[i], A) if lvl is not None else mult * A
        if not (d > 0 and fin(d)):
            continue
        tg = None
        if target is not None:
            t = F.tgt(side, c[i], target[side][i])
            if not (t > 0 and fin(t)):
                continue
            tg = {"far": t}
        out[i] = (side, d, tg)
    return out


# --------------------------------------------------------------------------- #
# 2025-2026: the NQ tests
# --------------------------------------------------------------------------- #

def morning_momentum(bars, ctx, p):
    """#2 (2026-10). "This strategy made $267,715": yesterday was volatile
    (its range at least `vol` x the 20-day mean range) and by 60 minutes
    after the open (his 10:30 NY) price sits at least `thr` x yesterday's
    range away from today's open -> trade that way at that bar, out at the
    close (his 15:55). His NQ: 962 trades, Sharpe 1.22, PF 1.3 in discovery
    + validation; the FROZEN forward test lost (Sharpe -0.56)."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    since = b["L"]["since"]
    d, o, h, l, cl = _daily(bars, ctx)
    rng = h - l
    mean_r = _sma(rng, 20)
    idx = d["day_idx"].tolist()
    dop = d["open"].tolist()
    a = _atr(bars, ctx)
    out = {}
    done = set()
    for i in range(len(c)):
        j = idx[i] - 1
        if j < 20 or idx[i] in done or since[i] < 60:
            continue
        done.add(idx[i])
        if not (fin(mean_r[j]) and rng[j] >= p["vol"] * mean_r[j]):
            continue
        move = c[i] - dop[i]
        if abs(move) < p["thr"] * rng[j] or not (a[i] > 0) or not _ok(b, i, p):
            continue
        side = 1 if move > 0 else -1
        out[i] = (side, float(p["stop"][4:]) * a[i], None)
    return out


def pd_level_retest(bars, ctx, p):
    """#3 (2026-09). "Previous day high and low retest" -- one of the three
    classic NQ setups that made money out of sample (+3.31 pts/trade; its
    opposite lost). Price breaks above the previous session's high, comes
    back to touch it (within `tol` ATR) and closes above -> long; mirror at
    the low. 1 ATR stop, 2 ATR target (his frame)."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl = d["pdh"].tolist(), d["pdl"].tolist()
    day = b["L"]["day"]
    out = {}
    cur, brk = None, 0
    for i in range(len(c)):
        if day[i] != cur:
            cur, brk = day[i], 0
        A = a[i]
        if not (fin(pdh[i]) and A > 0):
            continue
        if brk == 0:
            if c[i] > pdh[i]:
                brk = 1
            elif c[i] < pdl[i]:
                brk = -1
            continue
        t = p["tol"] * A
        if brk == 1 and l[i] <= pdh[i] + t and c[i] > pdh[i] and _ok(b, i, p):
            out[i] = (1, A, None)
            brk = 2
        elif brk == -1 and h[i] >= pdl[i] - t and c[i] < pdl[i] and _ok(b, i, p):
            out[i] = (-1, A, None)
            brk = 2
    return out


def ema_systems(bars, ctx, p):
    """#3 (2026-09). Two of his ten NQ classics. `system` "13_48": the 13 EMA
    crosses the 48 -> enter (+2.14 pts/trade OOS); "pullback": 9 EMA above
    the 20, a candle dips to the 20 and closes back above -> long (normal
    -5.47 pts OOS); "pullback_flip": the same signal traded AGAINST the trend
    (his +0.48, PF 1.02). 1 ATR stop, 2 ATR target."""
    b = _base(bars, ctx)
    c, l, h = b["c"], b["l"], b["h"]
    s = p["system"]
    if s == "13_48":
        f, sl = F.ema(bars, ctx, 13), F.ema(bars, ctx, 48)
        lu, ld = _cross_up(f, sl), _cross_dn(f, sl)
    else:
        e9, e20 = F.ema(bars, ctx, 9), F.ema(bars, ctx, 20)
        with numpy.errstate(invalid="ignore"):
            lu = (e9 > e20) & (l <= e20) & (c > e20)
            ld = (e9 < e20) & (h >= e20) & (c < e20)
        if s == "pullback_flip":
            lu, ld = ld, lu
    return _signals_to_events(bars, ctx, p, lu, ld, mult=p["atr"])


# --------------------------------------------------------------------------- #
# 2023-2024 replications
# --------------------------------------------------------------------------- #

def first_hour_fib(bars, ctx, p):
    """#163/#164. From the first hour's candle: D = its high - low; levels =
    the first hour's close + D x {-0.618 stop, +0.618 entry, +1.618 target}
    (he moved the anchor from the high to the close; `entry` 0.5 a nearer
    trigger); the first breach of the entry level -> long, stop/target at the levels. Long only in his test;
    `side` "both" adds the mirror."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    since = b["L"]["since"]
    day = b["L"]["day"]
    hh, ll, cc = F.base(bars, ctx)["h"], F.base(bars, ctx)["l"], F.base(bars, ctx)["c"]
    a = _atr(bars, ctx)
    out = {}
    cur = None
    lv = None
    hi = lo = NAN
    for i in range(len(c)):
        if day[i] != cur:
            cur, lv, hi, lo, fired = day[i], None, -numpy.inf, numpy.inf, False
        if since[i] < 60:
            hi, lo = max(hi, hh[i]), min(lo, ll[i])
            last_c = cc[i]
            continue
        if lv is None:
            D = hi - lo
            if not (D > 0):
                lv = ()
                continue
            lv = (last_c, D)
        if not lv or fired or not (a[i] > 0) or not _ok(b, i, p):
            continue
        base, D = lv
        en = p["entry"]
        up_e, dn_e = base + en * D, base - en * D
        if c[i] > up_e:
            fired = True
            out[i] = (1, max(c[i] - (base - en * D), 0.2 * a[i]), {"far": F.tgt(1, c[i], base + 1.618 * D)})
        elif p["side"] == "both" and c[i] < dn_e:
            fired = True
            out[i] = (-1, max((base + en * D) - c[i], 0.2 * a[i]), {"far": F.tgt(-1, c[i], base - 1.618 * D)})
    return {k: v for k, v in out.items() if v[2] is None or v[2]["far"] > 0 and fin(v[2]["far"])}


def pd_box(bars, ctx, p):
    """#52. "$1,000 a day box": the previous session's high-low box; price
    crossing DOWN through the box's low -> buy (he traded the long side),
    stop slightly below (`stop` ATR), target the box's middle; mirror short
    at the high. His: 57% win on 21 trades before costs."""
    b = _base(bars, ctx)
    c = b["c"]
    d = F.days(bars, ctx)
    pdh, pdl = d["pdh"], d["pdl"]
    mid = (pdh + pdl) / 2
    lu = _cross_dn(c, pdl)
    ld = _cross_up(c, pdh) if p["side"] == "both" else numpy.zeros(len(c), dtype=bool)
    return _signals_to_events(bars, ctx, p, lu, ld, target={1: mid, -1: mid})


def four_hour_fade(bars, ctx, p):
    """#49. Data Trader's "best 5-minute scalping strategy": the New York
    day's first 4-hour candle (00:00-04:00) sets the range; later, price
    breaks out of it and a candle closes back INSIDE -> fade the breakout
    (break above -> short; the break must clear the range by `min_ext` ATR);
    stop at the breakout's extreme; 2R. His EURUSD:
    32% win, -0.04R a trade."""
    b = _base(bars, ctx)
    c, h, l = b["L"]["c"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "h0_4")
    hi, lo = w["hi"].tolist(), w["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, ext_h, ext_l, out_up, out_dn, fired = day[i], -numpy.inf, numpy.inf, False, False, False
        if not fin(hi[i]) or fired or not (a[i] > 0):
            continue
        if h[i] > hi[i] + p["min_ext"] * a[i]:
            out_up, ext_h = True, max(ext_h, h[i])
        if l[i] < lo[i] - p["min_ext"] * a[i]:
            out_dn, ext_l = True, min(ext_l, l[i])
        if out_up and lo[i] < c[i] < hi[i] and _ok(b, i, p):
            fired = True
            out[i] = (-1, F.stop_dist(-1, c[i], ext_h, a[i]), None)
        elif out_dn and lo[i] < c[i] < hi[i] and _ok(b, i, p):
            fired = True
            out[i] = (1, F.stop_dist(1, c[i], ext_l, a[i]), None)
    return out


def rayner(bars, ctx, p):
    """#159/#250/#278/#289 (RSI), #184/#214 (Bollinger), #198 (Stochastic),
    #235 (10-day low): Rayner Teo's daily dip-buys he replicated, all above
    the 200-day SMA, entered at the next session's open (his 3%/2% limit
    orders dropped). `variant`: "rsi10" RSI(10) < 30 (exit RSI > 40 or 10
    days; his ~70% win); "bb" close under the lower BB(20, 2.5) and under the
    prior close (exit RSI(2) > 50); "stoch" Stoch(10) %K < 5 (exit a higher
    close); "low10" a new 10-day low (exit a close above the 5-day SMA).
    Held to the session close here."""
    d, o, h, l, c = _daily(bars, ctx)
    sma200 = _sma(c, 200)
    v = p["variant"]
    with numpy.errstate(invalid="ignore", divide="ignore"):
        if v == "rsi10":
            diff = numpy.diff(c, prepend=c[0])
            au = F._ema_arr(numpy.maximum(diff, 0), 19)
            ad = F._ema_arr(numpy.maximum(-diff, 0), 19)
            r = numpy.where(ad > 0, 100 - 100 / (1 + au / ad), 100.0)
            ok = r < 30
        elif v == "bb":
            from numpy.lib.stride_tricks import sliding_window_view
            sd = numpy.full(len(c), NAN)
            if len(c) >= 20:
                sd[19:] = sliding_window_view(c, 20).std(axis=1)
            lo_b = _sma(c, 20) - 2.5 * sd
            pc = numpy.full(len(c), NAN)
            pc[1:] = c[:-1]
            ok = (c < lo_b) & (c < pc)
        elif v == "stoch":
            hh, ll = F.rolling_max(h, 10), F.rolling_min(l, 10)
            k = numpy.where(hh > ll, 100 * (c - ll) / (hh - ll), 50.0)
            ok = k < 5
        else:
            ok = l <= F.rolling_min(l, 10)
        ok &= c > sma200
    return _first_bar_events(bars, ctx, p, ok)


def tradepro(bars, ctx, p):
    """#201/#202. TradePro's "76% win rate": close > EMA8 > EMA14 > EMA50 and
    the Stochastic RSI %K crosses above %D -> long; stop 3 ATR, target 2 ATR
    (`rr_0.67`; `atr` 2 a tighter stop). Mirror short. His EURUSD backtest: well under 76%."""
    b = _base(bars, ctx)
    c = b["c"]
    e8, e14, e50 = F.ema(bars, ctx, 8), F.ema(bars, ctx, 14), F.ema(bars, ctx, 50)
    r = F.rsi(bars, ctx, 14)
    rr = numpy.nan_to_num(r, nan=50.0)
    lo, hi = F.rolling_min(rr, 14), F.rolling_max(rr, 14)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        st = numpy.where(hi > lo, 100 * (rr - lo) / (hi - lo), 50.0)
    k = F.sma(st, 3)
    dline = F.sma(numpy.nan_to_num(k, nan=50.0), 3)
    with numpy.errstate(invalid="ignore"):
        lu = _cross_up(k, dline) & (c > e8) & (e8 > e14) & (e14 > e50)
        ld = _cross_dn(k, dline) & (c < e8) & (e8 < e14) & (e14 < e50)
    return _signals_to_events(bars, ctx, p, lu, ld, mult=p["atr"])


def short_momentum(bars, ctx, p):
    """#122, #127, #140 (crypto intraday, 2022-23). Price rose at least `k`
    ATR over the last `n` bars (his BTC: +1% in an hour, +2% in 12 hours)
    -> buy; his exits a 2% stop and target or a 2% trailing stop (here
    `stop` ATR with rr / trail). Long only as in his tests."""
    b = _base(bars, ctx)
    c = b["c"]
    n = p["n"]
    a = F.atr(bars, ctx, 14)
    ch = numpy.full(len(c), NAN)
    ch[n:] = c[n:] - c[:-n]
    with numpy.errstate(invalid="ignore"):
        hot = ch >= p["k"] * a
    lu = hot & ~numpy.concatenate(([False], hot[:-1]))
    return _signals_to_events(bars, ctx, p, lu, numpy.zeros(len(c), dtype=bool))


def dip_bounce(bars, ctx, p):
    """#70 (ETH hourly) and #258 (reversal bot). A drop of at least `k` ATR
    within `n` bars, then a bounce of half that from the low -> buy; his max
    hold 5 hours (`time_N`) or a trailing stop. Long only."""
    b = _base(bars, ctx)
    c, l = b["c"], b["l"]
    n = p["n"]
    a = F.atr(bars, ctx, 14)
    hh = F.rolling_max(b["h"], n)
    lo = F.rolling_min(l, n)
    with numpy.errstate(invalid="ignore"):
        dropped = (hh - lo) >= p["k"] * a
        bounce = (c - lo) >= 0.5 * p["k"] * a
        sig = dropped & bounce
    lu = sig & ~numpy.concatenate(([False], sig[:-1]))
    return _signals_to_events(bars, ctx, p, lu, numpy.zeros(len(c), dtype=bool))


def bad_day_reversal(bars, ctx, p):
    """#107 (crypto, from an IRF paper): yesterday's return in the bottom
    `pct`% of the last 250 sessions -> long today; top `pct`% -> short
    (short-term reversal). Held the session."""
    d, o, h, l, c = _daily(bars, ctx)
    r = numpy.full(len(c), NAN)
    r[1:] = c[1:] / c[:-1] - 1
    lo = numpy.full(len(c), NAN)
    hi = numpy.full(len(c), NAN)
    q = p["pct"] / 100.0
    for j in range(60, len(c)):
        w = r[max(1, j - 250):j]
        w = w[numpy.isfinite(w)]
        if len(w) >= 50:
            lo[j], hi[j] = numpy.quantile(w, q), numpy.quantile(w, 1 - q)
    with numpy.errstate(invalid="ignore"):
        ok_l, ok_s = r <= lo, r >= hi
    return _first_bar_events(bars, ctx, p, ok_l, ok_s)


def donchian_mid(bars, ctx, p):
    """#209 (crypto intraday). A channel of the rolling `n`-bar high and low;
    the close crosses ABOVE its midline -> buy; sell when the high reaches
    the upper band (`level`). Mirror short. Stop `stop` ATR."""
    b = _base(bars, ctx)
    c = b["c"]
    n = p["n"]
    up = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1]))
    dn = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1]))
    mid = (up + dn) / 2
    return _signals_to_events(bars, ctx, p, _cross_up(c, mid), _cross_dn(c, mid),
                              target={1: up, -1: dn})


def stoch_rsi_macd(bars, ctx, p):
    """#241 (Data Trader). After Stochastic %K and %D both dipped under 20,
    they are back between 20 and 80, RSI(14) > 50 and MACD crosses above its
    signal -> long within `win` bars of the oversold dip; mirror at 80.
    Stop `stop` ATR."""
    b = _base(bars, ctx)
    k = F.stochastic(bars, ctx, 14, 3)
    dline = F.sma(numpy.nan_to_num(k, nan=50.0), 3)
    r = F.rsi(bars, ctx, 14)
    m = F.macd(bars, ctx)
    w = p["win"]
    with numpy.errstate(invalid="ignore"):
        os_ = (k < 20) & (dline < 20)
        ob = (k > 80) & (dline > 80)
        rec_os = F.rolling_max(os_.astype(float), w) > 0
        rec_ob = F.rolling_max(ob.astype(float), w) > 0
        mid = (k > 20) & (k < 80) & (dline > 20) & (dline < 80)
        lu = rec_os & mid & (r > 50) & _cross_up(m["macd"], m["signal"])
        ld = rec_ob & mid & (r < 50) & _cross_dn(m["macd"], m["signal"])
    return _signals_to_events(bars, ctx, p, lu, ld)


def bb_rsi(bars, ctx, p):
    """#126 (ChatGPT's strategy), #194 (Brian Chen's), #254. Close under the
    lower Bollinger Band(20, 2) with RSI(14) < 30 -> buy; exit when the close
    is above the upper band with RSI > 70 (`signal`). Mirror short. Stop
    `stop` ATR."""
    b = _base(bars, ctx)
    c = b["c"]
    up, lo = _bb(bars, ctx, p["dev"])
    r = F.rsi(bars, ctx, 14)
    with numpy.errstate(invalid="ignore"):
        sl = (c < lo) & (r < 30)
        ss = (c > up) & (r > 70)
    lu = sl & ~numpy.concatenate(([False], sl[:-1]))
    ld = ss & ~numpy.concatenate(([False], ss[:-1]))
    return _signals_to_events(bars, ctx, p, lu, ld)


def _bb(bars, ctx, dev=2.0):
    def build():
        c = _base(bars, ctx)["c"]
        from numpy.lib.stride_tricks import sliding_window_view
        sd = numpy.full(len(c), NAN)
        if len(c) >= 20:
            sd[19:] = sliding_window_view(c, 20).std(axis=1)
        m = F.sma(c, 20)
        return m + dev * sd, m - dev * sd
    return F.cached(ctx, ("av_bb", dev), build)


def bb_rsi_exit(index, bars, ctx, params, side):
    up, lo = _bb(bars, ctx, params["dev"])
    c = _base(bars, ctx)["c"][index]
    r = F.rsi(bars, ctx, 14)[index]
    return (side == 1 and c > up[index] and r > 70) or (side == -1 and c < lo[index] and r < 30)


NQ = ("rr_2", "rr_1", "days_1")
STOPS = ("atr_1", "atr_2")

SPECS = {
    "av_morning_momentum": (morning_momentum, lambda g: {
        "vol": (1.0, 1.25), "thr": (0.15, 0.25), "stop": ("atr_2", "atr_4"),
        "exit_mode": ("days_1", "rr_1"), "last_entry_minute": g["last"]}, False, None),
    "av_pd_level_retest": (pd_level_retest, lambda g: {
        "tol": (0.1, 0.25), "exit_mode": NQ, "last_entry_minute": g["last"]}, False, None),
    "av_ema_systems": (ema_systems, lambda g: {
        "system": ("13_48", "pullback", "pullback_flip"), "atr": (1.0, 1.5),
        "exit_mode": NQ, "last_entry_minute": g["last"]}, False, None),
    "av_first_hour_fib": (first_hour_fib, lambda g: {
        "side": ("long", "both"), "entry": (0.5, 0.618),
        "exit_mode": ("level", "rr_1", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "av_pd_box": (pd_box, lambda g: {
        "side": ("long", "both"), "stop": STOPS,
        "exit_mode": ("level", "rr_1", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "av_four_hour_fade": (four_hour_fade, lambda g: {
        "min_ext": (0.0, 0.25), "exit_mode": ("rr_2", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "av_rayner": (rayner, lambda g: {
        "variant": ("rsi10", "bb", "stoch", "low10"), "stop": ("atr_2", "atr_4"),
        "exit_mode": ("days_1", "rr_1"), "last_entry_minute": g["last"]}, False, None),
    "av_tradepro": (tradepro, lambda g: {
        "atr": (2.0, 3.0), "exit_mode": ("rr_0.67", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "av_short_momentum": (short_momentum, lambda g: {
        "n": (2, 4), "k": (1.0, 2.0), "stop": STOPS,
        "exit_mode": ("rr_1", "trail_0.25", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "av_dip_bounce": (dip_bounce, lambda g: {
        "n": (2, 4), "k": (1.5, 3.0), "stop": STOPS,
        "exit_mode": ("time_10", "rr_1", "trail_0.25"), "last_entry_minute": g["last"]}, False, None),
    "av_bad_day_reversal": (bad_day_reversal, lambda g: {
        "pct": (10, 20), "stop": ("atr_2", "atr_4"),
        "exit_mode": ("days_1", "rr_1"), "last_entry_minute": g["last"]}, False, None),
    "av_donchian_mid": (donchian_mid, lambda g: {
        "n": (20, 50), "stop": STOPS,
        "exit_mode": ("level", "rr_1", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "av_stoch_rsi_macd": (stoch_rsi_macd, lambda g: {
        "win": (10, 20), "stop": ("atr_1.5", "atr_2"),
        "exit_mode": ("rr_1.5", "rr_2", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "av_bb_rsi": (bb_rsi, lambda g: {
        "dev": (2.0, 2.5), "stop": STOPS,
        "exit_mode": ("signal", "rr_1", "days_1"), "last_entry_minute": g["last"]}, False, bb_rsi_exit),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("system", "side", "stop", "variant")
