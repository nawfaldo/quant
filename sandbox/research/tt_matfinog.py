"""Matteo Conti (YouTube @MatFinOg, and his two IQCapital interviews) as
`cfd_tt_families` families. All of it is NQ (Nasdaq-100 futures); here it runs
on ustec (FundedNext ndx100).

Source (read 2026-11-13 from the videos' own auto-captions + descriptions):
  ueVIjmC9glg  "This Market Anomaly Pays While You Sleep"     -> mf_overnight
  wm6XQFw1GHI  "How to IMPROVE FaberVaale Strategy"           -> mf_orb30
  -2-vE-EbqBI  "...ORDERFLOW trading cannot be AUTOMATED?"    -> mf_delta_dislocation
  wm4A6qo0g3I  IQCapital "...Exact VWAP Setup..."             -> mf_vwap_drift
  A0G14JYYVTk  IQCapital "...FARM Prop Firm Payouts"          -> mf_vault_break,
                                                                 mf_orb_vwap_adx,
                                                                 mf_overnight_bias_orb

THE ENGINE'S LIMITS, stated once:
  * one entry per calendar day: his "max 4 trades a day" (VWAP drift) and "up
    to 3 a day" (vault break) become the day's first trade;
  * one exit mode per cell: a fixed target plus his own flat time cannot both
    be set, so target cells are flattened at the cash close (16:00 ET) instead
    of 15:00 / 15:30 / 15:55 ET -- the gap is said per family;
  * no bid/ask volume: the order-flow delta is a PROXY (each 1m bar's volume
    signed by its candle colour), labelled as such.
Points are NQ points (ustec quotes the same index); his dollar targets are
converted at $20 a point.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F
from sandbox.research import tt_tradebydata as T

NAN = float("nan")
fin = math.isfinite
RTH_OPEN, RTH_CLOSE = 570, 960          # 09:30 / 16:00 New York


def _rth_days(bars, ctx):
    """Per New York date, the cash session read from the FULL series (so a
    job in any window sees it): {day: (open, high, low, close, end_ts)}.
    `end_ts` is when it is complete (16:00 that day, New York seconds)."""
    def build():
        full = ctx.get("tt_full")
        if full is None or not len(full["ts"]):
            return {}
        shift = int(ctx["cfg"]["shift_hours"]) * 3600
        ts = full["ts"] - shift
        m = (ts // 60) % 1440
        day = ts // 86400
        inside = numpy.flatnonzero((m >= RTH_OPEN) & (m < RTH_CLOSE))
        out = {}
        for i in inside.tolist():
            d = int(day[i])
            row = out.get(d)
            if row is None:
                out[d] = [full["o"][i], full["h"][i], full["l"][i], full["c"][i],
                          d * 86400 + RTH_CLOSE * 60]
            else:
                row[1] = max(row[1], full["h"][i])
                row[2] = min(row[2], full["l"][i])
                row[3] = full["c"][i]
        return out
    return F.cached(ctx, "mf_rth_days", build)


def _daily_atr15(bars, ctx):
    """Per trading day (17:00 roll): the mean daily true range of the 15
    completed days before it -- his "15-session moving average of the daily
    ATR". {trading_day: value}."""
    def build():
        full = ctx.get("tt_full")
        if full is None or not len(full["ts"]):
            return {}
        shift = int(ctx["cfg"]["shift_hours"]) * 3600
        td = (full["ts"] - shift + 7 * 3600) // 86400
        starts = numpy.flatnonzero(numpy.append(True, td[1:] != td[:-1]))
        ends = numpy.append(starts[1:], len(td))
        hi = numpy.maximum.reduceat(full["h"], starts)
        lo = numpy.minimum.reduceat(full["l"], starts)
        cl = full["c"][ends - 1]
        prev = numpy.append(cl[0], cl[:-1])
        tr = numpy.maximum(hi - lo, numpy.maximum(abs(hi - prev), abs(lo - prev)))
        out = {}
        for k in range(15, len(starts)):
            out[int(td[starts[k]])] = float(tr[k - 15:k].mean())
        return out
    return F.cached(ctx, "mf_atr15", build)


def _full_window(bars, ctx, start, end):
    """High/low/open of the New York window [start, end) from the full series,
    keyed by the New York date it ENDS on (end < start wraps midnight)."""
    def build():
        full = ctx.get("tt_full")
        if full is None or not len(full["ts"]):
            return {}
        shift = int(ctx["cfg"]["shift_hours"]) * 3600
        ts = full["ts"] - shift
        m = (ts // 60) % 1440
        day = ts // 86400
        if start < end:
            inside, key = (m >= start) & (m < end), day
        else:
            inside, key = (m >= start) | (m < end), day + (m >= start)
        out = {}
        for i in numpy.flatnonzero(inside).tolist():
            d = int(key[i])
            row = out.get(d)
            if row is None:
                out[d] = [full["h"][i], full["l"][i], full["o"][i]]
            else:
                row[0] = max(row[0], full["h"][i])
                row[1] = min(row[1], full["l"][i])
        return out
    return F.cached(ctx, ("mf_win", start, end), build)


# =========================================================================== #
# ueVIjmC9glg -- the overnight drift
# =========================================================================== #

def overnight(bars, ctx, p):
    """Buy the Globex reopen (17:00 CT = 18:00 ET), sell at 03:00 CT (04:00 ET,
    the European open). `variant`: `red` = only after a red cash session (his
    final version: 628 trades, PF 1.39 since 2021), `all` = every night (his
    v2), `range_break` = his v1: range 16:00-17:00 ET, a close above it in the
    first two hours after the reopen buys, stop at the range low, 1R target. No
    stop otherwise: `stop_dr` daily ranges (ours). Run in the `off` window.
    The entry fills on the first bar FundedNext quotes after the reopen."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    a = T._atr(bars, ctx)
    m, B, td = k["min"], k["bar"], k["td"]
    rth = _rth_days(bars, ctx)
    pre = _full_window(bars, ctx, 960, 1020)
    ex = numpy.flatnonzero(T._at(m, B, 240)).tolist()
    hi_, cl_ = b["h"], b["c"]
    import bisect
    rdays, pdays = sorted(rth), sorted(pre)

    def latest(keys, table, d):
        q = bisect.bisect_right(keys, d) - 1
        return table[keys[q]] if q >= 0 and d - keys[q] <= 4 else None
    out, plan, tps = {}, {}, {}
    # The entry is offered on every bar from 18:00 to 20:00 ET: the engine
    # takes the first one it can fill (FundedNext quotes resume 18:15, and
    # 19:00 before 2024-07-08) and only one a day.
    for i in range(len(bars)):
        if m[i] < 1080 or m[i] >= 1200 or int(k["td_wd"][i]) >= 5:
            continue
        t = int(td[i])
        prev_day = int(k["day"][i])     # the reopen's NY date; Sunday -> Friday's
        if p["variant"] == "range_break":
            rng = latest(pdays, pre, prev_day)
            if rng is None or not cl_[i] > rng[0]:
                continue
            dist = F.stop_dist(1, cl_[i], rng[1], a[i])
            if T._emit(out, i, 1, dist):
                T._plan(plan, i, T._next_at(ex, i))
                tps.setdefault(t, cl_[i] + dist)    # 1R from the night's first signal
            continue
        row = latest(rdays, rth, prev_day)
        if p["variant"] == "red" and (row is None or not row[3] < row[0]):
            continue
        if T._emit(out, i, 1, T._wide(ctx, i, p)):
            T._plan(plan, i, T._next_at(ex, i))
    time_exit = T._plan_exit(plan)

    def exit_fn(j, side):
        if time_exit(j, side):
            return True
        lvl = tps.get(int(td[j]))
        return lvl is not None and hi_[j] >= lvl
    T._store_exit(ctx, "mf_overnight", p, exit_fn)
    return out


# =========================================================================== #
# wm6XQFw1GHI -- Fabio Valentini's ORB, as he kept it
# =========================================================================== #

def orb30(bars, ctx, p):
    """Opening range = the first `or_min` minutes of the NY cash session; a
    5m close above its high buys, stop at its low, 1R (`rr_1`). Long only
    (his short mirror had no edge; `side=both` keeps it as the check). Delta
    filter dropped, as he did. Sizing on the stop = his volatility-target
    version. He flattens at 15:00 ET; here at the 16:00 cash close."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    a = T._atr(bars, ctx)
    m, B = k["min"], k["bar"]
    out = {}
    day, hi, lo, done = None, None, None, False
    end = RTH_OPEN + p["or_min"]
    for i in range(len(bars)):
        if m[i] < RTH_OPEN or m[i] >= RTH_CLOSE:
            continue
        d = int(k["day"][i])
        if d != day:
            day, hi, lo, done = d, None, None, False
        if m[i] + B <= end:
            hi = b["h"][i] if hi is None else max(hi, b["h"][i])
            lo = b["l"][i] if lo is None else min(lo, b["l"][i])
            continue
        if done or hi is None or m[i] >= 900:
            continue
        c = b["c"][i]
        if c > hi:
            done = True
            T._emit(out, i, 1, F.stop_dist(1, c, lo, a[i]))
        elif p["side"] == "both" and c < lo:
            done = True
            T._emit(out, i, -1, F.stop_dist(-1, c, hi, a[i]))
    return out


# =========================================================================== #
# -2-vE-EbqBI -- the delta dislocation (PROXY delta)
# =========================================================================== #

def delta_dislocation(bars, ctx, p):
    """Long when price is above the previous cash session's high, the last
    hour was red, yet the last hour's delta was positive and large. Delta is a
    PROXY: each bar's volume signed by its candle colour, summed over the last
    hour of bars (run at 1m so the hour holds 60 of them); "large" = above the
    `q` quantile of the positive hourly proxy over the trailing 20 sessions,
    fixed at each session's start
    (his 500 contracts has no meaning on vendor volume). His exits were
    arbitrary ($10,000 TP/SL on NQ = 500 points): here `tp_pts`/`sl_pts`,
    out at the cash close."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    m, B = k["min"], k["bar"]
    n = max(1, 60 // B)
    sign = numpy.sign(b["c"] - b["o"])
    sv = sign * b["v"]
    cs = numpy.cumsum(numpy.insert(sv, 0, 0.0))
    hour = numpy.full(len(sv), NAN)
    hour[n - 1:] = cs[n:] - cs[:-n]
    rth = _rth_days(bars, ctx)
    days = sorted(rth)
    import bisect
    out = {}
    hist = []
    last_day, thr = None, NAN
    keep = 20 * 390 // B
    for i in range(n, len(bars)):
        if m[i] < RTH_OPEN + 60 or m[i] >= RTH_CLOSE:
            continue
        d = int(k["day"][i])
        if d != last_day:                  # the threshold is yesterday's view
            last_day = d
            hist = hist[-keep:]
            thr = (float(numpy.quantile(hist, p["q"])) if len(hist) >= 200 else NAN)
        if fin(hour[i]) and hour[i] > 0:
            hist.append(hour[i])
        j = bisect.bisect_left(days, d) - 1
        if j < 0 or not fin(thr):
            continue
        pdh = rth[days[j]][1]
        red = b["c"][i] < b["o"][i - n + 1]
        if not (b["c"][i] > pdh and red and fin(hour[i])):
            continue
        if hour[i] >= thr:
            T._emit(out, i, 1, float(p["sl_pts"]), float(p["tp_pts"]))
    return out


# =========================================================================== #
# wm4A6qo0g3I -- the drift VWAP pullback ("prop firm golden ticket")
# =========================================================================== #

def vwap_drift(bars, ctx, p):
    """Every 15 minutes: trend up = price above the session VWAP, VWAP higher
    than 15 minutes ago, NQ up >= `thr`% over the last hour (down mirrored).
    No trades before 10:30 or after 15:30 ET. Trigger: the first counter-
    colour 5m candle of the trend (red for longs) -> market order at the next
    open. Long TP 40 / SL 80 points, short TP 50 / SL 80. His guards (max 4
    trades / 2 losses a day) reduce to the engine's one trade a day; he
    flattens 15:55, here 16:00."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    vw = F.vwap(bars, ctx)
    m, B = k["min"], k["bar"]
    n15, n60 = max(1, 15 // B), max(1, 60 // B)
    out = {}
    trend, used, day = 0, False, None
    for i in range(n60, len(bars)):
        if m[i] < RTH_OPEN or m[i] >= RTH_CLOSE:
            continue
        d = int(k["day"][i])
        if d != day:
            day, trend, used = d, 0, False
        if (m[i] + B) % 15 == 0 and k["day"][i - n60] == d:
            c = b["c"][i]
            ret = 100 * (c / b["c"][i - n60] - 1)
            up = c > vw[i] and vw[i] > vw[i - n15] and ret >= p["thr"]
            dn = c < vw[i] and vw[i] < vw[i - n15] and ret <= -p["thr"]
            new = 1 if up else (-1 if dn else 0)
            if new != trend:
                trend, used = new, False
        if not trend or used or m[i] < 630 or m[i] >= 930:
            continue
        red = b["c"][i] < b["o"][i]
        green = b["c"][i] > b["o"][i]
        if (trend == 1 and red) or (trend == -1 and green):
            used = True
            tp = p["tp_long"] if trend == 1 else p["tp_short"]
            T._emit(out, i, trend, float(p["sl_pts"]), float(tp))
    return out


# =========================================================================== #
# A0G14JYYVTk -- three prop-firm strategies
# =========================================================================== #

def vault_break(bars, ctx, p):
    """Noise area: barrier = the price at midnight CT (01:00 ET) + `k` x the
    15-session mean daily range. A 30m close above the barrier and above VWAP
    buys, 10:00-14:30 CT (11:00-15:30 ET). TP $800 / SL $1,500 per NQ
    contract = 40 / 75 points. Long only. ASSUMED: VWAP anchored at the cash
    open (he anchors it at midnight; the window's bars start at 09:30). His
    "up to 3 trades a day" is the engine's one; he exits 15:30 ET, here 16:00."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    vw = F.vwap(bars, ctx)
    m = k["min"]
    atr15 = _daily_atr15(bars, ctx)
    mid = _full_window(bars, ctx, 60, 65)          # the 01:00 ET open
    out = {}
    for i in range(len(bars)):
        if m[i] < 660 or m[i] >= 930:
            continue
        d = int(k["day"][i])
        o = mid.get(d)
        rng = atr15.get(int(k["td"][i]))
        if o is None or rng is None:
            continue
        barrier = o[2] + p["k"] * rng
        c = b["c"][i]
        if c > barrier and c > vw[i]:
            T._emit(out, i, 1, float(p["sl_pts"]), float(p["tp_pts"]))
    return out


def orb_vwap_adx(bars, ctx, p):
    """1m, long only. Opening range 09:30-10:00 ET. Armed once a bar closes
    above the range high; then price touches VWAP and a bar closes back above
    it with ADX(14) > `adx_min` (his 20) and not rising (if ADX is the only thing missing,
    wait while price holds above VWAP). TP = the 5-bar high, SL = the 20-bar
    low (a TP at or below the entry skips the trade). He flattens 15:55 ET."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    vw = F.vwap(bars, ctx)
    adx = F.adx(bars, ctx, 14)["adx"]
    a = T._atr(bars, ctx)
    m, B = k["min"], k["bar"]
    hh, ll, cc = b["h"], b["l"], b["c"]
    hi5 = F.rolling_max(hh, 5)
    lo20 = F.rolling_min(ll, 20)
    out = {}
    day = hi = None
    armed = touched = done = False
    for i in range(1, len(bars)):
        if m[i] < RTH_OPEN or m[i] >= RTH_CLOSE:
            continue
        d = int(k["day"][i])
        if d != day:
            day, hi, armed, touched, done = d, None, False, False, False
        if m[i] + B <= 600:
            hi = hh[i] if hi is None else max(hi, hh[i])
            continue
        if done or hi is None or m[i] >= 945:
            continue
        if not armed:
            armed = cc[i] > hi
            continue
        if ll[i] <= vw[i]:
            touched = True
        if not touched:
            continue
        if cc[i] <= vw[i]:
            continue                       # still at / under VWAP: wait
        if fin(adx[i]) and adx[i] > p["adx_min"] and adx[i] <= adx[i - 1]:
            tp = F.tgt(1, cc[i], hi5[i])
            sl = F.stop_dist(1, cc[i], lo20[i], a[i])
            if fin(tp) and T._emit(out, i, 1, sl, tp):
                done = True
    return out


def overnight_bias_orb(bars, ctx, p):
    """15m, long and short. Overnight range 23:00 CT -> 08:30 CT (00:00-09:30
    ET). The 09:30 open in its top third -> longs only, bottom third -> shorts
    only, middle -> no trade. The first 15m bar is the opening range; a close
    beyond it on the bias side with ADX(14) > 20 trades, once a day. SL = 0.3 x
    the 15-session mean daily range, TP 3x (`rr_3`). He exits 15:30 ET, here
    16:00."""
    k = T._clock(bars, ctx)
    b = F.base(bars, ctx)
    adx = F.adx(bars, ctx, 14)["adx"]
    m, B = k["min"], k["bar"]
    on = _full_window(bars, ctx, 0, RTH_OPEN)
    atr15 = _daily_atr15(bars, ctx)
    out = {}
    day = bias = orh = orl = None
    done = False
    for i in range(len(bars)):
        if m[i] < RTH_OPEN or m[i] >= RTH_CLOSE:
            continue
        d = int(k["day"][i])
        if d != day:
            day, done, orh, orl, bias = d, False, None, None, 0
            rng = on.get(d)
            if rng is not None and rng[0] > rng[1]:
                pos = (b["o"][i] - rng[1]) / (rng[0] - rng[1])
                bias = 1 if pos >= 2 / 3 else (-1 if pos <= 1 / 3 else 0)
        if m[i] + B <= RTH_OPEN + p["or_min"]:
            orh = b["h"][i] if orh is None else max(orh, b["h"][i])
            orl = b["l"][i] if orl is None else min(orl, b["l"][i])
            continue
        if done or not bias or orh is None or m[i] >= 900:
            continue
        c = b["c"][i]
        if not (fin(adx[i]) and adx[i] > 20):
            continue
        r = atr15.get(int(k["td"][i]))
        if r is None:
            continue
        if (bias == 1 and c > orh) or (bias == -1 and c < orl):
            done = True
            T._emit(out, i, bias, p["stop_k"] * r)
    return out


# =========================================================================== #
# registry
# =========================================================================== #

_MD = "multiday"
#: name -> (compute, axes(g), ny_clock, exit_signal, hold, markets)
SPECS = {
    "mf_overnight": (overnight, lambda g: {
        "variant": ("red", "all", "range_break"), "stop_dr": (1.0, 2.0),
        "exit_mode": ("signal",), "last_entry_minute": (1440,)},
        False, T._exit_for("mf_overnight", overnight), _MD, ("ustec",)),
    "mf_orb30": (orb30, lambda g: {
        "or_min": (30, 15), "side": ("long", "both"),
        "exit_mode": ("rr_1",), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
    "mf_delta_dislocation": (delta_dislocation, lambda g: {
        "q": (0.5, 0.8), "tp_pts": (100, 250), "sl_pts": (100, 250),
        "exit_mode": ("level",), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
    "mf_vwap_drift": (vwap_drift, lambda g: {
        "thr": (0.1, 0.15), "tp_long": (40,), "tp_short": (50,), "sl_pts": (80, 60),
        "exit_mode": ("level",), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
    "mf_vault_break": (vault_break, lambda g: {
        "k": (0.3, 0.5), "tp_pts": (40,), "sl_pts": (75, 50),
        "exit_mode": ("level",), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
    "mf_orb_vwap_adx": (orb_vwap_adx, lambda g: {
        "adx_min": (20, 25),
        "exit_mode": ("level",), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
    "mf_overnight_bias_orb": (overnight_bias_orb, lambda g: {
        "or_min": (15, 30), "stop_k": (0.3, 0.5),
        "exit_mode": ("rr_3", "rr_2"), "last_entry_minute": g["last"]},
        False, None, "session", ("ustec",)),
}

CATEGORICAL = ("variant", "side")
