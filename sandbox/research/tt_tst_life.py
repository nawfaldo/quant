"""Trading Strategy Testing's TikTok (@tst_life) strategies, as
`cfd_tt_families` families.

Source: all 31 posts on the profile, read 2026-10-03 (17 videos, 15 with
English captions; 14 photo quizzes skipped). Each video is a "buy, sell or
pass?" quiz walking through one discretionary setup; the setups are built
here with causal stand-ins. Map and SKIP list:
`results/tiktok/tst_life_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close; structural stops floored at 0.2 ATR.
"""
from __future__ import annotations

import math

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


def _hammer(o, h, l, c, i, side, ratio=2.0):
    """A hammer (side 1: long lower wick) / shooting star (side -1), or an
    engulfing candle in `side`'s direction."""
    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    if rng <= 0:
        return False
    if side == 1:
        wick = min(o[i], c[i]) - l[i]
        engulf = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]
    else:
        wick = h[i] - max(o[i], c[i])
        engulf = c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1]
    return engulf or wick >= ratio * max(body, 0.05 * rng)


def double_top_bottom(bars, ctx, p):
    """#4, #9, #10 (double bottom -> buy), #5, #12 (double top -> sell).
    Two `k`-bar swing lows within `tol` ATR of each other, the price having
    come DOWN into the first (close under the 50 EMA there); the first close
    above the swing high between them (the neckline) -> long. `div` "on"
    (#5) also needs RSI(14) at the second low above RSI at the first (a
    bullish divergence). Stop under the lower low; `level` = the neckline
    plus the pattern's height (measured move). Mirror for tops."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    PL, PLp, PLi, PLpi = (pv[x].tolist() for x in ("l", "l_prev", "l_i", "l_prev_i"))
    PH, PHp, PHi, PHpi = (pv[x].tolist() for x in ("h", "h_prev", "h_i", "h_prev_i"))
    e = F.ema(bars, ctx, 50).tolist()
    r = F.rsi(bars, ctx, 14).tolist()
    used = set()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        # double bottom
        j1, j2 = PLpi[i], PLi[i]
        if j1 >= 0 and (1, j2) not in used and abs(PL[i] - PLp[i]) <= p["tol"] * A \
                and fin(e[j1]) and c[j1] < e[j1]:
            neck = max(h[j1:j2 + 1])
            if c[i] > neck and (p["div"] == "off" or (fin(r[j1]) and fin(r[j2]) and r[j2] > r[j1])):
                used.add((1, j2))
                low = min(PL[i], PLp[i])
                if _emit(out, i, 1, F.stop_dist(1, c[i], low, A),
                         {"far": F.tgt(1, c[i], neck + (neck - low))}):
                    continue
        j1, j2 = PHpi[i], PHi[i]
        if j1 >= 0 and (-1, j2) not in used and abs(PH[i] - PHp[i]) <= p["tol"] * A \
                and fin(e[j1]) and c[j1] > e[j1]:
            neck = min(l[j1:j2 + 1])
            if c[i] < neck and (p["div"] == "off" or (fin(r[j1]) and fin(r[j2]) and r[j2] < r[j1])):
                used.add((-1, j2))
                top = max(PH[i], PHp[i])
                _emit(out, i, -1, F.stop_dist(-1, c[i], top, A),
                      {"far": F.tgt(-1, c[i], neck - (top - neck))})
    return out


def fib_golden_bounce(bars, ctx, p):
    """#6, #15. A break of structure (`k` swings) starts a leg from the swing
    low before it; price pulls back into the golden zone -- between the 0.5
    and the `fib` retracement of the leg -- and prints a hammer or bullish
    engulfing there -> long. Stop under the candle (floored) or the leg's
    start (`stop` "leg"); `level` = the leg's high. Mirror for shorts. One
    entry per leg; the leg dies when price closes back through its start."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    st = F.structure(bars, ctx, k)
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, k)
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    day = b["L"]["day"]
    out = {}
    leg = None                   # [side, start, extreme, day]
    for i in range(1, len(c)):
        A = a[i]
        if up[i] and fin(PL[i]):
            leg = [1, PL[i], h[i], day[i]]
            continue
        if dn[i] and fin(PH[i]):
            leg = [-1, PH[i], l[i], day[i]]
            continue
        if leg is None or day[i] != leg[3] or not (A > 0):
            continue
        side, start, ext = leg[:3]
        if side * (c[i] - start) <= 0:
            leg = None
            continue
        size = abs(ext - start)
        lo_r, hi_r = 0.5, p["fib"]
        if side == 1:
            zone_hi, zone_lo = ext - lo_r * size, ext - hi_r * size
            touched = l[i] <= zone_hi and l[i] >= zone_lo - 0.1 * size
        else:
            zone_lo, zone_hi = ext + lo_r * size, ext + hi_r * size
            touched = h[i] >= zone_lo and h[i] <= zone_hi + 0.1 * size
        if not touched:
            if side == 1:
                leg[2] = max(ext, h[i])
            else:
                leg[2] = min(ext, l[i])
            continue
        if not _hammer(o, h, l, c, i, side):
            continue
        leg = None
        if not _ok(b, i, p):
            continue
        ref = (l[i] if side == 1 else h[i]) if p["stop"] == "candle" else start
        _emit(out, i, side, F.stop_dist(side, c[i], ref, A), {"far": F.tgt(side, c[i], ext)})
    return out


def order_block_retest(bars, ctx, p):
    """#7, #8, #14. A break of structure (`k` swings) on a displacement
    candle (body >= `disp` ATR, within the 5 bars before the break) that
    leaves an FVG; the order block is the
    last opposite-colour candle before the push. `sweep` "on" (#7) also needs
    the push to start from a low that undercut the previous swing low (a
    liquidity sweep). The first retest of the block within 30 bars that closes
    back out of it -> enter. Stop beyond the block (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    st = F.structure(bars, ctx, k)
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, k)
    PL, PLp, PH, PHp = (pv[x].tolist() for x in ("l", "l_prev", "h", "h_prev"))
    fv = F.fvg(bars, ctx)
    bull, bear = fv["bull"].tolist(), fv["bear"].tolist()
    out = {}
    block = None              # (side, lo, hi, born)
    for i in range(3, len(c)):
        A = a[i]
        if not (A > 0):
            continue
        for side, brk, gap in ((1, up, bull), (-1, dn, bear)):
            if not brk[i]:
                continue
            push = [j for j in range(max(i - 5, 0), i + 1) if side * (c[j] - o[j]) >= p["disp"] * A]
            if not push or not any(gap[j] for j in range(push[0], min(i + 1, len(c)))):
                continue
            if p["sweep"] == "on":
                P, Pp = (PL, PLp) if side == 1 else (PH, PHp)
                if not (fin(P[i]) and fin(Pp[i]) and side * (Pp[i] - P[i]) > 0):
                    continue
            j0 = push[0]
            ob = next((j for j in range(j0 - 1, max(j0 - 8, -1), -1)
                       if side * (c[j] - o[j]) < 0), None)
            if ob is None:
                continue
            block = (side, l[ob], h[ob], i)
        if block is None or block[3] == i:
            continue
        side, blo, bhi, born = block
        if i - born > 30 or b["L"]["day"][i] != b["L"]["day"][born]:
            block = None
            continue
        if side == 1 and l[i] <= bhi and c[i] > bhi:
            block = None
            if _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], blo, A), None)
        elif side == -1 and h[i] >= blo and c[i] < blo:
            block = None
            if _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], bhi, A), None)
        elif side * (c[i] - (blo if side == 1 else bhi)) < 0:
            block = None                          # closed through: mitigated
    return out


def ema_bounce(bars, ctx, p):
    """#17, #13. Trend by the 200 EMA (close above -> longs only); the bar's
    low tags the `ma` EMA (within 0.25 ATR, closing above it) with a hammer
    or bullish engulfing -> long. `ma` 200 is #13's "bounce off the 200".
    Stop under the candle (floored). Mirror below the 200."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    trend = F.ema(bars, ctx, 200).tolist()
    m = F.ema(bars, ctx, p["ma"]).tolist()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0) or not (fin(trend[i]) and fin(m[i])) or not _ok(b, i, p):
            continue
        t = 0.25 * A
        if c[i] > trend[i] and l[i] <= m[i] + t and c[i] > m[i] and _hammer(o, h, l, c, i, 1):
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), None)
        elif c[i] < trend[i] and h[i] >= m[i] - t and c[i] < m[i] and _hammer(o, h, l, c, i, -1):
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), None)
    return out


def pattern_break(bars, ctx, p):
    """#16 (triangle), #11 (falling wedge). The last two `k`-bar swing highs
    and lows define the pattern: `pattern` "triangle" = lower highs AND
    higher lows (converging); "wedge" = lower highs and lower lows with the
    highs falling faster (falling wedge -> long only; the rising-wedge mirror
    -> short). The first close through the line joining the two swing highs
    (projected to this bar) -> long, through the lows' line -> short. `adx`
    25 needs ADX(14) above 25 (#16 passed a breakout at ADX < 25); 0 is off.
    Stop at the last opposite swing (floored)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    pv = F.pivots(bars, ctx, k)
    H1, H0, H1i, H0i = (pv[x].tolist() for x in ("h", "h_prev", "h_i", "h_prev_i"))
    L1, L0, L1i, L0i = (pv[x].tolist() for x in ("l", "l_prev", "l_i", "l_prev_i"))
    ax = F.adx(bars, ctx, 14)["adx"].tolist()
    done = set()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (A > 0) or H0i[i] < 0 or L0i[i] < 0 or not _ok(b, i, p):
            continue
        if p["adx"] and not (fin(ax[i]) and ax[i] > p["adx"]):
            continue
        key = (H1i[i], L1i[i])
        if key in done:
            continue
        sh = (H1[i] - H0[i]) / max(H1i[i] - H0i[i], 1)
        sl = (L1[i] - L0[i]) / max(L1i[i] - L0i[i], 1)
        if p["pattern"] == "triangle":
            ok_up = ok_dn = sh < 0 < sl
        else:
            ok_up = sh < 0 and sl < 0 and sh < sl          # falling wedge
            ok_dn = sh > 0 and sl > 0 and sl > sh          # rising wedge
        upper = H1[i] + sh * (i - H1i[i])
        lower = L1[i] + sl * (i - L1i[i])
        if ok_up and c[i] > upper and c[i - 1] <= H1[i] + sh * (i - 1 - H1i[i]):
            done.add(key)
            _emit(out, i, 1, F.stop_dist(1, c[i], L1[i], A), None)
        elif ok_dn and c[i] < lower and c[i - 1] >= L1[i] + sl * (i - 1 - L1i[i]):
            done.add(key)
            _emit(out, i, -1, F.stop_dist(-1, c[i], H1[i], A), None)
    return out


RR = ("rr_1", "rr_2", "rr_3")

SPECS = {
    "tst_double_top_bottom": (double_top_bottom, lambda g: {
        "k": (3, 5), "tol": (0.25, 0.5), "div": ("off", "on"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "tst_fib_golden_bounce": (fib_golden_bounce, lambda g: {
        "k": (3, 5), "fib": (0.618, 0.786), "stop": ("candle", "leg"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "tst_order_block_retest": (order_block_retest, lambda g: {
        "k": (2, 3), "disp": (0.5, 0.8), "sweep": ("off", "on"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "tst_ema_bounce": (ema_bounce, lambda g: {
        "ma": (20, 50, 200),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "tst_pattern_break": (pattern_break, lambda g: {
        "k": (3, 5), "pattern": ("triangle", "wedge"), "adx": (0, 25),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("div", "stop", "sweep", "pattern")
