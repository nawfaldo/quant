"""Trading Data's TikTok (@tradingdata2) strategies, as `cfd_tt_families`
families.

Source: the 105 posts the profile grid loads (it claims 180), read 2026-09-28
from TikTok's English auto-captions. Long tutorials, each reposted several
times; the repeats are one family. Map: `results/tiktok/tradingdata2_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close; structural stops floored at 0.2 ATR.
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


def _crosses(f, s):
    """Bars where `f` crosses `s`: +1 up, -1 down."""
    out = [0] * len(f)
    for i in range(1, len(f)):
        if not (fin(f[i - 1]) and fin(s[i - 1]) and fin(f[i]) and fin(s[i])):
            continue
        if f[i - 1] <= s[i - 1] and f[i] > s[i]:
            out[i] = 1
        elif f[i - 1] >= s[i - 1] and f[i] < s[i]:
            out[i] = -1
    return out


# =========================================================================== #
# 2026-09 .. 2026-07
# =========================================================================== #

def trendline_break(bars, ctx, p):
    """#1 (2026-09-27), #17 (2026-04-25, 934k views). An uptrend (the last two
    `k`-bar swing lows AND highs both rising): the line through the two swing
    lows, projected to this bar; a CLOSE below it -> short. Stop above the
    last swing high; the video takes 2R. Mirror for a downtrend line through
    two falling swing highs."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, php, pl, plp = (pv[x].tolist() for x in ("h", "h_prev", "l", "l_prev"))
    phi, phpi, pli, plpi = (pv[x].tolist() for x in ("h_i", "h_prev_i", "l_i", "l_prev_i"))
    used = set()
    out = {}
    for i in range(1, len(c)):
        if not (a[i] > 0) or not _ok(b, i, p) or plpi[i] < 0 or phpi[i] < 0:
            continue
        up = pl[i] > plp[i] and ph[i] > php[i]
        dn = pl[i] < plp[i] and ph[i] < php[i]
        if up and ("u", pli[i]) not in used:
            slope = (pl[i] - plp[i]) / (pli[i] - plpi[i])
            line = pl[i] + slope * (i - pli[i])
            prev = pl[i] + slope * (i - 1 - pli[i])
            if c[i] < line and c[i - 1] >= prev:
                used.add(("u", pli[i]))
                _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
        elif dn and ("d", phi[i]) not in used:
            slope = (ph[i] - php[i]) / (phi[i] - phpi[i])
            line = ph[i] + slope * (i - phi[i])
            prev = ph[i] + slope * (i - 1 - phi[i])
            if c[i] > line and c[i - 1] <= prev:
                used.add(("d", phi[i]))
                _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
    return out


def ma_cross_filtered(bars, ctx, p):
    """#3 (2026-09-26). 20/50 SMA cross, taken only when the previous
    `need_prior` crosses both "worked" -- the close at the NEXT cross was
    beyond the close at that cross in its direction (known the moment the
    next cross prints, so causal). `trend200` 1 = only with the 200 SMA.
    Stop at the last 3-bar swing; the video takes 2R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    cc = b["c"]
    if p["ma"] == "ema9_20":                      # #5 (2026-05-28): same filter on 9/20 EMAs
        f, s = F.ema(bars, ctx, 9).tolist(), F.ema(bars, ctx, 20).tolist()
    else:
        f, s = F.sma(cc, 20).tolist(), F.sma(cc, 50).tolist()
    t200 = F.sma(cc, 200).tolist()
    x = _crosses(f, s)
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    hist = []                       # (bar, side, close) of past crosses
    wins = []                       # outcome of each COMPLETED cross
    out = {}
    for i in range(len(c)):
        if not x[i]:
            continue
        if hist:
            j, sd, cl = hist[-1]
            wins.append(sd * (c[i] - cl) > 0)
        hist.append((i, x[i], c[i]))
        side = x[i]
        n = p["need_prior"]
        if n and (len(wins) < n or not all(wins[-n:])):
            continue
        if p["trend200"] and not (fin(t200[i]) and side * (c[i] - t200[i]) > 0):
            continue
        if not _ok(b, i, p):
            continue
        ref = pl[i] if side == 1 else ph[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def ema_pullback(bars, ctx, p):
    """#6, #7 (2026-08-21). EMA9 above EMA`slow` (15 or 20) and the close
    above both; a pullback that touches EMA9 (this bar or the last) and a
    bullish candle closing back above EMA9 -> long; mirror short. `rsi50` 1
    = RSI(14) on the right side of 50. Stop below the pullback candle (or
    EMA`slow`, whichever is further, floored); 1:1-1.5, or out on a close back
    through EMA9 (`signal`)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    e9 = F.ema(bars, ctx, 9).tolist()
    es = F.ema(bars, ctx, p["slow"]).tolist()
    r = F.rsi(bars, ctx, 14).tolist()
    out = {}
    for i in range(1, len(c)):
        if not (fin(es[i]) and fin(es[i - 1])) or not _ok(b, i, p):
            continue
        if p["rsi50"] and not fin(r[i]):
            continue
        # `confirm` "prev_high" (#5, 2026-05-28): the pullback bar is a red
        # candle and this green candle closes above its high (mirror short).
        up_ok = c[i] > o[i] and (p["confirm"] == "ema9" or (c[i - 1] < o[i - 1] and c[i] > h[i - 1]))
        dn_ok = c[i] < o[i] and (p["confirm"] == "ema9" or (c[i - 1] > o[i - 1] and c[i] < l[i - 1]))
        if (e9[i] > es[i] and up_ok and c[i] > e9[i]
                and min(l[i], l[i - 1]) <= max(e9[i], e9[i - 1])
                and min(l[i], l[i - 1]) > es[i] - a[i]
                and (not p["rsi50"] or r[i] > 50)):
            ref = min(l[i], l[i - 1], es[i])
            _emit(out, i, 1, F.stop_dist(1, c[i], ref, a[i]), None)
        elif (e9[i] < es[i] and dn_ok and c[i] < e9[i]
                and max(h[i], h[i - 1]) >= min(e9[i], e9[i - 1])
                and max(h[i], h[i - 1]) < es[i] + a[i]
                and (not p["rsi50"] or r[i] < 50)):
            ref = max(h[i], h[i - 1], es[i])
            _emit(out, i, -1, F.stop_dist(-1, c[i], ref, a[i]), None)
    return out


def ema_pullback_exit(index, bars, ctx, params, side):
    e9 = F.ema(bars, ctx, 9)
    cl = F.base(bars, ctx)["c"][index]
    return (side == 1 and cl < e9[index]) or (side == -1 and cl > e9[index])


def ema_smi(bars, ctx, p):
    """#9-#11, #14-#16 (2026-07-19..08-08). EMA9 crossing EMA15 -> enter only
    if the SMI(7,2,2) at that bar is still RISING (falling for a short) and
    not beyond `cap` (their "skip above +60 and flat"; sweet spot +40..+60).
    Stop at the last 3-bar swing (floored); target a multiple of it."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    x = _crosses(F.ema(bars, ctx, 9).tolist(), F.ema(bars, ctx, 15).tolist())
    sm = F.smi(bars, ctx, 7, 2, 2)["smi"].tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    cap = p["cap"]
    out = {}
    for i in range(1, len(c)):
        side = x[i]
        if not side or not (fin(sm[i]) and fin(sm[i - 1])) or not _ok(b, i, p):
            continue
        if side == 1 and not (sm[i] > sm[i - 1] and sm[i] <= cap):
            continue
        if side == -1 and not (sm[i] < sm[i - 1] and sm[i] >= -cap):
            continue
        ref = pl[i] if side == 1 else ph[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def ema_cross_level(bars, ctx, p):
    """#4, #5 (2026-09-18, 84k views). A 9/20 EMA cross taken only off a
    tested level: support = the last two `k`-bar swing lows within `tol` ATR
    of each other (two touches); price comes back into that zone (a third
    touch) and within 6 bars EMA9 crosses up while the close holds above the
    zone -> long. Stop below the zone; the video takes 1:1.5. Mirror for
    resistance. ASSUMED: the flip-zone and premium/discount refinements are
    left out -- the video presents them as optional extra filters."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    x = _crosses(F.ema(bars, ctx, 9).tolist(), F.ema(bars, ctx, 20).tolist())
    pv = F.pivots(bars, ctx, p["k"])
    pl, plp, ph, php = (pv[k].tolist() for k in ("l", "l_prev", "h", "h_prev"))
    tol = p["tol"]
    touch_s = touch_r = -10 ** 9
    out = {}
    for i in range(len(c)):
        ai = a[i]
        if not (ai > 0):
            continue
        sup = fin(plp[i]) and abs(pl[i] - plp[i]) <= tol * ai
        res = fin(php[i]) and abs(ph[i] - php[i]) <= tol * ai
        zlo = min(pl[i], plp[i]) if sup else NAN
        zhi = max(ph[i], php[i]) if res else NAN
        if sup and l[i] <= max(pl[i], plp[i]) + tol * ai and c[i] > zlo:
            touch_s = i
        if res and h[i] >= min(ph[i], php[i]) - tol * ai and c[i] < zhi:
            touch_r = i
        if not _ok(b, i, p):
            continue
        if x[i] == 1 and sup and i - touch_s <= 6 and c[i] > zlo:
            _emit(out, i, 1, F.stop_dist(1, c[i], zlo - 0.1 * ai, ai), None)
        elif x[i] == -1 and res and i - touch_r <= 6 and c[i] < zhi:
            _emit(out, i, -1, F.stop_dist(-1, c[i], zhi + 0.1 * ai, ai), None)
    return out


def sweep_three_confirm(bars, ctx, p):
    """#8, #13 (2026-07-31..08-18). (1) LOCATION: a low below a meaningful
    level -- yesterday's low (`pdl`) or the last 10-bar swing low (`swing`);
    (2) DISPLACEMENT: within `w` bars a candle with a body >= `disp` ATR in
    the reversal direction; (3) STRUCTURE: a candle BODY closes above the last
    2-bar swing high -> long at that close. Stop below the sweep low; 2R.
    Mirror for highs. ASSUMED: the "oversized candle -> wait for the FVG
    retrace" branch is not built."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    if p["level"] == "pdl":
        lo_l, hi_l = d["pdl"].tolist(), d["pdh"].tolist()
    else:
        pv10 = F.pivots(bars, ctx, 10)
        lo_l, hi_l = pv10["l"].tolist(), pv10["h"].tolist()
    mn = F.pivots(bars, ctx, 2)
    mh, ml = mn["h"].tolist(), mn["l"].tolist()
    w, disp = p["w"], p["disp"]
    dn_sw = up_sw = None           # (bar, extreme, displaced?)
    out = {}
    for i in range(len(c)):
        ai = a[i]
        if not (ai > 0):
            continue
        if fin(lo_l[i]) and l[i] < lo_l[i] and (dn_sw is None or l[i] < dn_sw[1]):
            dn_sw = [i, l[i], False]
        if fin(hi_l[i]) and h[i] > hi_l[i] and (up_sw is None or h[i] > up_sw[1]):
            up_sw = [i, h[i], False]
        if dn_sw is not None:
            if i - dn_sw[0] > w:
                dn_sw = None
            else:
                dn_sw[1] = min(dn_sw[1], l[i])
                if c[i] - o[i] >= disp * ai:
                    dn_sw[2] = True
                if (dn_sw[2] and i > dn_sw[0] and fin(mh[i]) and c[i] > mh[i]
                        and o[i] < mh[i] and _ok(b, i, p)):
                    if _emit(out, i, 1, F.stop_dist(1, c[i], dn_sw[1], ai), None):
                        dn_sw = None
        if up_sw is not None:
            if i - up_sw[0] > w:
                up_sw = None
            else:
                up_sw[1] = max(up_sw[1], h[i])
                if o[i] - c[i] >= disp * ai:
                    up_sw[2] = True
                if (up_sw[2] and i > up_sw[0] and fin(ml[i]) and c[i] < ml[i]
                        and o[i] > ml[i] and _ok(b, i, p)):
                    if _emit(out, i, -1, F.stop_dist(-1, c[i], up_sw[1], ai), None):
                        up_sw = None
    return out


def halftrend(bars, ctx, p):
    """#12 (2026-08-05). BigBeluga's HalfTrend signal engine: the HalfTrend
    (amplitude 2) turns up -> long on that candle's close, down -> short.
    The indicator draws an ATR-based stop; here `stop` ATR. They exit at the
    second target, 1:2; `rr_3` is the table's 1:3."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    ht = F.halftrend(bars, ctx, p["amp"], 2)["flip"].tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(c)):
        if ht[i] and _ok(b, i, p):
            _emit(out, i, int(ht[i]), mult * a[i], None)
    return out


def first_4h_fakeout(bars, ctx, p):
    """#18 (2026-01-18, 2.1M views). The day's first 4-hour candle (00:00-04:00
    New York) sets the range. After it closes, a candle CLOSES outside it,
    then a later candle closes back inside -> fade: short after a break above,
    long after a break below. Stop at the breakout's extreme; 2R (`level`
    targets the far side of the range instead). Same day only. `buf` = how
    far (ATR) beyond the range the breakout close must be; 0 is theirs."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    win = F.clock_window(bars, ctx, "h0_4")
    wh, wl = win["hi"].tolist(), win["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = st = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, st = day[i], None
        hi, lo = wh[i], wl[i]
        if not fin(hi):
            continue
        if st is None:
            buf = p["buf"] * a[i] if a[i] > 0 else 0.0
            if c[i] > hi + buf:
                st = [1, h[i]]
            elif c[i] < lo - buf:
                st = [-1, l[i]]
            continue
        if st[0] == 1:
            st[1] = max(st[1], h[i])
            if c[i] < hi and _ok(b, i, p):
                far = F.tgt(-1, c[i], lo)
                if _emit(out, i, -1, F.stop_dist(-1, c[i], st[1], a[i]), {"far": far}):
                    st = [0, 0]
        elif st[0] == -1:
            st[1] = min(st[1], l[i])
            if c[i] > lo and _ok(b, i, p):
                far = F.tgt(1, c[i], hi)
                if _emit(out, i, 1, F.stop_dist(1, c[i], st[1], a[i]), {"far": far}):
                    st = [0, 0]
    return out


# =========================================================================== #
# 2026-08 .. 2026-06
# =========================================================================== #

def divergence_choch(bars, ctx, p):
    """#8 (2026-08-13). BigBeluga's Market Structure Trend Matrix flip is only
    a candidate; the trade needs RSI(14) divergence first: the last two
    `k`-bar swing highs make a higher high while RSI at them makes a lower
    high, then a CLOSE below the last swing low (the change of character)
    within `w` bars of the second high -> short. Stop above that high. Mirror
    for bullish divergence. The indicator trails an ATR line and stacks ATR
    targets; here a multiple of the stop."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, 14).tolist()
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    ph, php, pl, plp = (pv[x].tolist() for x in ("h", "h_prev", "l", "l_prev"))
    phi, phpi, pli, plpi = (pv[x].tolist() for x in ("h_i", "h_prev_i", "l_i", "l_prev_i"))
    done = set()
    out = {}
    for i in range(len(c)):
        if not (a[i] > 0) or not _ok(b, i, p):
            continue
        j, jp = phi[i], phpi[i]
        if (jp >= 0 and ("h", j) not in done and i - j <= p["w"] + k and ph[i] > php[i]
                and fin(r[j]) and fin(r[jp]) and r[j] < r[jp] and fin(pl[i]) and c[i] < pl[i]):
            done.add(("h", j))
            _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
            continue
        j, jp = pli[i], plpi[i]
        if (jp >= 0 and ("l", j) not in done and i - j <= p["w"] + k and pl[i] < plp[i]
                and fin(r[j]) and fin(r[jp]) and r[j] > r[jp] and fin(ph[i]) and c[i] > ph[i]):
            done.add(("l", j))
            _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
    return out


def squeeze_fire(bars, ctx, p):
    """#15 (2026-07-15). LazyBear's Squeeze Momentum: the squeeze (Bollinger
    inside Keltner, yellow dots) releases (white dots) -> go the way the
    momentum histogram points; `min_bars` = how long the squeeze had to
    last. Stop `stop` ATR. The video lists no exit; a multiple of the stop."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    sq = F.squeeze(bars, ctx)
    on, val = sq["on"].tolist(), sq["val"].tolist()
    mult = float(p["stop"][4:])
    run = 0
    out = {}
    for i in range(1, len(c)):
        if on[i]:
            run += 1
            continue
        fired = on[i - 1] and run >= p["min_bars"]
        run = 0
        if not fired or not fin(val[i]) or val[i] == 0 or not _ok(b, i, p):
            continue
        _emit(out, i, 1 if val[i] > 0 else -1, mult * a[i], None)
    return out


def ut_bot(bars, ctx, p):
    """#16, #19 (2026-07-09..11, 121k views). UT Bot Alerts run twice: buys
    from key 6 / ATR 10, sells from key 7 / ATR 20 (their 5-minute settings;
    `keys` "default" = key 1 / ATR 10 both sides), `ema200` 1 = only with the
    200 EMA. Stop at the last 3-bar swing; 2R, or out on the opposite UT
    signal (`signal`, their trend-ride variant)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    if p["keys"] == "tuned":
        buy = F.ut_bot(bars, ctx, 6.0, 10)["sig"].tolist()
        sell = F.ut_bot(bars, ctx, 7.0, 20)["sig"].tolist()
    else:
        buy = sell = F.ut_bot(bars, ctx, 1.0, 10)["sig"].tolist()
    e = F.ema(bars, ctx, 200).tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(len(c)):
        if not _ok(b, i, p):
            continue
        if buy[i] == 1 and (not p["ema200"] or (fin(e[i]) and c[i] > e[i])):
            _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
        elif sell[i] == -1 and (not p["ema200"] or (fin(e[i]) and c[i] < e[i])):
            _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
    return out


def ut_bot_exit(index, bars, ctx, params, side):
    if params["keys"] == "tuned":
        s = (F.ut_bot(bars, ctx, 7.0, 20) if side == 1 else F.ut_bot(bars, ctx, 6.0, 10))["sig"]
    else:
        s = F.ut_bot(bars, ctx, 1.0, 10)["sig"]
    return s[index] == -side


def adx_di(bars, ctx, p):
    """#17, #18, #20 (2026-07-07..09). ADX(14) above `level` (they use 20,
    not 25) and rising, +DI crossing above -DI -> long (mirror short);
    `ema_slope` 1 = the 200 EMA sloping the same way over the last 10 bars
    (their lag fix). Stop at the last 3-bar swing; a multiple of it."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.adx(bars, ctx, 14)
    ax, pdi, mdi = d["adx"].tolist(), d["pdi"].tolist(), d["mdi"].tolist()
    e = F.ema(bars, ctx, 200).tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(10, len(c)):
        if not (fin(ax[i]) and fin(ax[i - 1]) and fin(pdi[i - 1])) or not _ok(b, i, p):
            continue
        if not (ax[i] > p["level"] and ax[i] > ax[i - 1]):
            continue
        slope = e[i] - e[i - 10] if fin(e[i - 10]) else NAN
        if pdi[i - 1] <= mdi[i - 1] and pdi[i] > mdi[i] and (not p["ema_slope"] or slope > 0):
            _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
        elif pdi[i - 1] >= mdi[i - 1] and pdi[i] < mdi[i] and (not p["ema_slope"] or slope < 0):
            _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
    return out


def flag_break(bars, ctx, p):
    """#23 (2026-07-02). Bull flag: a pole -- the close up at least `pole`
    ATR over 5 bars -- then 3-5 bars of sideways/down drift that holds the
    pole's upper half; a close above the flag's high -> long. Stop under the
    flag's low. Mirror for bear flags."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    out = {}
    for i in range(10, len(c)):
        ai = a[i]
        if not (ai > 0) or not _ok(b, i, p):
            continue
        for fl in (3, 4, 5):
            s = i - fl                 # flag = bars s..i-1, pole ends at s-1
            p0, p1 = s - 6, s - 1
            if p0 < 0:
                break
            move = c[p1] - c[p0]
            if abs(move) < p["pole"] * ai:
                continue
            fh, flo = max(h[s:i]), min(l[s:i])
            if move > 0:
                if flo < c[p0] + move / 2 or fh > h[p1] + 0.1 * ai:
                    continue
                if c[i] > fh:
                    _emit(out, i, 1, F.stop_dist(1, c[i], flo, ai), None)
                    break
            else:
                if fh > c[p0] + move / 2 or flo < l[p1] - 0.1 * ai:
                    continue
                if c[i] < flo:
                    _emit(out, i, -1, F.stop_dist(-1, c[i], fh, ai), None)
                    break
    return out


def ema_macd(bars, ctx, p):
    """#25 (2026-06-26). Long only: the close above EMA200, MACD crossed above
    its signal within the last `within` bars (they use 3) and MACD above
    zero -> long. Out when MACD crosses back below its signal or the close
    falls under EMA200 (`signal`). Stop 1.5 ATR. Their freqtrade test (8
    crypto pairs, 4h, 2020-2026): +2,311%, DD 14.3%. `side` "both" mirrors."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m = F.macd(bars, ctx)
    ln, sg = m["macd"].tolist(), m["signal"].tolist()
    e = F.ema(bars, ctx, 200).tolist()
    x = _crosses(ln, sg)
    out = {}
    last_up = last_dn = -10 ** 9
    for i in range(len(c)):
        if x[i] == 1:
            last_up = i
        elif x[i] == -1:
            last_dn = i
        if not (fin(e[i]) and fin(ln[i]) and fin(sg[i])) or not _ok(b, i, p):
            continue
        if c[i] > e[i] and ln[i] > 0 and ln[i] > sg[i] and i - last_up < p["within"]:
            _emit(out, i, 1, 1.5 * a[i], None)
        elif (p["side"] == "both" and c[i] < e[i] and ln[i] < 0 and ln[i] < sg[i]
                and i - last_dn < p["within"]):
            _emit(out, i, -1, 1.5 * a[i], None)
    return out


def ema_macd_exit(index, bars, ctx, params, side):
    m = F.macd(bars, ctx)
    e = F.ema(bars, ctx, 200)
    cl = F.base(bars, ctx)["c"][index]
    ln, sg = m["macd"][index], m["signal"][index]
    if side == 1:
        return ln < sg or cl < e[index]
    return ln > sg or cl > e[index]


def choch_bos(bars, ctx, p):
    """#26 (2026-06-22). AlgoAlpha's Smart Money Breakout: a change of
    character, then a break of structure the same way -> enter at the BOS
    close; stop beyond the invalidation (the last opposite `k`-bar swing).
    Their TP1/2/3 scale-out becomes the exit axis."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    s = F.structure(bars, ctx, p["k"])
    up, dn, cu, cd = (s[x].tolist() for x in ("up", "dn", "choch_up", "choch_dn"))
    pv = F.pivots(bars, ctx, p["k"])
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    armed = 0
    out = {}
    for i in range(len(c)):
        if cu[i]:
            armed = 1
            continue
        if cd[i]:
            armed = -1
            continue
        if armed == 1 and up[i]:
            armed = 0
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
        elif armed == -1 and dn[i]:
            armed = 0
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
    return out


def ema_5_13_89(bars, ctx, p):
    """#29 (2026-06-16). EMA5 and EMA13 both above EMA89 (the trend); a
    pullback -- EMA5 dips to or under EMA13 -- then EMA5 crosses back above
    EMA13 on a green candle -> long. `trend_n` 89 is theirs. Stop below EMA89 (floored). Trend ride:
    out when both fast EMAs close under EMA89 (`signal`); or 1:1 / 1:2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    e5, e13, e89 = (F.ema(bars, ctx, n).tolist() for n in (5, 13, p["trend_n"]))
    out = {}
    for i in range(1, len(c)):
        if not (fin(e89[i]) and fin(e5[i - 1]) and fin(e13[i - 1])) or not _ok(b, i, p):
            continue
        if (e5[i] > e89[i] and e13[i] > e89[i] and e5[i - 1] <= e13[i - 1]
                and e5[i] > e13[i] and c[i] > o[i]):
            _emit(out, i, 1, F.stop_dist(1, c[i], e89[i], a[i]), None)
        elif (e5[i] < e89[i] and e13[i] < e89[i] and e5[i - 1] >= e13[i - 1]
                and e5[i] < e13[i] and c[i] < o[i]):
            _emit(out, i, -1, F.stop_dist(-1, c[i], e89[i], a[i]), None)
    return out


def ema_5_13_89_exit(index, bars, ctx, params, side):
    e5, e13, e89 = (F.ema(bars, ctx, n) for n in (5, 13, params["trend_n"]))
    if side == 1:
        return e5[index] < e89[index] and e13[index] < e89[index]
    return e5[index] > e89[index] and e13[index] > e89[index]


def zero_lag_trend(bars, ctx, p):
    """#31 (2026-06-07). AlgoAlpha's Zero Lag Trend Signals (ZLEMA `n`, band
    1.2 x the highest ATR): the trend flips up -> long, down -> short. Their
    5m/15m MTF table becomes `htf`: the same indicator on an `htf`x longer
    ZLEMA must agree. Stop at the last 3-bar swing; 1:1.5."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    flip = F.zlema_trend(bars, ctx, p["n"], 1.2)["flip"].tolist()
    slow = (F.zlema_trend(bars, ctx, p["n"] * p["htf"], 1.2)["trend"].tolist()
            if p["htf"] > 1 else None)
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(len(c)):
        side = int(flip[i])
        if not side or not _ok(b, i, p):
            continue
        if slow is not None and slow[i] != side:
            continue
        ref = pl[i] if side == 1 else ph[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


# =========================================================================== #
# 2026-05 .. 2026-03
# =========================================================================== #

def ema_cci(bars, ctx, p):
    """#6 (2026-05-26, 31k views). EMA10 crossing EMA20, confirmed by CCI(20)
    on the same side of zero at the cross or having crossed it within the
    last `lead` bars ("CCI often crosses zero before the EMAs do"). Stop at
    the last 3-bar swing (floored); 1:1.5-2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    x = _crosses(F.ema(bars, ctx, 10).tolist(), F.ema(bars, ctx, 20).tolist())
    cc = F.cci(bars, ctx, 20).tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(len(c)):
        side = x[i]
        if not side or not fin(cc[i]) or not _ok(b, i, p):
            continue
        if side * cc[i] <= 0:
            continue
        if p["lead"] and not any(fin(cc[j]) and side * cc[j] <= 0
                                 for j in range(max(0, i - p["lead"]), i)):
            continue
        ref = pl[i] if side == 1 else ph[i]
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def vixfix_stoch(bars, ctx, p):
    """#7, #8 (2026-05-25, 25k views). CM Williams VIX Fix flashes green (a
    volatility peak, "a potential bottom") within the last `w` bars, the
    Stochastic (14,3) %K is in oversold (< 20) and crosses up through %D on a
    closed candle -> long. Stop under the last 3-bar swing low; 2R. `side`
    "both" adds the inverted VIX Fix top flash + %K crossing down from > 80
    (they found the tops less reliable)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = F.vix_fix(bars, ctx)
    g, t = v["green"].tolist(), v["top"].tolist()
    base = F.base(bars, ctx)
    hh, ll = F.rolling_max(base["h"], 14), F.rolling_min(base["l"], 14)
    raw = numpy.where(hh > ll, 100.0 * (base["c"] - ll) / numpy.where(hh > ll, hh - ll, 1.0), 50.0)
    k = F.sma(raw, 3)
    d = F.sma(numpy.nan_to_num(k, nan=50.0), 3)
    d[:4] = NAN
    d = d.tolist()
    k = k.tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    last_g = last_t = -10 ** 9
    out = {}
    for i in range(1, len(c)):
        if g[i]:
            last_g = i
        if t[i]:
            last_t = i
        if not (fin(k[i]) and fin(d[i]) and fin(k[i - 1]) and fin(d[i - 1])) or not _ok(b, i, p):
            continue
        if i - last_g <= p["w"] and k[i - 1] < 20 and k[i - 1] <= d[i - 1] and k[i] > d[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None)
        elif (p["side"] == "both" and i - last_t <= p["w"] and k[i - 1] > 80
                and k[i - 1] >= d[i - 1] and k[i] < d[i]):
            _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None)
    return out


def bb_squeeze_break(bars, ctx, p):
    """#9 (2026-05-21). A tight compression -- Bollinger(20,2) width in the
    lowest `pct` percent of the last 100 bars, within the last 5 -- then a
    candle closing outside the band, the band's way, on volume >= 1.2x its
    20-bar mean (the video: body outside on a volume spike; loosened so the
    rule fires ~100+ times per setting). `entry`
    "close" enters on that close; "pullback" waits (up to 10 bars) for a
    pullback to the middle band and a candle closing back the breakout way.
    Stop below the middle band (floored); they aim for 5-7R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    base = F.base(bars, ctx)
    cc = base["c"]
    from numpy.lib.stride_tricks import sliding_window_view
    mid = F.sma(cc, 20)
    sd = numpy.full(len(cc), NAN)
    if len(cc) >= 20:
        sd[19:] = sliding_window_view(cc, 20).std(axis=1)
    width = 4 * sd / numpy.where(mid != 0, mid, NAN)
    wq = numpy.full(len(cc), NAN)
    wf = numpy.nan_to_num(width, nan=numpy.inf)
    if len(cc) > 120:
        wq[100:] = numpy.percentile(sliding_window_view(wf, 100)[:-1], p["pct"], axis=1)
    up_b, lo_b = (mid + 2 * sd).tolist(), (mid - 2 * sd).tolist()
    mid, width, wq = mid.tolist(), width.tolist(), wq.tolist()
    vm = numpy.concatenate(([NAN], F.sma(base["v"], 20)[:-1])).tolist()
    v = base["L"]["v"]
    pend = None
    out = {}
    for i in range(1, len(c)):
        ai = a[i]
        if not (ai > 0) or not fin(mid[i]):
            continue
        squeezed = any(fin(wq[j]) and fin(width[j]) and width[j] <= wq[j]
                       for j in range(max(0, i - 5), i))
        vol_ok = fin(vm[i]) and v[i] >= 1.2 * vm[i]
        side = 0
        if squeezed and vol_ok and c[i] > up_b[i] and c[i] > o[i]:
            side = 1
        elif squeezed and vol_ok and c[i] < lo_b[i] and c[i] < o[i]:
            side = -1
        if side:
            if p["entry"] == "close":
                if _ok(b, i, p):
                    _emit(out, i, side, F.stop_dist(side, c[i], mid[i], ai), None)
                continue
            pend = (side, i)
            continue
        if pend is not None:
            sd_, t0 = pend
            if i - t0 > 10:
                pend = None
            elif sd_ == 1 and l[i] <= mid[i] and c[i] > o[i] and c[i] > mid[i] and _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], min(l[i], mid[i]), ai), None)
                pend = None
            elif sd_ == -1 and h[i] >= mid[i] and c[i] < o[i] and c[i] < mid[i] and _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], max(h[i], mid[i]), ai), None)
                pend = None
    return out


def poc_flip(bars, ctx, p):
    """#12, #14 (2026-03-31..04-08). Yesterday's volume POC: price closes
    above it, then comes back to retest it and a candle closes back above
    (the retest held) -> long; stop below the POC (floored); target the value
    area high (`level`) or a multiple. Mirror below the POC. Volume is tick
    volume on Exness."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    prof = F.day_profile(bars, ctx)
    poc = F.prev_day(bars, ctx, prof["poc"]).tolist()
    vah = F.prev_day(bars, ctx, prof["vah"]).tolist()
    val = F.prev_day(bars, ctx, prof["val"]).tolist()
    day = b["L"]["day"]
    out = {}
    cur = state = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, state = day[i], 0
        pc = poc[i]
        if not fin(pc) or not (a[i] > 0):
            continue
        tol = p["tol"] * a[i]
        if state == 0:
            if c[i] > pc + tol:
                state = 1
            elif c[i] < pc - tol:
                state = -1
            continue
        if state == 1 and l[i] <= pc + tol and c[i] > pc and c[i] > o[i] and _ok(b, i, p):
            far = F.tgt(1, c[i], vah[i])
            if _emit(out, i, 1, F.stop_dist(1, c[i], pc - 0.1 * a[i], a[i]), {"far": far}):
                state = 2
        elif state == -1 and h[i] >= pc - tol and c[i] < pc and c[i] < o[i] and _ok(b, i, p):
            far = F.tgt(-1, c[i], val[i])
            if _emit(out, i, -1, F.stop_dist(-1, c[i], pc + 0.1 * a[i], a[i]), {"far": far}):
                state = 2
    return out


def volume_dryup_break(bars, ctx, p):
    """#14 (2026-03-31), strategy 2. A tight range -- the last `n` bars within
    `width` ATR -- on falling volume (their mean under the 20 bars before),
    then a close out of it on a volume spike (>= 2x that mean) -> enter; stop
    at the range's other side; 2-3R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    base = F.base(bars, ctx)
    rh = numpy.concatenate(([NAN], F.rolling_max(base["h"], n)[:-1])).tolist()
    rl = numpy.concatenate(([NAN], F.rolling_min(base["l"], n)[:-1])).tolist()
    vn = numpy.concatenate(([NAN], F.sma(base["v"], n)[:-1]))
    v20 = numpy.full(len(c), NAN)
    v20[n + 1:] = F.sma(base["v"], 20)[:-n - 1] if len(c) > n + 1 else []
    vn, v20 = vn.tolist(), v20.tolist()
    v = base["L"]["v"]
    out = {}
    for i in range(len(c)):
        ai = a[i]
        if not (ai > 0) or not fin(rh[i]) or not fin(v20[i]) or not _ok(b, i, p):
            continue
        if rh[i] - rl[i] > p["width"] * ai or not vn[i] < v20[i]:
            continue
        if not v[i] >= 2.0 * vn[i]:
            continue
        if c[i] > rh[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], rl[i], ai), None)
        elif c[i] < rl[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], rh[i], ai), None)
    return out


def supply_demand(bars, ctx, p):
    """#19 (2026-01-07, 511k views; = 2026-03-09). Demand: a base of 1-3
    small candles (range <= 0.6 ATR each) followed by an explosive candle
    (body >= `burst` ATR) up -- drop-base-rally or rally-base-rally; the zone
    is the base's lowest wick to highest body. The FIRST return into a fresh
    zone -> long on a candle that closes back above the zone top (their
    "smart entry": wait for rejection). Stop a few ticks under the zone; the
    video wants >= 2R. Mirror for supply."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    zones = []                      # [side, lo, hi, born, used]
    out = {}
    for i in range(4, len(c)):
        ai = a[i]
        if not (ai > 0):
            continue
        # a new zone: bars j..i-1 are the base, bar i the burst
        body = c[i] - o[i]
        if abs(body) >= p["burst"] * ai:
            for nb in (1, 2, 3):
                base_ = range(i - nb, i)
                if all(h[j] - l[j] <= 0.6 * ai for j in base_):
                    lo_b = min(l[j] for j in base_)
                    hi_b = max(h[j] for j in base_)
                    if body > 0:
                        zones.append([1, lo_b, max(max(o[j], c[j]) for j in base_), i, False])
                    else:
                        zones.append([-1, min(min(o[j], c[j]) for j in base_), hi_b, i, False])
                    break
            zones = zones[-20:]
            continue
        for z in zones:
            side, zlo, zhi, born, used = z
            if used or i - born < 2:
                continue
            if side == 1 and l[i] <= zhi:
                z[4] = True          # the first touch uses the zone
                if l[i] >= zlo - 0.25 * ai and c[i] > zhi and _ok(b, i, p):
                    _emit(out, i, 1, F.stop_dist(1, c[i], zlo - 0.1 * ai, ai), None)
            elif side == -1 and h[i] >= zlo:
                z[4] = True
                if h[i] <= zhi + 0.25 * ai and c[i] < zlo and _ok(b, i, p):
                    _emit(out, i, -1, F.stop_dist(-1, c[i], zhi + 0.1 * ai, ai), None)
    return out


SPECS = {
    "td_trendline_break": (trendline_break, lambda g: {
        "k": (3, 5, 8), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ma_cross_filtered": (ma_cross_filtered, lambda g: {
        "ma": ("sma20_50", "ema9_20"), "need_prior": (0, 2), "trend200": (0, 1),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_cross_level": (ema_cross_level, lambda g: {
        "k": (3, 5), "tol": (0.25, 0.5),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_pullback": (ema_pullback, lambda g: {
        "slow": (15, 20), "rsi50": (0, 1), "confirm": ("ema9", "prev_high"),
        "exit_mode": ("rr_1", "rr_1.5", "signal"),
        "last_entry_minute": g["last"]}, False, ema_pullback_exit),
    "td_sweep_three_confirm": (sweep_three_confirm, lambda g: {
        "level": ("pdl", "swing"), "w": (6, 12), "disp": (0.8, 1.2),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_smi": (ema_smi, lambda g: {
        "cap": (40, 60, 100), "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "td_halftrend": (halftrend, lambda g: {
        "amp": (2, 3, 5), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_first_4h_fakeout": (first_4h_fakeout, lambda g: {
        "buf": (0.0, 0.2), "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, True, None),
    "td_divergence_choch": (divergence_choch, lambda g: {
        "k": (3, 5), "w": (6, 12), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_squeeze_fire": (squeeze_fire, lambda g: {
        "min_bars": (1, 6), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ut_bot": (ut_bot, lambda g: {
        "keys": ("tuned", "default"), "ema200": (0, 1),
        "exit_mode": ("rr_1.5", "rr_2", "signal"),
        "last_entry_minute": g["last"]}, False, ut_bot_exit),
    "td_adx_di": (adx_di, lambda g: {
        "level": (20, 25), "ema_slope": (0, 1),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_flag_break": (flag_break, lambda g: {
        "pole": (1.5, 2.5, 3.5), "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_macd": (ema_macd, lambda g: {
        "within": (1, 3), "side": ("long", "both"),
        "exit_mode": ("signal", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, ema_macd_exit),
    "td_choch_bos": (choch_bos, lambda g: {
        "k": (3, 5, 10), "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_5_13_89": (ema_5_13_89, lambda g: {
        "trend_n": (55, 89), "exit_mode": ("signal", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, ema_5_13_89_exit),
    "td_zero_lag_trend": (zero_lag_trend, lambda g: {
        "n": (35, 70), "htf": (1, 3),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "td_ema_cci": (ema_cci, lambda g: {
        "lead": (0, 5), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_vixfix_stoch": (vixfix_stoch, lambda g: {
        "w": (3, 8), "side": ("long", "both"),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_bb_squeeze_break": (bb_squeeze_break, lambda g: {
        "pct": (10, 20), "entry": ("close", "pullback"),
        "exit_mode": ("rr_2", "rr_3", "rr_5"),
        "last_entry_minute": g["last"]}, False, None),
    "td_poc_flip": (poc_flip, lambda g: {
        "tol": (0.1, 0.25), "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "td_volume_dryup_break": (volume_dryup_break, lambda g: {
        "n": (6, 12), "width": (2.0, 3.0),
        "exit_mode": ("rr_1", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "td_supply_demand": (supply_demand, lambda g: {
        "burst": (0.8, 1.2, 1.6), "exit_mode": ("rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "level", "keys", "side", "ma", "confirm", "entry")
