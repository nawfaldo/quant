"""Trade By Data's TikTok (@tradebydata.en, @tradebydata.id) rules, as
`cfd_tt_families` families.

Source: all 51 @tradebydata.en posts (2026-10-22..11-12, every one with English
auto-captions) plus the three @tradebydata.id posts that have no English twin,
read 2026-11-13. Every post takes ONE paper or book, turns it into a rule and
backtests it 2015-Oct 2026, mostly on gold. The notes file
`results/tiktok/tradebydata_strategy_notes.md` maps every post to its family
and says what the creator added themselves (their stops and exits, usually).

Order is the profile's, newest first; `#N` = rank on the .en profile.
Skipped (notes say why): #13 three crows, #27 hourly reversal, #36 Sep/Nov,
#46 50-day high (the creator's own debunks), #28 5-second MOC move (needs
tick data), #30 12-month TSMOM (months-scale hold; the trade floor forbids it).

HOW THESE DIFFER FROM THE OTHER CREATORS. These rules live on the clock
(Tokyo 09:55, the London 16:00 fix, 13:30 COMEX close) and many hold for days
(Monday-to-Tuesday, overnight gold, always-in trend flips). Most families are
therefore registered `hold="multiday"` (the 5th SPECS element): they enter on
any bar, hold through closes and pay swap. RUN THEM WITH `EXNESS_FULL_DAY=1`, or
the bars outside the cash session do not exist and the clock events never
fire. The few that are flat by the close (#38, #43, #37) keep the session hold.
The 6th SPECS element (markets) is None: the user runs every FundedNext
symbol (2026-11-13); `_TABLE` still names the creator's own markets. Under a
`--window` only that window's bars exist: a clock event outside it never
fires, and a held position is marked only on the window's bars.

TIMES. Bars carry New York wall clock. A rule time T fires on the bar that
CONTAINS the moment just before T (start < T <= end); the engine fills at the
next bar's open, which is T when the bar size divides T. Coarse bars round the
time later: at 60m a 13:30 entry fills at 14:00. Tokyo/London/Frankfurt/Jakarta
times are converted per date with the zone database, so DST is honoured.

EXITS. `exit_mode` is `signal` almost everywhere: the family stores, per
setting, either a clock plan (exit on the bar ending at the rule's exit time)
or a regime (exit when the regime leaves the trade's side). Rules without a
stop get a disaster stop `stop_dr` x the average daily range, so the 1.5% risk
sizing still has a distance; that is ours, not the creator's. It is 1-2 daily
ranges because a wider one cannot be sized: on gold at the FundedNext $6k
balance (`CFD_BALANCE=6000`) a 4-range stop leaves most entries under 0.01 lot.

ALWAYS-IN RULES ("flip to a sell") cannot flip in one bar here: the engine reads
entries only while flat, and enters at most once per calendar day. A regime
family therefore emits its side on every bar of the regime (`fresh` = big),
so after the exit the next bar re-enters the new side; `fresh=2` is the "if
stopped out, wait for the next signal" reading. A flip on a day that already
had an entry waits for the next day.
"""
from __future__ import annotations

import bisect
import datetime
import math
import zoneinfo

import numpy

from sandbox.research import tt_features as F

NAN = float("nan")
fin = math.isfinite
TS, O, H, L, C, V = range(6)
BIG = 10 ** 6

#: Axes that never change the events -- the same set `cfd_tt_families` strips
#: before caching, so an exit lookup finds the setting compute stored.
EXIT_KEYS = frozenset(("exit_mode", "stop_day", "trend", "vol_mode",
                       "last_entry_minute", "stop_widen"))

GOLD = ("xauusd",)
JPY = ("usdjpy", "gbpjpy", "eurjpy")
USD_QUOTE = ("eurusd", "gbpusd", "audusd")        # XXXUSD: selling USD = buy
USD_BASE = ("usdjpy", "usdcad", "usdchf")          # USDXXX: selling USD = sell


# =========================================================================== #
# shared plumbing
# =========================================================================== #

def _key(name, p):
    return (name,) + tuple(sorted((k, repr(v)) for k, v in p.items()
                                  if k not in EXIT_KEYS))


def _store_exit(ctx, name, p, fn):
    ctx.setdefault("tbd_exit", {})[_key(name, p)] = fn


def _exit_for(name, compute):
    """The engine's exit hook for `name`: the plan `compute` stored."""
    def exit_signal(index, bars, ctx, params, side):
        key = _key(name, params)
        fn = ctx.get("tbd_exit", {}).get(key)
        if fn is None:
            compute(bars, ctx, dict(params))
            fn = ctx["tbd_exit"][key]
        return fn(index, side)
    exit_signal.__name__ = f"{name}_exit"
    return exit_signal


def _plan(plan, i, x):
    """Record event `i`'s exit bar; a missing one (the data ends first) holds."""
    plan[i] = BIG if x is None else x


def _plan_exit(plan):
    """Exit at the bar a time plan names for the latest entry event.

    `plan` maps event bar -> exit bar X; the entry filled at event+1, and the
    hook is asked about bar j (it exits at j+1's open), so it fires once j
    reaches X -- the bar ending at the rule's exit time."""
    keys = sorted(plan)

    def fn(j, _side):
        k = bisect.bisect_right(keys, j - 1) - 1
        return k >= 0 and j >= plan[keys[k]]
    return fn


def _regime_exit(r):
    return lambda j, side: r[j] != side


def _emit(out, i, side, dist, target=None):
    if not (dist > 0) or not fin(dist):
        return False
    if i not in out:
        out[i] = (side, dist, target)
    return True


def _atr(bars, ctx):
    return F.cached(ctx, "tt_atr_list", lambda: F.atr(bars, ctx, 14).tolist())


def _dr(ctx):
    """Average daily range per bar (the engine's own risk unit)."""
    return ctx["risk"]


def _wide(ctx, i, p):
    r = _dr(ctx)[i]
    return p["stop_dr"] * r if r and fin(r) else NAN


def _floor(dist, atr_v):
    return max(dist, 0.2 * atr_v) if fin(atr_v) else dist


# --------------------------------------------------------------------------- #
# clocks
# --------------------------------------------------------------------------- #

_NY = zoneinfo.ZoneInfo("America/New_York")
_ZONES = {"tokyo": "Asia/Tokyo", "london": "Europe/London",
          "frankfurt": "Europe/Berlin", "jakarta": "Asia/Jakarta"}


def _offset(zone, day):
    """UTC offset in seconds of `zone` at noon of epoch-day `day`."""
    d = datetime.datetime(1970, 1, 1) + datetime.timedelta(days=int(day), hours=12)
    return int(d.replace(tzinfo=zone).utcoffset().total_seconds())


def _clock(bars, ctx):
    """New York clock, the 17:00 trading day, and UTC, per bar."""
    def build():
        # Bars carry the market's own clock: New York plus `shift_hours`
        # (the Asian indices, and every non-rth `--window`, which runs +7h).
        shift = int(ctx["cfg"]["shift_hours"]) * 3600
        ts = numpy.fromiter((b[TS] for b in bars), dtype=numpy.int64,
                            count=len(bars)) - shift
        day = ts // 86400
        uniq, inv = numpy.unique(day, return_inverse=True)
        off = numpy.array([_offset(_NY, d) for d in uniq], dtype=numpy.int64)
        td = (ts + 7 * 3600) // 86400
        return {"ts": ts, "min": (ts // 60) % 1440, "day": day,
                "wd": (day + 3) % 7, "td": td, "td_wd": (td + 3) % 7,
                "utc": ts - off[inv],
                "bar": int(F.base(bars, ctx)["bar"])}
    return F.cached(ctx, "tbd_clock", build)


def _local(bars, ctx, zone):
    """Wall clock in `zone` (a `_ZONES` key) per bar: minute, day, weekday,
    day of month, month."""
    def build():
        k = _clock(bars, ctx)
        z = zoneinfo.ZoneInfo(_ZONES[zone])
        uday = k["utc"] // 86400
        uniq, inv = numpy.unique(uday, return_inverse=True)
        off = numpy.array([_offset(z, d) for d in uniq], dtype=numpy.int64)
        loc = k["utc"] + off[inv]
        day = loc // 86400
        dates = day.astype("datetime64[D]")
        months = dates.astype("datetime64[M]")
        dom = (dates - months).astype(numpy.int64) + 1
        return {"min": (loc // 60) % 1440, "day": day, "wd": (day + 3) % 7,
                "dom": dom, "month": months.astype(numpy.int64) % 12 + 1}
    return F.cached(ctx, f"tbd_local_{zone}", build)


def _at(minute, bar, t):
    """Bars whose span holds the moment just before clock minute `t`."""
    t = 1440 if t == 0 else t
    return (minute < t) & (minute + bar >= t)


def _next_at(mask_idx, e):
    """First bar index in sorted `mask_idx` strictly after `e`, or None."""
    k = bisect.bisect_right(mask_idx, e)
    return int(mask_idx[k]) if k < len(mask_idx) else None


def _first_of(arr):
    first = numpy.ones(len(arr), dtype=bool)
    first[1:] = arr[1:] != arr[:-1]
    return first


# --------------------------------------------------------------------------- #
# higher-timeframe candles, causal
# --------------------------------------------------------------------------- #

def _htf(bars, ctx, tf):
    """`tf`-minute candles aligned to the 17:00 New York rollover.

    `comp[i]` is the latest candle COMPLETE at bar i's close: bar i's own
    candle if bar i ends exactly on its boundary, else the one before. Decided
    from bar i's clock alone, so a truncated series gives the same answer."""
    def build():
        k = _clock(bars, ctx)
        b = F.base(bars, ctx)
        shifted = k["ts"] + 7 * 3600
        bucket = shifted // (tf * 60)
        aligned = (shifted + k["bar"] * 60) % (tf * 60) == 0
        _, starts, inv = numpy.unique(bucket, return_index=True, return_inverse=True)
        n = len(bars)
        ends = numpy.append(starts[1:], n) - 1
        o = b["o"][starts]
        c = b["c"][ends]
        h = numpy.maximum.reduceat(b["h"], starts)
        lo = numpy.minimum.reduceat(b["l"], starts)
        v = numpy.add.reduceat(b["v"], starts)
        comp = numpy.where(aligned, inv, inv - 1).astype(numpy.int64)
        new = numpy.zeros(n, dtype=bool)
        new[1:] = comp[1:] != comp[:-1]
        new[0] = comp[0] >= 0
        return {"o": o, "h": h, "l": lo, "c": c, "v": v, "comp": comp,
                "new": new, "end_bar": ends}
    return F.cached(ctx, f"tbd_htf_{tf}", build)


def _to_bars(h, series):
    """Per-candle `series` read at every bar (NaN before the first candle)."""
    comp = h["comp"]
    out = numpy.full(len(comp), NAN)
    ok = comp >= 0
    out[ok] = numpy.asarray(series, dtype=float)[comp[ok]]
    return out


# --------------------------------------------------------------------------- #
# indicator math on candle arrays
# --------------------------------------------------------------------------- #

def _ema(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.empty_like(x)
    a = 2.0 / (n + 1)
    acc = x[0] if len(x) else NAN
    for i in range(len(x)):
        acc += a * (x[i] - acc)
        out[i] = acc
    return out


def _wilder(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), NAN)
    if len(x) < n:
        return out
    acc = float(numpy.nanmean(x[:n]))
    out[n - 1] = acc
    for i in range(n, len(x)):
        acc += (x[i] - acc) / n
        out[i] = acc
    return out


def _sma(x, n):
    x = numpy.asarray(x, dtype=float)
    out = numpy.full(len(x), NAN)
    if n <= 0 or len(x) < n:
        return out
    cs = numpy.cumsum(numpy.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def _tr(h, l, c):
    prev = numpy.append(c[0], c[:-1])
    return numpy.maximum(h - l, numpy.maximum(abs(h - prev), abs(l - prev)))


def _rsi(c, n):
    d = numpy.diff(c, prepend=c[0])
    up, dn = _wilder(numpy.maximum(d, 0), n), _wilder(numpy.maximum(-d, 0), n)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        return numpy.where(dn > 0, 100 - 100 / (1 + up / dn), 100.0)


def _roll_max(x, n):
    return numpy.asarray(F.rolling_max(numpy.asarray(x, dtype=float), n))


def _roll_min(x, n):
    return numpy.asarray(F.rolling_min(numpy.asarray(x, dtype=float), n))


def _dmi(h, l, c, n):
    up = numpy.diff(h, prepend=h[0])
    dn = -numpy.diff(l, prepend=l[0])
    pdm = numpy.where((up > dn) & (up > 0), up, 0.0)
    mdm = numpy.where((dn > up) & (dn > 0), dn, 0.0)
    atr = _wilder(_tr(h, l, c), n)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        pdi = 100 * _wilder(pdm, n) / atr
        mdi = 100 * _wilder(mdm, n) / atr
        dx = 100 * abs(pdi - mdi) / (pdi + mdi)
    adx = _wilder(numpy.nan_to_num(dx, nan=0.0), n)
    adx[:2 * n - 1] = NAN
    return pdi, mdi, adx


def _supertrend(h, l, c, n, mult):
    atr = _wilder(_tr(h, l, c), n)
    mid = (h + l) / 2
    line = numpy.full(len(c), NAN)
    side = numpy.zeros(len(c), dtype=int)
    up_b = dn_b = NAN
    s = 1
    for i in range(len(c)):
        if not fin(atr[i]):
            continue
        ub, lb = mid[i] + mult * atr[i], mid[i] - mult * atr[i]
        if fin(dn_b) and i and c[i - 1] > dn_b:
            lb = max(lb, dn_b)
        if fin(up_b) and i and c[i - 1] < up_b:
            ub = min(ub, up_b)
        if s == 1 and c[i] < lb:
            s = -1
        elif s == -1 and c[i] > ub:
            s = 1
        up_b, dn_b = ub, lb
        side[i] = s
        line[i] = lb if s == 1 else ub
    return side, line


def _cci(h, l, c, n):
    tp = (h + l + c) / 3
    m = _sma(tp, n)
    out = numpy.full(len(c), NAN)
    for i in range(n - 1, len(c)):
        md = numpy.mean(abs(tp[i - n + 1:i + 1] - m[i]))
        out[i] = (tp[i] - m[i]) / (0.015 * md) if md > 0 else 0.0
    return out


# --------------------------------------------------------------------------- #
# regime families: one shared event/exit builder
# --------------------------------------------------------------------------- #

def _regime(name, bars, ctx, p, r, stop_at):
    """Emit `r`'s side on the first `fresh` bars of every regime run.

    `r[i]` is +1/-1/0, known at bar i's close; `stop_at(i, side)` is the stop
    distance at bar i (NaN skips). Exit: the regime leaves the trade's side."""
    r = [int(x) for x in r]
    fresh = p.get("fresh", BIG)
    out = {}
    since = BIG
    for i in range(len(r)):
        since = 0 if (i == 0 or r[i] != r[i - 1]) else since + 1
        if r[i] and since < fresh:
            _emit(out, i, r[i], stop_at(i, r[i]))
    _store_exit(ctx, name, p, _regime_exit(r))
    return out


def _htf_regime_bars(h, r_candle):
    """Candle regime read at every bar (0 before the first candle)."""
    comp = h["comp"]
    r = numpy.asarray(r_candle, dtype=int)
    out = numpy.zeros(len(comp), dtype=int)
    ok = comp >= 0
    out[ok] = r[comp[ok]]
    return out


def _hold_state(enter_long, enter_short):
    """+1 from a long trigger until a short trigger, and back (0 before any)."""
    r = numpy.zeros(len(enter_long), dtype=int)
    s = 0
    for i in range(len(r)):
        if enter_long[i] and not enter_short[i]:
            s = 1
        elif enter_short[i] and not enter_long[i]:
            s = -1
        r[i] = s
    return r


def _ext_stop(bars, ctx, h, n):
    """Stop at the extreme of the last `n` completed candles."""
    lo = _to_bars(h, _roll_min(h["l"], n))
    hi = _to_bars(h, _roll_max(h["h"], n))
    cl = F.base(bars, ctx)["c"]
    a = _atr(bars, ctx)

    def stop(i, side):
        lvl = lo[i] if side == 1 else hi[i]
        return F.stop_dist(side, cl[i], lvl, a[i]) if fin(lvl) else NAN
    return stop


def _wide_stop(ctx, p):
    return lambda i, _side: _wide(ctx, i, p)


# --------------------------------------------------------------------------- #
# day bookkeeping
# --------------------------------------------------------------------------- #

def _ny_daily_close(bars, ctx, t=960):
    """Per NY day: close of the bar ending at minute `t` (NaN when absent),
    and the bar index holding it."""
    def build():
        k = _clock(bars, ctx)
        cl = F.base(bars, ctx)["c"]
        idx = numpy.flatnonzero(_at(k["min"], k["bar"], t))
        return {int(k["day"][i]): (float(cl[i]), int(i)) for i in idx}
    return F.cached(ctx, f"tbd_nyclose_{t}", build)


def _rth_candles(bars, ctx, start=570, end=960):
    """Per NY weekday, the cash-session candle built from bars inside
    [start, end): {day: (o, h, l, c, last_bar)} -- complete only once its last
    bar has closed, which is when callers read it."""
    def build():
        k = _clock(bars, ctx)
        b = F.base(bars, ctx)
        m, B = k["min"], k["bar"]
        inside = (m + B > start) & (m < end)
        out = {}
        for i in numpy.flatnonzero(inside):
            d = int(k["day"][i])
            if d not in out:
                out[d] = [b["o"][i], b["h"][i], b["l"][i], b["c"][i], int(i)]
            else:
                row = out[d]
                row[1] = max(row[1], b["h"][i])
                row[2] = min(row[2], b["l"][i])
                row[3] = b["c"][i]
                row[4] = int(i)
        return out
    return F.cached(ctx, f"tbd_rth_{start}_{end}", build)


_BENCH = {}


def _bench_close(symbol, t=960):
    """Another market's close at NY minute `t`, per NY day: {day: close}.

    Read from its own 60m table once per process; used only for days whose
    `t` has passed at the bar asking, so nothing leaks forward."""
    hit = _BENCH.get((symbol, t))
    if hit is None:
        from sandbox.research import cfd_families as cf   # noqa: PLC0415
        if symbol not in cf.INSTRUMENTS:
            cf.resolve(symbol, allow_stale=True)
        rows = cf.all_bars(symbol, "validate", 60)
        shift = int(cf.INSTRUMENTS[symbol]["shift_hours"]) * 3600
        hit = {}
        for row in rows:
            ts = row[TS] - shift
            m = (ts // 60) % 1440
            if m < t <= m + 60:
                hit[int(ts // 86400)] = float(row[C])
        _BENCH[(symbol, t)] = hit
    return hit


def _bench_extreme(symbol, k, sd_n, t=960):
    """{day: +1/-1/0}: the benchmark's day return vs `k` x its trailing sd."""
    closes = _bench_close(symbol, t)
    days = sorted(closes)
    rets = []
    out = {}
    for a, b in zip(days, days[1:]):
        r = math.log(closes[b] / closes[a]) if closes[a] > 0 else 0.0
        if len(rets) >= sd_n:
            w = rets[-sd_n:]
            mu = sum(w) / sd_n
            sd = math.sqrt(sum((x - mu) ** 2 for x in w) / (sd_n - 1))
            out[b] = 1 if r > k * sd else (-1 if r < -k * sd else 0)
        rets.append(r)
    return out


def _bdays(y, m):
    """Business days (epoch-day ints) of month `m` of year `y`."""
    first = numpy.datetime64(f"{y:04d}-{m:02d}-01")
    nxt = (first.astype("datetime64[M]") + 1).astype("datetime64[D]")
    days = numpy.arange(first, nxt, dtype="datetime64[D]")
    days = days[numpy.is_busday(days)]
    return days.astype(numpy.int64)


# =========================================================================== #
# 2026-11 (newest first)
# =========================================================================== #

def session_box(bars, ctx, p):
    """id 7707464542355328277 (2026-11-13, gold) + #3 (2026-11-11, USDJPY).
    Mark the high/low of a night box -- `19_24`: 19:00-24:00 NY (#3), `19_03`:
    19:00-03:00 NY (= 07:00-15:00 WIB in winter, the .id post) -- then follow
    the first break: `touch` = a buy/sell stop at the edge (#3), `close` = the
    first close outside (.id, on 5m). Stop: the other side (`box`, #3) or none
    (`wide`, .id). Close at `exit_at` NY (#3: 13:30; .id: 04:00 WIB = 16:00).
    ASSUMED: the stop order fills at the next bar's open, not at the edge."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    m, B = k["min"].tolist(), k["bar"]
    hh, ll, cc = b["L"]["h"], b["L"]["l"], b["L"]["c"]
    end = 1440 if p["box"] == "19_24" else 180

    def in_box(x):
        return x >= 1140 or (end == 180 and x < 180)

    exit_idx = numpy.flatnonzero(_at(k["min"], B, p["exit_at"])).tolist()
    out, plan = {}, {}
    bh = bl = None
    armed = False
    for i in range(len(m)):
        if in_box(m[i]):
            if m[i] >= 1140 and (i == 0 or not in_box(m[i - 1])
                                 or k["ts"][i] - k["ts"][i - 1] > 6 * 3600):
                bh, bl, armed = hh[i], ll[i], False
            elif bh is not None:
                bh, bl = max(bh, hh[i]), min(bl, ll[i])
            if bh is not None and (m[i] + B) % 1440 == end % 1440:
                armed = True
            continue
        if not armed:
            continue
        if m[i] >= p["exit_at"]:
            armed = False
            continue
        if p["trigger"] == "touch":
            up, dn = hh[i] > bh, ll[i] < bl
        else:
            up, dn = cc[i] > bh, cc[i] < bl
        if up == dn:
            continue
        side = 1 if up else -1
        if p["stop"] == "box":
            dist = F.stop_dist(side, cc[i], bl if side == 1 else bh, a[i])
        else:
            dist = _wide(ctx, i, p)
        armed = False
        x = _next_at(exit_idx, i)
        if _emit(out, i, side, dist):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_session_box", p, _plan_exit(plan))
    return out


def weekday(bars, ctx, p):
    """#1 (2026-11-12, USDJPY), #15 (2026-11-05, GBPJPY). Buy at Monday's
    open, close at Tuesday's close; sell at Wednesday's open, close at Friday's
    close (FX days roll at 17:00 NY). No stop: `stop_dr` daily ranges.
    `leg` keeps one half for the creator's own "is it the start of the week"
    check. Entry is one bar after the open (signals read a closed bar)."""
    k = _clock(bars, ctx)
    first = _first_of(k["td"])
    end_idx = numpy.flatnonzero(_at(k["min"], k["bar"], 1020)).tolist()
    td_wd = k["td_wd"]
    out, plan = {}, {}
    for i in numpy.flatnonzero(first).tolist():
        w = int(td_wd[i])
        if w == 0 and p["leg"] in ("both", "buy"):
            side, until = 1, 1
        elif w == 2 and p["leg"] in ("both", "sell"):
            side, until = -1, 4
        else:
            continue
        x = next((j for j in end_idx[bisect.bisect_right(end_idx, i):]
                  if td_wd[j] == until), None)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_weekday", p, _plan_exit(plan))
    return out


def gotobi(bars, ctx, p):
    """#2 (EURJPY), #31 (GBPJPY), #44 (USDJPY, the original). On Tokyo dates
    divisible by 5, sell at 09:55 Tokyo (the bank fixing), stop `sl_pips`,
    close at `exit_at` Tokyo (10:30). Their own warning: one minute late halves
    it, so only 5m bars mean anything. Weekdays only; ASSUMED no roll to the
    previous business day when the 5th falls on a weekend."""
    k = _clock(bars, ctx)
    t = _local(bars, ctx, "tokyo")
    B = k["bar"]
    entry = _at(t["min"], B, p["t_in"]) & (t["dom"] % 5 == 0) & (t["wd"] < 5)
    exit_idx = numpy.flatnonzero(_at(t["min"], B, p["exit_at"])).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(entry).tolist():
        x = _next_at(exit_idx, i)
        if _emit(out, i, -1, p["sl_pips"] * 0.01):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_gotobi", p, _plan_exit(plan))
    return out


def nfp_drift(bars, ctx, p):
    """#4 (2026-11-11, gold). First Friday of the month (NFP day): price at
    08:00 NY vs 16:00 NY; higher -> buy at 16:00, lower -> sell; hold to the
    `exit_wd` weekday's 16:00 (video: Wednesday; Tue/Thu keep about half). No
    stop: `stop_dr`."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    B = k["bar"]
    dom = (k["day"].astype("datetime64[D]") - k["day"].astype("datetime64[D]")
           .astype("datetime64[M]").astype("datetime64[D]")).astype(int) + 1
    at8 = _at(k["min"], B, 480)
    at16 = _at(k["min"], B, 960)
    ref = {}
    for i in numpy.flatnonzero(at8).tolist():
        ref[int(k["day"][i])] = b["c"][i]
    ex = numpy.flatnonzero(at16 & (k["wd"] == p["exit_wd"])).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(at16 & (k["wd"] == 4) & (dom <= 7)).tolist():
        r = ref.get(int(k["day"][i]))
        if r is None or b["c"][i] == r:
            continue
        side = 1 if b["c"][i] > r else -1
        x = _next_at(ex, i)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_nfp_drift", p, _plan_exit(plan))
    return out


def cross_session(bars, ctx, p):
    """#5 (2026-11-10, gold), #24's winter finding. At 03:00 NY, buy if the
    Asian session (from `start` NY, the evening open) went up, sell if down;
    close at `exit_at` (09:00). No stop: `stop_dr`. `winter=1` trades
    November-March only (#24: all of the Asian edge came from those months)."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    B = k["bar"]
    month = (k["day"].astype("datetime64[D]").astype("datetime64[M]")
             .astype(numpy.int64) % 12 + 1)
    opens = {}
    for i in numpy.flatnonzero(_at(k["min"], B, p["start"] + B)).tolist():
        opens[int(k["td"][i])] = b["o"][i]
    ex = numpy.flatnonzero(_at(k["min"], B, p["exit_at"])).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(_at(k["min"], B, 180)).tolist():
        if p["winter"] and month[i] not in (11, 12, 1, 2, 3):
            continue
        o = opens.get(int(k["td"][i]))
        if o is None or b["c"][i] == o:
            continue
        side = 1 if b["c"][i] > o else -1
        x = _next_at(ex, i)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_cross_session", p, _plan_exit(plan))
    return out


def friday_close(bars, ctx, p):
    """id 7706327693243010325 (2026-11-10, gold). Friday 16:00 NY: if the
    week is down (price under Monday's 16:00) buy and close `hold_min` minutes
    later (55). No stop: `stop_dr`. At 60m the exit rounds past the weekly
    close -- run it at 5m or 15m."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    B = k["bar"]
    at16 = numpy.flatnonzero(_at(k["min"], B, 960)).tolist()
    mon = {}
    for i in at16:
        if k["wd"][i] == 0:
            mon[int(k["day"][i]) + 4] = b["c"][i]
    ex = numpy.flatnonzero(_at(k["min"], B, 960 + p["hold_min"])).tolist()
    out, plan = {}, {}
    for i in at16:
        if k["wd"][i] != 4:
            continue
        ref = mon.get(int(k["day"][i]))
        if ref is None or not b["c"][i] < ref:
            continue
        x = _next_at(ex, i)
        if _emit(out, i, 1, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_friday_close", p, _plan_exit(plan))
    return out


def supertrend(bars, ctx, p):
    """#6 (2026-11-09, gold 4h). Supertrend, always in: green buy, red sell.
    The settings were only on screen; ASSUMED the paper's defaults ATR 10 x
    `mult`. Stop: the line (where it would flip anyway), floored 0.2 ATR."""
    h = _htf(bars, ctx, p["tf"])
    side, line = _supertrend(h["h"], h["l"], h["c"], 10, p["mult"])
    r = _htf_regime_bars(h, side)
    lb = _to_bars(h, line)
    cl = F.base(bars, ctx)["c"]
    a = _atr(bars, ctx)
    return _regime("tbd_supertrend", bars, ctx, p, r,
                   lambda i, s: F.stop_dist(s, cl[i], lb[i], a[i]) if fin(lb[i]) else NAN)


def gap_down(bars, ctx, p):
    """id 7706119883595058484 (2026-11-09, gold). At 10:30 NY, if price is
    `thr`% or more under yesterday's 16:00 close, sell; close at 16:00. Gap-up
    buying lost for them; `side=both` keeps it as the check. No stop:
    `stop_dr`."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    B = k["bar"]
    closes = _ny_daily_close(bars, ctx, 960)
    days = sorted(closes)
    ex = numpy.flatnonzero(_at(k["min"], B, 960)).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(_at(k["min"], B, 630) & (k["wd"] < 5)).tolist():
        d = int(k["day"][i])
        j = bisect.bisect_left(days, d) - 1
        if j < 0 or d - days[j] > 4:
            continue
        ref = closes[days[j]][0]
        move = 100 * (b["c"][i] / ref - 1)
        if move <= -p["thr"]:
            side = -1
        elif p["side"] == "both" and move >= p["thr"]:
            side = 1
        else:
            continue
        x = _next_at(ex, i)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_gap_down", p, _plan_exit(plan))
    return out


def sma_closes(bars, ctx, p):
    """#7 (2026-11-08, gold daily; Chande). Three daily closes above SMA(n):
    buy; three below: sell; always in. No stop: `stop_dr`. The creator's own
    verdict: no better than random once one 2025 trade is removed."""
    h = _htf(bars, ctx, 1440)
    c = h["c"]
    s = _sma(c, p["n"])
    above = c > s
    below = c < s
    k3 = p["k"]
    up = numpy.array([i >= k3 - 1 and above[i - k3 + 1:i + 1].all() for i in range(len(c))])
    dn = numpy.array([i >= k3 - 1 and below[i - k3 + 1:i + 1].all() for i in range(len(c))])
    r = _htf_regime_bars(h, _hold_state(up, dn))
    return _regime("tbd_sma_closes", bars, ctx, p, r, _wide_stop(ctx, p))


def turn_of_month(bars, ctx, p):
    """#8 (2026-11-08, gold; McConnell & Xu). Buy at the close of the
    trading day `before` days ahead of the month's last trading day (video: 1,
    "the day before the last"), sell at the close of the `after`-th trading day
    of next month (3). Business days by the weekday calendar (no holidays).
    No stop: `stop_dr`."""
    k = _clock(bars, ctx)
    td = k["td"]
    end_idx = numpy.flatnonzero(_at(k["min"], k["bar"], 1020))
    out, plan = {}, {}
    cache = {}

    def month_days(y, m):
        if (y, m) not in cache:
            cache[(y, m)] = _bdays(y, m)
        return cache[(y, m)]

    by_td = {int(td[i]): int(i) for i in end_idx}
    for i in end_idx.tolist():
        d = int(td[i])
        date = numpy.datetime64(d, "D").astype(object)
        bd = month_days(date.year, date.month)
        if len(bd) <= p["before"] or d != bd[-1 - p["before"]]:
            continue
        ny, nm = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
        nbd = month_days(ny, nm)
        if len(nbd) < p["after"]:
            continue
        target = int(nbd[p["after"] - 1])
        x = by_td.get(target)
        if x is None:
            later = [t for t in by_td if t >= target]
            x = by_td[min(later)] if later else None
        if _emit(out, i, 1, _wide(ctx, i, p)):
            _plan(plan, i, x if x is not None and x > i else None)
    _store_exit(ctx, "tbd_turn_of_month", p, _plan_exit(plan))
    return out


def rsi50(bars, ctx, p):
    """#9 (2026-11-07, gold 4h; Chong/Ng/Liew 2014). RSI(n) crosses above 50:
    buy, below: sell, flipping. Stop (theirs): the extreme of the last 20
    candles."""
    h = _htf(bars, ctx, p["tf"])
    rs = _rsi(h["c"], p["n"])
    r = _htf_regime_bars(h, numpy.where(rs > 50, 1, numpy.where(rs < 50, -1, 0)))
    return _regime("tbd_rsi50", bars, ctx, p, r, _ext_stop(bars, ctx, h, 20))


def overnight(bars, ctx, p):
    """#10 (2026-11-07), #24 (2026-11-03), #51 (2026-10-22): gold rises
    outside COMEX hours. `comex`: buy 13:30, sell 08:20 next morning (#10).
    `winter`: buy 16:50, sell 03:00, November-March only, stop at the last 8
    hours' low (#24's own stop). `asia`: buy 16:00, sell 02:00 (#51). Long
    only. Other variants: no stop, `stop_dr`."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    B = k["bar"]
    t_in, t_out = {"comex": (810, 500), "winter": (1010, 180),
                   "asia": (960, 120)}[p["variant"]]
    month = (k["day"].astype("datetime64[D]").astype("datetime64[M]")
             .astype(numpy.int64) % 12 + 1)
    low8 = _roll_min(b["l"], max(1, 480 // B))
    ex = numpy.flatnonzero(_at(k["min"], B, t_out)).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(_at(k["min"], B, t_in) & (k["wd"] < 5)).tolist():
        if p["variant"] == "winter":
            if month[i] not in (11, 12, 1, 2, 3):
                continue
            dist = F.stop_dist(1, b["c"][i], low8[i], a[i])
        else:
            dist = _wide(ctx, i, p)
        x = _next_at(ex, i)
        if _emit(out, i, 1, dist):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_overnight", p, _plan_exit(plan))
    return out


def adx_jump(bars, ctx, p):
    """#11 (2026-11-07, gold 1h; Chande). ADX(18) up more than `jump` points
    in one candle: buy if SMA3 > SMA12, else sell. Stop (theirs): the last 10
    candles' extreme. Close 20 candles later."""
    h = _htf(bars, ctx, p["tf"])
    _, _, adx = _dmi(h["h"], h["l"], h["c"], 18)
    s3, s12 = _sma(h["c"], 3), _sma(h["c"], 12)
    comp, new = h["comp"], h["new"]
    stop = _ext_stop(bars, ctx, h, 10)
    end_bar = h["end_bar"]
    out, plan = {}, {}
    for i in numpy.flatnonzero(new).tolist():
        q = comp[i]
        if q < 1 or not (fin(adx[q]) and fin(adx[q - 1]) and fin(s12[q])):
            continue
        if adx[q] - adx[q - 1] <= p["jump"] or s3[q] == s12[q]:
            continue
        side = 1 if s3[q] > s12[q] else -1
        x = int(end_bar[q + 20]) if q + 20 < len(end_bar) else None
        if _emit(out, i, side, stop(i, side)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_adx_jump", p, _plan_exit(plan))
    return out


def busy_breakout(bars, ctx, p):
    """#12 (2026-11-06, gold; De Rico 2017). Volume 01:00-09:00 NY above
    `ratio` x its mean over the last 5 days: mark the 09:00-10:00 high/low,
    OCO stops, stop on the other side, close at 16:50. Quiet days lost for
    them. ASSUMED: the stop fills at the next bar's open."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    m, B = k["min"], k["bar"]
    days = k["day"]
    pre = (m >= 60) & (m + B <= 540)
    vol = {}
    for i in numpy.flatnonzero(pre).tolist():
        vol[int(days[i])] = vol.get(int(days[i]), 0.0) + b["v"][i]
    ex = numpy.flatnonzero(_at(m, B, 1010)).tolist()
    out, plan = {}, {}
    hist = []
    cur = None
    for i in range(len(bars)):
        d = int(days[i])
        if cur is None or cur[0] != d:
            if cur is not None and cur[0] in vol:
                hist.append(vol[cur[0]])
            cur = [d, None, None, False, False]
        if m[i] >= 540 and m[i] + B <= 600:
            cur[1] = b["h"][i] if cur[1] is None else max(cur[1], b["h"][i])
            cur[2] = b["l"][i] if cur[2] is None else min(cur[2], b["l"][i])
            if m[i] + B == 600:
                busy = (len(hist) >= 5 and d in vol
                        and vol[d] > p["ratio"] * (sum(hist[-5:]) / 5))
                cur[3] = busy
            continue
        if not cur[3] or cur[4] or m[i] < 600 or m[i] + B > 1010:
            continue
        up, dn = b["h"][i] > cur[1], b["l"][i] < cur[2]
        if up == dn:
            continue
        side = 1 if up else -1
        cur[4] = True
        dist = F.stop_dist(side, b["c"][i], cur[2] if side == 1 else cur[1], a[i])
        x = _next_at(ex, i)
        if _emit(out, i, side, dist):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_busy_breakout", p, _plan_exit(plan))
    return out


def candle_count(bars, ctx, p):
    """#14 (2026-11-05, gold 1h; Sepp & Lucic 2026). Of the last `n` candles,
    more up than down: long, more down: short, checked every candle, always
    in. No stop: `stop_dr`."""
    h = _htf(bars, ctx, p["tf"])
    d = numpy.sign(h["c"] - h["o"])
    cs = numpy.cumsum(numpy.insert(d, 0, 0.0))
    n = p["n"]
    net = numpy.full(len(d), NAN)
    net[n - 1:] = cs[n:] - cs[:-n]
    r = _htf_regime_bars(h, _hold_state(net > 0, net < 0))
    return _regime("tbd_candle_count", bars, ctx, p, r, _wide_stop(ctx, p))


def ema_cross(bars, ctx, p):
    """#16 (2026-11-05, gold 1h; Levine & Pedersen 2016). EMA(fast) over
    EMA(65): buy, under: sell, flipping. Stop (theirs): last 20 candles'
    extreme."""
    h = _htf(bars, ctx, p["tf"])
    f, s = _ema(h["c"], p["fast"]), _ema(h["c"], 65)
    r = _htf_regime_bars(h, numpy.sign(f - s).astype(int))
    return _regime("tbd_ema_cross", bars, ctx, p, r, _ext_stop(bars, ctx, h, 20))


def dmi(bars, ctx, p):
    """#17 (2026-11-05, gold 4h; Wilder 1978). +DI crosses over -DI: buy
    stop at that candle's high; -DI over +DI: sell stop at its low. Hold the
    old side until the new trigger breaks; the trigger candle's other extreme
    is the stop. ASSUMED: a fill at the next bar's open after the break."""
    h = _htf(bars, ctx, p["tf"])
    pdi, mdi, _ = _dmi(h["h"], h["l"], h["c"], p["n"])
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    comp, new = h["comp"], h["new"]
    r = numpy.zeros(len(bars), dtype=int)
    stops = numpy.full(len(bars), NAN)
    trig = None
    s = 0
    for i in range(len(bars)):
        if trig is not None:
            side, lvl, stop = trig
            if (side == 1 and b["h"][i] > lvl) or (side == -1 and b["l"][i] < lvl):
                s = side
                stops[i] = stop
                trig = None
        if new[i]:
            q = comp[i]
            if q >= 1 and fin(pdi[q]) and fin(pdi[q - 1]):
                if pdi[q] > mdi[q] and pdi[q - 1] <= mdi[q - 1]:
                    trig = (1, h["h"][q], h["l"][q])
                elif mdi[q] > pdi[q] and mdi[q - 1] <= pdi[q - 1]:
                    trig = (-1, h["l"][q], h["h"][q])
        r[i] = s
    last = NAN
    for i in range(len(bars)):
        if fin(stops[i]):
            last = stops[i]
        stops[i] = last
    cl = b["c"]
    return _regime("tbd_dmi", bars, ctx, p, r,
                   lambda i, sd: F.stop_dist(sd, cl[i], stops[i], a[i]) if fin(stops[i]) else NAN)


def fractal3(bars, ctx, p):
    """#18 (2026-11-04, gold 4h; Masteika & Rutkauskas 2012). Three candles,
    the middle one with the highest high: buy stop above the third's high,
    stop under its low, the stop trailed to each closed candle's low. Filters:
    third low over the low `look` candles back; middle close over the close
    two candles before it. The video states the long side; `side=both`
    mirrors it. ASSUMED: the order lives until the next fractal; the trail
    exits a bar late (a market order after the breach)."""
    h = _htf(bars, ctx, p["tf"])
    hh, ll, cc = h["h"], h["l"], h["c"]
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    comp, new = h["comp"], h["new"]
    look = p["look"]
    out = {}
    order = None
    for i in range(len(bars)):
        if order is not None:
            side, lvl, stop = order
            if (side == 1 and b["h"][i] > lvl) or (side == -1 and b["l"][i] < lvl):
                _emit(out, i, side, F.stop_dist(side, b["c"][i], stop, a[i]))
                order = None
        if new[i]:
            q = comp[i]
            if q >= max(3, look):
                if hh[q - 1] > hh[q - 2] and hh[q - 1] > hh[q] and \
                        ll[q] > ll[q - look] and cc[q - 1] > cc[q - 3]:
                    order = (1, hh[q], ll[q])
                elif p["side"] == "both" and ll[q - 1] < ll[q - 2] and \
                        ll[q - 1] < ll[q] and hh[q] < hh[q - look] and cc[q - 1] < cc[q - 3]:
                    order = (-1, ll[q], hh[q])
    low_c = _to_bars(h, ll)
    high_c = _to_bars(h, hh)
    bl, bh = b["l"], b["h"]

    def trail(j, side):
        if side == 1:
            return fin(low_c[j]) and bl[j] < low_c[j]
        return fin(high_c[j]) and bh[j] > high_c[j]
    _store_exit(ctx, "tbd_fractal3", p, trail)
    return out


def fib_dynamic(bars, ctx, p):
    """#19 (2026-11-04, gold 1h; Willig 2009). A Fibonacci redrawn every
    candle over the last `n`: a close above the 38.2% line buys, a close below
    the 61.8% line sells, flipping. ASSUMED: lines measured down from the
    high (38.2 = high - 0.382 range). Stop (theirs): the window's low/high."""
    h = _htf(bars, ctx, p["tf"])
    hi, lo = _roll_max(h["h"], p["n"]), _roll_min(h["l"], p["n"])
    rng = hi - lo
    up = h["c"] > hi - 0.382 * rng
    dn = h["c"] < hi - 0.618 * rng
    r = _htf_regime_bars(h, _hold_state(up, dn))
    return _regime("tbd_fib_dynamic", bars, ctx, p, r, _ext_stop(bars, ctx, h, p["n"]))


def adaptive_sar(bars, ctx, p):
    """#20 (2026-11-04, gold 1h; McKenna 2021). Parabolic SAR, stop and
    reverse, with the step sped up when price moved more than `thr` over the
    last three days and slowed when it did not. ASSUMED steps: fast
    0.02/0.2, slow 0.01/0.1 (not stated). Stop: the dot."""
    h = _htf(bars, ctx, p["tf"])
    hh, ll, cc = h["h"], h["l"], h["c"]
    lag = max(1, 3 * 1440 // p["tf"])
    n = len(cc)
    sar = numpy.full(n, NAN)
    side = numpy.zeros(n, dtype=int)
    if n > 2:
        s, ep, af = 1, hh[0], 0.0
        dot = ll[0]
        for q in range(1, n):
            fast = q >= lag and abs(cc[q - 1] / cc[q - 1 - lag] - 1) > p["thr"]
            step, mx = (0.02, 0.2) if fast else (0.01, 0.1)
            af = af or step
            dot = dot + af * (ep - dot)
            if s == 1:
                dot = min(dot, ll[q - 1], ll[q - 2] if q > 1 else ll[q - 1])
                if ll[q] < dot:
                    s, dot, ep, af = -1, ep, ll[q], step
                elif hh[q] > ep:
                    ep, af = hh[q], min(af + step, mx)
            else:
                dot = max(dot, hh[q - 1], hh[q - 2] if q > 1 else hh[q - 1])
                if hh[q] > dot:
                    s, dot, ep, af = 1, ep, hh[q], step
                elif ll[q] < ep:
                    ep, af = ll[q], min(af + step, mx)
            sar[q], side[q] = dot, s
    r = _htf_regime_bars(h, side)
    dots = _to_bars(h, sar)
    cl = F.base(bars, ctx)["c"]
    a = _atr(bars, ctx)
    return _regime("tbd_adaptive_sar", bars, ctx, p, r,
                   lambda i, s: F.stop_dist(s, cl[i], dots[i], a[i]) if fin(dots[i]) else NAN)


def skew_week(bars, ctx, p):
    """#21 (2026-11-04, USDCHF; Fernandez-Perez et al.). Daily realized
    skewness of the bar returns, averaged Monday-Friday and ranked against the
    last 52 weeks: bottom `q` -> buy Friday 16:00 NY, top `q` -> sell; hold
    to next Friday. The creator's own rule (the paper is commodities). No
    stop: `stop_dr`. They used 5m returns; coarser bars give a noisier skew."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    cl = b["c"]
    ret = numpy.zeros(len(cl))
    ret[1:] = numpy.log(cl[1:] / cl[:-1])
    td = k["td"]
    first = _first_of(td)
    ret[first] = 0.0
    daily = {}
    for d, r in zip(td.tolist(), ret.tolist()):
        s = daily.setdefault(d, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += r ** 3
        s[2] += r ** 2
    rsk = {d: (math.sqrt(s[0]) * s[1] / s[2] ** 1.5) for d, s in daily.items() if s[2] > 0}
    at16 = numpy.flatnonzero(_at(k["min"], k["bar"], 960) & (k["wd"] == 4)).tolist()
    out, plan = {}, {}
    hist = []
    for n_i, i in enumerate(at16):
        d = int(td[i])
        week = [rsk[x] for x in range(d - 4, d + 1) if x in rsk]
        if len(week) < 3:
            continue
        v = sum(week) / len(week)
        if len(hist) >= 52:
            past = sorted(hist[-52:])
            lo, hi = past[int(p["q"] * 52)], past[int((1 - p["q"]) * 52) - 1]
            side = 1 if v <= lo else (-1 if v >= hi else 0)
            x = at16[n_i + 1] if n_i + 1 < len(at16) else None
            if side and _emit(out, i, side, _wide(ctx, i, p)):
                _plan(plan, i, x)
        hist.append(v)
    _store_exit(ctx, "tbd_skew_week", p, _plan_exit(plan))
    return out


def cci_trend(bars, ctx, p):
    """#22 (2026-11-03, gold 1h; Lambert via Rice 2022). CCI(n) breaks above
    +100: buy; below -100: sell (trend reading, not overbought). Stop (theirs):
    the last 10 candles' extreme; hold to the opposite break; stopped out ->
    wait for the next break (`fresh=2`)."""
    h = _htf(bars, ctx, p["tf"])
    cci = _cci(h["h"], h["l"], h["c"], p["n"])
    prev = numpy.append(NAN, cci[:-1])
    up = (cci > 100) & (prev <= 100)
    dn = (cci < -100) & (prev >= -100)
    r = _htf_regime_bars(h, _hold_state(up, dn))
    return _regime("tbd_cci_trend", bars, ctx, p, r, _ext_stop(bars, ctx, h, 10))


def macdv(bars, ctx, p):
    """#23 (2026-11-03, gold 1h; Spiroglou 2022). MACD-V = (EMA12 - EMA26) /
    ATR26 x 100. Long while it sits between `lo` and 150 above its 9-signal;
    out when it crosses under the signal. Shorts mirror. No stop: `stop_dr`.
    The `lo` floor is what made it steady (6/12 years without it)."""
    h = _htf(bars, ctx, p["tf"])
    c = h["c"]
    atr = _wilder(_tr(h["h"], h["l"], c), 26)
    with numpy.errstate(invalid="ignore", divide="ignore"):
        mv = (_ema(c, 12) - _ema(c, 26)) / atr * 100
    sig = _ema(numpy.nan_to_num(mv), 9)
    state = numpy.zeros(len(c), dtype=int)
    s = 0
    for q in range(len(c)):
        if not fin(mv[q]):
            continue
        if s == 1 and mv[q] < sig[q]:
            s = 0
        elif s == -1 and mv[q] > sig[q]:
            s = 0
        if s == 0:
            if p["lo"] < mv[q] < 150 and mv[q] > sig[q]:
                s = 1
            elif -150 < mv[q] < -p["lo"] and mv[q] < sig[q]:
                s = -1
        state[q] = s
    r = _htf_regime_bars(h, state)
    return _regime("tbd_macdv", bars, ctx, p, r, _wide_stop(ctx, p))


def spx_gold(bars, ctx, p):
    """#25 (2026-11-03, gold; after Baur-style studies). S&P 500 day return
    beyond `k` sd (trailing `sd_n` days): up -> buy gold at 16:00, down ->
    sell; hold one day. No stop: `stop_dr`. The creator's own rule."""
    k = _clock(bars, ctx)
    ext = _bench_extreme("us500", p["k"], p["sd_n"])
    at16 = numpy.flatnonzero(_at(k["min"], k["bar"], 960) & (k["wd"] < 5)).tolist()
    out, plan = {}, {}
    for n_i, i in enumerate(at16):
        side = ext.get(int(k["day"][i]), 0)
        x = at16[n_i + 1] if n_i + 1 < len(at16) else None
        if side and _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_spx_gold", p, _plan_exit(plan))
    return out


def fx_fix(bars, ctx, p):
    """#26 (2026-11-02) and #41 (2026-10-30), EURUSD (Krohn/Mueller/Whelan,
    JF 2024). `ecb`: sell at 08:00 Frankfurt, stop `sl_pct`%, close at 14:15
    Frankfurt (the ECB fix). `london`: buy right after the 16:00 London fix,
    stop at the last hour's low but at least `sl_pct`%... the video's 0.1%,
    close at 17:00 NY. The creator: both stopped working after 2019."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    B = k["bar"]
    if p["variant"] == "ecb":
        z = _local(bars, ctx, "frankfurt")
        ent = _at(z["min"], B, 480) & (z["wd"] < 5)
        ex = numpy.flatnonzero(_at(z["min"], B, 855)).tolist()
        side = -1
    else:
        z = _local(bars, ctx, "london")
        ent = _at(z["min"], B, 960) & (z["wd"] < 5)
        ex = numpy.flatnonzero(_at(k["min"], B, 1020)).tolist()
        side = 1
    low1 = _roll_min(b["l"], max(1, 60 // B))
    out, plan = {}, {}
    for i in numpy.flatnonzero(ent).tolist():
        c = b["c"][i]
        dist = c * p["sl_pct"] / 100
        if p["variant"] == "london":
            dist = max(dist, F.stop_dist(1, c, low1[i], a[i]))
        x = _next_at(ex, i)
        if _emit(out, i, side, dist):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_fx_fix", p, _plan_exit(plan))
    return out


# =========================================================================== #
# 2026-11-02 .. 2026-10-22
# =========================================================================== #

def multi_span(bars, ctx, p):
    """#29 / #40 (gold daily; Safari & Schmidt-Hieber 2025). Trend strength
    s over spans 2, 4, 8 ... 2^m days (move / (daily sd x sqrt(span))), each
    scored by the paper's response, positive below ~1.44 and turning against
    the trend above it. The video's two points pin a cubic: phi(s) = 1.333 s
    - 0.633 s^3 (phi(1)=+0.7, phi(2)=-2.4, zero at 1.45). Sum the spans; long
    if positive, short if negative, held. No stop: `stop_dr`. ASSUMED: daily
    sd over the last 100 days; spans capped at 2^m by the data's length."""
    h = _htf(bars, ctx, 1440)
    lc = numpy.log(h["c"])
    ret = numpy.diff(lc, prepend=lc[0])
    n = len(lc)
    sd = numpy.full(n, NAN)
    for q in range(100, n):
        sd[q] = numpy.std(ret[q - 99:q + 1], ddof=1)
    total = numpy.zeros(n)
    ok = numpy.zeros(n, dtype=bool)
    for e in range(1, p["m"] + 1):
        T = 2 ** e
        s = numpy.full(n, NAN)
        s[T:] = (lc[T:] - lc[:-T]) / (sd[T:] * math.sqrt(T))
        good = numpy.isfinite(s)
        total[good] += 1.333 * s[good] - 0.633 * s[good] ** 3
        ok = good if e == 1 else (ok & good)
    state = numpy.where(ok, numpy.sign(total), 0).astype(int)
    r = _htf_regime_bars(h, state)
    return _regime("tbd_multi_span", bars, ctx, p, r, _wide_stop(ctx, p))


def nasdaq_usd(bars, ctx, p):
    """#32 (2026-11-01; Hau & Rey 2006). NASDAQ day return beyond `k` x its
    60-day sd: up -> sell the dollar at the US close (buy EUR/GBP/AUD-USD, sell
    USD-JPY/CAD/CHF), down -> buy it; stop `sl_pct`%; close at the next US
    close. The creator's own rule, run per pair here."""
    sym = ctx["symbol"]
    if sym in USD_QUOTE:
        sign = 1
    elif sym in USD_BASE:
        sign = -1
    else:
        return {}
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    ext = _bench_extreme("ustec", p["k"], 60)
    at16 = numpy.flatnonzero(_at(k["min"], k["bar"], 960) & (k["wd"] < 5)).tolist()
    out, plan = {}, {}
    for n_i, i in enumerate(at16):
        move = ext.get(int(k["day"][i]), 0)
        x = at16[n_i + 1] if n_i + 1 < len(at16) else None
        if move and _emit(out, i, move * sign, b["c"][i] * p["sl_pct"] / 100):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_nasdaq_usd", p, _plan_exit(plan))
    return out


def overnight_follow(bars, ctx, p):
    """#33 (2026-11-01, gold; Branch & Ma; Ma et al. 2025). Gold at 08:20
    NY vs yesterday's 13:30: higher -> buy, lower -> sell; close at 13:30. No
    stop: `stop_dr`. Fading it (the stock-market reading) lost $37k for them."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    B = k["bar"]
    c1330 = _ny_daily_close(bars, ctx, 810)
    days = sorted(c1330)
    ex = numpy.flatnonzero(_at(k["min"], B, 810)).tolist()
    out, plan = {}, {}
    for i in numpy.flatnonzero(_at(k["min"], B, 500) & (k["wd"] < 5)).tolist():
        d = int(k["day"][i])
        j = bisect.bisect_left(days, d) - 1
        if j < 0 or d - days[j] > 4:
            continue
        ref = c1330[days[j]][0]
        if b["c"][i] == ref:
            continue
        side = 1 if b["c"][i] > ref else -1
        x = _next_at(ex, i)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_overnight_follow", p, _plan_exit(plan))
    return out


def ibs(bars, ctx, p):
    """#34 (2026-11-01, S&P 500; Pagonidis 2014). The cash-session candle
    closes in its bottom `thr` of the range (IBS < 0.2): buy at the close,
    sell at the next close. Long only (their short side lost). No stop:
    `stop_dr`. ASSUMED: the 09:30-16:00 candle (they used futures dailies)."""
    k = _clock(bars, ctx)
    rth = _rth_candles(bars, ctx)
    at16 = numpy.flatnonzero(_at(k["min"], k["bar"], 960) & (k["wd"] < 5)).tolist()
    out, plan = {}, {}
    for n_i, i in enumerate(at16):
        row = rth.get(int(k["day"][i]))
        if row is None or row[4] != i or not row[1] > row[2]:
            continue
        v = (row[3] - row[2]) / (row[1] - row[2])
        x = at16[n_i + 1] if n_i + 1 < len(at16) else None
        if v < p["thr"] and _emit(out, i, 1, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_ibs", p, _plan_exit(plan))
    return out


def bb_fade(bars, ctx, p):
    """#35 (2026-10-31, AUDUSD daily; Coakley/Marzano/Nankervis 2016).
    Bollinger(20, `w`): a close above the upper band sells at the next open, a
    close below the lower buys; stop `sl_pct`%; out (and reversed) on a close
    beyond the opposite band; stopped out -> wait for the next signal. The
    breakout reading lost $7k for them."""
    h = _htf(bars, ctx, 1440)
    c = h["c"]
    m = _sma(c, 20)
    sd = numpy.array([numpy.std(c[q - 19:q + 1]) if q >= 19 else NAN
                      for q in range(len(c))])
    up, dn = c > m + p["w"] * sd, c < m - p["w"] * sd
    r = _htf_regime_bars(h, _hold_state(dn, up))
    cl = F.base(bars, ctx)["c"]
    return _regime("tbd_bb_fade", bars, ctx, p, r,
                   lambda i, _s: cl[i] * p["sl_pct"] / 100)


def nfp_straddle(bars, ctx, p):
    """#37 (2026-10-31, gold; "Seven Trading Ideas, One Protocol" 2026). On
    NFP Fridays, mark the `range_min` minutes before 08:30 NY; first break
    after 08:30 trades, stop the other side, 2R target. ASSUMED: NFP = the
    first Friday of the month (it is, most months). Non-NFP Fridays lost for
    them -- `day=any_friday` is that check."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    m, B = k["min"], k["bar"]
    dom = (k["day"].astype("datetime64[D]") - k["day"].astype("datetime64[D]")
           .astype("datetime64[M]").astype("datetime64[D]")).astype(int) + 1
    ok_day = (k["wd"] == 4) & ((dom <= 7) if p["day"] == "nfp" else True)
    out = {}
    cur = None
    for i in range(len(bars)):
        if not ok_day[i]:
            continue
        d = int(k["day"][i])
        if cur is None or cur[0] != d:
            cur = [d, None, None, False]
        if 510 - p["range_min"] <= m[i] and m[i] + B <= 510:
            cur[1] = b["h"][i] if cur[1] is None else max(cur[1], b["h"][i])
            cur[2] = b["l"][i] if cur[2] is None else min(cur[2], b["l"][i])
            continue
        if m[i] < 510 or cur[1] is None or cur[3] or m[i] > 720:
            continue
        up, dn = b["h"][i] > cur[1], b["l"][i] < cur[2]
        if up == dn:
            continue
        side = 1 if up else -1
        cur[3] = True
        _emit(out, i, side, F.stop_dist(side, b["c"][i],
                                        cur[2] if side == 1 else cur[1], a[i]))
    return out


def volatile_red(bars, ctx, p):
    """#38 (2026-10-31, NASDAQ; Sentana & Wadhwani 1992). Yesterday's cash
    session closed red and its `vol_n`-day volatility ranks in the top `q`
    of the past year: buy the open, sell before the close (the session
    flatten). No stop: `stop_dr`. The creator's own rule from the paper."""
    k = _clock(bars, ctx)
    rth = _rth_candles(bars, ctx)
    days = sorted(rth)
    closes = numpy.array([rth[d][3] for d in days])
    ret = numpy.diff(numpy.log(closes), prepend=numpy.log(closes[0])) if len(days) else numpy.array([])
    vol = {}
    for j in range(p["vol_n"], len(days)):
        vol[days[j]] = float(numpy.std(ret[j - p["vol_n"] + 1:j + 1]))
    m, B = k["min"], k["bar"]
    first = numpy.flatnonzero((m >= 570) & (m - B < 570) & (k["wd"] < 5)).tolist()
    out = {}
    for i in first:
        d = int(k["day"][i])
        j = bisect.bisect_left(days, d) - 1
        if j < 252:
            continue
        y = days[j]
        o, _, _, c, _ = rth[y]
        past = [vol[x] for x in days[j - 251:j + 1] if x in vol]
        if not (c < o) or y not in vol or len(past) < 200:
            continue
        if vol[y] >= sorted(past)[int(p["q"] * len(past)) - 1]:
            _emit(out, i, 1, _wide(ctx, i, p))
    return out


def month_end_fix(bars, ctx, p):
    """#39 (2026-10-30, GBPUSD; Melvin & Prins 2015). Last trading day of the
    month: S&P 500 up on the month -> buy GBPUSD at 15:00 London, down ->
    sell; stop at the last hour's extreme but at least `min_pct`%; close at
    16:00 London (the WM/R fix). ASSUMED: the month's move = the latest S&P
    16:00 close against the prior month's last one."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    z = _local(bars, ctx, "london")
    B = k["bar"]
    spx = _bench_close("us500")
    sdays = sorted(spx)
    ent = numpy.flatnonzero(_at(z["min"], B, 900) & (z["wd"] < 5)).tolist()
    ex = numpy.flatnonzero(_at(z["min"], B, 960)).tolist()
    lo1, hi1 = _roll_min(b["l"], max(1, 60 // B)), _roll_max(b["h"], max(1, 60 // B))
    out, plan = {}, {}
    cache = {}
    for i in ent:
        d = int(z["day"][i])
        date = numpy.datetime64(d, "D").astype(object)
        ym = (date.year, date.month)
        if ym not in cache:
            cache[ym] = _bdays(*ym)
        if not len(cache[ym]) or d != cache[ym][-1]:
            continue
        first_day = int(numpy.datetime64(f"{ym[0]:04d}-{ym[1]:02d}-01").astype(numpy.int64))
        prev = [x for x in sdays[max(0, bisect.bisect_left(sdays, first_day) - 3):
                                 bisect.bisect_left(sdays, first_day)]]
        now = [x for x in sdays[bisect.bisect_left(sdays, first_day):
                                bisect.bisect_left(sdays, int(k["day"][i]))]]
        if not prev or not now:
            continue
        move = spx[now[-1]] - spx[prev[-1]]
        if move == 0:
            continue
        side = 1 if move > 0 else -1
        c = b["c"][i]
        lvl = lo1[i] if side == 1 else hi1[i]
        dist = max(c * p["min_pct"] / 100, F.stop_dist(side, c, lvl, a[i]))
        x = _next_at(ex, i)
        if _emit(out, i, side, dist):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_month_end_fix", p, _plan_exit(plan))
    return out


def overnight_drift(bars, ctx, p):
    """#42 (2026-10-30, S&P 500; Boyarchenko et al., NY Fed). Buy at 02:00
    NY (the European open), stop at `frac` of the range since the 18:00
    evening open, close at 03:00. The creator: the hour stopped paying in 2021
    (rank 17 of 23)."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    m, B = k["min"], k["bar"]
    td = k["td"]
    ex = numpy.flatnonzero(_at(m, B, 180)).tolist()
    at2 = _at(m, B, 120)
    hi = lo = None
    cur = None
    out, plan = {}, {}
    for i in range(len(bars)):
        if cur != td[i]:
            cur, hi, lo = td[i], b["h"][i], b["l"][i]
        else:
            hi, lo = max(hi, b["h"][i]), min(lo, b["l"][i])
        if at2[i] and k["wd"][i] < 5:
            dist = max(p["frac"] * (hi - lo), 0.2 * a[i] if fin(a[i]) else 0)
            x = _next_at(ex, i)
            if _emit(out, i, 1, dist):
                _plan(plan, i, x)
    _store_exit(ctx, "tbd_overnight_drift", p, _plan_exit(plan))
    return out


def orb_10r(bars, ctx, p):
    """#43 (2026-10-29, NASDAQ; Zarattini & Aziz 2023). The first `or_bars`
    bars of the cash session: green -> buy, red -> sell, stop at the range's
    other end, target 10R (`rr_10`), else out at the close. Built for 5m
    bars; coarser bars make the "first five minutes" a first half-hour."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    m, B = k["min"], k["bar"]
    out = {}
    day = None
    o0 = hi = lo = None
    count = 0
    for i in range(len(bars)):
        if m[i] < 570 or m[i] >= 960 or k["wd"][i] >= 5:
            continue
        d = int(k["day"][i])
        if d != day:
            day, count = d, 0
            o0, hi, lo = b["o"][i], b["h"][i], b["l"][i]
        else:
            hi, lo = max(hi, b["h"][i]), min(lo, b["l"][i])
        count += 1
        if count == p["or_bars"]:
            c = b["c"][i]
            if c == o0:
                continue
            side = 1 if c > o0 else -1
            _emit(out, i, side, F.stop_dist(side, c, lo if side == 1 else hi, a[i]))
    return out


def macd_slow(bars, ctx, p):
    """#45 (2026-10-24, gold; a 2026 Japanese MACD-range study). MACD
    (7, 37, 39) crosses, always in. Timeframe not stated (~14 trades a year:
    ASSUMED `tf` 240 or daily). No stop: `stop_dr`. The creator: dead
    2022-25, alive again in 2026."""
    h = _htf(bars, ctx, p["tf"])
    c = h["c"]
    line = _ema(c, 7) - _ema(c, 37)
    sig = _ema(line, 39)
    r = _htf_regime_bars(h, numpy.sign(line - sig).astype(int))
    return _regime("tbd_macd_slow", bars, ctx, p, r, _wide_stop(ctx, p))


def vol_breakout(bars, ctx, p):
    """#47 (2026-10-23, gold; Larry Williams 1999). Buy line = today's open
    + `k` x yesterday's range, sell line = open - `k` x range; the first one
    hit trades, stop at the open, out at the day's end. Entering the next
    morning instead (the 2023 study) lost for them. ASSUMED: the trading day
    rolls at 17:00 NY; the touch fills at the next bar's open."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    td = k["td"]
    end_idx = numpy.flatnonzero(_at(k["min"], k["bar"], 1020)).tolist()
    out, plan = {}, {}
    cur = None
    prev_rng = None
    o0 = hi = lo = None
    done = False
    for i in range(len(bars)):
        if cur != td[i]:
            if cur is not None:
                prev_rng = hi - lo
            cur, o0, hi, lo, done = td[i], b["o"][i], b["h"][i], b["l"][i], False
        else:
            hi, lo = max(hi, b["h"][i]), min(lo, b["l"][i])
        if done or prev_rng is None or k["td_wd"][i] >= 5:
            continue
        up = b["h"][i] >= o0 + p["k"] * prev_rng
        dn = b["l"][i] <= o0 - p["k"] * prev_rng
        if up == dn:
            continue
        done = True
        side = 1 if up else -1
        x = _next_at(end_idx, i)
        if _emit(out, i, side, F.stop_dist(side, b["c"][i], o0, a[i])):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_vol_breakout", p, _plan_exit(plan))
    return out


def sma_regime(bars, ctx, p):
    """#48 (2026-10-23, gold 5m; a 2018 HF gold study). SMA44 over SMA332 on
    5m bars: long, under: short. Stop (theirs): yesterday's low/high; stopped
    out -> wait for the next cross. Lengths are kept in MINUTES (220 / 1,660)
    so other bar sizes read the same span; `scale` moves both (their 20
    neighbouring pairs were all profitable)."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    B = k["bar"]
    nf = max(2, round(220 * p["scale"] / B))
    ns = max(nf + 1, round(1660 * p["scale"] / B))
    r = numpy.nan_to_num(numpy.sign(_sma(b["c"], nf) - _sma(b["c"], ns))).astype(int)
    td = k["td"]
    first = numpy.flatnonzero(_first_of(td))
    ph = numpy.full(len(bars), NAN)
    pl = numpy.full(len(bars), NAN)
    ends = numpy.append(first[1:], len(bars))
    for a0, a1, b1 in zip(first, ends, numpy.append(ends[1:], len(bars))):
        ph[a1:b1] = b["h"][a0:a1].max()
        pl[a1:b1] = b["l"][a0:a1].min()
    cl = b["c"]
    return _regime("tbd_sma_regime", bars, ctx, p, r,
                   lambda i, s: F.stop_dist(s, cl[i], pl[i] if s == 1 else ph[i], a[i])
                   if fin(pl[i]) else NAN)


def multi_orb(bars, ctx, p):
    """#49 (2026-10-23, gold; a 2019 Taiwan timely-ORB study). Opening range
    of `or_min` minutes at a big open -- `london` 08:00 London, `ny` 08:20 NY
    (COMEX), `nasdaq` 09:30 NY -- break above buys, below sells, stop the
    other side, out at the session's end (16:30 London; 13:30 NY). The video
    re-picks the minutes monthly from the trailing year; here they are an
    axis, which the selection then picks."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    a = _atr(bars, ctx)
    B = k["bar"]
    if p["session"] == "london":
        z = _local(bars, ctx, "london")
        m, day, wd, t0, t1 = z["min"], z["day"], z["wd"], 480, 990
    else:
        m, day, wd = k["min"], k["day"], k["wd"]
        t0, t1 = (500, 810) if p["session"] == "ny" else (570, 810)
    ex = numpy.flatnonzero(_at(m, B, t1)).tolist()
    out, plan = {}, {}
    cur = None
    for i in range(len(bars)):
        if wd[i] >= 5:
            continue
        d = int(day[i])
        if cur is None or cur[0] != d:
            cur = [d, None, None, False]
        if t0 <= m[i] and m[i] + B <= t0 + p["or_min"]:
            cur[1] = b["h"][i] if cur[1] is None else max(cur[1], b["h"][i])
            cur[2] = b["l"][i] if cur[2] is None else min(cur[2], b["l"][i])
            continue
        if cur[1] is None or cur[3] or m[i] < t0 + p["or_min"] or m[i] + B > t1:
            continue
        up, dn = b["h"][i] > cur[1], b["l"][i] < cur[2]
        if up == dn:
            continue
        side = 1 if up else -1
        cur[3] = True
        x = _next_at(ex, i)
        if _emit(
                out, i, side, F.stop_dist(side, b["c"][i],
                                          cur[2] if side == 1 else cur[1], a[i])):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_multi_orb", p, _plan_exit(plan))
    return out


def ny_orb_sd(bars, ctx, p):
    """#50 (2026-10-23, gold; Holmberg et al. 2013, oil). The NY session
    candle 08:00-13:30: trigger = open +/- (mean + `z` sd) of its absolute
    open-to-close move over the last 250 days (~0.8% on gold); first touch
    trades, once a day, out at 13:30, no stop (`stop_dr`)."""
    k = _clock(bars, ctx)
    b = F.base(bars, ctx)
    m, B = k["min"], k["bar"]
    moves = []
    ex = numpy.flatnonzero(_at(m, B, 810)).tolist()
    out, plan = {}, {}
    cur = None
    for i in range(len(bars)):
        d = int(k["day"][i])
        if m[i] == 480 and k["wd"][i] < 5:
            if cur is not None and cur[3] is not None:
                moves.append(abs(cur[3] / cur[1] - 1))
            cur = [d, b["o"][i], False, None]
        if cur is None or cur[0] != d:
            continue
        if m[i] + B <= 810:
            cur[3] = b["c"][i]
        if cur[2] or m[i] + B > 810 or len(moves) < 250:
            continue
        w = moves[-250:]
        mu = sum(w) / 250
        sd = math.sqrt(sum((x - mu) ** 2 for x in w) / 249)
        th = cur[1] * (mu + p["z"] * sd)
        up, dn = b["h"][i] >= cur[1] + th, b["l"][i] <= cur[1] - th
        if up == dn:
            continue
        cur[2] = True
        side = 1 if up else -1
        x = _next_at(ex, i)
        if _emit(out, i, side, _wide(ctx, i, p)):
            _plan(plan, i, x)
    _store_exit(ctx, "tbd_ny_orb_sd", p, _plan_exit(plan))
    return out


# =========================================================================== #
# registry: name -> (compute, axes, ny_clock, exit_signal, hold, symbols)
# =========================================================================== #

SIG = ("signal",)
NOCUT = (1440,)          # held positions have no close to be late for
STOP_DR = (1.0, 2.0)
FRESH = (2, BIG)
MD = "multiday"

_TABLE = [
    # name, compute, axes(g), hold, symbols
    ("tbd_session_box", session_box, lambda g: {
        "box": ("19_24", "19_03"), "trigger": ("touch", "close"),
        "stop": ("box", "wide"), "exit_at": (810, 960), "stop_dr": (1.5,)},
     MD, GOLD + ("usdjpy",)),
    ("tbd_weekday", weekday, lambda g: {
        "leg": ("both", "buy", "sell"), "stop_dr": STOP_DR}, MD, JPY),
    ("tbd_gotobi", gotobi, lambda g: {
        "t_in": (595,), "exit_at": (630, 600), "sl_pips": (10, 20)}, MD, JPY),
    ("tbd_nfp_drift", nfp_drift, lambda g: {
        "exit_wd": (1, 2, 3), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_cross_session", cross_session, lambda g: {
        "start": (1080, 1200), "exit_at": (540, 600), "winter": (0, 1),
        "stop_dr": (1.5,)}, MD, GOLD),
    ("tbd_friday_close", friday_close, lambda g: {
        "hold_min": (55, 30), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_supertrend", supertrend, lambda g: {
        "tf": (240, 60), "mult": (2.0, 3.0), "fresh": FRESH}, MD, GOLD),
    ("tbd_gap_down", gap_down, lambda g: {
        "thr": (0.2, 0.4), "side": ("short", "both"), "stop_dr": STOP_DR},
     MD, GOLD),
    ("tbd_sma_closes", sma_closes, lambda g: {
        "n": (50, 65, 80), "k": (3,), "fresh": FRESH, "stop_dr": STOP_DR},
     MD, GOLD),
    ("tbd_turn_of_month", turn_of_month, lambda g: {
        "before": (1, 2), "after": (3, 2), "stop_dr": STOP_DR},
     MD, GOLD + ("us500",)),
    ("tbd_rsi50", rsi50, lambda g: {
        "tf": (240, 60), "n": (14, 21), "fresh": FRESH}, MD, GOLD),
    ("tbd_overnight", overnight, lambda g: {
        "variant": ("comex", "winter", "asia"), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_adx_jump", adx_jump, lambda g: {
        "tf": (60,), "jump": (1.0, 1.5)}, MD, GOLD),
    ("tbd_busy_breakout", busy_breakout, lambda g: {
        "ratio": (1.0, 1.25)}, MD, GOLD),
    ("tbd_candle_count", candle_count, lambda g: {
        "tf": (60,), "n": (50, 100, 200), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_ema_cross", ema_cross, lambda g: {
        "tf": (60,), "fast": (12, 17), "fresh": FRESH}, MD, GOLD),
    ("tbd_dmi", dmi, lambda g: {
        "tf": (240, 60), "n": (14, 21), "fresh": FRESH}, MD, GOLD),
    ("tbd_fractal3", fractal3, lambda g: {
        "tf": (240, 60), "look": (20, 40), "side": ("long", "both")},
     MD, GOLD),
    ("tbd_fib_dynamic", fib_dynamic, lambda g: {
        "tf": (60,), "n": (20, 40), "fresh": FRESH}, MD, GOLD),
    ("tbd_adaptive_sar", adaptive_sar, lambda g: {
        "tf": (60,), "thr": (0.01, 0.02), "fresh": (BIG,)}, MD, GOLD),
    ("tbd_skew_week", skew_week, lambda g: {
        "q": (0.33, 0.25), "stop_dr": STOP_DR}, MD, ("usdchf",)),
    ("tbd_cci_trend", cci_trend, lambda g: {
        "tf": (60,), "n": (14, 20), "fresh": FRESH}, MD, GOLD),
    ("tbd_macdv", macdv, lambda g: {
        "tf": (60,), "lo": (30, 50), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_spx_gold", spx_gold, lambda g: {
        "k": (1.5, 2.0), "sd_n": (60, 250), "stop_dr": (1.5,)}, MD, GOLD),
    ("tbd_fx_fix", fx_fix, lambda g: {
        "variant": ("ecb", "london"), "sl_pct": (0.1, 0.3)}, MD, ("eurusd",)),
    ("tbd_multi_span", multi_span, lambda g: {
        "m": (6, 8), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_nasdaq_usd", nasdaq_usd, lambda g: {
        "k": (1.5, 2.0), "sl_pct": (0.5, 1.0)}, MD, USD_QUOTE + USD_BASE),
    ("tbd_overnight_follow", overnight_follow, lambda g: {
        "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_ibs", ibs, lambda g: {
        "thr": (0.15, 0.2), "stop_dr": STOP_DR}, MD, ("us500",)),
    ("tbd_bb_fade", bb_fade, lambda g: {
        "w": (2.0, 2.5), "sl_pct": (1.0, 2.0), "fresh": (2,)}, MD, ("audusd",)),
    ("tbd_month_end_fix", month_end_fix, lambda g: {
        "min_pct": (0.1, 0.2)}, MD, ("gbpusd",)),
    ("tbd_overnight_drift", overnight_drift, lambda g: {
        "frac": (0.5, 1.0)}, MD, ("us500",)),
    ("tbd_macd_slow", macd_slow, lambda g: {
        "tf": (240, 1440), "stop_dr": STOP_DR}, MD, GOLD),
    ("tbd_vol_breakout", vol_breakout, lambda g: {
        "k": (0.5, 0.6)}, MD, GOLD),
    ("tbd_sma_regime", sma_regime, lambda g: {
        "scale": (0.75, 1.0, 1.25), "fresh": (2,)}, MD, GOLD),
    ("tbd_multi_orb", multi_orb, lambda g: {
        "session": ("london", "ny", "nasdaq"), "or_min": (15, 30, 60)},
     MD, GOLD),
    ("tbd_ny_orb_sd", ny_orb_sd, lambda g: {
        "z": (1.0, 1.28), "stop_dr": STOP_DR}, MD, GOLD),
]

#: Flat by the cash-session close: the engine's own flatten is the exit.
_SESSION = [
    ("tbd_nfp_straddle", nfp_straddle, lambda g: {
        "range_min": (15, 30), "day": ("nfp", "any_friday"),
        "exit_mode": ("rr_2", "rr_3"), "last_entry_minute": g["last"]}, GOLD),
    ("tbd_volatile_red", volatile_red, lambda g: {
        "vol_n": (20,), "q": (0.75, 0.5), "stop_dr": STOP_DR,
        "exit_mode": ("days_1",), "last_entry_minute": g["last"]}, ("ustec",)),
    ("tbd_orb_10r", orb_10r, lambda g: {
        "or_bars": (1, 3), "exit_mode": ("rr_10", "rr_5"),
        "last_entry_minute": g["last"]}, ("ustec",)),
]


def _with_exit(axes):
    return lambda g: {**axes(g), "exit_mode": SIG, "last_entry_minute": NOCUT}


SPECS = {}
for _n, _fn, _ax, _hold, _syms in _TABLE:
    SPECS[_n] = (_fn, _with_exit(_ax), False, _exit_for(_n, _fn), _hold, None)
for _n, _fn, _ax, _syms in _SESSION:
    SPECS[_n] = (_fn, _ax, False, None, "session", None)

#: Axis names that are labels, not scales, for the robustness neighbours.
CATEGORICAL = ("box", "trigger", "stop", "leg", "variant", "side", "session",
               "day")
