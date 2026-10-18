"""Chart Fanatics' TikTok (@chartfanatics) strategies, as `cfd_tt_families`
families.

Source: 521 posts on the grid (podcast clips of guest traders: order flow,
liquidity, risk), screened by caption 2026-10-04; 10 clips whose captions
state a rule read. Each family names the guest's rule; the 80/20 levels
(Okala) are `o80_capitulation_fade`, the 5m ORB is `fx_orb`. Map:
`results/tiktok/chartfanatics_strategy_notes.md`.
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


def _emit(out, i, side, dist, target=None):
    if not (dist > 0) or not fin(dist):
        return False
    if isinstance(target, dict) and any(not (v > 0) or not fin(v) for v in target.values()):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _up_days(bars, ctx):
    """Per session: the run of consecutive up closes ending at it."""
    def build():
        d = F.days(bars, ctx)
        dc = numpy.asarray(d["d_close"], dtype=float)
        run = numpy.zeros(len(dc), dtype=int)
        for j in range(1, len(dc)):
            run[j] = run[j - 1] + 1 if dc[j] > dc[j - 1] else 0
        return run.tolist()
    return F.cached(ctx, "cf_updays", build)


def runner_red_flip(bars, ctx, p):
    """"THIS 98% win rate strategy PRINTS" / "THIS daily setup signals the
    DUMP" (small-cap runners): after `days` consecutive up days, mark
    yesterday's close. Either the day runs up and then FLIPS RED (a close
    back below yesterday's close after trading above it) -> short; or it
    gaps down, bounces to yesterday's close and fails (a high at/above it,
    close back below) -> short. Stop above the day's high so far; out by the
    close or the bracket. One a day."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    run = _up_days(bars, ctx)
    idx = d["day_idx"].tolist()
    pdc, rh = d["pdc"].tolist(), d["run_h"].tolist()
    day = b["L"]["day"]
    above = {}
    done = None
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        j = idx[i] - 1
        if not (A > 0 and j >= 0 and fin(pdc[i])) or day[i] == done or run[j] < p["days"]:
            continue
        if h[i] > pdc[i]:
            above[day[i]] = True
        if not _ok(b, i, p):
            continue
        if above.get(day[i]) and c[i] < pdc[i] and c[i - 1] >= pdc[i - 1] if day[i - 1] == day[i] else False:
            _emit(out, i, -1, F.stop_dist(-1, c[i], rh[i], A))
            done = day[i]
        elif h[i] >= pdc[i] and c[i] < pdc[i] and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], rh[i], A))
            done = day[i]
    return out


def prevday_continuation(bars, ctx, p):
    """"If the previous day was trending long, the higher probability is
    continuation the next day. Don't fight the auction." A previous session
    that closed in the top `zone` of its range (and up on the day) -> long
    at the next session's first bar; the bottom `zone` and down -> short.
    Stop `stop` x ATR; out at the close."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    dh, dl, do, dc = d["d_hi"], d["d_lo"], d["d_open"], d["d_close"]
    out = {}
    for i in range(len(c)):
        if not first[i]:
            continue
        A = a[i]
        j = idx[i] - 1
        if not (A > 0 and j >= 0) or not _ok(b, i, p):
            continue
        rng = dh[j] - dl[j]
        if not (rng > 0):
            continue
        pos = (dc[j] - dl[j]) / rng
        if pos >= 1 - p["zone"] and dc[j] > do[j]:
            _emit(out, i, 1, p["stop"] * A)
        elif pos <= p["zone"] and dc[j] < do[j]:
            _emit(out, i, -1, p["stop"] * A)
    return out


def pd_sfp(bars, ctx, p):
    """"EVERY trader MUST do THIS": price runs below the previous day's low;
    do nothing until a candle CLOSES back above it (the swing failure) ->
    long; stop below the newly created low (floored); target the previous
    day's high (`level`). Mirror at the previous day's high."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl = d["pdh"].tolist(), d["pdl"].tolist()
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    day = b["L"]["day"]
    done = set()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(pdl[i])) or not _ok(b, i, p):
            continue
        if (day[i], 1) not in done and rl[i] < pdl[i] - p["depth"] * A and c[i] > pdl[i] and c[i - 1] <= pdl[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], rl[i], A), {"far": F.tgt(1, c[i], pdh[i])})
            done.add((day[i], 1))
        elif (day[i], -1) not in done and rh[i] > pdh[i] + p["depth"] * A and c[i] < pdh[i] and c[i - 1] >= pdh[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], rh[i], A), {"far": F.tgt(-1, c[i], pdl[i])})
            done.add((day[i], -1))
    return out


def consol_break_volume(bars, ctx, p):
    """"The ONLY strategy you NEED" (volume confirmation): a consolidation
    (the last `n` bars inside a range <= 1.5 x ATR); a close below its low
    on volume >= `vol` x the 20-bar average (no spike = ignore) ; then a
    retest of the broken low that is rejected (high >= the low, close back
    below) within 8 bars -> short; stop above the retest high (floored);
    2:1. Mirror for breakouts."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    v = b["L"]["v"]
    va = F.cached(ctx, "cf_vavg", lambda: numpy.concatenate(([NAN], F.sma(b["v"].astype(float), 20)[:-1])).tolist())
    n = p["n"]
    hh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    pend = None                         # (side, level, bar)
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(hh[i]) and fin(va[i])):
            continue
        if pend is not None:
            side, lvl, t0 = pend
            if i - t0 > 8:
                pend = None
            elif side == -1 and h[i] >= lvl and c[i] < lvl and _ok(b, i, p):
                _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A))
                pend = None
                continue
            elif side == 1 and l[i] <= lvl and c[i] > lvl and _ok(b, i, p):
                _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A))
                pend = None
                continue
        if hh[i] - ll[i] > 1.5 * A:
            continue
        if c[i] < ll[i] and v[i] >= p["vol"] * va[i]:
            pend = (-1, ll[i], i)
        elif c[i] > hh[i] and v[i] >= p["vol"] * va[i]:
            pend = (1, hh[i], i)
    return out


SPECS = {
    "cf_runner_red_flip": (runner_red_flip, lambda g: {
        "days": (2, 3), "exit_mode": ("days_1", "rr_2", "rr_1"),
        "last_entry_minute": g["last"]}, False, None),
    "cf_prevday_continuation": (prevday_continuation, lambda g: {
        "zone": (0.2, 0.3), "stop": (1.5, 3.0), "exit_mode": ("days_1", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "cf_pd_sfp": (pd_sfp, lambda g: {
        "depth": (0.0, 0.25), "exit_mode": ("level", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
    "cf_consol_break_volume": (consol_break_volume, lambda g: {
        "n": (6, 12), "vol": (1.5, 2.0), "exit_mode": ("rr_2", "rr_1", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
