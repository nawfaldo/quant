"""The CrypTeller's TikTok (@thecrypteller) strategies, as `cfd_tt_families`
families.

Source: 138 posts on the grid (SMC/ICT concept explainers: FVG, IFVG, OB,
breaker, BPR, inducement, CRT, ORB), screened by caption 2026-10-04; 10
step-by-step posts read. Most restate setups other creators' families
already cover (ORB retest, CRT = `lux_` CRT, sweep -> BOS -> FVG). Two are
new. Map: `results/tiktok/thecrypteller_strategy_notes.md`.
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


def three_candle_exhaustion(bars, ctx, p):
    """"Easy 3-candle setup that prints daily": price pushes up on
    SHRINKING candle bodies (`run` up-closes, each body smaller than the
    one before); a bearish candle closes (confirmation); the next candle
    prints a top wick and flips bearish -> short at its close. Stop above
    the wick (floored); his "fast 300 pip target" -> fixed R exits. Mirror
    for longs."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["run"]
    out = {}
    for i in range(k + 2, len(c)):
        A = a[i]
        if not (A > 0) or not _ok(b, i, p):
            continue
        j = i - 1                                    # the confirmation candle
        seq = range(j - k, j)                        # the exhausting push
        bodies = [c[x] - o[x] for x in seq]
        up = all(v > 0 for v in bodies) and all(abs(bodies[t + 1]) < abs(bodies[t]) for t in range(k - 1))
        dn = all(v < 0 for v in bodies) and all(abs(bodies[t + 1]) < abs(bodies[t]) for t in range(k - 1))
        if up and c[j] < o[j] and c[i] < o[i] and h[i] - max(o[i], c[i]) > 0:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A))
        elif dn and c[j] > o[j] and c[i] > o[i] and min(o[i], c[i]) - l[i] > 0:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A))
    return out


def two_hour_10am(bars, ctx, p):
    """"Only have time for two candles? Use this 2H setup": the 10:00-12:00
    New York candle with a LONG BODY (body >= `body` x its range) sets the
    bias; the next 2-hour block (12:00-14:00) must respect it with a
    rejection wick (for a bullish bias: it trades below its open and closes
    back in the upper half). Then the first bar after 14:00 that sweeps
    below the 12:00 block's low and closes back above it (his "5m bullish
    FVG after the low is swept") -> long; stop below the sweep; `level` =
    the 10:00 candle's high. Mirror."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    real, day = b["L"]["real"], b["L"]["day"]
    st = {}
    out = {}
    for i in range(len(c)):
        d, m = day[i], real[i]
        s = st.setdefault(d, {"a": [NAN, -NAN, NAN, NAN], "b": [NAN, -NAN, NAN, NAN], "done": False})
        # running OHLC of the two 2h blocks: [open, high, low, close]
        for key, lo_m, hi_m in (("a", 600, 720), ("b", 720, 840)):
            if lo_m <= m < hi_m:
                blk = s[key]
                if not fin(blk[0]):
                    blk[0], blk[1], blk[2] = o[i], h[i], l[i]
                blk[1] = max(blk[1], h[i]) if fin(blk[1]) else h[i]
                blk[2] = min(blk[2], l[i]) if fin(blk[2]) else l[i]
                blk[3] = c[i]
        A = a[i]
        if s["done"] or m < 840 or not (A > 0) or not _ok(b, i, p):
            continue
        ao, ah, al, ac = s["a"]
        bo, bh, bl, bc = s["b"]
        if not all(fin(x) for x in (ao, ah, al, ac, bo, bh, bl, bc)) or ah <= al:
            s["done"] = True
            continue
        if abs(ac - ao) < p["body"] * (ah - al):
            s["done"] = True
            continue
        side = 1 if ac > ao else -1
        mid_b = (bh + bl) / 2.0
        respected = (bl < bo and bc > mid_b) if side == 1 else (bh > bo and bc < mid_b)
        if not respected:
            s["done"] = True
            continue
        if side == 1 and l[i] < bl and c[i] > bl:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], A), {"far": F.tgt(1, c[i], ah)})
            s["done"] = True
        elif side == -1 and h[i] > bh and c[i] < bh:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], A), {"far": F.tgt(-1, c[i], al)})
            s["done"] = True
    return out


SPECS = {
    "ctel_three_candle_exhaustion": (three_candle_exhaustion, lambda g: {
        "run": (3, 4), "exit_mode": ("rr_1", "rr_2", "time_6"),
        "last_entry_minute": g["last"]}, False, None),
    "ctel_two_hour_10am": (two_hour_10am, lambda g: {
        "body": (0.5, 0.65), "exit_mode": ("level", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ()
