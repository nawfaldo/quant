"""SAFU CRYPTO's TikTok (@safucrypto) rule, as a `cfd_tt_families` family.

Source: 208 posts on the grid (mostly Middle-East geopolitics; the trading
posts sell the paid "Safu AI" scripts), screened by caption 2026-10-04; 6
strategy posts read. The ORB scalper and "8 strategies" indicator are
paywalled; the golden-zone fib (MSS -> 0.618-0.705) is covered by the
existing fib families. One stated rule is new. Map:
`results/tiktok/safucrypto_strategy_notes.md`.
"""
from __future__ import annotations

import math

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite


def _tpo_va(h, l, bins=30, share=0.70):
    """TPO value area of a block of bars: each bar adds one count to every
    price bin its range touches (time at price, not volume)."""
    lo, hi = float(l.min()), float(h.max())
    if not hi > lo:
        return NAN, NAN
    edges = numpy.linspace(lo, hi, bins + 1)
    a = numpy.clip(numpy.searchsorted(edges, l, side="right") - 1, 0, bins - 1)
    z = numpy.clip(numpy.searchsorted(edges, h, side="left") - 1, 0, bins - 1)
    diff = numpy.zeros(bins + 1)
    numpy.add.at(diff, a, 1.0)
    numpy.add.at(diff, z + 1, -1.0)
    hist = numpy.cumsum(diff)[:bins]
    top = int(hist.argmax())
    total = hist.sum()
    s, e, acc = top, top, hist[top]
    while acc < share * total and (s > 0 or e < bins - 1):
        left = hist[s - 1] if s > 0 else -1
        right = hist[e + 1] if e < bins - 1 else -1
        if right >= left:
            e += 1
            acc += hist[e]
        else:
            s -= 1
            acc += hist[s]
    return edges[e + 1], edges[s]


def _cva(bars, ctx, overlap, max_days):
    """Per day: the composite value area (hi, lo) of the balance that ended
    yesterday -- walking back from yesterday, days are merged while each
    one's TPO value area overlaps the running composite by >= `overlap` of
    its own width; at least 2 days, else NaN."""
    def build():
        b = F.base(bars, ctx)
        d = F.days(bars, ctx)
        h, l = b["h"], b["l"]
        st, en = d["d_start"], d["d_end"]
        nd = len(st)
        va = [_tpo_va(h[s:e], l[s:e]) if e > s else (NAN, NAN) for s, e in zip(st, en)]
        chi, clo = numpy.full(nd, NAN), numpy.full(nd, NAN)
        for j in range(1, nd):
            k = j - 1
            hi, lo = va[k]
            if not fin(hi):
                continue
            first = k
            while first - 1 >= 0 and j - first < max_days:
                ph, pl = va[first - 1]
                if not fin(ph) or ph <= pl:
                    break
                ov = min(ph, hi) - max(pl, lo)
                if ov < overlap * (ph - pl):
                    break
                first -= 1
                hi, lo = max(hi, ph), min(lo, pl)
            if j - first >= 2:
                chi[j], clo[j] = _tpo_va(h[st[first]:en[j - 1]], l[st[first]:en[j - 1]])
        idx = d["day_idx"]
        return chi[idx].tolist(), clo[idx].tolist()
    return F.cached(ctx, "safu_cva_%s_%s" % (overlap, max_days), build)


def cva_retest(bars, ctx, p):
    """"My 2026 trading strategy -- TPO chart and composite value area": merge
    several days' TPO profiles where their value areas overlap >= 50% into a
    composite value area (CVA). Inside it, skip the chop. When price CLOSES
    outside the CVA and then comes back to retest the edge (a bar touching
    it that closes back outside, within `win` bars) -> trade the breakout
    direction ("A+ setup"). Stop beyond the retest bar (floored); one trade
    per side a day. Mirror."""
    b = F.base(bars, ctx)
    o, h, l, c = b["L"]["o"], b["L"]["h"], b["L"]["l"], b["L"]["c"]
    a = F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())
    chi, clo = _cva(bars, ctx, p["overlap"], 10)
    day = b["L"]["day"]
    pend = None                       # (side, edge, bar)
    done = set()
    out = {}
    for i in range(1, len(c)):
        A, hi, lo = a[i], chi[i], clo[i]
        if not (A > 0 and fin(hi)) or day[i] != day[i - 1]:
            pend = None
            continue
        if b["L"]["minute"][i] > p.get("last_entry_minute", 10 ** 6):
            continue
        if pend is not None:
            side, edge, t0 = pend
            if i - t0 > p["win"] or side * (c[i] - edge) < 0:
                pend = None
            elif (l[i] <= edge if side == 1 else h[i] >= edge) and (day[i], side) not in done:
                stop = l[i] if side == 1 else h[i]
                dist = F.stop_dist(side, c[i], stop, A)
                if dist > 0 and fin(dist):
                    out[i] = (side, dist, None)
                    done.add((day[i], side))
                pend = None
                continue
        if c[i] > hi >= c[i - 1]:
            pend = (1, hi, i)
        elif c[i] < lo <= c[i - 1]:
            pend = (-1, lo, i)
    return out


SPECS = {
    "safu_cva_retest": (cva_retest, lambda g: {
        "overlap": (0.5, 0.7), "win": (6, 12), "exit_mode": ("rr_2", "rr_1", "days_1"),
        "last_entry_minute": g["last"]}, False, None),
}

CATEGORICAL = ()
