"""Trade with Pat's TikTok (@tradewithpat) strategies, as `cfd_tt_families`
families.

Source: 278 posts on the grid (mostly VIP-signal, trading-robot and mansion
promos), screened by caption 2026-10-04; 12 strategy posts read. Two rules
are specific enough to code; his MA bounce and 25/50/100 MA fan are
`tst_ema_bounce` / `ma_ribbon_pullback`. Map and SKIP list:
`results/tiktok/tradewithpat_strategy_notes.md`.
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


def orb_fvg_engulf(bars, ctx, p):
    """"This ORB trading strategy is EVERYTHING", "5 minute scalping strategy
    for over 10 years", "Verified ORB trading strategy": the first 15
    minutes' high/low; a 5m candle breaks out and closes beyond it WITH
    displacement (it leaves a fair value gap); wait for price to come back
    into that gap and enter on an engulfing candle in the breakout's
    direction. Stop beyond the gap's far side (floored). `ema_f` 1 adds his
    "ORB + market trend" rule: only with the 200 EMA side."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    orl = F.opening_range(bars, ctx, 15)
    fv = F.fvg(bars, ctx)
    e200 = F.ema(bars, ctx, 200)
    day = b["L"]["day"]
    st = {}                          # day -> [side, lo, hi, made]  (or "done")
    out = {}
    for i in range(2, len(c)):
        A = a[i]
        ohi, olo = orl["hi"][i], orl["lo"][i]
        if not (A > 0 and fin(ohi)):
            continue
        s = st.get(day[i])
        if s == "done":
            continue
        if s is None:
            side = 1 if (c[i] > ohi and fv["bull"][i]) else -1 if (c[i] < olo and fv["bear"][i]) else 0
            if side and (not p["ema_f"] or (fin(e200[i]) and side * (c[i] - e200[i]) > 0)):
                st[day[i]] = [side, fv["lo"][i], fv["hi"][i], i]
            elif c[i] > ohi or c[i] < olo:
                st[day[i]] = "done"          # a breakout without displacement
            continue
        side, glo, ghi, t0 = s
        if i - t0 > 12:
            st[day[i]] = "done"
            continue
        touched = l[i] <= ghi if side == 1 else h[i] >= glo
        engulf = (side * (c[i] - o[i]) > 0 and side * (o[i - 1] - c[i - 1]) > 0
                  and abs(c[i] - o[i]) >= abs(c[i - 1] - o[i - 1])
                  and side * (c[i] - o[i - 1]) >= 0)
        if touched and engulf and _ok(b, i, p):
            stop = glo if side == 1 else ghi
            _emit(out, i, side, F.stop_dist(side, c[i], stop, A))
            st[day[i]] = "done"
    return out


def asia_london_ny(bars, ctx, p):
    """"I've backtested this trading strategy for 15 years" / "If Asia ranges
    tight and London pushes hard in one direction... New York is probably
    reversing it": Asia range (19:00-03:00 NY) TIGHT (width <= `tight` x the
    median of the last 20 Asia widths); London (03:00-08:00) pushes through one side (sweeps
    the Asia low); in New York, buy (expecting the reversal to take the Asia
    high). First New York bar that closes back up in the sweep's reverse
    direction, within `win` minutes of the open. Stop beyond London's
    extreme; `level` = the far side of the Asia range."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    asia = F.clock_window(bars, ctx, "asia")
    lon = F.clock_window(bars, ctx, "london")
    real, day = b["L"]["real"], b["L"]["day"]
    widths, last_day = [], None
    done = None
    out = {}
    for i in range(len(c)):
        A = a[i]
        ah, al = asia["hi"][i], asia["lo"][i]
        if day[i] != last_day and fin(ah):
            last_day = day[i]
            med = sorted(widths)[len(widths) // 2] if len(widths) >= 10 else NAN
            widths.append(ah - al)
            widths = widths[-20:]
            tight_today = fin(med) and (ah - al) <= p["tight"] * med
        if not (A > 0) or day[i] == done or not (9 * 60 + 30 <= real[i] <= 9 * 60 + 30 + p["win"]):
            continue
        lh, ll = lon["hi"][i], lon["lo"][i]
        if not all(fin(x) for x in (ah, al, lh, ll)) or not _ok(b, i, p) or day[i] != last_day:
            continue
        if not tight_today:
            done = day[i]
            continue
        swept_low, swept_high = ll < al, lh > ah
        if swept_low and not swept_high and c[i] > o[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], ll, A), {"far": F.tgt(1, c[i], ah)})
            done = day[i]
        elif swept_high and not swept_low and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], lh, A), {"far": F.tgt(-1, c[i], al)})
            done = day[i]
    return out


SPECS = {
    "twp_orb_fvg_engulf": (orb_fvg_engulf, lambda g: {
        "ema_f": (0, 1), "exit_mode": ("rr_2", "rr_1", "rr_3"),
        "last_entry_minute": g["last"]}, True, None),
    "twp_asia_london_ny": (asia_london_ny, lambda g: {
        "tight": (0.7, 1.0), "win": (60, 120), "exit_mode": ("level", "rr_2", "days_1"),
        "last_entry_minute": g["last"]}, True, None),
}

CATEGORICAL = ()
