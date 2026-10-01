"""QuantPad's TikTok (@quantpad) strategies, as `cfd_tt_families` families.

Source: all 53 posts on the profile, read 2026-09-28 from TikTok's English
auto-captions (49 carry them). QuantPad is a research channel: nearly every
post runs a popular rule on 16 years of ES minute bars against a matched
control and reports that it does not pay. The rules they STATE are kept here
as families anyway -- their verdict is on ES at one contract, ours is on the
Exness book -- and the no-rule posts (options, insider buying, leakage, IV
surfaces, ...) are SKIP in `results/tiktok/quantpad_strategy_notes.md`.

Not built: the overnight hold (buy the close, sell the open) -- every cell
here is flattened at the session close, so it cannot be expressed; Connors RSI
(one walk-forward mention, no rule given); the 1-minute RSI-2 and MA grids are
built but run at the study's 5m+ bars.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close; structural stops are floored at 0.2 ATR.
`time_N` exits hold N bars, `days_1` holds to the close (their "out at the
close").
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


def _stop(side, price, level, atr_v, mode):
    """`atr_N` -> N ATR; anything else -> the structural `level`."""
    if mode.startswith("atr_"):
        return float(mode[4:]) * atr_v if atr_v > 0 else NAN
    return F.stop_dist(side, price, level, atr_v)


# =========================================================================== #
# 2026-09
# =========================================================================== #

def gap_fade(bars, ctx, p):
    """#30 (2026-09-10). An open away from yesterday's close by at least
    `min_gap` ATR -> fade it on the first bar's close, target yesterday's
    close (`level`) or a multiple of the stop. Their result: 59.9% fill vs
    57.5% for a fake level; only gaps over 16 ES points had any edge.
    ASSUMED: stop `stop` ATR (the video names none)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdc = d["pdc"].tolist()
    first = b["first"].tolist()
    out = {}
    for i in range(len(c)):
        if not first[i] or not fin(pdc[i]) or not (a[i] > 0) or not _ok(b, i, p):
            continue
        gap = o[i] - pdc[i]
        if abs(gap) < p["min_gap"] * a[i]:
            continue
        side = -1 if gap > 0 else 1
        far = F.tgt(side, c[i], pdc[i])
        _emit(out, i, side, float(p["stop"][4:]) * a[i], {"far": far})
    return out


def inside_day_break(bars, ctx, p):
    """#31 (2026-09-08). Yesterday traded entirely inside the day before;
    today's first close beyond yesterday's high (low) by `buf` ATR -> long
    (short). Stop at yesterday's other side (`far`) or its midpoint (`mid`).
    Their result: the next day IS bigger (+4.25 pts vs matched quiet days) but
    the direction is a coin flip; +$67k vs $335k buy-and-hold."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl, ppdh, ppdl = (d[k].tolist() for k in ("pdh", "pdl", "ppdh", "ppdl"))
    day = b["L"]["day"]
    out = {}
    cur = done = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, done = day[i], False
        if done or not fin(ppdh[i]) or not (a[i] > 0):
            continue
        if not (pdh[i] < ppdh[i] and pdl[i] > ppdl[i]):
            continue
        buf = p["buf"] * a[i]
        side = 1 if c[i] > pdh[i] + buf else (-1 if c[i] < pdl[i] - buf else 0)
        if not side:
            continue
        done = True
        if not _ok(b, i, p):
            continue
        ref = (pdl[i] if side == 1 else pdh[i]) if p["stop"] == "far" else (pdh[i] + pdl[i]) / 2
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def stop_hunt_fade(bars, ctx, p):
    """#32 (2026-09-05). Price pokes through yesterday's high by no more than
    `max_poke` ATR and the bar closes back below it -> short; stop above the
    poke. Their result: only yesterday's HIGH beat fake levels (+1.8 pts of
    percentage); the low and overnight levels were noise -- `level` "pdh"
    is their finding, "both" adds the mirror at yesterday's low.
    `exit_mode` "level" targets today's open."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl, dop = d["pdh"].tolist(), d["pdl"].tolist(), d["open"].tolist()
    out = {}
    for i in range(len(c)):
        if not fin(pdh[i]) or not (a[i] > 0) or not _ok(b, i, p):
            continue
        poke = p["max_poke"] * a[i]
        if pdh[i] < h[i] <= pdh[i] + poke and c[i] < pdh[i]:
            far = F.tgt(-1, c[i], dop[i])
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], a[i]), {"far": far})
        elif p["level"] == "both" and pdl[i] > l[i] >= pdl[i] - poke and c[i] > pdl[i]:
            far = F.tgt(1, c[i], dop[i])
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], a[i]), {"far": far})
    return out


def volume_breakout(bars, ctx, p):
    """#33 (2026-09-04). The first close above yesterday's high on a bar whose
    volume is at least `vol_mult` x its 20-bar mean -> long, held 30 minutes
    (`time_N`). `side` "both" adds the mirror at yesterday's low. Their
    result: +0.47 pt at 30 min against 1.23 for an ordinary bar. Stop 1 ATR
    (`atr_1`) or the breakout bar's low (`candle`). Tick volume on Exness."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl = d["pdh"].tolist(), d["pdl"].tolist()
    vm = F.sma(b["v"], 20)
    vm = numpy.concatenate(([NAN], vm[:-1])).tolist()
    v = b["L"]["v"]
    day = b["L"]["day"]
    out = {}
    cur = up = dn = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, up, dn = day[i], False, False
        if not fin(pdh[i]) or not (a[i] > 0):
            continue
        side = 0
        if not up and c[i] > pdh[i]:
            up, side = True, 1
        elif not dn and p["side"] == "both" and c[i] < pdl[i]:
            dn, side = True, -1
        if not side or not _ok(b, i, p):
            continue
        if not (fin(vm[i]) and v[i] >= p["vol_mult"] * vm[i]):
            continue
        ref = l[i] if side == 1 else h[i]
        _emit(out, i, side, _stop(side, c[i], ref, a[i], p["stop"]), None)
    return out


def orb_close(bars, ctx, p):
    """#29 (2026-09-10), and the 9:30-10:00 range of their sizing post. The
    first close outside the `or_min` opening range -> enter, stop at the other
    side of the range, out at the close (`days_1`). Their result: +$29k, but a
    midday range made $27.7k -- the open is special, the trade is not."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = done = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, done = day[i], False
        hi, lo = Rh[i], Rl[i]
        if done or not fin(hi):
            continue
        side = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
        if not side:
            continue
        done = True
        if not _ok(b, i, p):
            continue
        ref = (lo if side == 1 else hi) if p["stop"] == "far" else (hi + lo) / 2
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def afternoon_range_break(bars, ctx, p):
    """Early-September post ("range completion by noon"). The session's
    morning range -- open to noon on ES, here the first `split` of the
    session so it means the same on every market -- then the first close
    outside it after that -> enter, stop at the other side, out at the close.
    Their result: +$58k after costs."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    since = b["L"]["since"]
    length = (b["close_min"] - b["open_min"]) % 1440 or 1440
    cut = p["split"] * length
    day = b["L"]["day"]
    out = {}
    cur = hi = lo = None
    done = False
    for i in range(len(c)):
        if day[i] != cur:
            cur, hi, lo, done = day[i], h[i], l[i], False
            continue
        if since[i] + b["bar"] <= cut:
            hi, lo = max(hi, h[i]), min(lo, l[i])
            continue
        if done:
            continue
        side = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
        if not side:
            continue
        done = True
        if not _ok(b, i, p):
            continue
        ref = lo if side == 1 else hi
        _emit(out, i, side, _stop(side, c[i], ref, a[i], p["stop"]), None)
    return out


def momentum_top(bars, ctx, p):
    """Early-September momentum post. A 30-minute move in the top `pct`
    percent of its own history -> go with it and hold an hour (`time_N`).
    Their result: up moves continue; the top 5% held 1h netted +$949k.
    The move is measured in ATR (`c[i] - c[i-n]` over 30 minutes' bars) and
    ranked against the previous 2,000 bars' moves at each session's open, so
    the threshold never sees today. `side` "long" = their up-only finding."""
    b, o, h, l, c = _lists(bars, ctx)
    a = numpy.asarray(_atr(bars, ctx))
    n = max(1, 30 // b["bar"])
    cc = b["c"]
    mv = numpy.full(len(cc), NAN)
    mv[n:] = (cc[n:] - cc[:-n]) / numpy.where(a[n:] > 0, a[n:], NAN)
    d = F.days(bars, ctx)
    up = numpy.full(len(cc), NAN)
    dn = numpy.full(len(cc), NAN)
    for s, e in zip(d["d_start"], d["d_end"]):
        past = mv[max(0, s - 2000):s]
        past = past[numpy.isfinite(past)]
        if len(past) < 500:
            continue
        up[s:e] = numpy.percentile(past, 100 - p["pct"])
        dn[s:e] = numpy.percentile(past, p["pct"])
    mv, up, dn = mv.tolist(), up.tolist(), dn.tolist()
    a = a.tolist()
    pos = b["L"]["pos"]
    out = {}
    for i in range(len(c)):
        if pos[i] < n or not fin(mv[i]) or not fin(up[i]) or not _ok(b, i, p):
            continue
        if mv[i] >= up[i]:
            _emit(out, i, 1, float(p["stop"][4:]) * a[i], None)
        elif p["side"] == "both" and mv[i] <= dn[i]:
            _emit(out, i, -1, float(p["stop"][4:]) * a[i], None)
    return out


def third_touch(bars, ctx, p):
    """Early-September prior-day levels post. Yesterday's high (low) is
    touched -- a bar's high within `tol` ATR of it that closes back below --
    for the `touch_n`th time today -> fade it; stop beyond the level. Their
    result: the third touch holds, +3 points; late breakouts made $30k.
    `exit_mode` "level" targets today's open."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl, dop = d["pdh"].tolist(), d["pdl"].tolist(), d["open"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = th = tl = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, th, tl = day[i], 0, 0
        if not fin(pdh[i]) or not (a[i] > 0):
            continue
        tol = p["tol"] * a[i]
        if h[i] >= pdh[i] - tol and c[i] < pdh[i]:
            th += 1
            if th == p["touch_n"] and _ok(b, i, p):
                far = F.tgt(-1, c[i], dop[i])
                _emit(out, i, -1, F.stop_dist(-1, c[i], max(h[i], pdh[i]) + 0.1 * a[i], a[i]),
                      {"far": far})
        elif h[i] > pdh[i]:
            th = 10 ** 6                       # broken: no more touches today
        if l[i] <= pdl[i] + tol and c[i] > pdl[i]:
            tl += 1
            if tl == p["touch_n"] and _ok(b, i, p):
                far = F.tgt(1, c[i], dop[i])
                _emit(out, i, 1, F.stop_dist(1, c[i], min(l[i], pdl[i]) - 0.1 * a[i], a[i]),
                      {"far": far})
        elif l[i] < pdl[i]:
            tl = 10 ** 6
    return out


def rsi_extreme(bars, ctx, p):
    """Early-September RSI posts. RSI(14) crossing above `level` -> LONG
    (momentum: their RSI>70 short lost, buying RSI>80 was marginally
    positive); `side` "both" adds the mirror below 100-`level`. Stop `stop`
    ATR; held a few bars (`time_N`) or to the close (`days_1`)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, 14).tolist()
    lv = p["level"]
    out = {}
    for i in range(1, len(c)):
        if not fin(r[i - 1]) or not _ok(b, i, p):
            continue
        if r[i - 1] < lv <= r[i]:
            _emit(out, i, 1, float(p["stop"][4:]) * a[i], None)
        elif p["side"] == "both" and r[i - 1] > 100 - lv >= r[i]:
            _emit(out, i, -1, float(p["stop"][4:]) * a[i], None)
    return out


# =========================================================================== #
# 2026-08
# =========================================================================== #

def rsi2_pullback(bars, ctx, p):
    """#48 (2026-08-22). RSI(2) below `lo` with the close above its 200-bar
    SMA -> long; mirror above 100-`lo` under the SMA. Exit on a close back
    through the 5-bar SMA (`signal`, the standard RSI-2 exit) or a clock.
    Their result: +$6.66/trade gross, -$125 after costs at 1-minute bars."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, 2).tolist()
    ma = F.sma(b["c"], 200).tolist()
    out = {}
    for i in range(len(c)):
        if not fin(r[i]) or not fin(ma[i]) or not _ok(b, i, p):
            continue
        if c[i] > ma[i] and r[i] < p["lo"]:
            _emit(out, i, 1, float(p["stop"][4:]) * a[i], None)
        elif c[i] < ma[i] and r[i] > 100 - p["lo"]:
            _emit(out, i, -1, float(p["stop"][4:]) * a[i], None)
    return out


def rsi2_exit(index, bars, ctx, params, side):
    ma = F.cached(ctx, "qp_sma5", lambda: F.sma(F.base(bars, ctx)["c"], 5))
    cl = F.base(bars, ctx)["c"][index]
    return (side == 1 and cl > ma[index]) or (side == -1 and cl < ma[index])


def ma_cross(bars, ctx, p):
    """#49 (2026-08-21), #44. Fast EMA crossing the slow EMA -> go that way;
    `side` "long" is the long-only version their viewers defend. Exit on the
    opposite cross (`signal`), a multiple of the stop, or the close. Their
    result: 0 of 129 pairs profitable after costs; best long-only $112k vs
    $329k buy-and-hold. Stop `stop` ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    f = F.ema(bars, ctx, p["fast"]).tolist()
    s = F.ema(bars, ctx, p["slow"]).tolist()
    out = {}
    for i in range(1, len(c)):
        if not (fin(f[i - 1]) and fin(s[i - 1])) or not _ok(b, i, p):
            continue
        if f[i - 1] <= s[i - 1] and f[i] > s[i]:
            _emit(out, i, 1, float(p["stop"][4:]) * a[i], None)
        elif p["side"] == "both" and f[i - 1] >= s[i - 1] and f[i] < s[i]:
            _emit(out, i, -1, float(p["stop"][4:]) * a[i], None)
    return out


def ma_cross_exit(index, bars, ctx, params, side):
    f = F.ema(bars, ctx, params["fast"])
    s = F.ema(bars, ctx, params["slow"])
    return (side == 1 and f[index] < s[index]) or (side == -1 and f[index] > s[index])


def donchian(bars, ctx, p):
    """#44 (2026-08-28). A close above the previous `n`-bar high -> long,
    below the `n`-bar low -> short; exit on a close through the opposite
    `n/2`-bar channel (`signal`). Stop at the opposite `n`-bar channel,
    floored. Their walk-forward result: -$605k out of sample, and randomly
    chosen settings beat the optimised ones."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    for i in range(len(c)):
        if not fin(hh[i]) or not _ok(b, i, p):
            continue
        if c[i] > hh[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], ll[i], a[i]), None)
        elif c[i] < ll[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], hh[i], a[i]), None)
    return out


def donchian_exit(index, bars, ctx, params, side):
    m = max(2, params["n"] // 2)
    def build():
        b = F.base(bars, ctx)
        return (numpy.concatenate(([NAN], F.rolling_max(b["h"], m)[:-1])),
                numpy.concatenate(([NAN], F.rolling_min(b["l"], m)[:-1])))
    hh, ll = F.cached(ctx, ("qp_dc_exit", m), build)
    cl = F.base(bars, ctx)["c"][index]
    return (side == 1 and cl < ll[index]) or (side == -1 and cl > hh[index])


RR = ("rr_1", "rr_2", "rr_3")
CLOCK = ("time_2", "time_4", "days_1")

SPECS = {
    "qp_gap_fade": (gap_fade, lambda g: {
        "min_gap": (0.5, 1.0, 2.0), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("level", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_inside_day_break": (inside_day_break, lambda g: {
        "buf": (0.0, 0.1), "stop": ("far", "mid"),
        "exit_mode": ("rr_1", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_stop_hunt_fade": (stop_hunt_fade, lambda g: {
        "max_poke": (0.1, 0.25, 0.5), "level": ("pdh", "both"),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_volume_breakout": (volume_breakout, lambda g: {
        "vol_mult": (1.0, 1.5, 2.0), "side": ("long", "both"),
        "stop": ("atr_1", "candle"), "exit_mode": ("time_2", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_orb_close": (orb_close, lambda g: {
        "or_min": (30, 60), "stop": ("far", "mid"),
        "exit_mode": ("days_1", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_afternoon_range_break": (afternoon_range_break, lambda g: {
        "split": (0.4, 0.5, 0.6), "stop": ("far", "atr_1"),
        "exit_mode": ("days_1", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_momentum_top": (momentum_top, lambda g: {
        "pct": (5, 10), "side": ("long", "both"), "stop": ("atr_1", "atr_2"),
        "exit_mode": CLOCK, "last_entry_minute": g["last"]}, False, None),
    "qp_third_touch": (third_touch, lambda g: {
        "touch_n": (2, 3), "tol": (0.1, 0.25),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "qp_rsi_extreme": (rsi_extreme, lambda g: {
        "level": (70, 80), "side": ("long", "both"), "stop": ("atr_1", "atr_2"),
        "exit_mode": CLOCK, "last_entry_minute": g["last"]}, False, None),
    "qp_rsi2_pullback": (rsi2_pullback, lambda g: {
        "lo": (5, 10), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("signal", "time_4", "days_1"),
        "last_entry_minute": g["last"]}, False, rsi2_exit),
    "qp_ma_cross": (ma_cross, lambda g: {
        "fast": (5, 10, 20), "slow": (50, 100), "side": ("long", "both"),
        "stop": ("atr_1", "atr_2"), "exit_mode": ("signal", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, ma_cross_exit),
    "qp_donchian": (donchian, lambda g: {
        "n": (20, 55), "exit_mode": ("signal", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, donchian_exit),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "side", "level")
