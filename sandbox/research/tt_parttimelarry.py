"""Part Time Larry's YouTube (@parttimelarry, "Hacking the Markets")
strategies, as `cfd_tt_families` families.

Source: 168 videos listed 2026-10-03; nearly all are API / app-building
tutorials. The 14 that implement a trading rule were read from the English
captions. Map and SKIP list: `results/tiktok/parttimelarry_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _base(bars, ctx):
    return F.base(bars, ctx)


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _ok(b, i, p):
    return b["L"]["minute"][i] <= p.get("last_entry_minute", 10 ** 6)


def orb_candle(bars, ctx, p):
    """#15, #17 (2025), coding Concretum's "Can day trading be profitable?":
    the session's first `or_min`-minute candle closes up -> long at the next
    bar, down -> short (a doji: no trade); stop at the opening range's other
    side; target 10R in the paper (`rr_10`) or the close. One trade a day."""
    b = _base(bars, ctx)
    c, o = b["L"]["c"], b["L"]["o"]
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl, done = r["hi"].tolist(), r["lo"].tolist(), r["done"].tolist()
    day = b["L"]["day"]
    dop = F.days(bars, ctx)["open"].tolist()
    out = {}
    cur = None
    for i in range(1, len(c)):
        if day[i] != cur:
            cur, fired = day[i], False
        if fired or not done[i] or not (a[i] > 0):
            continue
        fired = True
        last = c[i - 1]                         # the opening range's last close
        side = 1 if last > dop[i] else (-1 if last < dop[i] else 0)
        if not side or not _ok(b, i, p):
            continue
        ref = Rl[i] if side == 1 else Rh[i]
        out[i] = (side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def orb_range_target(bars, ctx, p):
    """#89, #100, #94, #95 (2020): the first 15 minutes' range; the first
    close above it -> long (below -> short, his "breakdown" script); stop at
    the range's other side... his scripts used a stop and a target ONE RANGE
    away from the entry (1:1); `mult` widens the target to 1.5 / 2 ranges."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, fired = day[i], False
        if fired or not fin(Rh[i]) or not (a[i] > 0):
            continue
        side = 1 if c[i] > Rh[i] else (-1 if c[i] < Rl[i] else 0)
        if not side:
            continue
        fired = True
        if not _ok(b, i, p):
            continue
        w = max(Rh[i] - Rl[i], 0.2 * a[i])
        out[i] = (side, w, {"far": p["mult"] * w})
    return out


def gap_down_short(bars, ctx, p):
    """#51, #53 (2021, after QuantRocket): the session opens `lo`..`hi`%
    below yesterday's close AND below its 20-day average -> short the open;
    bracket: target and stop `stop` ATR (his percent bracket), flat by the
    close."""
    b = _base(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdc = d["pdc"].tolist()
    dc = d["d_close"]
    ma20 = F.sma(dc, 20)
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    op = b["L"]["o"]
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(first)):
        j = idx[i] - 1
        if not first[i] or j < 20 or not (a[i] > 0) or not fin(pdc[i]):
            continue
        gap = 100 * (op[i] / pdc[i] - 1)
        if -p["hi"] <= gap <= -p["lo"] and op[i] < ma20[j] and _ok(b, i, p):
            out[i] = (-1, mult * a[i], None)
    return out


def bb_reentry(bars, ctx, p):
    """#93 (2020): the previous candle closed below the lower Bollinger Band
    (20, 2) and this one closes back above it (green) -> long; stop under the
    red candle's low; target 3x this candle's range (his "take profit kind of
    early"). Mirror short at the upper band."""
    b = _base(bars, ctx)
    c, o, h, l = b["L"]["c"], b["L"]["o"], b["L"]["h"], b["L"]["l"]
    a = _atr(bars, ctx)
    cc = b["c"]
    from numpy.lib.stride_tricks import sliding_window_view
    sd = numpy.full(len(cc), NAN)
    if len(cc) >= 20:
        sd[19:] = sliding_window_view(cc, 20).std(axis=1)
    mid = F.sma(cc, 20)
    up, lo = (mid + p["dev"] * sd).tolist(), (mid - p["dev"] * sd).tolist()
    out = {}
    for i in range(1, len(c)):
        A = a[i]
        if not (A > 0 and fin(lo[i - 1])) or not _ok(b, i, p):
            continue
        rng = max(h[i] - l[i], 0.1 * A)
        if c[i - 1] < lo[i - 1] and c[i] > lo[i] and c[i] > o[i]:
            out[i] = (1, F.stop_dist(1, c[i], min(l[i - 1], l[i]), A), {"far": 3 * rng})
        elif c[i - 1] > up[i - 1] and c[i] < up[i] and c[i] < o[i]:
            out[i] = (-1, F.stop_dist(-1, c[i], max(h[i - 1], h[i]), A), {"far": 3 * rng})
    return out


def ttm_squeeze(bars, ctx, p):
    """#113 (2020). TTM Squeeze: Bollinger(20, 2) inside Keltner(20, `kc`)
    = squeeze on; the first bar it turns off with the squeeze momentum
    positive and rising -> long ("ride that momentum"); negative and falling
    -> short. Exit as momentum fades (rr / close). Stop `stop` ATR."""
    b = _base(bars, ctx)
    a = _atr(bars, ctx)
    sq = F.squeeze(bars, ctx, 20, 2.0, p["kc"])
    on, val = sq["on"].tolist(), sq["val"].tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(2, len(on)):
        if not (on[i - 1] and not on[i]) or not fin(val[i]) or not fin(val[i - 1]):
            continue
        if not (a[i] > 0) or not _ok(b, i, p):
            continue
        if val[i] > 0 and val[i] > val[i - 1]:
            out[i] = (1, mult * a[i], None)
        elif val[i] < 0 and val[i] < val[i - 1]:
            out[i] = (-1, mult * a[i], None)
    return out


def consolidation_breakout(bars, ctx, p):
    """#114 (2020): a stock "consolidating" -- the last `n` bars' closes all
    within `pct`% of each other (his 15 days within 2%) -- then a close above
    that range -> long ("poised to break out"); stop at the range's low.
    Mirror short below."""
    b = _base(bars, ctx)
    c = b["L"]["c"]
    a = _atr(bars, ctx)
    n = p["n"]
    cc = b["c"]
    hh = numpy.concatenate(([NAN], F.rolling_max(cc, n)[:-1])).tolist()
    ll = numpy.concatenate(([NAN], F.rolling_min(cc, n)[:-1])).tolist()
    out = {}
    for i in range(len(c)):
        A = a[i]
        if not (fin(hh[i]) and A > 0) or not _ok(b, i, p):
            continue
        if (hh[i] - ll[i]) / ll[i] * 100 > p["pct"]:
            continue
        if c[i] > hh[i]:
            out[i] = (1, F.stop_dist(1, c[i], ll[i], A), None)
        elif c[i] < ll[i]:
            out[i] = (-1, F.stop_dist(-1, c[i], hh[i], A), None)
    return out


SPECS = {
    "ptl_orb_candle": (orb_candle, lambda g: {
        "or_min": (5, 15), "exit_mode": ("rr_10", "days_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "ptl_orb_range_target": (orb_range_target, lambda g: {
        "or_min": (15, 30), "mult": (1.0, 1.5, 2.0),
        "exit_mode": ("level", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "ptl_gap_down_short": (gap_down_short, lambda g: {
        "lo": (0.5, 1.0), "hi": (1.5, 3.0), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_1", "days_1"), "last_entry_minute": g["last"]}, False, None),
    "ptl_bb_reentry": (bb_reentry, lambda g: {
        "dev": (2.0, 2.5), "exit_mode": ("level", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "ptl_ttm_squeeze": (ttm_squeeze, lambda g: {
        "kc": (1.5, 2.0), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("rr_1", "rr_2", "trail_0.25"), "last_entry_minute": g["last"]}, False, None),
    "ptl_consolidation_breakout": (consolidation_breakout, lambda g: {
        "n": (15, 30), "pct": (0.5, 1.0, 2.0),
        "exit_mode": ("rr_1", "rr_2", "days_1"), "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop",)
