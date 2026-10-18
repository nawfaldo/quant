"""JJ Simon's (@itsjjsimon) "fair price" session method, as a
`cfd_tt_families` family.

Source: the TikTok grid would not load (2026-10-04, throttled); his bio
points to YouTube, whose /videos page is prop-firm talk except "The ONE
Setup That Made Me $400,000 in 90 Days (6 & 8PM Session, Step by Step)"
(BLvsYJ4sqn8), read from its captions. Map:
`results/tiktok/itsjjsimon_strategy_notes.md`.
"""
from __future__ import annotations

import math

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite

#: session open (NY minutes) -> minutes he trades it
LENGTH = {18 * 60: 120, 20 * 60: 90}


def fair_price(bars, ctx, p):
    """"Fair price theory" on NQ futures: the session open (`open` 18:00
    Globex reopen, or 20:00 Asia open) is fair price. CONTINUATION: the
    opening candle's direction, taken at its close -- with `bias` 1 only
    when that morning's 09:30 open lies on the same side (his "higher time
    frame bias"). REVERSION: once price has moved >= `away` x ATR from the
    open, a DISPLACEMENT candle back toward it (its body larger than the
    previous body, closing beyond the previous candle's wick) -> trade back
    to fair price, at most `rev` times. He trades 2 h of the 18:00 session
    and 90 min of Asia; fixed 38 / 25 points (1.5R) -> stop `stop_atr` x ATR,
    rr_1.5 or `level` (fair price; 1.5R for
    the continuation)."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    real, day, mins = b["L"]["real"], b["L"]["day"], b["L"]["minute"]
    ny = F.clock_window(bars, ctx, "ny_h1")
    start, length = p["open"], LENGTH[p["open"]]
    last = p.get("last_entry_minute", 10 ** 6)
    st = None                      # [day, fair, moved_away, reversions]
    out = {}
    for i in range(1, len(c)):
        m = real[i]
        if not (start <= m < start + length):
            st = None if st is not None and m >= start + length else st
            continue
        A = a[i]
        if not (A > 0) or mins[i] > last:
            continue
        if st is None or st[0] != day[i]:
            if m >= start + max(5, b["bar"]):
                continue           # the open was missed (hole in the data)
            fair = o[i]
            st = [day[i], fair, False, 0]
            side = 1 if c[i] > o[i] else -1 if c[i] < o[i] else 0
            b930 = ny["op"][i]
            ok = side and (not p["bias"] or (fin(b930) and side * (b930 - fair) > 0))
            if ok:
                out[i] = (side, p["stop_atr"] * A, {"far": 1.5 * p["stop_atr"] * A})
            continue
        fair = st[1]
        if abs(c[i] - fair) >= p["away"] * A or abs(h[i] - fair) >= p["away"] * A and h[i] > fair \
                or abs(fair - l[i]) >= p["away"] * A and l[i] < fair:
            st[2] = True
        if not st[2] or st[3] >= p["rev"]:
            continue
        side = 1 if c[i] < fair else -1
        body, pbody = abs(c[i] - o[i]), abs(c[i - 1] - o[i - 1])
        disp = side * (c[i] - o[i]) > 0 and body > pbody and (c[i] > h[i - 1] if side == 1 else c[i] < l[i - 1])
        room = side * (fair - c[i])
        if disp and room >= 0.5 * p["away"] * A:
            out[i] = (side, p["stop_atr"] * A, {"far": room})
            st[2] = False          # need a fresh move away for the next one
            st[3] += 1
    return out


SPECS = {
    "jjs_fair_price": (fair_price, lambda g: {
        "open": (18 * 60, 20 * 60), "bias": (1, 0), "away": (1.0, 2.0), "rev": (1, 2),
        "stop_atr": (1.0, 1.5), "exit_mode": ("rr_1.5", "level", "time_12"),
        "last_entry_minute": g["last"]}, False, None),   # reads the true NY minute; runs on the Asian indices
}

CATEGORICAL = ()
