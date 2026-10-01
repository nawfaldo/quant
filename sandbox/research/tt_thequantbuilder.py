"""thequantbuilder's TikTok (@thequantbuilder, "Quantlab") strategies, as
`cfd_tt_families` families.

Source: all 19 posts on the profile, read 2026-09-28 from TikTok's English
auto-captions (9 carry them). Six posts sell one Nasdaq bot; the rest are
"does X work?" tests with no rule. Map: `results/tiktok/thequantbuilder_strategy_notes.md`.

Shape and conventions are `tt_luxalgo`'s: `compute(bars, ctx, p)` returns
`{bar: (side, stop_distance, target)}`, entries fill at the next bar's open,
one trade a day, flat by the close.
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
    if i not in out:
        out[i] = (side, dist, target)
    return True


def first_candle_ema(bars, ctx, p):
    """#1, #3, #4, #6, #10, #13 (2026-09-15..27). The session's first candle
    closes above the `ema_n` EMA -> long, below -> short. No fixed target: the
    stop trails (`trail_X`) or the trade is held to the close (`days_1`) --
    their bot held one over a weekend, which a session cell cannot. Initial
    stop under the first candle (`candle`, floored) or `atr_1`. Their claim
    (NQ 5m, 2019-2026): 1,448 trades, +982%, 57% win, PF 1.29, DD < 20%."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    e = F.ema(bars, ctx, p["ema_n"]).tolist()
    first = b["first"].tolist()
    out = {}
    for i in range(len(c)):
        if not first[i] or not fin(e[i]) or not _ok(b, i, p):
            continue
        side = 1 if c[i] > e[i] else (-1 if c[i] < e[i] else 0)
        if not side:
            continue
        if p["stop"] == "candle":
            dist = F.stop_dist(side, c[i], l[i] if side == 1 else h[i], a[i])
        else:
            dist = a[i]
        _emit(out, i, side, dist, None)
    return out


def red_days_long(bars, ctx, p):
    """#5 (2026-09-23). After `n_red` red sessions in a row -> long on the
    next session's first candle; `side` "both" adds the mirror short after as
    many green sessions. Their result: next day green 58.4% vs 54.9% on any
    day, not significant, and gone outside crisis periods. Stop `stop` ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    red = (d["d_close"] < d["d_open"]).astype(int)
    green = (d["d_close"] > d["d_open"]).astype(int)
    n = p["n_red"]
    k = numpy.ones(n, dtype=int)
    runs_r = numpy.convolve(red, k, "full")[:len(red)] >= n     # days j-n+1..j red
    runs_g = numpy.convolve(green, k, "full")[:len(green)] >= n
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(c)):
        j = idx[i] - 1                        # yesterday
        if not first[i] or j < n - 1 or not _ok(b, i, p):
            continue
        if runs_r[j]:
            _emit(out, i, 1, mult * a[i], None)
        elif p["side"] == "both" and runs_g[j]:
            _emit(out, i, -1, mult * a[i], None)
    return out


SPECS = {
    "tqb_first_candle_ema": (first_candle_ema, lambda g: {
        "ema_n": (9, 12, 21), "stop": ("candle", "atr_1"),
        "exit_mode": ("trail_0.25", "trail_0.5", "days_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "tqb_red_days_long": (red_days_long, lambda g: {
        "n_red": (2, 3, 4), "side": ("long", "both"), "stop": ("atr_1", "atr_2"),
        "exit_mode": ("days_1", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "side")
