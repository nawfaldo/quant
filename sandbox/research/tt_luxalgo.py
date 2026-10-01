"""LuxAlgo's TikTok (@luxalgo) strategies, as `cfd_tt_families` families.

Source: all 607 posts on the profile, read 2026-09-28 from TikTok's own English
auto-captions (430 posts carry them). ~75 posts state a tradeable rule; the rest
are product promotions, memes or indicator ads. Rules that repeat across videos
are ONE family here, with the variants as axes -- the notes file
`results/tiktok/luxalgo_strategy_notes.md` maps every post to its family.

Order is the profile's, newest first. Each family's docstring names the posts
(`#N` = rank on the profile, video id in the notes) and what was assumed where a
video left a rule open -- they usually do: "wait for a reaction", "target
liquidity". Those gaps are filled with the plainest reading and stated.

HOW A FAMILY WORKS. `compute(bars, ctx, p)` returns `{bar_index: (side,
stop_distance, target)}` for one setting of the signal axes; the wrapper in
`cfd_tt_families` caches it and hands the engine `event.get(index)`. `target`
is a distance, or a dict of distances keyed for the `level_*` exit modes (the
engine picks `level_mid` -> "mid", `level`/`level_far` -> "far"). An event is
only emitted when every level target it names is valid (on the trade's side),
so the firing set does not depend on the exit axis. Entries fill at the NEXT
bar's open, one trade a day, flat by the session close -- the engine's rules.

Stops are structural where the video says so, floored at 0.2 ATR(14).
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
    """Inside the cell's entry cutoff."""
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


def _as_list(x):
    return x.tolist() if hasattr(x, "tolist") else list(x)


# =========================================================================== #
# 2026-09 .. 2026-07
# =========================================================================== #

def structure_poc(bars, ctx, p):
    """#6 (2026-09-15). After `n_bos` same-direction breaks of structure, take
    the leg from the last swing low to the running high, find its volume POC,
    and buy the first retrace into the POC (continuation). Mirror for downs.
    Video stop/target: 2 ATR / 4 ATR. ASSUMED: the leg is armed at the break
    (the video arms it at the opposing CHoCH, which it never pins down)."""
    b, o, h, l, c = _lists(bars, ctx)
    s = F.structure(bars, ctx, p["k"])
    pv = F.pivots(bars, ctx, p["k"])
    a = _atr(bars, ctx)
    up, dn, run = s["up"].tolist(), s["dn"].tolist(), s["run"].tolist()
    plo, plo_i = pv["l"].tolist(), pv["l_i"].tolist()
    phi, phi_i = pv["h"].tolist(), pv["h_i"].tolist()
    v, hh, ll, cc = b["v"], b["h"], b["l"], b["c"]

    def leg(side, j, k0):
        seg = slice(j, k0 + 1)
        lo, hi = ll[seg].min(), hh[seg].max()
        if not hi > lo:
            return None
        tp = (hh[seg] + ll[seg] + cc[seg]) / 3
        hist, edges = numpy.histogram(tp, bins=20, range=(lo, hi), weights=v[seg])
        t = int(hist.argmax())
        return (side, k0, (edges[t] + edges[t + 1]) / 2, lo, hi)

    out = {}
    arm = None
    for i in range(len(c)):
        if up[i] and run[i] >= p["n_bos"] and plo_i[i] >= 0:
            arm = leg(1, plo_i[i], i)
        elif dn[i] and run[i] >= p["n_bos"] and phi_i[i] >= 0:
            arm = leg(-1, phi_i[i], i)
        elif arm is not None and ((up[i] and arm[0] == -1) or (dn[i] and arm[0] == 1)):
            arm = None
        if arm is None or i == arm[1] or not _ok(b, i, p):
            continue
        side, _k0, poc, lo, hi = arm
        if side == 1 and l[i] <= poc < c[i] and l[i] > lo:
            stop = 2 * a[i] if p["stop"] == "atr_2" else F.stop_dist(1, c[i], lo, a[i])
            if _emit(out, i, 1, stop, F.tgt(1, c[i], hi)):
                arm = None
        elif side == -1 and h[i] >= poc > c[i] and h[i] < hi:
            stop = 2 * a[i] if p["stop"] == "atr_2" else F.stop_dist(-1, c[i], hi, a[i])
            if _emit(out, i, -1, stop, F.tgt(-1, c[i], lo)):
                arm = None
    return out


def no_wick_retest(bars, ctx, p):
    """#9 (2026-09-09), #122 (2026-04-01). A candle with no upper wick leaves
    a level at its high; the first return up to it that closes back below is a
    short (mirror: no lower wick -> long). `tol` is the wick allowed, in ATR.
    `trend_f`: #122 took only with-trend levels (EMA filter); #9 took all.
    `directional`: only bearish no-top-wick / bullish no-bottom-wick candles."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    ema = (F.ema(bars, ctx, int(p["trend_f"][3:])).tolist()
           if p["trend_f"] != "none" else None)
    pv = F.pivots(bars, ctx, 3)
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    out = {}
    res, sup = [], []          # (level, born)
    for i in range(len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        # touches of existing levels first (a level cannot fire on its own bar)
        hit = None
        for lv in res:
            if h[i] >= lv[0]:
                hit = lv
                break
        if hit is not None:
            res.remove(hit)
            if (c[i] < hit[0] and _ok(b, i, p)
                    and (ema is None or c[i] < ema[i])):
                stop = ai if p["stop"] == "atr_1" else F.stop_dist(-1, c[i], max(h[i], ph[i]) if fin(ph[i]) else h[i], ai)
                _emit(out, i, -1, stop, None)
        hit = None
        for lv in sup:
            if l[i] <= lv[0]:
                hit = lv
                break
        if hit is not None:
            sup.remove(hit)
            if (c[i] > hit[0] and _ok(b, i, p)
                    and (ema is None or c[i] > ema[i])):
                stop = ai if p["stop"] == "atr_1" else F.stop_dist(1, c[i], min(l[i], pl[i]) if fin(pl[i]) else l[i], ai)
                _emit(out, i, 1, stop, None)
        age = p["max_age"]
        res = [lv for lv in res if i - lv[1] < age]
        sup = [lv for lv in sup if i - lv[1] < age]
        top, bot = max(o[i], c[i]), min(o[i], c[i])
        if h[i] - top <= p["tol"] * ai and (not p["directional"] or c[i] < o[i]):
            res.append((h[i], i))
        if bot - l[i] <= p["tol"] * ai and (not p["directional"] or c[i] > o[i]):
            sup.append((l[i], i))
    return out


def session_sweep_bos(bars, ctx, p):
    """#13 (2026-08-27), #25, #33, #108, #142, #154, #374. A pre-session range
    (Asia, Sydney, London, or the whole overnight) is swept, then structure
    breaks back the other way within `max_bars` -> trade toward the range.
    Stop beyond the sweep extreme; `level_mid` = range midline (#13),
    `level_far` = the opposite side (#108, #154). #33 found the structure
    shift itself better than waiting for an OTE retrace, which is what this is."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    if p["window"] == "overnight":
        d = F.days(bars, ctx)
        R_hi, R_lo = d["on_h"].tolist(), d["on_l"].tolist()
    else:
        w = F.clock_window(bars, ctx, p["window"])
        R_hi, R_lo = w["hi"].tolist(), w["lo"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    hs = ls = None                   # (bar, extreme) of the day's sweeps
    for i in range(len(c)):
        if day[i] != cur:
            cur, hs, ls = day[i], None, None
        hi, lo = R_hi[i], R_lo[i]
        if not (fin(hi) and fin(lo) and hi > lo):
            continue
        if h[i] > hi:
            hs = (i, h[i]) if hs is None else (hs[0], max(hs[1], h[i]))
        if l[i] < lo:
            ls = (i, l[i]) if ls is None else (ls[0], min(ls[1], l[i]))
        if not _ok(b, i, p):
            continue
        mid = (hi + lo) / 2
        if hs and i - hs[0] <= p["max_bars"] and fin(pl[i]) and c[i] < pl[i] and c[i] < hi:
            _emit(out, i, -1, F.stop_dist(-1, c[i], hs[1], a[i]),
                  {"mid": F.tgt(-1, c[i], mid), "far": F.tgt(-1, c[i], lo)})
        if ls and i - ls[0] <= p["max_bars"] and fin(ph[i]) and c[i] > ph[i] and c[i] > lo:
            _emit(out, i, 1, F.stop_dist(1, c[i], ls[1], a[i]),
                  {"mid": F.tgt(1, c[i], mid), "far": F.tgt(1, c[i], hi)})
    return out


def htf_stoch_bucket(bars, ctx, p):
    """#14 (2026-08-21). A higher-timeframe stochastic in its overbought zone
    (the HTF approximated as `htf_m` bars: a 14*m-bar stochastic smoothed over
    3*m) and an LTF RSI turning down out of 70 inside it -> short; mirror at
    oversold. Stop 1 ATR or the last swing."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    m = p["htf_m"]
    st = F.stochastic(bars, ctx, 14 * m, 3 * m).tolist()
    r = F.rsi(bars, ctx, p["rsi_n"]).tolist()
    pv = F.pivots(bars, ctx, 3)
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    z = p["zone"]
    out = {}
    for i in range(1, len(c)):
        if not _ok(b, i, p) or not fin(st[i]):
            continue
        if st[i] >= z and r[i - 1] >= 70 > r[i]:
            stop = a[i] if p["stop"] == "atr_1" else F.stop_dist(-1, c[i], ph[i], a[i])
            _emit(out, i, -1, stop, None)
        elif st[i] <= 100 - z and r[i - 1] <= 30 < r[i]:
            stop = a[i] if p["stop"] == "atr_1" else F.stop_dist(1, c[i], pl[i], a[i])
            _emit(out, i, 1, stop, None)
    return out


def value_area_reversion(bars, ctx, p):
    """#15 (2026-08-19; Fabio Valentini's idea), #139. Previous day's value
    area. A close outside VAL, then a bullish engulfing back inside it (on
    declining volume into the low, if `vol_decline`) -> long; stop under the
    excursion low; `level_mid` = previous POC, `level_far` = VAH. Mirror."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    prof = F.day_profile(bars, ctx)
    val = F.prev_day(bars, ctx, prof["val"]).tolist()
    vah = F.prev_day(bars, ctx, prof["vah"]).tolist()
    poc = F.prev_day(bars, ctx, prof["poc"]).tolist()
    v = b["L"]["v"]
    day = b["L"]["day"]
    out = {}
    below = above = None
    cur = None
    for i in range(2, len(c)):
        if day[i] != cur:
            cur, below, above = day[i], None, None
        if not fin(val[i]):
            continue
        if c[i] < val[i]:
            below = l[i] if below is None else min(below, l[i])
        if c[i] > vah[i]:
            above = h[i] if above is None else max(above, h[i])
        if not _ok(b, i, p):
            continue
        top_prev = h[i - 1] if p["engulf"] == "range" else max(o[i - 1], c[i - 1])
        bot_prev = l[i - 1] if p["engulf"] == "range" else min(o[i - 1], c[i - 1])
        vol_ok = (not p["vol_decline"]) or v[i - 1] < v[i - 2]
        if (below is not None and c[i - 1] < o[i - 1] and c[i] > o[i]
                and c[i] > top_prev and c[i] > val[i] and vol_ok):
            if _emit(out, i, 1, F.stop_dist(1, c[i], below, a[i]),
                     {"mid": F.tgt(1, c[i], poc[i]), "far": F.tgt(1, c[i], vah[i])}):
                below = None
        if (above is not None and c[i - 1] > o[i - 1] and c[i] < o[i]
                and c[i] < bot_prev and c[i] < vah[i] and vol_ok):
            if _emit(out, i, -1, F.stop_dist(-1, c[i], above, a[i]),
                     {"mid": F.tgt(-1, c[i], poc[i]), "far": F.tgt(-1, c[i], val[i])}):
                above = None
    return out


def eight_am_range(bars, ctx, p):
    """#16 (2026-08-17), #43 (2026-06-24, 446k views), #151. The 08:00-09:00
    New York candle. After it closes, the FIRST side traded through is faded
    toward the other side once confirmed: `close_in` = a close back inside,
    `bos` = a close through the last `k` swing. Stop beyond the sweep."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "h8")
    R_hi, R_lo = w["hi"].tolist(), w["lo"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = first = ext = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, first, ext = day[i], None, None
        hi, lo = R_hi[i], R_lo[i]
        if not (fin(hi) and fin(lo) and hi > lo):
            continue
        if first is None:
            if h[i] > hi:
                first, ext = -1, h[i]
            elif l[i] < lo:
                first, ext = 1, l[i]
        elif first == -1:
            ext = max(ext, h[i])
        else:
            ext = min(ext, l[i])
        if first is None or not _ok(b, i, p):
            continue
        mid = (hi + lo) / 2
        if first == -1:
            ok = c[i] < hi if p["confirm"] == "close_in" else (fin(pl[i]) and c[i] < pl[i] and c[i] < hi)
            if ok:
                _emit(out, i, -1, F.stop_dist(-1, c[i], ext, a[i]),
                      {"mid": F.tgt(-1, c[i], mid), "far": F.tgt(-1, c[i], lo)})
        else:
            ok = c[i] > lo if p["confirm"] == "close_in" else (fin(ph[i]) and c[i] > ph[i] and c[i] > lo)
            if ok:
                _emit(out, i, 1, F.stop_dist(1, c[i], ext, a[i]),
                      {"mid": F.tgt(1, c[i], mid), "far": F.tgt(1, c[i], hi)})
    return out


def eight_am_roadmap(bars, ctx, p):
    """#42 (2026-06-29). The 08:00-08:15 New York candle is a zone. Price above
    it at the first bar after it -> longs on a retest of the zone (`mid` = its
    midpoint, `edge` = its top) that closes back above; below -> shorts;
    inside -> no trade. Stop `stop_atr` x ATR (the video: a fixed 10 points)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "m8")
    Z_hi, Z_lo = w["hi"].tolist(), w["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = bias = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, bias = day[i], None
        zh, zl = Z_hi[i], Z_lo[i]
        if not (fin(zh) and fin(zl)):
            continue
        if bias is None:
            bias = 1 if o[i] > zh else (-1 if o[i] < zl else 0)
            continue
        if bias == 0 or not _ok(b, i, p):
            continue
        mid = (zh + zl) / 2
        if bias == 1:
            lvl = mid if p["entry"] == "mid" else zh
            if l[i] <= lvl < c[i]:
                _emit(out, i, 1, p["stop_atr"] * a[i], None)
        else:
            lvl = mid if p["entry"] == "mid" else zl
            if h[i] >= lvl > c[i]:
                _emit(out, i, -1, p["stop_atr"] * a[i], None)
    return out


def equal_levels(bars, ctx, p):
    """#17 (2026-08-10), #18 (2026-08-06), #128/#130. Equal highs: the last two
    swing highs within `tol` ATR. `breakout`: a close through them that leaves
    a gap, then a retrace into the gap with a reaction close -> continuation
    (#17). `sweep`: a wick through that closes back, a structure break the
    other way, then a retrace into the fresh gap -> reversal (#18). Mirrors
    for equal lows. Stop beyond the gap / sweep; setups expire after 20 bars."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, php, pl, plp = (pv["h"].tolist(), pv["h_prev"].tolist(),
                        pv["l"].tolist(), pv["l_prev"].tolist())
    ph_i, pl_i = pv["h_i"].tolist(), pv["l_i"].tolist()
    minor = F.pivots(bars, ctx, 2)
    mh, ml = minor["h"].tolist(), minor["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    setups = []                      # dicts
    used_h = used_l = -1
    for i in range(2, len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        eqh = fin(ph[i]) and fin(php[i]) and abs(ph[i] - php[i]) <= p["tol"] * ai
        eql = fin(pl[i]) and fin(plp[i]) and abs(pl[i] - plp[i]) <= p["tol"] * ai
        if eqh and ph_i[i] != used_h:
            lvl = max(ph[i], php[i])
            if p["mode"] == "breakout" and c[i] > lvl and bull[i]:
                setups.append({"side": 1, "lo": glo[i], "hi": ghi[i], "t": i, "stage": 2})
                used_h = ph_i[i]
            elif p["mode"] == "sweep" and h[i] > lvl > c[i]:
                setups.append({"side": -1, "ext": h[i], "t": i, "stage": 0})
                used_h = ph_i[i]
        if eql and pl_i[i] != used_l:
            lvl = min(pl[i], plp[i])
            if p["mode"] == "breakout" and c[i] < lvl and bear[i]:
                setups.append({"side": -1, "lo": glo[i], "hi": ghi[i], "t": i, "stage": 2})
                used_l = pl_i[i]
            elif p["mode"] == "sweep" and l[i] < lvl < c[i]:
                setups.append({"side": 1, "ext": l[i], "t": i, "stage": 0})
                used_l = pl_i[i]
        keep = []
        for s in setups:
            if i == s["t"]:
                keep.append(s)
                continue
            if i - s["t"] > 20:
                continue
            side = s["side"]
            if s["stage"] == 0:                       # waiting for the MSS
                s["ext"] = max(s["ext"], h[i]) if side == -1 else min(s["ext"], l[i])
                if (side == -1 and fin(ml[i]) and c[i] < ml[i]) or \
                        (side == 1 and fin(mh[i]) and c[i] > mh[i]):
                    s["stage"] = 1
                keep.append(s)
                continue
            if s["stage"] == 1:                       # waiting for a gap
                if (side == -1 and bear[i]) or (side == 1 and bull[i]):
                    s.update(lo=glo[i], hi=ghi[i], stage=2, gt=i)
                keep.append(s)
                continue
            if s.get("gt") == i:
                keep.append(s)
                continue
            fired = False
            if _ok(b, i, p):
                if side == 1 and l[i] <= s["hi"] and c[i] > s["lo"] and c[i] > o[i]:
                    ref = s.get("ext", s["lo"])
                    fired = _emit(out, i, 1, F.stop_dist(1, c[i], min(ref, s["lo"]), ai), None)
                elif side == -1 and h[i] >= s["lo"] and c[i] < s["hi"] and c[i] < o[i]:
                    ref = s.get("ext", s["hi"])
                    fired = _emit(out, i, -1, F.stop_dist(-1, c[i], max(ref, s["hi"]), ai), None)
            if not fired:
                keep.append(s)
        setups = keep
    return out


def rsi_divergence(bars, ctx, p):
    """#19 (2026-08-05), #84, #143, #371, #60. Regular RSI divergence on `k`
    swings (a lower price low with a higher RSI low -> long; mirror). `regime`
    `range` keeps only divergences while the 50-EMA moved < 1 ATR over 20 bars
    (#19). `confirm`: none; `break` = a close above the last swing high (#371);
    `fvg` = a bullish gap within 5 bars (#143). Stop under the divergence low."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k = p["k"]
    r = F.rsi(bars, ctx, p["rsi_n"]).tolist()
    pv = F.pivots(bars, ctx, k)
    pl_i, plp_i = pv["l_i"].tolist(), pv["l_prev_i"].tolist()
    ph_i, php_i = pv["h_i"].tolist(), pv["h_prev_i"].tolist()
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    e50 = F.ema(bars, ctx, 50).tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    out = {}
    pend = []
    seen_l = seen_h = -1
    for i in range(20, len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        rng = abs(e50[i] - e50[i - 20]) < ai if fin(e50[i - 20]) else False
        if pl_i[i] >= 0 and pl_i[i] != seen_l and plp_i[i] >= 0:
            seen_l = pl_i[i]
            j, jp = pl_i[i], plp_i[i]
            if l[j] < l[jp] and r[j] > r[jp] and (p["regime"] == "any" or rng):
                pend.append((1, i, l[j]))
        if ph_i[i] >= 0 and ph_i[i] != seen_h and php_i[i] >= 0:
            seen_h = ph_i[i]
            j, jp = ph_i[i], php_i[i]
            if h[j] > h[jp] and r[j] < r[jp] and (p["regime"] == "any" or rng):
                pend.append((-1, i, h[j]))
        keep = []
        for side, t, ext in pend:
            wait = 5 if p["confirm"] == "fvg" else 20
            if i - t > wait:
                continue
            if (side == 1 and l[i] < ext) or (side == -1 and h[i] > ext):
                continue                              # divergence broken
            conf = (p["confirm"] == "none" or
                    (p["confirm"] == "break" and ((side == 1 and fin(ph[i]) and c[i] > ph[i]) or
                                                  (side == -1 and fin(pl[i]) and c[i] < pl[i]))) or
                    (p["confirm"] == "fvg" and ((side == 1 and bull[i]) or (side == -1 and bear[i]))))
            if conf and _ok(b, i, p) and _emit(out, i, side, F.stop_dist(side, c[i], ext, ai), None):
                continue
            keep.append((side, t, ext))
        pend = keep
    return out


def vwap_ema(bars, ctx, p):
    """#20 (2026-08-03). Long on a close up through the session VWAP, short on
    a close down through it; the video exits on a close back through the
    9 EMA (`exit_mode` "signal", `ema_n`). Stop beyond the signal candle
    (`candle`) or 1 ATR. Their backtest: 30% win, PF 1.6."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx).tolist()
    first = b["first"].tolist()
    out = {}
    for i in range(1, len(c)):
        if first[i] or not _ok(b, i, p):
            continue
        if c[i - 1] <= vw[i - 1] and c[i] > vw[i]:
            stop = F.stop_dist(1, c[i], l[i], a[i]) if p["stop"] == "candle" else a[i]
            _emit(out, i, 1, stop, None)
        elif c[i - 1] >= vw[i - 1] and c[i] < vw[i]:
            stop = F.stop_dist(-1, c[i], h[i], a[i]) if p["stop"] == "candle" else a[i]
            _emit(out, i, -1, stop, None)
    return out


def vwap_ema_exit(index, bars, ctx, params, side):
    e = F.ema(bars, ctx, params["ema_n"])
    cl = F.base(bars, ctx)["c"][index]
    return (side == 1 and cl < e[index]) or (side == -1 and cl > e[index])


def htf_liquidity_fvg(bars, ctx, p):
    """#22 (2026-07-29), #133, #141. Higher-timeframe liquidity (a swing of
    strength `htf_k` bars) is swept; within 20 bars the LTF breaks structure
    (`k` swing) with a gap in the break -> trade toward the next HTF swing.
    Stop beyond the sweep; `level` = the opposite HTF swing."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    H = F.pivots(bars, ctx, p["htf_k"])
    Hh, Hl = H["h"].tolist(), H["l"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    out = {}
    ls = hs = None
    for i in range(2, len(c)):
        if fin(Hl[i]) and l[i] < Hl[i] and (ls is None or i - ls[0] > 20):
            ls = (i, l[i], Hh[i])
        elif ls is not None:
            ls = (ls[0], min(ls[1], l[i]), ls[2])
        if fin(Hh[i]) and h[i] > Hh[i] and (hs is None or i - hs[0] > 20):
            hs = (i, h[i], Hl[i])
        elif hs is not None:
            hs = (hs[0], max(hs[1], h[i]), hs[2])
        if not _ok(b, i, p):
            continue
        if ls and 0 < i - ls[0] <= 20 and fin(ph[i]) and c[i] > ph[i] and (bull[i] or bull[i - 1]):
            if _emit(out, i, 1, F.stop_dist(1, c[i], ls[1], a[i]), F.tgt(1, c[i], ls[2])):
                ls = None
        if hs and 0 < i - hs[0] <= 20 and fin(pl[i]) and c[i] < pl[i] and (bear[i] or bear[i - 1]):
            if _emit(out, i, -1, F.stop_dist(-1, c[i], hs[1], a[i]), F.tgt(-1, c[i], hs[2])):
                hs = None
    return out


def sweep_ifvg(bars, ctx, p):
    """#50 (2026-06-10), #85, #93, #96 (581k), #360 (485k), #388, #25.
    A swing high (strength `k`) is swept. `ifvg`: a bullish gap printed around
    the sweep is then closed below (inversion) within `w` bars -> short
    (#50/#85/#96). `fvg_retrace`: a bearish gap forms after the sweep and price
    retraces into it and closes back below -> short (#93). `trend_ma` 100 = only
    with a 100-bar SMA (#360). Stop beyond the sweep; `level` = the last swing
    low. Mirrors for lows."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    ma = (F.sma(b["c"], p["trend_ma"]).tolist() if p["trend_ma"] else None)
    w = p["w"]
    out = {}
    hs = ls = None
    for i in range(3, len(c)):
        if fin(ph[i]) and h[i] > ph[i] and (hs is None or i - hs["t"] > w):
            gaps = [(glo[j], ghi[j]) for j in (i - 2, i - 1, i) if bull[j]]
            hs = {"t": i, "ext": h[i], "gaps": gaps, "tgt": pl[i], "rev": None}
        elif hs is not None:
            hs["ext"] = max(hs["ext"], h[i])
            if bull[i]:
                hs["gaps"].append((glo[i], ghi[i]))
        if fin(pl[i]) and l[i] < pl[i] and (ls is None or i - ls["t"] > w):
            gaps = [(glo[j], ghi[j]) for j in (i - 2, i - 1, i) if bear[j]]
            ls = {"t": i, "ext": l[i], "gaps": gaps, "tgt": ph[i], "rev": None}
        elif ls is not None:
            ls["ext"] = min(ls["ext"], l[i])
            if bear[i]:
                ls["gaps"].append((glo[i], ghi[i]))
        if not _ok(b, i, p):
            continue
        for s, side in ((hs, -1), (ls, 1)):
            if s is None or not 0 < i - s["t"] <= w:
                continue
            if ma is not None and not (fin(ma[i]) and side * (c[i] - ma[i]) > 0):
                continue
            hit = False
            if p["entry"] == "ifvg":
                hit = any((side == -1 and c[i] < lo) or (side == 1 and c[i] > hi)
                          for lo, hi in s["gaps"])
            else:
                if side == -1 and bear[i] and s["rev"] is None:
                    s["rev"] = (glo[i], ghi[i], i)
                elif side == 1 and bull[i] and s["rev"] is None:
                    s["rev"] = (glo[i], ghi[i], i)
                elif s["rev"] is not None and i > s["rev"][2]:
                    lo, hi, _t = s["rev"]
                    hit = ((side == -1 and h[i] >= lo and c[i] < lo) or
                           (side == 1 and l[i] <= hi and c[i] > hi))
            if hit:
                _emit(out, i, side, F.stop_dist(side, c[i], s["ext"], a[i]),
                      F.tgt(side, c[i], s["tgt"]))
    return out


def break_gap_reaction(bars, ctx, p):
    """#26 (2026-07-24). A break of a swing no older than `max_age` bars that
    leaves a gap; a retrace into the gap with a reaction candle -> continuation.
    Stop under the gap; the video's favourite target is 2R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl, ph_i, pl_i = pv["h"].tolist(), pv["l"].tolist(), pv["h_i"].tolist(), pv["l_i"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    arm = []
    used_h = used_l = -1
    for i in range(2, len(c)):
        if (fin(ph[i]) and ph_i[i] != used_h and c[i] > ph[i]
                and i - ph_i[i] <= p["max_age"] and bull[i]):
            arm.append((1, glo[i], ghi[i], i))
            used_h = ph_i[i]
        if (fin(pl[i]) and pl_i[i] != used_l and c[i] < pl[i]
                and i - pl_i[i] <= p["max_age"] and bear[i]):
            arm.append((-1, glo[i], ghi[i], i))
            used_l = pl_i[i]
        keep = []
        for side, lo, hi, t in arm:
            if i == t:
                keep.append((side, lo, hi, t))
                continue
            if i - t > 20:
                continue
            if side == 1 and c[i] < lo or side == -1 and c[i] > hi:
                continue                              # gap failed
            if _ok(b, i, p) and (
                    (side == 1 and l[i] <= hi and c[i] > o[i]) or
                    (side == -1 and h[i] >= lo and c[i] < o[i])):
                ref = lo if side == 1 else hi
                if _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None):
                    continue
            keep.append((side, lo, hi, t))
        arm = keep
    return out


def trend_pullback(bars, ctx, p):
    """#27 (2026-07-22), #31, #35, #63, #73, #123, #578. With-trend pullback
    entries on `k` structure, optionally only above an `ema` (the videos' HTF
    50/200 EMA read on the chart's own bars).
      fib50        after a CHoCH back with the trend, buy a retrace to 50% of
                   the breaking leg (#27, #63)
      golden       after a BOS, buy the 0.5-0.618 retrace of the leg (#35)
      sweep_shift  after a BOS, the retrace sweeps the last minor swing low and
                   then breaks the last minor swing high (#31, #73, #123)
    Stop under the leg low; `level` = the leg high. Mirrors for downtrends."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    s = F.structure(bars, ctx, p["k"])
    up, dn = s["up"].tolist(), s["dn"].tolist()
    cu, cd = s["choch_up"].tolist(), s["choch_dn"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    mn = F.pivots(bars, ctx, 2)
    mh, ml = mn["h"].tolist(), mn["l"].tolist()
    ema = F.ema(bars, ctx, p["ema"]).tolist() if p["ema"] else None
    v = p["variant"]
    out = {}
    leg = None
    for i in range(len(c)):
        trig_up = cu[i] if v == "fib50" else up[i]
        trig_dn = cd[i] if v == "fib50" else dn[i]
        if trig_up and fin(pl[i]):
            leg = {"side": 1, "lo": pl[i], "hi": h[i], "t": i, "swept": None}
        elif trig_dn and fin(ph[i]):
            leg = {"side": -1, "lo": l[i], "hi": ph[i], "t": i, "swept": None}
        if leg is None or i == leg["t"]:
            continue
        side = leg["side"]
        if side == 1:
            leg["hi"] = max(leg["hi"], h[i]) if leg["swept"] is None and v != "fib50" else leg["hi"]
            if l[i] < leg["lo"]:
                leg = None
                continue
        else:
            leg["lo"] = min(leg["lo"], l[i]) if leg["swept"] is None and v != "fib50" else leg["lo"]
            if h[i] > leg["hi"]:
                leg = None
                continue
        if i - leg["t"] > 60 or not _ok(b, i, p):
            continue
        if ema is not None and not (fin(ema[i]) and side * (c[i] - ema[i]) > 0):
            continue
        lo, hi = leg["lo"], leg["hi"]
        rng = hi - lo
        if not rng > 0:
            continue
        fire = False
        if v == "fib50":
            lvl = lo + 0.5 * rng
            fire = (l[i] <= lvl < c[i]) if side == 1 else (h[i] >= lvl > c[i])
        elif v == "golden":
            if side == 1:
                fire = l[i] <= hi - 0.5 * rng and l[i] >= hi - 0.618 * rng - 0.1 * rng and c[i] > hi - 0.5 * rng
            else:
                fire = h[i] >= lo + 0.5 * rng and h[i] <= lo + 0.618 * rng + 0.1 * rng and c[i] < lo + 0.5 * rng
        else:
            if leg["swept"] is None:
                if side == 1 and fin(ml[i]) and l[i] < ml[i] and ml[i] > lo:
                    leg["swept"] = l[i]
                elif side == -1 and fin(mh[i]) and h[i] > mh[i] and mh[i] < hi:
                    leg["swept"] = h[i]
            else:
                leg["swept"] = min(leg["swept"], l[i]) if side == 1 else max(leg["swept"], h[i])
                fire = (side == 1 and fin(mh[i]) and c[i] > mh[i]) or (side == -1 and fin(ml[i]) and c[i] < ml[i])
        if fire:
            ref = leg["swept"] if v == "sweep_shift" else (lo if side == 1 else hi)
            target = hi if side == 1 else lo
            if _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), F.tgt(side, c[i], target)):
                leg = None
    return out


def htf_manipulation(bars, ctx, p):
    """#40 (2026-07-03). The manipulation candle (#28) on a higher timeframe:
    an `htf_m`-bar candle trades below the previous one's low and closes above
    its high (`close_above` high) or close (`close_above` close) -> long at the
    next bar; mirror. `prior`: the previous HTF candle closed the same way.
    `ema` 200 filter (#28's LuxAlgo version). Stop under the HTF candle or 2 ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    x = F.htf(bars, ctx, p["htf_m"])
    closed = x["closed"].tolist()
    po, ph_, pl_, pc = (x["po"].tolist(), x["ph"].tolist(), x["pl"].tolist(), x["pc"].tolist())
    qo, qh, ql, qc = (x["qo"].tolist(), x["qh"].tolist(), x["ql"].tolist(), x["qc"].tolist())
    ema = F.ema(bars, ctx, p["ema"]).tolist() if p["ema"] else None
    out = {}
    for i in range(len(c)):
        if not closed[i] or not fin(qh[i]) or not _ok(b, i, p):
            continue
        ref_up = qh[i] if p["close_above"] == "high" else qc[i]
        ref_dn = ql[i] if p["close_above"] == "high" else qc[i]
        bull = pl_[i] < ql[i] and pc[i] > ref_up and pc[i] > po[i]
        bear = ph_[i] > qh[i] and pc[i] < ref_dn and pc[i] < po[i]
        if p["prior"]:
            bull = bull and qc[i] > qo[i]
            bear = bear and qc[i] < qo[i]
        if ema is not None:
            bull = bull and fin(ema[i]) and c[i] > ema[i]
            bear = bear and fin(ema[i]) and c[i] < ema[i]
        if bull:
            stop = F.stop_dist(1, c[i], pl_[i], a[i]) if p["stop"] == "candle" else 2 * a[i]
            _emit(out, i, 1, stop, None)
        elif bear:
            stop = F.stop_dist(-1, c[i], ph_[i], a[i]) if p["stop"] == "candle" else 2 * a[i]
            _emit(out, i, -1, stop, None)
    return out


def orb_retest(bars, ctx, p):
    """#29 (2026-07-16), #135 (330k), #144. Opening range of `or_min`; the
    first close outside sets the bias; a later retest back to the range edge
    confirmed by a close back outside (`close`) or a bullish/bearish
    engulfing (`engulf`) -> enter with the bias. Stop under the retest swing
    (`swing`) or at the range midpoint (`range`)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = bias = ext = None
    for i in range(1, len(c)):
        if day[i] != cur:
            cur, bias, ext = day[i], None, None
        hi, lo = Rh[i], Rl[i]
        if not fin(hi):
            continue
        if bias is None:
            if c[i] > hi:
                bias, ext = 1, l[i]
            elif c[i] < lo:
                bias, ext = -1, h[i]
            continue
        ext = min(ext, l[i]) if bias == 1 else max(ext, h[i])
        if not _ok(b, i, p):
            continue
        eng_up = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] > h[i - 1]
        eng_dn = c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] < l[i - 1]
        mid = (hi + lo) / 2
        if bias == 1 and l[i] <= hi and c[i] > hi and (p["confirm"] == "close" or eng_up):
            ref = min(ext, l[i]) if p["stop"] == "swing" else mid
            _emit(out, i, 1, F.stop_dist(1, c[i], ref, a[i]), None)
        elif bias == -1 and h[i] >= lo and c[i] < lo and (p["confirm"] == "close" or eng_dn):
            ref = max(ext, h[i]) if p["stop"] == "swing" else mid
            _emit(out, i, -1, F.stop_dist(-1, c[i], ref, a[i]), None)
    return out


def orb_breakout(bars, ctx, p):
    """#30 (2026-07-15), #117. The first close outside the `or_min` opening
    range -> enter; `vol_mult` > 0 requires that bar's volume >= mult x the
    20-bar mean (LuxAlgo's high-volume "HV" breakout). Stop at the range
    midpoint (`mid`) or the far side (`far`); the video targets 1:3."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    vm = F.sma(b["v"], 20)
    vm = numpy.concatenate(([NAN], vm[:-1])).tolist()
    v = b["L"]["v"]
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
        if p["vol_mult"] and not (fin(vm[i]) and v[i] >= p["vol_mult"] * vm[i]):
            continue
        ref = (hi + lo) / 2 if p["stop"] == "mid" else (lo if side == 1 else hi)
        _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), None)
    return out


def swing_sweep_mss(bars, ctx, p):
    """#34 (2026-07-09), #396. A new high beyond the last `k` swing, then within
    `w` bars a market-structure shift (a close below the last minor swing low)
    -> short at the shift (`mss`) or on a retrace to 50% of the displacement
    (`fib50`). `ema` 200 = LuxAlgo's trend filter. Stop above the sweep."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    mn = F.pivots(bars, ctx, 2)
    mh, ml = mn["h"].tolist(), mn["l"].tolist()
    ema = F.ema(bars, ctx, p["ema"]).tolist() if p["ema"] else None
    w = p["w"]
    out = {}
    st = {1: None, -1: None}
    for i in range(len(c)):
        if fin(ph[i]) and h[i] > ph[i] and (st[-1] is None or i - st[-1]["t"] > w):
            st[-1] = {"t": i, "ext": h[i], "mss": None}
        if fin(pl[i]) and l[i] < pl[i] and (st[1] is None or i - st[1]["t"] > w):
            st[1] = {"t": i, "ext": l[i], "mss": None}
        for side in (1, -1):
            s = st[side]
            if s is None or i == s["t"]:
                continue
            if s["mss"] is None:
                s["ext"] = min(s["ext"], l[i]) if side == 1 else max(s["ext"], h[i])
                if i - s["t"] > w:
                    st[side] = None
                    continue
                shift = (side == 1 and fin(mh[i]) and c[i] > mh[i]) or \
                        (side == -1 and fin(ml[i]) and c[i] < ml[i])
                if not shift:
                    continue
                s["mss"] = (i, h[i] if side == 1 else l[i])
                if p["entry"] != "mss":
                    continue
            elif p["entry"] == "mss" or i - s["mss"][0] > 20:
                st[side] = None
                continue
            if not _ok(b, i, p) or (ema is not None and not (fin(ema[i]) and side * (c[i] - ema[i]) > 0)):
                continue
            if p["entry"] == "mss":
                fire = True
            else:
                lvl = (s["ext"] + s["mss"][1]) / 2
                fire = (side == 1 and l[i] <= lvl < c[i]) or (side == -1 and h[i] >= lvl > c[i])
            if fire and _emit(out, i, side, F.stop_dist(side, c[i], s["ext"], a[i]), None):
                st[side] = None
    return out


def sweep_reclaim(bars, ctx, p):
    """#39 (2026-07-04), #89, #124 (340k), #354, #358, #514, #577. Price trades
    below a level and closes back above it within `confirm_bars` -> long
    (a spring / swing-failure / liquidity grab); mirror at highs. `level`:
    the last swing (strength 5 or 10), the previous day's low/high (#124, the
    daily CRT of #89), or the overnight low/high. Stop under the sweep;
    `level` exit = the opposite level."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    src = p["level"]
    if src.startswith("swing"):
        pv = F.pivots(bars, ctx, int(src[5:]))
        LO, HI = pv["l"].tolist(), pv["h"].tolist()
    elif src == "prev_day":
        d = F.days(bars, ctx)
        LO, HI = d["pdl"].tolist(), d["pdh"].tolist()
    else:
        d = F.days(bars, ctx)
        LO, HI = d["on_l"].tolist(), d["on_h"].tolist()
    n = p["confirm_bars"]
    out = {}
    ls = hs = None
    for i in range(len(c)):
        lo, hi = LO[i], HI[i]
        if fin(lo) and l[i] < lo:
            ls = (i, l[i], lo) if ls is None or ls[2] != lo else (ls[0], min(ls[1], l[i]), lo)
        if fin(hi) and h[i] > hi:
            hs = (i, h[i], hi) if hs is None or hs[2] != hi else (hs[0], max(hs[1], h[i]), hi)
        if not _ok(b, i, p):
            continue
        if ls and i - ls[0] < n and c[i] > ls[2]:
            if _emit(out, i, 1, F.stop_dist(1, c[i], ls[1], a[i]), F.tgt(1, c[i], hi)):
                ls = None
        if hs and i - hs[0] < n and c[i] < hs[2]:
            if _emit(out, i, -1, F.stop_dist(-1, c[i], hs[1], a[i]), F.tgt(-1, c[i], lo)):
                hs = None
    return out


def cisd(bars, ctx, p):
    """#41 (2026-07-01), #55 (402k), #79, #110. A higher-timeframe candle-range
    setup: an `htf_m`-bar candle sweeps the previous one's low and closes back
    inside (bullish CRT) -> while the next HTF candle forms, the first LTF CISD
    (a close above the open of the latest run of down-closes) -> long toward
    the previous HTF high. `killzone` ny_am = 09:00-11:00 New York only (#79).
    Stop under the sweep; `level` = the previous HTF candle's high."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    x = F.htf(bars, ctx, p["htf_m"])
    closed = x["closed"].tolist()
    ph_, pl_, pc = x["ph"].tolist(), x["pl"].tolist(), x["pc"].tolist()
    qh, ql = x["qh"].tolist(), x["ql"].tolist()
    real = b["L"]["real"]
    out = {}
    setup = None
    run_open = None
    run_side = 0
    for i in range(1, len(c)):
        col = 1 if c[i] > o[i] else (-1 if c[i] < o[i] else 0)
        # the open of the latest run of opposite-colour candles
        if col == -1:
            run_open = o[i] if run_side != -1 else run_open
            run_side = -1
        elif col == 1:
            run_open = o[i] if run_side != 1 else run_open
            run_side = 1
        if setup is not None and i - setup["t"] > p["htf_m"]:
            setup = None
        if setup is not None and _ok(b, i, p) and (
                p["killzone"] == "all" or 9 * 60 <= real[i] < 11 * 60):
            side = setup["side"]
            if side == 1 and col == 1 and setup["ro"] is not None and c[i] > setup["ro"]:
                if _emit(out, i, 1, F.stop_dist(1, c[i], setup["ext"], a[i]), F.tgt(1, c[i], setup["tgt"])):
                    setup = None
            elif side == -1 and col == -1 and setup["ro"] is not None and c[i] < setup["ro"]:
                if _emit(out, i, -1, F.stop_dist(-1, c[i], setup["ext"], a[i]), F.tgt(-1, c[i], setup["tgt"])):
                    setup = None
        if setup is not None:
            # track the run the CISD must close through
            if setup["side"] == 1 and col == -1:
                setup["ro"] = run_open
            elif setup["side"] == -1 and col == 1:
                setup["ro"] = run_open
        if closed[i] and fin(ql[i]):
            if pl_[i] < ql[i] and ql[i] < pc[i] < qh[i]:
                setup = {"side": 1, "t": i, "ext": pl_[i], "tgt": qh[i],
                         "ro": run_open if run_side == -1 else None}
            elif ph_[i] > qh[i] and ql[i] < pc[i] < qh[i]:
                setup = {"side": -1, "t": i, "ext": ph_[i], "tgt": ql[i],
                         "ro": run_open if run_side == 1 else None}
    return out


def open_candle_fade(bars, ctx, p):
    """#44 (2026-06-22). The session's first `or_min` candle; if its range is at
    least `frac` of the average daily range it is a "manipulation candle" and
    is faded at the next bar toward its 38.2 / 50 / 61.8% retracement
    (`level_382` / `level_50` / `level_618`). Stop beyond its extreme."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    risk = ctx["risk"]
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    d = F.days(bars, ctx)
    out = {}
    for s, e in zip(d["d_start"], d["d_end"]):
        idx = [i for i in range(s, e) if fin(Rh[i])]
        if not idx:
            continue
        i = idx[0]                       # first bar after the range closes
        rk = risk[i]
        hi, lo = Rh[i], Rl[i]
        rng = hi - lo
        if not (rk and rk > 0 and rng >= p["frac"] * rk) or not _ok(b, i - 1, p):
            continue
        j = i - 1                        # signal on the range's last bar
        up = c[j] > o[s]
        side = -1 if up else 1
        lv = {"382": (hi - 0.382 * rng) if up else (lo + 0.382 * rng),
              "50": (hi - 0.5 * rng) if up else (lo + 0.5 * rng),
              "618": (hi - 0.618 * rng) if up else (lo + 0.618 * rng)}
        _emit(out, j, side, F.stop_dist(side, c[j], hi if up else lo, a[j]),
              {k: F.tgt(side, c[j], v) for k, v in lv.items()})
    return out


def trendline_break(bars, ctx, p):
    """#45 (2026-06-19), #127, #69. A falling line through the last `touches`
    swing highs (strength `k`), each within `tol` ATR of the line, broken by a
    close above it -> long; mirror for rising lows. Stop under the last swing
    low."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    k, nt = p["k"], p["touches"]
    pv = F.pivots(bars, ctx, k)
    hi_i, lo_i = pv["h_i"].tolist(), pv["l_i"].tolist()
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    out = {}
    his, los = [], []
    last_h = last_l = -1
    line_h = line_l = None
    for i in range(len(c)):
        if hi_i[i] >= 0 and hi_i[i] != last_h:
            last_h = hi_i[i]
            his = (his + [(last_h, h[last_h])])[-nt:]
            line_h = None
            if len(his) == nt:
                (x0, y0), (x1, y1) = his[0], his[-1]
                if x1 > x0 and y1 < y0:
                    slope = (y1 - y0) / (x1 - x0)
                    if all(abs(y - (y0 + slope * (x - x0))) <= p["tol"] * (a[i] or 0) for x, y in his):
                        line_h = (x0, y0, slope)
        if lo_i[i] >= 0 and lo_i[i] != last_l:
            last_l = lo_i[i]
            los = (los + [(last_l, l[last_l])])[-nt:]
            line_l = None
            if len(los) == nt:
                (x0, y0), (x1, y1) = los[0], los[-1]
                if x1 > x0 and y1 > y0:
                    slope = (y1 - y0) / (x1 - x0)
                    if all(abs(y - (y0 + slope * (x - x0))) <= p["tol"] * (a[i] or 0) for x, y in los):
                        line_l = (x0, y0, slope)
        if not _ok(b, i, p) or i == 0:
            continue
        if line_h is not None:
            x0, y0, sl = line_h
            y = y0 + sl * (i - x0)
            yp = y0 + sl * (i - 1 - x0)
            if c[i] > y and c[i - 1] <= yp:
                if _emit(out, i, 1, F.stop_dist(1, c[i], pl[i], a[i]), None):
                    line_h = None
        if line_l is not None:
            x0, y0, sl = line_l
            y = y0 + sl * (i - x0)
            yp = y0 + sl * (i - 1 - x0)
            if c[i] < y and c[i - 1] >= yp:
                if _emit(out, i, -1, F.stop_dist(-1, c[i], ph[i], a[i]), None):
                    line_l = None
    return out


def rubber_band(bars, ctx, p):
    """#47 (2026-06-15), #119. A tight box (the last `n` bars within `width`
    ATR) after a move; its far side is swept and closes back in (the fake-out),
    then a close out of the box with the prior move -> enter; stop beyond the
    sweep. The prior move is the 50-EMA's direction over the box."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    e = F.ema(bars, ctx, 50).tolist()
    bh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    bl = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    setup = None
    for i in range(n + 1, len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        if fin(bh[i]) and bh[i] - bl[i] <= p["width"] * ai and fin(e[i - n]):
            if e[i] > e[i - n] and l[i] < bl[i] <= c[i]:
                setup = (1, bh[i], l[i], i)
            elif e[i] < e[i - n] and h[i] > bh[i] >= c[i]:
                setup = (-1, bl[i], h[i], i)
        if setup is None or i == setup[3]:
            continue
        side, edge, ext, t = setup
        if i - t > 10:
            setup = None
            continue
        ext = min(ext, l[i]) if side == 1 else max(ext, h[i])
        setup = (side, edge, ext, t)
        if _ok(b, i, p) and side * (c[i] - edge) > 0:
            if _emit(out, i, side, F.stop_dist(side, c[i], ext, ai), None):
                setup = None
    return out


def gap_fill_breakout(bars, ctx, p):
    """#48 (2026-06-12, 514k views). A close through the last `k` swing into an
    untouched gap no more than `max_dist` ATR away -> trade toward it;
    `level_near` = the gap's near edge, `level_far` = a full fill. Stop beyond
    the signal candle."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl, ph_i, pl_i = pv["h"].tolist(), pv["l"].tolist(), pv["h_i"].tolist(), pv["l_i"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    below, above = [], []      # untouched bullish gaps below / bearish above
    used_h = used_l = -1
    for i in range(2, len(c)):
        below = [z for z in below if l[i] > z[1]]
        above = [z for z in above if h[i] < z[0]]
        ai = a[i]
        if ai > 0 and _ok(b, i, p):
            if fin(pl[i]) and pl_i[i] != used_l and c[i] < pl[i]:
                used_l = pl_i[i]
                cand = [z for z in below if z[1] < c[i] and c[i] - z[1] <= p["max_dist"] * ai]
                if cand:
                    z = max(cand, key=lambda q: q[1])
                    _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], ai),
                          {"near": F.tgt(-1, c[i], z[1]), "far": F.tgt(-1, c[i], z[0])})
            if fin(ph[i]) and ph_i[i] != used_h and c[i] > ph[i]:
                used_h = ph_i[i]
                cand = [z for z in above if z[0] > c[i] and z[0] - c[i] <= p["max_dist"] * ai]
                if cand:
                    z = min(cand, key=lambda q: q[0])
                    _emit(out, i, 1, F.stop_dist(1, c[i], l[i], ai),
                          {"near": F.tgt(1, c[i], z[0]), "far": F.tgt(1, c[i], z[1])})
        if bull[i]:
            below.append((glo[i], ghi[i]))
        if bear[i]:
            above.append((glo[i], ghi[i]))
        below, above = below[-30:], above[-30:]
    return out


def three_step_trap(bars, ctx, p):
    """#52 (2026-06-08), #71. A range (`or15`/`or30` opening range, or `box16`
    = the 16 bars before the break); step 1 a close outside, step 2 a close back
    inside within `m` bars, step 3 a close beyond the failure bar -> trade the
    failure. Stop beyond the breakout extreme; `level_mid` / `level_far` = the
    range midpoint / far side."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    src, m = p["src"], p["m"]
    if src.startswith("or"):
        r = F.opening_range(bars, ctx, int(src[2:]))
        Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    else:
        Rh = numpy.concatenate(([NAN], F.rolling_max(b["h"], 16)[:-1])).tolist()
        Rl = numpy.concatenate(([NAN], F.rolling_min(b["l"], 16)[:-1])).tolist()
    day = b["L"]["day"]
    out = {}
    st = None
    cur = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, st = day[i], None
        if st is None:
            hi, lo = Rh[i], Rl[i]
            if not fin(hi):
                continue
            if c[i] > hi:
                st = {"side": 1, "hi": hi, "lo": lo, "t": i, "ext": h[i], "fail": None}
            elif c[i] < lo:
                st = {"side": -1, "hi": hi, "lo": lo, "t": i, "ext": l[i], "fail": None}
            continue
        side = st["side"]
        st["ext"] = max(st["ext"], h[i]) if side == 1 else min(st["ext"], l[i])
        if st["fail"] is None:
            if i - st["t"] > m:
                st = None
                continue
            if (side == 1 and c[i] < st["hi"]) or (side == -1 and c[i] > st["lo"]):
                st["fail"] = (i, l[i] if side == 1 else h[i])
            continue
        if i - st["fail"][0] > m:
            st = None
            continue
        if _ok(b, i, p) and ((side == 1 and c[i] < st["fail"][1]) or (side == -1 and c[i] > st["fail"][1])):
            s2 = -side
            mid = (st["hi"] + st["lo"]) / 2
            far = st["lo"] if side == 1 else st["hi"]
            if _emit(out, i, s2, F.stop_dist(s2, c[i], st["ext"], a[i]),
                     {"mid": F.tgt(s2, c[i], mid), "far": F.tgt(s2, c[i], far)}):
                st = None
    return out


def momentum_flip(bars, ctx, p):
    """#53 (2026-06-05). `n_shrink` down-candles with shrinking bodies, then a
    green candle (the signal); the NEXT candle must trade below its open and
    close above it -> long (mirror). `near_level`: the slowdown happens within
    1 ATR of the last 5-bar swing low (the video's support)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    body = F.body(bars, ctx).tolist()
    pv = F.pivots(bars, ctx, 5)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    n = p["n_shrink"]
    out = {}
    for t in range(n + 2, len(c)):
        s = t - 1
        if not _ok(b, t, p):
            continue
        run = range(s - n, s)
        down = all(c[j] < o[j] for j in run) and all(body[j] < body[j - 1] for j in list(run)[1:])
        up = all(c[j] > o[j] for j in run) and all(body[j] < body[j - 1] for j in list(run)[1:])
        if down and c[s] > o[s] and l[t] < o[t] < c[t]:
            if p["near_level"] and not (fin(pl[t]) and abs(min(l[s - n:s + 1]) - pl[t]) <= a[t]):
                continue
            _emit(out, t, 1, F.stop_dist(1, c[t], min(l[s], l[t]), a[t]), None)
        elif up and c[s] < o[s] and h[t] > o[t] > c[t]:
            if p["near_level"] and not (fin(ph[t]) and abs(max(h[s - n:s + 1]) - ph[t]) <= a[t]):
                continue
            _emit(out, t, -1, F.stop_dist(-1, c[t], max(h[s], h[t]), a[t]), None)
    return out


def body_momentum(bars, ctx, p):
    """#56 (2026-06-01). The mean body of the last two same-colour candles is
    at least `mult` x the 20-bar average body -> enter with them; the video
    exits when momentum slows (`exit_mode` signal: a body under half the
    average, or an opposite candle) or at 2:1. Stop at the last 3-bar swing
    or 1 ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    body = F.body(bars, ctx)
    avg = numpy.concatenate(([NAN, NAN], F.sma(body, 20)[:-2])).tolist()
    body = body.tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(2, len(c)):
        if not fin(avg[i]) or not _ok(b, i, p):
            continue
        same_up = c[i] > o[i] and c[i - 1] > o[i - 1]
        same_dn = c[i] < o[i] and c[i - 1] < o[i - 1]
        if (same_up or same_dn) and (body[i] + body[i - 1]) / 2 >= p["mult"] * avg[i]:
            side = 1 if same_up else -1
            ref = (pl[i] if side == 1 else ph[i]) if p["stop"] == "swing" else NAN
            stop = F.stop_dist(side, c[i], ref, a[i]) if p["stop"] == "swing" else a[i]
            _emit(out, i, side, stop, None)
    return out


def body_momentum_exit(index, bars, ctx, params, side):
    b = F.base(bars, ctx)
    body = F.body(bars, ctx)
    avg = F.cached(ctx, "tt_body_avg", lambda: F.sma(body, 20))
    col = numpy.sign(b["c"][index] - b["o"][index])
    return col == -side or (fin(avg[index]) and body[index] < 0.5 * avg[index])


def first_hour_sweep(bars, ctx, p):
    """#58 (2026-05-29), #137. The session's first `range_min` minutes; within
    the next `window` minutes a candle wicks through one side but closes back
    inside -> bias to the other side; enter at that close (`close`) or on the
    retrace into the next opposite gap (`fvg`). Stop beyond the sweep;
    `level_mid` / `level_far` = the range midpoint / far side. The videos used
    the London open (EURUSD, gold) and the Asian open; this is each market's
    own session open."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["range_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    since = b["L"]["since"]
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = st = None
    end = p["range_min"] + p["window"]
    for i in range(len(c)):
        if day[i] != cur:
            cur, st = day[i], None
        hi, lo = Rh[i], Rl[i]
        if not fin(hi) or since[i] >= end:
            continue
        mid = (hi + lo) / 2
        if st is None:
            if h[i] > hi and c[i] <= hi:
                st = {"side": -1, "ext": h[i], "gap": None, "t": i}
            elif l[i] < lo and c[i] >= lo:
                st = {"side": 1, "ext": l[i], "gap": None, "t": i}
            else:
                continue
        side = st["side"]
        st["ext"] = max(st["ext"], h[i]) if side == -1 else min(st["ext"], l[i])
        if not _ok(b, i, p):
            continue
        tg = {"mid": F.tgt(side, c[i], mid), "far": F.tgt(side, c[i], lo if side == -1 else hi)}
        if p["entry"] == "close":
            if i == st["t"] and _emit(out, i, side, F.stop_dist(side, c[i], st["ext"], a[i]), tg):
                st["done"] = True
            continue
        if st["gap"] is None:
            if (side == -1 and bear[i]) or (side == 1 and bull[i]):
                st["gap"] = (glo[i], ghi[i], i)
            continue
        zlo, zhi, t = st["gap"]
        if i > t and ((side == -1 and h[i] >= zlo and c[i] < zlo) or (side == 1 and l[i] <= zhi and c[i] > zhi)):
            _emit(out, i, side, F.stop_dist(side, c[i], st["ext"], a[i]), tg)
    return out


def range_breakout_retest(bars, ctx, p):
    """#62 (2026-05-25, 1.0M views), #97, #346, #390, #484. A box (the last
    `n` bars within `width` ATR); a close out of it; within 20 bars a retest of
    the broken edge that closes back outside -> enter. Stop at the box's far
    side (`far`) or midpoint (`mid`)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    bh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    bl = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    out = {}
    arm = None
    for i in range(n + 1, len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        if fin(bh[i]) and bh[i] - bl[i] <= p["width"] * ai:
            if c[i] > bh[i]:
                arm = (1, bh[i], bl[i], i)
            elif c[i] < bl[i]:
                arm = (-1, bh[i], bl[i], i)
        if arm is None or i == arm[3]:
            continue
        side, hi, lo, t = arm
        if i - t > 20 or (side == 1 and c[i] < lo) or (side == -1 and c[i] > hi):
            arm = None
            continue
        if not _ok(b, i, p):
            continue
        ref = (hi + lo) / 2 if p["stop"] == "mid" else (lo if side == 1 else hi)
        if side == 1 and l[i] <= hi < c[i]:
            if _emit(out, i, 1, F.stop_dist(1, c[i], ref, ai), None):
                arm = None
        elif side == -1 and h[i] >= lo > c[i]:
            if _emit(out, i, -1, F.stop_dist(-1, c[i], ref, ai), None):
                arm = None
    return out


def rsi_50_pullback(bars, ctx, p):
    """#66 (2026-05-21). RSI(`rsi_n`) above `ob` within the last 20 bars, then
    back down to 50 -> long (continuation); mirror below 100-`ob`. Stop at the
    last 3-bar swing or 1 ATR; the video took 1:2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.rsi(bars, ctx, p["rsi_n"])
    hot = numpy.concatenate(([False], F.rolling_max(numpy.nan_to_num(r, nan=50.0), 20)[:-1] >= p["ob"])).tolist()
    cold = numpy.concatenate(([False], F.rolling_min(numpy.nan_to_num(r, nan=50.0), 20)[:-1] <= 100 - p["ob"])).tolist()
    r = r.tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(1, len(c)):
        if not _ok(b, i, p) or not fin(r[i - 1]):
            continue
        if hot[i] and r[i - 1] > 50 >= r[i]:
            stop = F.stop_dist(1, c[i], pl[i], a[i]) if p["stop"] == "swing" else a[i]
            _emit(out, i, 1, stop, None)
        elif cold[i] and r[i - 1] < 50 <= r[i]:
            stop = F.stop_dist(-1, c[i], ph[i], a[i]) if p["stop"] == "swing" else a[i]
            _emit(out, i, -1, stop, None)
    return out


def fvg_sweep_wick(bars, ctx, p):
    """#68 (2026-05-19), #91. An untouched bearish gap sits just above a swing
    high (`k`); a candle sweeps the swing high, wicks into the gap and closes
    back below both -> short; mirror. Stop above the candle."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    above, below = [], []
    for i in range(2, len(c)):
        age = p["max_age"]
        above = [z for z in above if i - z[2] <= age]
        below = [z for z in below if i - z[2] <= age]
        if _ok(b, i, p):
            if fin(ph[i]) and h[i] > ph[i]:
                for z in above:
                    if z[0] > ph[i] and h[i] >= z[0] and c[i] < min(z[0], ph[i]):
                        _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], a[i]), None)
                        break
            if fin(pl[i]) and l[i] < pl[i]:
                for z in below:
                    if z[1] < pl[i] and l[i] <= z[1] and c[i] > max(z[1], pl[i]):
                        _emit(out, i, 1, F.stop_dist(1, c[i], l[i], a[i]), None)
                        break
        # a gap touched is spent
        above = [z for z in above if h[i] < z[0] or i == z[2]]
        below = [z for z in below if l[i] > z[1] or i == z[2]]
        if bear[i]:
            above.append((glo[i], ghi[i], i))
        if bull[i]:
            below.append((glo[i], ghi[i], i))
    return out


def box_theory(bars, ctx, p):
    """#70 (2026-05-18, 890k views). Yesterday's high/low box and its midline:
    sell only in the top `zone` of the box, buy only in the bottom, on a
    rejection candle (engulfing or a wick >= 2x the body), optionally with
    volume >= `vol_mult` x the 20-bar mean. `mid_touch`: the day opened inside
    the box and already touched the midline (the video's best days). Stop
    beyond the candle; `level` = the midline."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdh, pdl, dop = d["pdh"].tolist(), d["pdl"].tolist(), d["open"].tolist()
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    body = F.body(bars, ctx).tolist()
    vm = numpy.concatenate(([NAN], F.sma(b["v"], 20)[:-1])).tolist()
    v = b["L"]["v"]
    out = {}
    for i in range(1, len(c)):
        H, Lo = pdh[i], pdl[i]
        if not (fin(H) and H > Lo) or not _ok(b, i, p):
            continue
        rng = H - Lo
        mid = (H + Lo) / 2
        if p["vol_mult"] and not (fin(vm[i]) and v[i] >= p["vol_mult"] * vm[i]):
            continue
        if p["mid_touch"] and not (Lo < dop[i] < H and rl[i - 1] <= mid <= rh[i - 1]):
            continue
        up_wick = h[i] - max(o[i], c[i])
        dn_wick = min(o[i], c[i]) - l[i]
        bear_rej = (c[i] < o[i] and c[i] < min(o[i - 1], c[i - 1]) and c[i - 1] > o[i - 1]) or up_wick >= 2 * body[i]
        bull_rej = (c[i] > o[i] and c[i] > max(o[i - 1], c[i - 1]) and c[i - 1] < o[i - 1]) or dn_wick >= 2 * body[i]
        if h[i] >= H - p["zone"] * rng and bear_rej and c[i] < H:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], a[i]), F.tgt(-1, c[i], mid))
        elif l[i] <= Lo + p["zone"] * rng and bull_rej and c[i] > Lo:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], a[i]), F.tgt(1, c[i], mid))
    return out


def prior_poc_reaction(bars, ctx, p):
    """#75 (2026-05-12). The POCs of the last `days` sessions as zones; a
    reaction candle there -- a hammer (lower wick >= 2x body) through a POC
    from above, or a shooting star from below -> trade the rejection. Stop
    beyond the wick."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    prof = F.day_profile(bars, ctx)
    pocs = [F.prev_day(bars, ctx, prof["poc"], lag).tolist() for lag in range(1, p["days"] + 1)]
    body = F.body(bars, ctx).tolist()
    out = {}
    for i in range(len(c)):
        if not _ok(b, i, p):
            continue
        up_w = h[i] - max(o[i], c[i])
        dn_w = min(o[i], c[i]) - l[i]
        for arr in pocs:
            z = arr[i]
            if not fin(z):
                continue
            if dn_w >= 2 * body[i] and l[i] <= z < min(o[i], c[i]):
                _emit(out, i, 1, F.stop_dist(1, c[i], l[i], a[i]), None)
                break
            if up_w >= 2 * body[i] and h[i] >= z > max(o[i], c[i]):
                _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], a[i]), None)
                break
    return out


def alternating_sequence(bars, ctx, p):
    """#76 (2026-05-12). After `n` candles alternating colour, bet the next one
    repeats the last colour (`follow`) or keeps alternating (`fade`), held one
    or two bars (`time_1` / `time_2`). Stop 1 ATR. The video's own caveat:
    most sequence counts have too few occurrences to mean anything."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["n"]
    col = numpy.sign(b["c"] - b["o"]).tolist()
    out = {}
    for i in range(n, len(c)):
        seq = col[i - n + 1:i + 1]
        if 0 in seq or not all(seq[j] != seq[j - 1] for j in range(1, n)):
            continue
        if not _ok(b, i, p):
            continue
        side = int(seq[-1]) if p["direction"] == "follow" else -int(seq[-1])
        _emit(out, i, side, a[i], None)
    return out


def unicorn_breaker(bars, ctx, p):
    """#81 (2026-05-07), #300, #381. A swing high (`k`) is swept, then price
    displaces below the swing low that preceded it, leaving a bearish gap; the
    breaker is the last up-close candle before the sweep run. A retrace into
    the gap where it overlaps the breaker, closing back below -> short; mirror.
    Stop above the gap / breaker."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    st = {1: None, -1: None}
    last_up = last_dn = None
    for i in range(2, len(c)):
        if fin(ph[i]) and h[i] > ph[i] and st[-1] is None and fin(pl[i]):
            st[-1] = {"t": i, "brk": last_up, "base": pl[i], "zone": None}
        if fin(pl[i]) and l[i] < pl[i] and st[1] is None and fin(ph[i]):
            st[1] = {"t": i, "brk": last_dn, "base": ph[i], "zone": None}
        for side in (1, -1):
            s = st[side]
            if s is None:
                continue
            if i - s["t"] > 30 or s["brk"] is None:
                st[side] = None
                continue
            if s["zone"] is None:
                if side == -1 and c[i] < s["base"] and bear[i]:
                    s["zone"] = (glo[i], ghi[i], i)
                elif side == 1 and c[i] > s["base"] and bull[i]:
                    s["zone"] = (glo[i], ghi[i], i)
                continue
            zlo, zhi, t = s["zone"]
            blo, bhi = s["brk"]
            if i == t or not (zlo <= bhi and zhi >= blo) or not _ok(b, i, p):
                continue
            if side == -1 and h[i] >= zlo and c[i] < zlo:
                if _emit(out, i, -1, F.stop_dist(-1, c[i], max(zhi, bhi), a[i]), None):
                    st[side] = None
            elif side == 1 and l[i] <= zhi and c[i] > zhi:
                if _emit(out, i, 1, F.stop_dist(1, c[i], min(zlo, blo), a[i]), None):
                    st[side] = None
        if c[i] > o[i]:
            last_up = (l[i], h[i])
        elif c[i] < o[i]:
            last_dn = (l[i], h[i])
    return out


def silver_bullet(bars, ctx, p):
    """#92 (2026-04-29), #159. Inside the New York window (`10_11` = 10:00-11:00,
    `0930_11` = 09:30-11:00): a sweep of the last `k` swing, then a gap back the
    other way, then a retrace into the gap -> enter. Stop beyond the sweep
    (`sweep`) or 1 ATR (the video's fixed 5 handles); `level` = the day's
    opposite extreme so far (the "opposing liquidity")."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    real = b["L"]["real"]
    lo_m = 10 * 60 if p["window"] == "10_11" else 9 * 60 + 30
    hi_m = 11 * 60
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    d = F.days(bars, ctx)
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = None
    st = {1: None, -1: None}
    for i in range(len(c)):
        if day[i] != cur:
            cur, st = day[i], {1: None, -1: None}
        if not lo_m <= real[i] < hi_m:
            continue
        if fin(ph[i]) and h[i] > ph[i] and st[-1] is None:
            st[-1] = {"ext": h[i], "gap": None}
        if fin(pl[i]) and l[i] < pl[i] and st[1] is None:
            st[1] = {"ext": l[i], "gap": None}
        for side in (1, -1):
            s = st[side]
            if s is None:
                continue
            s["ext"] = min(s["ext"], l[i]) if side == 1 else max(s["ext"], h[i])
            if s["gap"] is None:
                if (side == -1 and bear[i]) or (side == 1 and bull[i]):
                    s["gap"] = (glo[i], ghi[i], i)
                continue
            zlo, zhi, t = s["gap"]
            if i == t or not _ok(b, i, p):
                continue
            if (side == -1 and h[i] >= zlo and c[i] < zhi) or (side == 1 and l[i] <= zhi and c[i] > zlo):
                stop = F.stop_dist(side, c[i], s["ext"], a[i]) if p["stop"] == "sweep" else a[i]
                target = rl[i] if side == -1 else rh[i]
                if _emit(out, i, side, stop, F.tgt(side, c[i], target)):
                    st[side] = None
    return out


def po3_midnight(bars, ctx, p):
    """#99 (2026-04-24). The New York midnight open. With a bullish bias
    (`prev_day`: yesterday closed up; `none`: either side) only buy BELOW the
    midnight open: a sweep of the last `k` swing low, then a close above the
    last swing high with a bullish gap in the last 3 bars. Mirror above it.
    Stop under the sweep."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "midnight")
    M = w["op"].tolist()
    d = F.days(bars, ctx)
    pdo, pdc = d["pdo"].tolist(), d["pdc"].tolist()
    pv = F.pivots(bars, ctx, p["k"])
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = ls = hs = None
    for i in range(2, len(c)):
        if day[i] != cur:
            cur, ls, hs = day[i], None, None
        m = M[i]
        if not fin(m):
            continue
        bias = 0
        if p["bias"] == "prev_day" and fin(pdc[i]):
            bias = 1 if pdc[i] > pdo[i] else -1
        if fin(pl[i]) and l[i] < pl[i]:
            ls = l[i] if ls is None else min(ls, l[i])
        if fin(ph[i]) and h[i] > ph[i]:
            hs = h[i] if hs is None else max(hs, h[i])
        if not _ok(b, i, p):
            continue
        if ls is not None and bias >= 0 and c[i] < m and fin(ph[i]) and c[i] > ph[i] and (bull[i] or bull[i - 1] or bull[i - 2]):
            if _emit(out, i, 1, F.stop_dist(1, c[i], ls, a[i]), None):
                ls = None
        if hs is not None and bias <= 0 and c[i] > m and fin(pl[i]) and c[i] < pl[i] and (bear[i] or bear[i - 1] or bear[i - 2]):
            if _emit(out, i, -1, F.stop_dist(-1, c[i], hs, a[i]), None):
                hs = None
    return out


def ny_vwap_pullback(bars, ctx, p):
    """#105 (2026-04-21), #95. After the `or_min` opening range, inside the
    next `window` minutes: a new low of day below the range, a retrace up to
    the session VWAP and a bearish candle closing back under it -> short
    ("directional"); mirror. Stop `stop_mult` x ATR; `level` = the day's low so
    far (the "next lower low")."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    vw = F.vwap(bars, ctx).tolist()
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    d = F.days(bars, ctx)
    rh, rl = d["run_h"].tolist(), d["run_l"].tolist()
    since = b["L"]["since"]
    end = p["or_min"] + p["window"]
    out = {}
    for i in range(1, len(c)):
        if not fin(Rh[i]) or since[i] >= end or not _ok(b, i, p):
            continue
        if rl[i - 1] < Rl[i] and h[i] >= vw[i] > c[i] and c[i] < o[i]:
            _emit(out, i, -1, p["stop_mult"] * a[i], F.tgt(-1, c[i], rl[i - 1]))
        elif rh[i - 1] > Rh[i] and l[i] <= vw[i] < c[i] and c[i] > o[i]:
            _emit(out, i, 1, p["stop_mult"] * a[i], F.tgt(1, c[i], rh[i - 1]))
    return out


def supply_demand(bars, ctx, p):
    """#64 (2026-05-22), #112 (158k), #152. A zone = the `base_len` quiet
    candles before an impulse bar whose range is >= `impulse` x the base's and
    that closes through the last 5-bar swing (the videos' "imbalance 2x the
    base, removes opposing structure"). The first return into the zone that
    closes back out -> trade it; stop beyond the zone; the videos targeted 3R."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n = p["base_len"]
    pv = F.pivots(bars, ctx, 5)
    ph, pl = pv["h"].tolist(), pv["l"].tolist()
    out = {}
    zones = []                       # (side, lo, hi, born)
    for i in range(n + 1, len(c)):
        ai = a[i]
        keep = []
        for z in zones:
            side, lo, hi, t = z
            if i - t > 100 or (side == 1 and c[i] < lo) or (side == -1 and c[i] > hi):
                continue
            if i > t and _ok(b, i, p) and ((side == 1 and l[i] <= hi and c[i] > hi) or
                                           (side == -1 and h[i] >= lo and c[i] < lo)):
                _emit(out, i, side, F.stop_dist(side, c[i], lo if side == 1 else hi, ai), None)
                continue                      # a zone trades once
            keep.append(z)
        zones = keep[-20:]
        blo, bhi = min(l[i - n:i]), max(h[i - n:i])
        brng = bhi - blo
        if not brng > 0:
            continue
        if h[i] - l[i] >= p["impulse"] * brng:
            if c[i] > o[i] and fin(ph[i]) and c[i] > ph[i]:
                zones.append((1, blo, bhi, i))
            elif c[i] < o[i] and fin(pl[i]) and c[i] < pl[i]:
                zones.append((-1, blo, bhi, i))
    return out


def amd_fvg(bars, ctx, p):
    """#115 (2026-04-09, 992k views), #85. Accumulation = the last `n` bars
    within `width` ATR; manipulation = a break of one side within `m` bars;
    distribution = a gap back the other way within `m` more bars -> enter at
    the gap. Stop beyond the manipulation extreme; the video's 1:2."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    n, m = p["n"], p["m"]
    bh = numpy.concatenate(([NAN], F.rolling_max(b["h"], n)[:-1])).tolist()
    bl = numpy.concatenate(([NAN], F.rolling_min(b["l"], n)[:-1])).tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    out = {}
    box = None
    man = None
    for i in range(n + 1, len(c)):
        ai = a[i]
        if not ai > 0:
            continue
        if man is None and fin(bh[i]) and bh[i] - bl[i] <= p["width"] * ai:
            box = (bh[i], bl[i], i)
        if box and man is None and i - box[2] <= m:
            if l[i] < box[1]:
                man = (1, l[i], i)
            elif h[i] > box[0]:
                man = (-1, h[i], i)
        if man is None:
            continue
        side, ext, t = man
        ext = min(ext, l[i]) if side == 1 else max(ext, h[i])
        man = (side, ext, t)
        if i - t > m:
            man, box = None, None
            continue
        if i > t and _ok(b, i, p) and ((side == 1 and bull[i]) or (side == -1 and bear[i])):
            if _emit(out, i, side, F.stop_dist(side, c[i], ext, ai), None):
                man, box = None, None
    return out


def fvg_violation(bars, ctx, p):
    """#118 (2026-04-06), #304. A gap of at least `th` ATR is closed through
    within `max_age` bars (a bearish gap closed above) -> trade the violation;
    mirror. Stop under the violation candle / gap."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    out = {}
    zones = []
    for i in range(2, len(c)):
        ai = a[i]
        keep = []
        for side, lo, hi, t in zones:
            if i - t > p["max_age"]:
                continue
            if side == -1 and c[i] > hi:            # bearish gap violated
                if _ok(b, i, p):
                    _emit(out, i, 1, F.stop_dist(1, c[i], min(l[i], lo), ai), None)
                continue
            if side == 1 and c[i] < lo:
                if _ok(b, i, p):
                    _emit(out, i, -1, F.stop_dist(-1, c[i], max(h[i], hi), ai), None)
                continue
            keep.append((side, lo, hi, t))
        zones = keep[-30:]
        if ai > 0:
            if bear[i] and ghi[i] - glo[i] >= p["th"] * ai:
                zones.append((-1, glo[i], ghi[i], i))
            if bull[i] and ghi[i] - glo[i] >= p["th"] * ai:
                zones.append((1, glo[i], ghi[i], i))
    return out


def htf_trend_fakeout(bars, ctx, p):
    """#121 (2026-04-02), #152. Daily trend = yesterday's close vs the mean of
    the last `trend_n` daily closes. After the open, within `window` minutes, a
    fake-out below today's open (in an uptrend), then a bullish gap, then a
    pullback into it -> long; mirror. Stop under the fake-out low; `level` =
    the previous day's high (low)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    dc = d["d_close"]
    ma = F.sma(dc, p["trend_n"])
    trend = numpy.sign(dc - ma)
    tr = F.prev_day(bars, ctx, trend).tolist()
    dop, rl, rh = d["open"].tolist(), d["run_l"].tolist(), d["run_h"].tolist()
    pdh, pdl = d["pdh"].tolist(), d["pdl"].tolist()
    since = b["L"]["since"]
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = gap = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, gap = day[i], None
        t = tr[i]
        if not fin(t) or t == 0 or since[i] >= p["window"]:
            continue
        faked = rl[i] < dop[i] if t > 0 else rh[i] > dop[i]
        if not faked:
            continue
        if gap is None:
            if (t > 0 and bull[i]) or (t < 0 and bear[i]):
                gap = (glo[i], ghi[i], i)
            continue
        zlo, zhi, gt = gap
        if i == gt or not _ok(b, i, p):
            continue
        if t > 0 and l[i] <= zhi and c[i] > zlo:
            _emit(out, i, 1, F.stop_dist(1, c[i], rl[i], a[i]), F.tgt(1, c[i], pdh[i]))
        elif t < 0 and h[i] >= zlo and c[i] < zhi:
            _emit(out, i, -1, F.stop_dist(-1, c[i], rh[i], a[i]), F.tgt(-1, c[i], pdl[i]))
    return out


def first_break_fvg(bars, ctx, p):
    """#126 (2026-03-27), #149. The first `or_min` minutes' high/low; a strong
    breakout candle (body >= half its range) through one side; a gap in the
    break direction within `max_bars`; a pullback into the gap closing back out
    -> enter. Stop beyond the breakout candle (#149: "the first candle that
    closed outside"). LuxAlgo's own 1-year GBPJPY test of #149: does not work."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    body = F.body(bars, ctx).tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = brk = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, brk = day[i], None
        hi, lo = Rh[i], Rl[i]
        if not fin(hi):
            continue
        strong = body[i] >= 0.5 * (h[i] - l[i])
        if brk is None:
            if c[i] > hi and strong:
                brk = {"side": 1, "t": i, "ref": l[i], "gap": (glo[i], ghi[i], i) if bull[i] else None}
            elif c[i] < lo and strong:
                brk = {"side": -1, "t": i, "ref": h[i], "gap": (glo[i], ghi[i], i) if bear[i] else None}
            continue
        side = brk["side"]
        if brk["gap"] is None:
            if i - brk["t"] > p["max_bars"]:
                continue
            if (side == 1 and bull[i]) or (side == -1 and bear[i]):
                brk["gap"] = (glo[i], ghi[i], i)
            continue
        zlo, zhi, t = brk["gap"]
        if i == t or not _ok(b, i, p):
            continue
        if side == 1 and l[i] <= zhi and c[i] > zhi:
            _emit(out, i, 1, F.stop_dist(1, c[i], brk["ref"], a[i]), None)
        elif side == -1 and h[i] >= zlo and c[i] < zlo:
            _emit(out, i, -1, F.stop_dist(-1, c[i], brk["ref"], a[i]), None)
    return out


def session_open_reaction(bars, ctx, p):
    """#129 (2026-03-25). A session open as a level (`rth_open` = today's
    session open, `london_open` = 03:00 New York, `midnight` = 00:00 New York);
    a candle wicks through it and closes back with a body in the other
    direction -> trade the rejection. Stop beyond the wick; the video took ~1:1."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    if p["level"] == "rth_open":
        d = F.days(bars, ctx)
        LV = d["open"].tolist()
        first = b["first"].tolist()
    else:
        LV = F.clock_window(bars, ctx, p["level"])["op"].tolist()
        first = [False] * len(c)
    out = {}
    for i in range(len(c)):
        z = LV[i]
        if first[i] or not fin(z) or not _ok(b, i, p):
            continue
        if l[i] < z < c[i] and c[i] > o[i]:
            _emit(out, i, 1, F.stop_dist(1, c[i], l[i], a[i]), None)
        elif h[i] > z > c[i] and c[i] < o[i]:
            _emit(out, i, -1, F.stop_dist(-1, c[i], h[i], a[i]), None)
    return out


def key_levels_orb(bars, ctx, p):
    """#134 (2026-03-21). The first close out of the `or_min` opening range, in
    the direction of the latest 3-bar structure break (`struct`) or either
    way (`none`) -> target the nearest untouched liquidity beyond it: the
    overnight or previous day's high (low). Stop at the range midpoint."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    r = F.opening_range(bars, ctx, p["or_min"])
    Rh, Rl = r["hi"].tolist(), r["lo"].tolist()
    d = F.days(bars, ctx)
    cands_hi = (d["on_h"].tolist(), d["pdh"].tolist())
    cands_lo = (d["on_l"].tolist(), d["pdl"].tolist())
    tr = F.structure(bars, ctx, 3)["trend"].tolist()
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
        if not _ok(b, i, p) or (p["bias"] == "struct" and tr[i] != side):
            continue
        levels = [arr[i] for arr in (cands_hi if side == 1 else cands_lo)
                  if fin(arr[i]) and side * (arr[i] - c[i]) > 0]
        if not levels:
            continue
        near = min(levels) if side == 1 else max(levels)
        _emit(out, i, side, F.stop_dist(side, c[i], (hi + lo) / 2, a[i]), F.tgt(side, c[i], near))
    return out


def london_range(bars, ctx, p):
    """#148 (2026-03-12), #339, #410. The London session range (03:00-08:00 New
    York). `reversal` (#148): the low is swept and closes back, then a close
    above the sweep candle's high (the order-block bounce) -> long toward the
    London high. `continuation` (#339/#410): a close below the London low, then
    a retrace into the next bearish gap closing back under it -> short. Mirrors.
    Stop beyond the sweep / gap; `level` = the other side (reversal) or one
    range beyond (continuation)."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    w = F.clock_window(bars, ctx, "london")
    Hh, Ll = w["hi"].tolist(), w["lo"].tolist()
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    day = b["L"]["day"]
    out = {}
    cur = st = None
    for i in range(len(c)):
        if day[i] != cur:
            cur, st = day[i], {1: None, -1: None}
        hi, lo = Hh[i], Ll[i]
        if not (fin(hi) and fin(lo) and hi > lo):
            continue
        rng = hi - lo
        if p["mode"] == "reversal":
            if l[i] < lo <= c[i] and st[1] is None:
                st[1] = (h[i], l[i], i)
            if h[i] > hi >= c[i] and st[-1] is None:
                st[-1] = (l[i], h[i], i)
            for side in (1, -1):
                s = st[side]
                if not s or i == s[2] or i - s[2] > 10 or not _ok(b, i, p):
                    continue
                trig, ext = s[0], s[1]
                if (side == 1 and c[i] > trig) or (side == -1 and c[i] < trig):
                    if _emit(out, i, side, F.stop_dist(side, c[i], ext, a[i]),
                             F.tgt(side, c[i], hi if side == 1 else lo)):
                        st[side] = False
        else:
            if c[i] < lo and st[-1] is None:
                st[-1] = {"gap": None}
            if c[i] > hi and st[1] is None:
                st[1] = {"gap": None}
            for side in (1, -1):
                s = st[side]
                if not s:
                    continue
                if s["gap"] is None:
                    if (side == -1 and bear[i]) or (side == 1 and bull[i]):
                        s["gap"] = (glo[i], ghi[i], i)
                    continue
                zlo, zhi, t = s["gap"]
                if i == t or not _ok(b, i, p):
                    continue
                if (side == -1 and h[i] >= zlo and c[i] < zlo) or (side == 1 and l[i] <= zhi and c[i] > zhi):
                    ref = zhi if side == -1 else zlo
                    target = lo - rng if side == -1 else hi + rng
                    if _emit(out, i, side, F.stop_dist(side, c[i], ref, a[i]), F.tgt(side, c[i], target)):
                        st[side] = False
    return out


def prior_day_direction(bars, ctx, p):
    """#349 (2024-06-03), #445 (224k). Assume today follows yesterday's candle
    colour and take only reversal signals that way: RSI(14) crossing back up
    through 40 (`rsi_cross`) or a 3-bar structure break up (`bos`) after a
    pullback. Stop under the last 3-bar swing."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    pdo, pdc = d["pdo"].tolist(), d["pdc"].tolist()
    r = F.rsi(bars, ctx, 14).tolist()
    s = F.structure(bars, ctx, 3)
    up, dn = s["up"].tolist(), s["dn"].tolist()
    pv = F.pivots(bars, ctx, 3)
    pl, ph = pv["l"].tolist(), pv["h"].tolist()
    out = {}
    for i in range(1, len(c)):
        if not fin(pdc[i]) or not _ok(b, i, p):
            continue
        bias = 1 if pdc[i] > pdo[i] else -1
        if p["signal"] == "rsi_cross":
            go = (bias == 1 and r[i - 1] < 40 <= r[i]) or (bias == -1 and r[i - 1] > 60 >= r[i])
        else:
            go = (bias == 1 and up[i]) or (bias == -1 and dn[i])
        if go:
            ref = pl[i] if bias == 1 else ph[i]
            _emit(out, i, bias, F.stop_dist(bias, c[i], ref, a[i]), None)
    return out


def first_session_fvg(bars, ctx, p):
    """#356 (2024-05-13), #398. Only the session's first gap. A bearish gap: a
    candle closes above it, and the next candle closes back below that candle's
    open -> short; mirror for a bullish gap. The video exits when the MACD
    histogram turns (`exit_mode` signal). Stop beyond the two candles."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    g = F.fvg(bars, ctx)
    bull, bear = g["bull"].tolist(), g["bear"].tolist()
    glo, ghi = g["lo"].tolist(), g["hi"].tolist()
    pos = b["L"]["pos"]
    day = b["L"]["day"]
    out = {}
    cur = gap = None
    for i in range(1, len(c)):
        if day[i] != cur:
            cur, gap = day[i], None
        if gap is None:
            if pos[i] >= 2 and (bull[i] or bear[i]):
                gap = (-1 if bear[i] else 1, glo[i], ghi[i], i)
            continue
        side, lo, hi, t = gap
        if i - 1 <= t or not _ok(b, i, p):
            continue
        if side == -1 and c[i - 1] > hi and c[i] < o[i - 1]:
            stop = F.stop_dist(-1, c[i], max(h[i], h[i - 1]), a[i]) if p["stop"] == "candle" else a[i]
            _emit(out, i, -1, stop, None)
        elif side == 1 and c[i - 1] < lo and c[i] > o[i - 1]:
            stop = F.stop_dist(1, c[i], min(l[i], l[i - 1]), a[i]) if p["stop"] == "candle" else a[i]
            _emit(out, i, 1, stop, None)
    return out


def _macd_hist(bars, ctx):
    def build():
        cl = F.base(bars, ctx)["c"]
        def e(x, n):
            out = numpy.empty_like(x)
            acc = x[0]
            al = 2.0 / (n + 1)
            for i in range(len(x)):
                acc += al * (x[i] - acc)
                out[i] = acc
            return out
        line = e(cl, 12) - e(cl, 26)
        return line - e(line, 9)
    return F.cached(ctx, "tt_macd_hist", build)


def macd_turn_exit(index, bars, ctx, params, side):
    hist = _macd_hist(bars, ctx)
    if index < 1:
        return False
    return (side == -1 and hist[index] > hist[index - 1]) or \
           (side == 1 and hist[index] < hist[index - 1])


def friday_monday(bars, ctx, p):
    """#153 (2026-03-10). If Friday's high stays under Thursday's, Monday
    revisits Friday's low (their count: 24/25; LuxAlgo's own check ~60%). Enter
    at Monday's first bar toward Friday's low (`level`); `both` also takes the
    mirror (Friday's low above Thursday's -> Friday's high). Stop at Friday's
    other extreme (`fri_extreme`) or 2 ATR."""
    b, o, h, l, c = _lists(bars, ctx)
    a = _atr(bars, ctx)
    d = F.days(bars, ctx)
    wd = d["weekday"]
    idx = d["day_idx"]
    wprev = numpy.full(len(c), -1)
    wprev2 = numpy.full(len(c), -1)
    wprev[idx >= 1] = wd[d["d_start"][idx[idx >= 1] - 1]]
    wprev2[idx >= 2] = wd[d["d_start"][idx[idx >= 2] - 2]]
    pdh, pdl, ppdh, ppdl = (d["pdh"].tolist(), d["pdl"].tolist(),
                            d["ppdh"].tolist(), d["ppdl"].tolist())
    first = b["first"].tolist()
    wd, wprev, wprev2 = wd.tolist(), wprev.tolist(), wprev2.tolist()
    out = {}
    for i in range(len(c)):
        if not first[i] or wd[i] != 0 or wprev[i] != 4 or wprev2[i] != 3:
            continue
        if not _ok(b, i, p):
            continue
        if pdh[i] < ppdh[i]:
            ref = pdh[i] if p["stop"] == "fri_extreme" else c[i] + 2 * a[i]
            _emit(out, i, -1, F.stop_dist(-1, c[i], ref, a[i]), F.tgt(-1, c[i], pdl[i]))
        elif p["side"] == "both" and pdl[i] > ppdl[i]:
            ref = pdl[i] if p["stop"] == "fri_extreme" else c[i] - 2 * a[i]
            _emit(out, i, 1, F.stop_dist(1, c[i], ref, a[i]), F.tgt(1, c[i], pdh[i]))
    return out


# =========================================================================== #
# registry: name -> (compute, axes, needs_ny_clock, exit_signal)
# =========================================================================== #

RR = ("rr_1", "rr_2", "rr_3")
RR_LVL = ("rr_1", "rr_2", "rr_3", "level")
RR_MF = ("rr_1", "rr_2", "level_mid", "level_far")

#: Newest first, as on the profile. Axes values are tuples; `last` is the
#: shared entry cutoff (`cfd_families.axes`' two values: 2h and 1h before close).
SPECS = {
    "lux_structure_poc": (structure_poc, lambda g: {
        "k": (3, 5), "n_bos": (2, 3), "stop": ("atr_2", "swing"),
        "exit_mode": RR_LVL, "last_entry_minute": g["last"]}, False, None),
    "lux_no_wick_retest": (no_wick_retest, lambda g: {
        "tol": (0.0, 0.05), "max_age": (24, 96), "directional": (0, 1),
        "trend_f": ("none", "ema50", "ema200"), "stop": ("atr_1", "swing"),
        "exit_mode": ("rr_1", "rr_2"), "last_entry_minute": g["last"]}, False, None),
    "lux_session_sweep_bos": (session_sweep_bos, lambda g: {
        "window": ("asia", "sydney", "london", "overnight"), "k": (2, 3, 5),
        "max_bars": (6, 12, 24),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2", "level_mid", "level_far"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_htf_stoch_bucket": (htf_stoch_bucket, lambda g: {
        "htf_m": (4, 8, 12), "zone": (75, 80), "rsi_n": (9, 14),
        "stop": ("atr_1", "swing"), "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_value_area_reversion": (value_area_reversion, lambda g: {
        "vol_decline": (0, 1), "engulf": ("body", "range"),
        "exit_mode": RR_MF, "last_entry_minute": g["last"]}, False, None),
    "lux_eight_am_range": (eight_am_range, lambda g: {
        "confirm": ("close_in", "bos"), "k": (2, 3), "exit_mode": RR_MF,
        "last_entry_minute": g["last"]}, True, None),
    "lux_eight_am_roadmap": (eight_am_roadmap, lambda g: {
        "entry": ("mid", "edge"), "stop_atr": (1.0, 2.0),
        "exit_mode": ("rr_2", "rr_3", "rr_4"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_equal_levels": (equal_levels, lambda g: {
        "mode": ("breakout", "sweep"), "k": (3, 5), "tol": (0.1, 0.25),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_rsi_divergence": (rsi_divergence, lambda g: {
        "rsi_n": (9, 14), "k": (3, 5), "regime": ("any", "range"),
        "confirm": ("none", "break", "fvg"),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_vwap_ema": (vwap_ema, lambda g: {
        "ema_n": (9, 21), "stop": ("candle", "atr_1"),
        "exit_mode": ("signal", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, vwap_ema_exit),
    "lux_htf_liquidity_fvg": (htf_liquidity_fvg, lambda g: {
        "htf_k": (6, 12), "k": (2, 3), "exit_mode": ("rr_2", "rr_3", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_sweep_ifvg": (sweep_ifvg, lambda g: {
        "k": (3, 5, 10), "entry": ("ifvg", "fvg_retrace"), "w": (5, 10),
        "trend_ma": (0, 100), "exit_mode": RR_LVL,
        "last_entry_minute": g["last"]}, False, None),
    "lux_break_gap_reaction": (break_gap_reaction, lambda g: {
        "k": (3, 5), "max_age": (20, 50), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_trend_pullback": (trend_pullback, lambda g: {
        "variant": ("fib50", "golden", "sweep_shift"), "ema": (0, 50, 200),
        "k": (3, 5), "exit_mode": RR_LVL,
        "last_entry_minute": g["last"]}, False, None),
    "lux_htf_manipulation": (htf_manipulation, lambda g: {
        "htf_m": (2, 4, 8), "close_above": ("high", "close"), "prior": (0, 1),
        "ema": (0, 200), "stop": ("candle", "atr_2"), "exit_mode": RR,
        "last_entry_minute": g["last"]}, False, None),
    "lux_orb_retest": (orb_retest, lambda g: {
        "or_min": (5, 15, 30), "confirm": ("close", "engulf"),
        "stop": ("swing", "range"), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_orb_breakout": (orb_breakout, lambda g: {
        "or_min": (15, 30, 60), "vol_mult": (0.0, 1.5), "stop": ("mid", "far"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "lux_swing_sweep_mss": (swing_sweep_mss, lambda g: {
        "k": (3, 5), "w": (5, 10), "entry": ("mss", "fib50"), "ema": (0, 200),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_sweep_reclaim": (sweep_reclaim, lambda g: {
        "level": ("swing5", "swing10", "prev_day", "overnight"),
        "confirm_bars": (1, 2), "exit_mode": RR_LVL,
        "last_entry_minute": g["last"]}, False, None),
    "lux_cisd": (cisd, lambda g: {
        "htf_m": (4, 12), "killzone": ("all", "ny_am"),
        "exit_mode": ("rr_2", "rr_2.5", "rr_4", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_open_candle_fade": (open_candle_fade, lambda g: {
        "or_min": (15, 30), "frac": (0.25, 0.4),
        "exit_mode": ("level_382", "level_50", "level_618", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_trendline_break": (trendline_break, lambda g: {
        "k": (3, 5), "touches": (2, 3), "tol": (0.25, 0.5),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_rubber_band": (rubber_band, lambda g: {
        "n": (8, 16), "width": (2.5, 4.0), "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_gap_fill_breakout": (gap_fill_breakout, lambda g: {
        "k": (5, 10), "max_dist": (3.0, 6.0),
        "exit_mode": ("rr_1", "rr_2", "level_near", "level_far"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_three_step_trap": (three_step_trap, lambda g: {
        "src": ("or15", "or30", "box16"), "m": (3, 6), "exit_mode": RR_MF,
        "last_entry_minute": g["last"]}, False, None),
    "lux_momentum_flip": (momentum_flip, lambda g: {
        "n_shrink": (2, 3), "near_level": (0, 1),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_body_momentum": (body_momentum, lambda g: {
        "mult": (1.5, 2.0, 2.5), "stop": ("swing", "atr_1"),
        "exit_mode": ("signal", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, body_momentum_exit),
    "lux_first_hour_sweep": (first_hour_sweep, lambda g: {
        "range_min": (30, 60), "window": (60, 120), "entry": ("close", "fvg"),
        "exit_mode": RR_MF, "last_entry_minute": g["last"]}, False, None),
    "lux_range_breakout_retest": (range_breakout_retest, lambda g: {
        "n": (12, 24), "width": (3.0, 5.0), "stop": ("far", "mid"),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_rsi_50_pullback": (rsi_50_pullback, lambda g: {
        "rsi_n": (9, 14), "ob": (65, 70, 75), "stop": ("swing", "atr_1"),
        "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "lux_fvg_sweep_wick": (fvg_sweep_wick, lambda g: {
        "k": (3, 5), "max_age": (20, 60), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_box_theory": (box_theory, lambda g: {
        "zone": (0.25, 0.35), "vol_mult": (0.0, 2.0), "mid_touch": (0, 1),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_prior_poc_reaction": (prior_poc_reaction, lambda g: {
        "days": (1, 2, 3), "exit_mode": RR, "last_entry_minute": g["last"]}, False, None),
    "lux_alternating_sequence": (alternating_sequence, lambda g: {
        "n": (3, 4, 6), "direction": ("follow", "fade"),
        "exit_mode": ("time_1", "time_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_unicorn_breaker": (unicorn_breaker, lambda g: {
        "k": (3, 5), "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_silver_bullet": (silver_bullet, lambda g: {
        "window": ("10_11", "0930_11"), "k": (2, 3), "stop": ("sweep", "atr_1"),
        "exit_mode": ("rr_2", "rr_3", "level"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_po3_midnight": (po3_midnight, lambda g: {
        "k": (2, 3), "bias": ("prev_day", "none"), "exit_mode": ("rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_ny_vwap_pullback": (ny_vwap_pullback, lambda g: {
        "or_min": (15, 30), "window": (90, 180), "stop_mult": (1.5, 2.0),
        "exit_mode": ("rr_1", "rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_supply_demand": (supply_demand, lambda g: {
        "base_len": (1, 2, 3), "impulse": (2.0, 3.0), "exit_mode": ("rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_amd_fvg": (amd_fvg, lambda g: {
        "n": (12, 30), "width": (2.5, 4.0), "m": (5, 10),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_fvg_violation": (fvg_violation, lambda g: {
        "th": (0.1, 0.25, 0.5), "max_age": (10, 30),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_htf_trend_fakeout": (htf_trend_fakeout, lambda g: {
        "trend_n": (5, 20), "window": (60, 120), "exit_mode": ("rr_2", "rr_3", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_first_break_fvg": (first_break_fvg, lambda g: {
        "or_min": (5, 10, 15), "max_bars": (10, 20),
        "exit_mode": ("rr_1.5", "rr_2", "rr_3"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_session_open_reaction": (session_open_reaction, lambda g: {
        "level": ("rth_open", "london_open", "midnight"),
        "exit_mode": ("rr_1", "rr_1.5", "rr_2"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_key_levels_orb": (key_levels_orb, lambda g: {
        "or_min": (15, 30), "bias": ("struct", "none"), "exit_mode": ("rr_2", "level"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_london_range": (london_range, lambda g: {
        "mode": ("reversal", "continuation"), "exit_mode": ("rr_2", "rr_3", "level"),
        "last_entry_minute": g["last"]}, True, None),
    "lux_friday_monday": (friday_monday, lambda g: {
        "side": ("both", "short_only"), "stop": ("fri_extreme", "atr_2"),
        "exit_mode": ("level", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, None),
    "lux_first_session_fvg": (first_session_fvg, lambda g: {
        "stop": ("candle", "atr_1"), "exit_mode": ("signal", "rr_1", "rr_2"),
        "last_entry_minute": g["last"]}, False, macd_turn_exit),
    "lux_prior_day_direction": (prior_day_direction, lambda g: {
        "signal": ("rsi_cross", "bos"), "exit_mode": RR,
        "last_entry_minute": g["last"]}, False, None),
}

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("stop", "window", "engulf", "confirm", "entry", "mode",
               "regime", "variant", "close_above", "level", "src", "killzone",
               "direction", "bias", "side", "signal", "trend_f")
