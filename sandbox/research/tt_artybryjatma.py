"""The Moving Average's TikTok (@artybryjatma, "Arty") strategies, as
`cfd_tt_families` families.

Source: all 152 posts on the profile, read 2026-10-03 from TikTok's English
auto-captions (long-form: 3-13 minute videos; reposted tutorials deduped).
Arty trades EURUSD / DE40 / US30 / NAS100 intraday and keeps returning to the
same handful of setups: the Tokyo-range breakout, the Fibonacci "gold zone"
retracement, his "secret sauce" RSI divergence, and moving-average pullbacks.
Map and SKIP list: `results/tiktok/artybryjatma_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close; structural stops floored at 0.2 ATR. His
moving averages are SMOOTHED (Wilder) averages; `_smma` builds them.
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
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _smma(bars, ctx, n):
    """Wilder's smoothed moving average of the close (TradingView `ta.rma`)."""
    def build():
        c = F.base(bars, ctx)["c"]
        out = numpy.full(len(c), NAN)
        if len(c) >= n:
            acc = c[:n].mean()
            out[n - 1] = acc
            for i in range(n, len(c)):
                acc += (c[i] - acc) / n
                out[i] = acc
        return out.tolist()
    return F.cached(ctx, ("ma_smma", n), build)


def _rejection(o, h, l, c, i, side):
    """A rejection candle for `side`: it closes in `side`'s direction, or
    leaves a wick against it at least as long as its body."""
    body = abs(c[i] - o[i])
    if side == 1:
        return c[i] > o[i] or (min(o[i], c[i]) - l[i]) >= body > 0
    return c[i] < o[i] or (h[i] - max(o[i], c[i])) >= body > 0


# =========================================================================== #
# 2026-10 .. 2025-04
# =========================================================================== #

def tokyo_breakout(bars, ctx, p):
    """#5, #6, #2, #54 (2026-06..10). Box the Tokyo session (the New York
    clock's 19:00-03:00 Asia window); the first candle that CLOSES above the
    box -> long, below -> short. Stop `stop_frac` x the box's height from the
    entry (his 50%); 1:2 (`rr_2`). #2: skip the day when the breakout candle
    itself is bigger than the box ("it would have skewed the risk-reward") --
    `skip_big` "on". #54 adds the same box as the day's bias for London/NY."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "asia")
    hi, lo = w["hi"].tolist(), w["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = done = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, done = day[i], False
        if done or not fin(hi[i]) or not (a[i] > 0):
            continue
        side = 1 if c[i] > hi[i] else (-1 if c[i] < lo[i] else 0)
        if not side:
            continue
        done = True
        rng = hi[i] - lo[i]
        if p["skip_big"] == "on" and h[i] - l[i] > rng:
            continue
        if _ok(b, i, p):
            _emit(out, i, side, max(p["stop_frac"] * rng, 0.2 * a[i]), None)
    return out


def fib_gold_zone(bars, ctx, p):
    """#2, #4, #5, #9, #37, #43, #57 (gold zone) and #21, #24, #59 (with a
    hidden divergence). An impulse that breaks structure (`k` swings) defines
    a leg from the swing it started at; price retraces into the "gold zone"
    between the 0.5 and the 0.618 of the leg. `confirm` "candle": a
    rejection candle in the zone (his usual trigger); "hidden_div": price is
    still above the leg's start while RSI(14) is below its value at the
    leg's start (#21's hidden bullish divergence at the 0.618). `ema` 200
    only takes legs on the 200 EMA's side (#2's filter); 0 = off. Stop at
    the leg's start (#9: "stop at the top of the previous high"); `level` =
    the leg's end (his take-profit). Mirror for shorts; one entry per leg."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    st = F.structure(bars, ctx, k)
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, k)
    PL, PLi, PH, PHi = (pv[x].tolist() for x in ("l", "l_i", "h", "h_i"))
    r = F.rsi(bars, ctx, 14).tolist()
    e = F.ema(bars, ctx, 200).tolist() if p["ema"] else None
    day = b["L"]["day"]
    out = {}
    leg = None                     # [side, start, start_i, extreme, day]
    for i in range(1, len(c)):
        A = a[i]
        if up[i] and fin(PL[i]):
            leg = [1, PL[i], PLi[i], h[i], day[i]]
            continue
        if dn[i] and fin(PH[i]):
            leg = [-1, PH[i], PHi[i], l[i], day[i]]
            continue
        if leg is None or day[i] != leg[4] or not (A > 0):
            continue
        side, start, si, ext = leg[:4]
        if side * (c[i] - start) <= 0:
            leg = None
            continue
        size = abs(ext - start)
        if side == 1:
            z_top, z_bot = ext - 0.5 * size, ext - 0.618 * size
            inside = l[i] <= z_top and l[i] >= z_bot - 0.05 * size
        else:
            z_bot, z_top = ext + 0.5 * size, ext + 0.618 * size
            inside = h[i] >= z_bot and h[i] <= z_top + 0.05 * size
        if not inside:
            leg[3] = max(ext, h[i]) if side == 1 else min(ext, l[i])
            continue
        if e is not None and (not fin(e[i]) or side * (c[i] - e[i]) <= 0):
            continue
        if p["confirm"] == "candle":
            if not _rejection(o, h, l, c, i, side):
                continue
        elif not (si >= 0 and fin(r[si]) and fin(r[i]) and side * (r[i] - r[si]) < 0):
            continue
        leg = None
        if _ok(b, i, p):
            _emit(out, i, side, F.stop_dist(side, c[i], start, A), {"far": F.tgt(side, c[i], ext)})
    return out


def rsi_div_secret(bars, ctx, p):
    """#7, #14, #18, #24, #10 (2026-09). His "secret sauce" regular RSI
    divergence: price makes a lower (or equal, within `tol` ATR -- his double
    bottom) low than the last `k` swing low, while RSI(14) at the first low
    was BELOW 30 and at the new low is back ABOVE 30 and higher -> on the
    first bullish rejection candle within 3 bars of the new low, long. Mirror
    at highs with 70. Stop `stop` x ATR (#14, #17: 1.5 ATR); 1:2 (`rr_2`),
    or #18's exit when RSI reaches the other band (`signal`). His EURUSD 1m
    week: 33 trades, 17 won, PF 2. He trades it 02:00-12:00 UK only."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    PL, PLi, PH, PHi = (pv[x].tolist() for x in ("l", "l_i", "h", "h_i"))
    r = F.rsi(bars, ctx, 14).tolist()
    mult = float(p["stop"][4:])
    used = set()
    out = {}
    for i in range(3, len(c)):
        A = a[i]
        if not (A > 0) or not fin(r[i]) or not _ok(b, i, p):
            continue
        t = p["tol"] * A
        # the new extreme: the lowest / highest of the last 3 bars
        j = min(range(i - 2, i + 1), key=lambda x: l[x])
        if PLi[i] >= 0 and PLi[i] < j - 1 and (1, PLi[i]) not in used:
            r0, r1 = r[PLi[i]], r[j]
            if fin(r0) and fin(r1) and l[j] <= PL[i] + t and r0 < 30 < r1 and r1 > r0 \
                    and c[i] > o[i] and c[i] > c[i - 1]:
                used.add((1, PLi[i]))
                _emit(out, i, 1, mult * A, None)
                continue
        j = max(range(i - 2, i + 1), key=lambda x: h[x])
        if PHi[i] >= 0 and PHi[i] < j - 1 and (-1, PHi[i]) not in used:
            r0, r1 = r[PHi[i]], r[j]
            if fin(r0) and fin(r1) and h[j] >= PH[i] - t and r0 > 70 > r1 and r1 < r0 \
                    and c[i] < o[i] and c[i] < c[i - 1]:
                used.add((-1, PHi[i]))
                _emit(out, i, -1, mult * A, None)
    return out


def rsi_div_exit(index, bars, ctx, params, side):
    """#18: hold until RSI reaches the opposite band."""
    v = F.rsi(bars, ctx, 14)[index]
    return (side == 1 and v >= 70) or (side == -1 and v <= 30)


def ma_ribbon_pullback(bars, ctx, p):
    """#23 (2026-09-04). Smoothed 21/50/200 MAs fanned out in order (21 below
    50 below 200 = downtrend); a candle rejects off the 21 -- its high
    reaches the 21 and it closes back below it, bearish -> short (mirror
    up). `fast` is his 21 (13 = a quicker ribbon). Stop beyond the 50 MA
    (`ma50`; floored) or `atr_1.5`; 1:2. "When
    the averages intertwine, walk away" = the fan condition."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m21, m50, m200 = _smma(bars, ctx, p["fast"]), _smma(bars, ctx, 50), _smma(bars, ctx, 200)
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0 and fin(m200[i])) or not _ok(b, i, p):
            continue
        if m21[i] < m50[i] < m200[i] and h[i] >= m21[i] and c[i] < m21[i] and c[i] < o[i]:
            side = -1
        elif m21[i] > m50[i] > m200[i] and l[i] <= m21[i] and c[i] > m21[i] and c[i] > o[i]:
            side = 1
        else:
            continue
        if p["stop"] == "ma50":
            dist = F.stop_dist(side, c[i], m50[i], A)
        else:
            dist = float(p["stop"][4:]) * A
        _emit(out, i, side, dist, None)
    return out


def macd_sr_retest(bars, ctx, p):
    """#39 (2026-08-07). Above the smoothed 200 MA (trend up), price breaks a
    `k`-bar swing high; it comes back to retest that level as support (low
    within 0.25 ATR of it, close above) while the MACD histogram is red but
    shrinking (losing bearish momentum), on a bullish candle -> long. Stop
    under the retest candle (floored); 1:2. Mirror below the 200."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    st = F.structure(bars, ctx, k)
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, k)
    PH, PL = pv["h"].tolist(), pv["l"].tolist()
    m200 = _smma(bars, ctx, 200)
    hist = F.macd(bars, ctx)["hist"].tolist()
    day = b["L"]["day"]
    out = {}
    lvl = None                                 # (side, level, born)
    for i in range(1, len(c)):
        A = a[i]
        if up[i] and fin(PH[i - 1]):
            lvl = (1, PH[i - 1], i)
            continue
        if dn[i] and fin(PL[i - 1]):
            lvl = (-1, PL[i - 1], i)
            continue
        if lvl is None or not (A > 0) or not fin(m200[i]) or not fin(hist[i - 1]):
            continue
        side, level, born = lvl
        if i - born > 30 or day[i] != day[born]:
            lvl = None
            continue
        if side * (c[i] - m200[i]) <= 0:
            continue
        if side == 1:
            hit = l[i] <= level + 0.25 * A and c[i] > level and c[i] > o[i]
            mom = hist[i] < 0 and hist[i] > hist[i - 1]
        else:
            hit = h[i] >= level - 0.25 * A and c[i] < level and c[i] < o[i]
            mom = hist[i] > 0 and hist[i] < hist[i - 1]
        if p["macd"] == "off":
            mom = True
        if hit and mom:
            lvl = None
            if _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], l[i] if side == 1 else h[i], A), None)
    return out


def three_line_strike(bars, ctx, p):
    """#38 (2026-08-08), his example of a rule set: below the 21 MA, a three
    line strike (three candles of one colour, then a fourth of the other
    colour that closes beyond the first one's open), RSI(14) under 50 ->
    short, stop above the swing (the four candles' high), 1:2. Mirror long.
    `run` = the number of same-colour candles (3 is the textbook pattern).
    `filter` "none" trades the bare pattern."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m21 = _smma(bars, ctx, 21)
    r = F.rsi(bars, ctx, 14).tolist()
    out = {}
    for i in range(4, len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        n = p["run"]
        ups = all(c[j] > o[j] for j in range(i - n, i))
        dns = all(c[j] < o[j] for j in range(i - n, i))
        if ups and c[i] < o[i] and c[i] < o[i - n]:
            side = -1
        elif dns and c[i] > o[i] and c[i] > o[i - n]:
            side = 1
        else:
            continue
        if p["filter"] == "ma_rsi":
            if not (fin(m21[i]) and fin(r[i])) or side * (c[i] - m21[i]) <= 0 or side * (r[i] - 50) <= 0:
                continue
        ext = min(l[i - n:i + 1]) if side == 1 else max(h[i - n:i + 1])
        _emit(out, i, side, F.stop_dist(side, c[i], ext, A), None)
    return out


def session_hour_break(bars, ctx, p):
    """#48, #61 (2025-05-17). "Look at the market open and a breakout of the
    first hour of the session" -- London's (03:00-04:00 New York) or New
    York's (09:30-10:30). The first close beyond that hour's range (by `buf`
    ATR) -> enter;
    stop the other side (`far`) or the middle (`mid`). His scalping rule (the
    next 3-4 candles must go your way or you are out) has no engine form;
    `rr_1` / `rr_2` / close."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, p["session"])
    hi, lo = w["hi"].tolist(), w["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = done = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, done = day[i], False
        if done or not fin(hi[i]) or not (a[i] > 0):
            continue
        buf = p["buf"] * a[i]
        side = 1 if c[i] > hi[i] + buf else (-1 if c[i] < lo[i] - buf else 0)
        if not side:
            continue
        done = True
        if not _ok(b, i, p):
            continue
        ref = (lo[i] if side == 1 else hi[i]) if p["stop"] == "far" else (hi[i] + lo[i]) / 2
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def htf_level_rejection(bars, ctx, p):
    """#49, #56, #62 (2025-04..2026-06). Mark higher-timeframe liquidity --
    `level` "pd": the previous session's high/low; "week": the high/low of
    the last 5 sessions -- then on the low timeframe wait for price to tap it
    and reject: the candle trades through, closes back inside, with a wick
    at least `wick` x its body -> fade it. Stop beyond the wick (floored); 1:2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    if p["level"] == "pd":
        top, bot = d["pdh"].tolist(), d["pdl"].tolist()
    else:
        dh, dl = d["d_hi"], d["d_lo"]
        n = len(dh)
        wh = numpy.full(n, NAN)
        wl = numpy.full(n, NAN)
        for j in range(5, n):
            wh[j] = dh[j - 5:j].max()
            wl[j] = dl[j - 5:j].min()
        idx = d["day_idx"]
        top, bot = wh[idx].tolist(), wl[idx].tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    fired = set()
    for i in range(len(c)):
        if day[i] != cur:
            cur, fired = day[i], set()
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        body = abs(c[i] - o[i])
        need = p["wick"] * body
        if fin(top[i]) and -1 not in fired and h[i] > top[i] > c[i] and (h[i] - max(o[i], c[i])) >= need:
            fired.add(-1)
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
        elif fin(bot[i]) and 1 not in fired and l[i] < bot[i] < c[i] and (min(o[i], c[i]) - l[i]) >= need:
            fired.add(1)
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
    return out


# =========================================================================== #
# 2026-05 .. 2025-11 (the 69 newest posts, read 2026-10-04)
# =========================================================================== #

def ema_rsi3_signal(bars, ctx, p):
    """2026-04-26 "Highest win rate strategy I have ever tested" (DE40 5m):
    his signal indicator set to EMA 100 / EMA 200, RSI(3) 80/20, ATR(10) x3.
    Read as: EMA100 above EMA200 and RSI(3) crossing back up through 20 ->
    long (mirror short). Stop at the last confirmed `k`-bar swing, or the
    100 EMA if that swing is closer than 0.5 ATR; capped at 3 x ATR. His
    target 1:4 (break-even at 1R is not modelled)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    e1, e2 = F.ema(bars, ctx, 100), F.ema(bars, ctx, 200)
    r = F.rsi(bars, ctx, 3)
    pv = F.pivots(bars, ctx, p["k"])
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(e2[i]) and fin(r[i - 1])) or not _ok(b, i, p):
            continue
        if e1[i] > e2[i] and r[i - 1] < 20 <= r[i]:
            lvl = pv["l"][i]
            if not fin(lvl) or c[i] - lvl < 0.5 * A:
                lvl = e1[i]
            _emit(out, i, 1, min(F.stop_dist(1, c[i], lvl, A), 3 * A), None)
        elif e1[i] < e2[i] and r[i - 1] > 80 >= r[i]:
            lvl = pv["h"][i]
            if not fin(lvl) or lvl - c[i] < 0.5 * A:
                lvl = e1[i]
            _emit(out, i, -1, min(F.stop_dist(-1, c[i], lvl, A), 3 * A), None)
    return out


def ma200_break_retest(bars, ctx, p):
    """2025-12-13 "5 minute scalping strategy improved" and 2026-05-13
    "Break and retest": price closes through the 200 SMA, comes back to test
    it within `win` bars and is rejected (closes back on the broken side) ->
    trade the break. `htf` 3: only when the higher timeframe agrees (close
    on the same side of the 3x-longer SMA, his 15m-vs-5m check); 0 = off.
    Stop beyond the retest candle (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    cc = F.base(bars, ctx)["c"]
    s = F.cached(ctx, ("ma_sma", 200), lambda: F.sma(cc, 200).tolist())
    sh = (F.cached(ctx, ("ma_sma", 600), lambda: F.sma(cc, 600).tolist())
          if p["htf"] else None)
    brk = None                       # (side, bar of the break)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(s[i]) and fin(s[i - 1])):
            continue
        if c[i - 1] >= s[i - 1] > c[i]:
            brk = (-1, i)
            continue
        if c[i - 1] <= s[i - 1] < c[i]:
            brk = (1, i)
            continue
        if brk is None or i - brk[1] > p["win"] or not _ok(b, i, p):
            continue
        side = brk[0]
        if sh is not None and not (fin(sh[i]) and side * (c[i] - sh[i]) > 0):
            continue
        if side == -1 and h[i] >= s[i] > c[i] and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
            brk = None
        elif side == 1 and l[i] <= s[i] < c[i] and c[i] > o[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
            brk = None
    return out


RR = ("rr_1", "rr_2", "rr_3")

SPECS = {
    "ma_tokyo_breakout": (tokyo_breakout, lambda g: {
        "stop_frac": (0.5, 1.0), "skip_big": ("off", "on"),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "ma_fib_gold_zone": (fib_gold_zone, lambda g: {
        "k": (3, 5), "confirm": ("candle", "hidden_div"), "ema": (0, 200),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "ma_rsi_div_secret": (rsi_div_secret, lambda g: {
        "k": (3, 5), "tol": (0.1, 0.3), "stop": ("atr_1", "atr_1.5"),
        "exit_mode": ("rr_2", "rr_1", "signal"),
        "last_entry_minute": g["last"]}, False, rsi_div_exit),
    "ma_ribbon_pullback": (ma_ribbon_pullback, lambda g: {
        "fast": (13, 21), "stop": ("ma50", "atr_1.5"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "ma_macd_sr_retest": (macd_sr_retest, lambda g: {
        "k": (3, 5), "macd": ("on", "off"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "ma_three_line_strike": (three_line_strike, lambda g: {
        "run": (2, 3), "filter": ("ma_rsi", "none"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "ma_session_hour_break": (session_hour_break, lambda g: {
        "session": ("london_h1", "ny_h1"), "stop": ("far", "mid"), "buf": (0.0, 0.1),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
    "ma_htf_level_rejection": (htf_level_rejection, lambda g: {
        "level": ("pd", "week"), "wick": (1.0, 2.0),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "ma_ema_rsi3_signal": (ema_rsi3_signal, lambda g: {
        "k": (3, 5), "exit_mode": ("rr_4", "rr_2", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "ma_200_break_retest": (ma200_break_retest, lambda g: {
        "win": (6, 12), "htf": (0, 3), "exit_mode": RR,
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("skip_big", "confirm", "stop", "macd", "filter", "session", "level")
