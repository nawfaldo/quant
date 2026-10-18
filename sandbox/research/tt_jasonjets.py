"""Jason Jets' TikTok (@jasonjets) strategies, as `cfd_tt_families` families.

Source: all 26 posts on the profile, read 2026-10-03 (24 with English
captions). Mostly agency/app promos; two rules are stated well enough to
build. Map and SKIP list: `results/tiktok/jasonjets_strategy_notes.md`.

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


def tsmom(bars, ctx, p):
    """#1, #2 (2026-09-29..10-01), #11 (07-03). Time-series momentum: the
    sign of the last `n` sessions' close-to-close return -> trade that way on
    the session's first bar. `side` "both" is TSMOM; "long" is Antonacci's
    absolute momentum (long when positive, else flat). Stop `stop` ATR; held
    to the close (`days_1`) or a trail."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    dc = d["d_close"].tolist()
    idx = d["day_idx"].tolist()
    first = b["first"].tolist()
    n = p["n"]
    mult = float(p["stop"][4:])
    out = {}
    for i in range(len(c)):
        j = idx[i] - 1                       # yesterday
        if not first[i] or j - n < 0 or not _ok(b, i, p):
            continue
        r = dc[j] / dc[j - n] - 1.0
        side = 1 if r > 0 else (-1 if r < 0 else 0)
        if side == -1 and p["side"] == "long" or not side:
            continue
        _emit(out, i, side, mult * a[i], None)
    return out


def icc(bars, ctx, p):
    """#3 (2026-09-28). ICC -- indication, correction, continuation.
    Indication: a close through the last `k` swing high (break of
    structure); the impulse leg runs from the swing low before it to the
    highest high since. Correction: price pulls back to at least the `fib`
    retracement of that leg (and not below its start). Continuation: the
    first close back above the previous bar's high -> long. Stop under the
    correction's low (floored); `level` = the impulse high. Mirror for
    shorts. A new break re-arms; a close beyond the leg's start cancels."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    st = F.structure(bars, ctx, k)
    up, dn = st["up"].tolist(), st["dn"].tolist()
    pv = F.pivots(bars, ctx, k)
    PL, PH = pv["l"].tolist(), pv["h"].tolist()
    day = b["L"]["day"]
    fib = p["fib"]
    out = {}
    leg = None              # [side, start, extreme, corrected, corr_ext, day]
    for i in range(1, len(c)):
        A = a[i]
        if up[i] and fin(PL[i]):
            leg = [1, PL[i], h[i], False, NAN, day[i]]
            continue
        if dn[i] and fin(PH[i]):
            leg = [-1, PH[i], l[i], False, NAN, day[i]]
            continue
        if leg is None or day[i] != leg[5] or not (A > 0):
            leg = None if leg is not None and day[i] != leg[5] else leg
            continue
        side, start, ext, corrected, cext = leg[:5]
        if side * (c[i] - start) <= 0:          # back through the leg's start
            leg = None
            continue
        if not corrected:
            if side == 1:
                ext = leg[2] = max(ext, h[i])
                if l[i] <= ext - fib * (ext - start):
                    leg[3], leg[4] = True, l[i]
            else:
                ext = leg[2] = min(ext, l[i])
                if h[i] >= ext + fib * (start - ext):
                    leg[3], leg[4] = True, h[i]
            continue
        leg[4] = cext = min(cext, l[i]) if side == 1 else max(cext, h[i])
        if side * (c[i] - (h[i - 1] if side == 1 else l[i - 1])) > 0:
            leg = None
            if _ok(b, i, p):
                _emit(out, i, side, F.stop_dist(side, c[i], cext, A),
                      {"far": F.tgt(side, c[i], ext)})
    return out


SPECS = {
    "jj_tsmom": (tsmom, lambda g: {
        "n": (20, 60, 250), "side": ("both", "long"), "stop": ("atr_2", "atr_4"),
        "exit_mode": ("days_1", "trail_0.25"),
        "last_entry_minute": g["last"]}, False, None),
    "jj_icc": (icc, lambda g: {
        "k": (3, 5), "fib": (0.382, 0.5, 0.618),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("side", "stop")
