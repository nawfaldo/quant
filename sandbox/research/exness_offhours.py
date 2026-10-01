"""The `offhours` wave: rules that only exist OUTSIDE the cash session.

WHY THIS EXISTS. Every family before it was written, scored and sealed inside
the market's own session (`cfd_families.SESSION`), so the study has never asked
what happens in the other two thirds of the day -- the Asian hours on a US
index, the US afternoon on a European one, the night on gold. These CFDs quote
through all of it, and the broker's own minute table prices every one of those
hours at the spread that hour actually carried.

So these rules read the STRUCTURE OF THE BROKER DAY, which is the one thing no
in-session rule can see: which session set the range, how the last cash session
closed, what the night has done since, and whether a given clock hour has been
drifting. Every window is a fixed, pre-registered convention rather than a
searched one, in real New York time:

    asia     18:00-03:00   the reopen through Tokyo
    london   03:00-08:00   London's morning, before New York
    newyork  08:00-17:00   everything after, up to the rollover
    rth      the market's own cash session (`spec["rth"]`), which sits inside
             one of the above for every symbol in the study

HOW IT RUNS. On any `--window`, the cash session included. The structure is
built on every bar the market printed and then read at the traded window's
bars (`build_all`), so an Asian range can be traded in a London window and the
last cash close read at night. A rule whose event never falls inside the
traded window -- the London drive in an `evening` window -- simply never fires
there. `off` refuses any entry while the cash session is open, which kills the
one family whose thesis is AN ENTRY AT THE OPEN (`DAY_ONLY`); it is skipped
there. Bars above 60m are refused (`BAR_MAX`): a 240m bar straddles the
session boundaries these rules are built on.

THE DIRECTION AXIS MEANS WHAT IT MEANS IN `exness_fastbar`. Each signal works
out the side its event points to -- a break of the Asian high points up, a
night that has run far above the last close points up, a clock hour that has
drifted up points up -- and `follow` trades that side while `fade` trades the
other. So "fade the London fakeout" and "follow the Asian breakout" are both
cells, and neither is baked in as the family's opinion.

CAUSAL. Every reading at bar `i` is built from bars `0..i`; the engine fills at
bar `i + 1`'s open. A session's range exists only once its last bar has closed,
and the prior cash session is the last one that has FINISHED.

WHAT THIS WAVE IS NOT. It is not a claim that any of these work. It was written
after twenty-odd waves of results, and the null is the usual one: judge a winner
by its holdout and by `why`, not by its in-sample rank.
"""
from __future__ import annotations

import math
from array import array

TS, O, H, L, C, V = range(6)
NAN = float("nan")

#: Real New York minutes. Asia wraps past midnight.
ASIA = (18 * 60, 3 * 60)
LONDON = (3 * 60, 8 * 60)
NEWYORK = (8 * 60, 17 * 60)
#: The broker day starts at the 17:00 rollover; minutes are counted from there.
ROLLOVER = 17 * 60

#: Coarsest bar the wave may run on; see the module docstring.
BAR_MAX = 60

DIRECTION = ("follow", "fade")

#: Families whose event IS an entry at the cash open, which `--window off`
#: refuses by construction.
DAY_ONLY = frozenset(("oh_open_vs_overnight",))

#: Label axes; everything else here is a scale.
CATEGORICAL = ("phase", "entry_at", "handoff", "pattern")

#: The grid for `oh_hour_drift`, whose thesis is ONE clock slot: hold it for one,
#: two or four bars and leave. The shared grid's targets and day-scale trail
#: would replace that thesis with a different one.
HOUR_EXITS = {"exit_mode": ("time_1", "time_2", "time_4"),
              "stop_day": (0.2, 0.4), "trend": ("none",),
              "vol_mode": ("none", "calm")}


# --------------------------------------------------------------------------- #
# the block
# --------------------------------------------------------------------------- #

def _phase(minute):
    if minute >= ASIA[0] or minute < ASIA[1]:
        return 0
    return 1 if minute < LONDON[1] else 2


def _inside(minute, opened, closed):
    if opened < closed:
        return opened <= minute < closed
    return minute >= opened or minute < closed


def _nan_array(n):
    return array("d", [NAN]) * n


def build(bars, spec, bar_minutes):
    """`ctx["oh"]`: the broker day's structure, one entry per bar.

    `bars` is every bar the market printed, on whatever clock the traded window
    uses; the broker day and the real minute are worked out from `shift_hours`,
    so the structure is the same whichever window it is later sampled at.
    Float series are `array('d')` with NaN for "not yet"; flags are
    `bytearray`s; the counters are `array('i')`.
    """
    n = len(bars)
    shift = spec["shift_hours"] * 3600
    rth_open, rth_close = spec["rth"]
    real = array("i", [((bar[TS] - shift) // 60) % 1440 for bar in bars])
    # Minutes since the rollover -- the broker day's own clock, which never
    # wraps inside a day -- and the broker day itself.
    clock = array("i", [(minute - ROLLOVER) % 1440 for minute in real])
    day = [(bar[TS] - shift + (1440 - ROLLOVER) * 60) // 86_400 for bar in bars]
    phase = bytearray(_phase(minute) for minute in real)
    in_rth = bytearray(_inside(minute, rth_open, rth_close) for minute in real)

    out = {
        "real": real, "clock": clock, "phase": phase, "rth": in_rth,
        "day_bar": array("i", [0]) * n, "phase_bar": array("i", [0]) * n,
        "day_open": _nan_array(n), "day_vwap": _nan_array(n),
        # A finished session's range, carried on every later bar of the day.
        "asia_high": _nan_array(n), "asia_low": _nan_array(n),
        "asia_open": _nan_array(n), "asia_close": _nan_array(n),
        "london_high": _nan_array(n), "london_low": _nan_array(n),
        "london_open": _nan_array(n), "london_close": _nan_array(n),
        # The phase in progress, INCLUDING this bar.
        "phase_open": _nan_array(n), "phase_high": _nan_array(n),
        "phase_low": _nan_array(n),
        # The last cash session to have finished.
        "rth_open": _nan_array(n), "rth_high": _nan_array(n),
        "rth_low": _nan_array(n), "rth_close": _nan_array(n),
        "rth_last_hour": _nan_array(n),
        # This broker day's range BEFORE its cash session, on the first
        # in-session bar only.
        "pre_high": _nan_array(n), "pre_low": _nan_array(n),
        # True on the first bar after a cash session finished, same day.
        "post_close": bytearray(n),
        "asia_ratio": {},
    }

    last_hour_bars = max(1, 60 // bar_minutes)
    current_day = None
    sessions = {}            # phase -> [open, high, low, close] for today
    running = None           # [phase, open, high, low]
    rth = None               # [day, open, high, low, closes]
    finished = None          # (open, high, low, close, last_hour)
    pre = None               # [high, low] before today's cash session
    day_count = phase_count = 0
    widths = []              # (day, asia width) in day order

    for i, bar in enumerate(bars):
        if day[i] != current_day:
            if 0 in sessions:
                widths.append((current_day, sessions[0][1] - sessions[0][2]))
            current_day, sessions, running, pre = day[i], {}, None, None
            day_count = 0
            day_open = bar[O]
            notional = volume = 0.0
        out["day_open"][i] = day_open
        notional += (bar[H] + bar[L] + bar[C]) / 3 * bar[V]
        volume += bar[V]
        if volume > 0:
            out["day_vwap"][i] = notional / volume
        out["day_bar"][i] = day_count
        day_count += 1

        now = phase[i]
        if running is None or running[0] != now:
            running = [now, bar[O], bar[H], bar[L]]
            phase_count = 0
        else:
            running[2] = max(running[2], bar[H])
            running[3] = min(running[3], bar[L])
        out["phase_bar"][i] = phase_count
        phase_count += 1
        out["phase_open"][i], out["phase_high"][i], out["phase_low"][i] = (
            running[1], running[2], running[3])

        # A session is FINISHED on every bar of a later phase the same day.
        for key, name in ((0, "asia"), (1, "london")):
            done = sessions.get(key)
            if done is not None and now > key:
                out[name + "_open"][i], out[name + "_high"][i] = done[0], done[1]
                out[name + "_low"][i], out[name + "_close"][i] = done[2], done[3]
        record = sessions.get(now)
        if record is None:
            sessions[now] = [bar[O], bar[H], bar[L], bar[C]]
        else:
            record[1] = max(record[1], bar[H])
            record[2] = min(record[2], bar[L])
            record[3] = bar[C]

        # The cash session. Closed on the first bar outside it, or when a
        # new broker day starts without one (a half day, a missing bar).
        if in_rth[i]:
            if rth is not None and rth[0] != day[i]:
                finished = _close_rth(rth, last_hour_bars)
                rth = None
            if rth is None:
                if pre is not None:
                    out["pre_high"][i], out["pre_low"][i] = pre
                rth = [day[i], bar[O], bar[H], bar[L], [bar[C]]]
            else:
                rth[2] = max(rth[2], bar[H])
                rth[3] = min(rth[3], bar[L])
                rth[4].append(bar[C])
        else:
            if rth is not None:
                finished = _close_rth(rth, last_hour_bars)
                if rth[0] == day[i]:
                    out["post_close"][i] = 1
                rth = None
            pre = ([bar[H], bar[L]] if pre is None
                   else [max(pre[0], bar[H]), min(pre[1], bar[L])])
        if finished is not None:
            (out["rth_open"][i], out["rth_high"][i], out["rth_low"][i],
             out["rth_close"][i], out["rth_last_hour"][i]) = finished

    if 0 in sessions:
        widths.append((current_day, sessions[0][1] - sessions[0][2]))
    out["asia_ratio"] = _asia_ratios(bars, day, phase, widths)
    return out


def _close_rth(rth, last_hour_bars):
    closes = rth[4]
    hour = (closes[-1] - closes[-1 - last_hour_bars]
            if len(closes) > last_hour_bars else NAN)
    return rth[1], rth[2], rth[3], closes[-1], hour


#: Trailing Asian ranges the squeeze is measured against, in days.
SQUEEZE_DAYS = (10, 20)


def _asia_ratios(bars, day, phase, widths):
    """Per lookback: today's finished Asian range over the median of the
    previous N days' -- on the bars AFTER Asia only, NaN elsewhere."""
    out = {}
    for lookback in SQUEEZE_DAYS:
        ratio = {}
        for k in range(lookback, len(widths)):
            prior = sorted(w for _, w in widths[k - lookback:k])
            middle = prior[len(prior) // 2]
            if middle > 0:
                ratio[widths[k][0]] = widths[k][1] / middle
        series = _nan_array(len(bars))
        for i in range(len(bars)):
            if phase[i] > 0:
                value = ratio.get(day[i])
                if value is not None:
                    series[i] = value
        out[lookback] = series
    return out


#: Lookbacks for the clock-slot drift, in days.
DRIFT_DAYS = (60, 120)


def hour_drift(bars, clock, bar_minutes):
    """`{lookback: t}`: at bar `i`, the t-statistic of the NEXT clock slot's
    open-to-close log return over the previous `lookback` days.

    The next slot's list cannot hold today's value yet -- that bar has not
    happened -- so every reading is from earlier days only.
    """
    sums = {}                # slot -> ([cumulative sum], [cumulative square])
    out = {n: _nan_array(len(bars)) for n in DRIFT_DAYS}
    for i, bar in enumerate(bars):
        nxt = (clock[i] + bar_minutes) % 1440
        table = sums.get(nxt)
        if table is not None:
            s, q = table
            count = len(s) - 1
            for n in DRIFT_DAYS:
                if count < n:
                    continue
                total = s[-1] - s[-1 - n]
                mean = total / n
                var = (q[-1] - q[-1 - n]) / n - mean * mean
                if var > 0:
                    out[n][i] = mean / math.sqrt(var / n)
        if bar[O] > 0 and bar[C] > 0:
            value = math.log(bar[C] / bar[O])
            s, q = sums.setdefault(clock[i], ([0.0], [0.0]))
            s.append(s[-1] + value)
            q.append(q[-1] + value * value)
    return out


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #

def _late(ctx, bar, params):
    return bar[TS] % 86_400 // 60 > params["last_entry_minute"]


def _out(side, params):
    if not side:
        return None
    return side if params["direction"] == "follow" else -side


def _ok(*values):
    return all(v == v for v in values)


FAMILIES = {}


def family(axes, exits=None):
    """Register `oh_<name>`: the engine signature, the cutoff, the flip."""
    def register(body):
        name = "oh_" + body.__name__.lstrip("_")

        def signal(index, bars, ctx, params, _state):
            if index < 1 or _late(ctx, bars[index], params):
                return None
            return _out(body(index, bars, ctx, ctx["oh"], params), params)
        signal.__name__ = name + "_signal"
        signal.__doc__ = body.__doc__
        FAMILIES[name] = (signal, dict(axes, direction=DIRECTION), exits)
        return body
    return register


def _cross_out(prev, now, high, low, buffer):
    """+1 on the close that crosses above `high + buffer`, -1 below `low - buffer`."""
    if prev <= high + buffer < now:
        return 1
    if prev >= low - buffer > now:
        return -1
    return None


# ---- ranges set by one session, broken in the next --------------------------

@family({"buffer_atr": (0.0, 0.25), "phase": ("london", "any")})
def _asia_break(i, bars, ctx, f, p):
    """The finished Asian range, broken on a close. `london` takes the break
    only in London's morning; `any` takes it until the cutoff."""
    if f["phase"][i] == 0 or (p["phase"] == "london" and f["phase"][i] != 1):
        return None
    high, low, atr = f["asia_high"][i], f["asia_low"][i], ctx["atr"][i]
    if not _ok(high, low) or not atr:
        return None
    return _cross_out(bars[i - 1][C], bars[i][C], high, low,
                      p["buffer_atr"] * atr)


@family({"buffer_atr": (0.0, 0.25)})
def _london_break(i, bars, ctx, f, p):
    """London's finished morning range, broken in the New York hours."""
    if f["phase"][i] != 2:
        return None
    high, low, atr = f["london_high"][i], f["london_low"][i], ctx["atr"][i]
    if not _ok(high, low) or not atr:
        return None
    return _cross_out(bars[i - 1][C], bars[i][C], high, low,
                      p["buffer_atr"] * atr)


@family({"lookback": (10, 20), "ratio": (0.6, 0.8)})
def _asia_squeeze(i, bars, ctx, f, p):
    """An Asian range narrow against its own recent days, then broken -- the
    coil is the session, not a band."""
    squeeze = f["asia_ratio"][p["lookback"]][i]
    if not squeeze == squeeze or squeeze > p["ratio"]:
        return None
    high, low = f["asia_high"][i], f["asia_low"][i]
    if not _ok(high, low):
        return None
    return _cross_out(bars[i - 1][C], bars[i][C], high, low, 0.0)


@family({"until": (5 * 60, 7 * 60)})
def _london_fakeout(i, bars, ctx, f, p):
    """London pokes through the Asian high (low) and closes back inside it.
    The event points BACK INTO the range: `follow` trades the rejection."""
    if f["phase"][i] != 1 or f["real"][i] >= p["until"]:
        return None
    high, low = f["asia_high"][i], f["asia_low"][i]
    if not _ok(high, low):
        return None
    close = bars[i][C]
    if f["phase_high"][i] > high and low < close < high:
        return -1
    if f["phase_low"][i] < low and low < close < high:
        return 1
    return None


# ---- the last cash session, read from the night after it --------------------

def _entry_bar(f, i, when):
    if when == "reopen":
        return f["day_bar"][i] == 0
    return f["phase"][i] == 1 and f["phase_bar"][i] == 0


@family({"threshold": (0.25, 0.5), "entry_at": ("reopen", "london")})
def _rth_handoff(i, bars, ctx, f, p):
    """The last cash session's open-to-close move, in daily ranges, carried
    into the night -- entered at the 18:00 reopen or at London's open."""
    if not _entry_bar(f, i, p["entry_at"]):
        return None
    opened, closed, risk = f["rth_open"][i], f["rth_close"][i], ctx["risk"][i]
    if not _ok(opened, closed) or not risk:
        return None
    move = (closed - opened) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"location": (0.8, 0.9), "entry_at": ("reopen", "london")})
def _rth_close_location(i, bars, ctx, f, p):
    """Where the last cash session CLOSED inside its own range. A close on the
    high points up whatever the session's net move was."""
    if not _entry_bar(f, i, p["entry_at"]):
        return None
    high, low, closed = f["rth_high"][i], f["rth_low"][i], f["rth_close"][i]
    if not _ok(high, low, closed) or high <= low:
        return None
    where = (closed - low) / (high - low)
    return 1 if where >= p["location"] else (
        -1 if where <= 1.0 - p["location"] else None)


@family({"threshold": (0.05, 0.15)})
def _post_close(i, bars, ctx, f, p):
    """The cash session's LAST HOUR, carried into the hours right after it --
    the handoff from the closing flows to whoever trades next."""
    if not f["post_close"][i]:
        return None
    hour, risk = f["rth_last_hour"][i], ctx["risk"][i]
    if not _ok(hour) or not risk:
        return None
    move = hour / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"mode": ("break", "reject"), "buffer_atr": (0.0, 0.25)})
def _prior_level(i, bars, ctx, f, p):
    """The last cash session's high and low, met in the night. `break` points
    through the level on a closing cross; `reject` points back from a bar that
    traded through it and closed on the near side."""
    if f["rth"][i]:
        return None
    high, low, atr = f["rth_high"][i], f["rth_low"][i], ctx["atr"][i]
    if not _ok(high, low) or not atr:
        return None
    bar = bars[i]
    if p["mode"] == "break":
        return _cross_out(bars[i - 1][C], bar[C], high, low, p["buffer_atr"] * atr)
    buffer = p["buffer_atr"] * atr
    if bar[H] > high + buffer and bar[C] < high:
        return -1
    if bar[L] < low - buffer and bar[C] > low:
        return 1
    return None


@family({"threshold": (0.3, 0.5, 0.8), "phase": ("asia", "london", "any")})
def _night_extension(i, bars, ctx, f, p):
    """How far the night has carried price from the last cash close, in daily
    ranges. The event points WITH the extension; `fade` is the reversion to
    the close that an illiquid night is supposed to owe."""
    if f["rth"][i] or (p["phase"] == "asia" and f["phase"][i] != 0) or (
            p["phase"] == "london" and f["phase"][i] != 1):
        return None
    closed, risk = f["rth_close"][i], ctx["risk"][i]
    if not _ok(closed) or not risk:
        return None
    move = (bars[i][C] - closed) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"hours_before": (1, 2, 4), "threshold": (0.1, 0.25)})
def _pre_open_drift(i, bars, ctx, f, p):
    """The night's move since the last cash close, read `hours_before` the next
    cash open and carried INTO it. Refused where that hour falls before the
    18:00 reopen."""
    opened = (ctx["cfg"]["rth"][0] - 17 * 60) % 1440
    target = opened - 60 * p["hours_before"]
    if target <= 60:
        return None
    clock = f["clock"][i]
    if not clock < target <= clock + ctx["bar_minutes"]:
        return None
    closed, risk = f["rth_close"][i], ctx["risk"][i]
    if not _ok(closed) or not risk:
        return None
    move = (bars[i][C] - closed) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"location": (0.8, 0.9)})
def _open_vs_overnight(i, bars, ctx, f, p):
    """The cash open's place in the range the night built. Opening on the
    night's high points up. An entry AT the open, so `day` window only."""
    high, low = f["pre_high"][i], f["pre_low"][i]
    if not _ok(high, low) or high <= low:
        return None
    where = (bars[i][O] - low) / (high - low)
    return 1 if where >= p["location"] else (
        -1 if where <= 1.0 - p["location"] else None)


# ---- one session's move, handed to the next ---------------------------------

@family({"handoff": ("asia_london", "london_newyork"),
         "threshold": (0.1, 0.25)})
def _session_momentum(i, bars, ctx, f, p):
    """The finished session's open-to-close move, entered on the first bar of
    the next one."""
    target, name = (1, "asia") if p["handoff"] == "asia_london" else (2, "london")
    if f["phase"][i] != target or f["phase_bar"][i] != 0:
        return None
    opened, closed, risk = f[name + "_open"][i], f[name + "_close"][i], ctx["risk"][i]
    if not _ok(opened, closed) or not risk:
        return None
    move = (closed - opened) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"pattern": ("agree", "disagree"), "threshold": (0.05, 0.15)})
def _dual_session(i, bars, ctx, f, p):
    """Asia and London together, read at New York's first bar. `agree` points
    the way both went; `disagree` points the way LONDON went against Asia."""
    if f["phase"][i] != 2 or f["phase_bar"][i] != 0:
        return None
    risk = ctx["risk"][i]
    a0, a1 = f["asia_open"][i], f["asia_close"][i]
    l0, l1 = f["london_open"][i], f["london_close"][i]
    if not _ok(a0, a1, l0, l1) or not risk:
        return None
    asia, london = (a1 - a0) / risk, (l1 - l0) / risk
    if abs(asia) < p["threshold"] or abs(london) < p["threshold"]:
        return None
    same = (asia > 0) == (london > 0)
    if same != (p["pattern"] == "agree"):
        return None
    return 1 if london > 0 else -1


@family({"bars_in": (1, 2, 4), "threshold": (0.1, 0.2)})
def _reopen_drive(i, bars, ctx, f, p):
    """The first bars after the 18:00 reopen, their net move in daily ranges.
    Not `orb`: no range and no break, just which way the reopen went."""
    if f["day_bar"][i] != p["bars_in"] - 1 or f["phase"][i] != 0:
        return None
    opened, risk = f["day_open"][i], ctx["risk"][i]
    if not _ok(opened) or not risk:
        return None
    move = (bars[i][C] - opened) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


@family({"hours": (1, 2), "threshold": (0.1, 0.2)})
def _london_drive(i, bars, ctx, f, p):
    """London's first one or two hours, their net move -- the European open's
    verdict on the Asian night."""
    count = max(1, 60 * p["hours"] // ctx["bar_minutes"])
    if f["phase"][i] != 1 or f["phase_bar"][i] != count - 1:
        return None
    opened, risk = f["phase_open"][i], ctx["risk"][i]
    if not _ok(opened) or not risk:
        return None
    move = (bars[i][C] - opened) / risk
    return 1 if move > p["threshold"] else (-1 if move < -p["threshold"] else None)


# ---- the night on its own terms ---------------------------------------------

@family({"threshold": (0.2, 0.4), "phase": ("asia", "london")})
def _vwap_stretch(i, bars, ctx, f, p):
    """Distance from the broker day's own VWAP (anchored at the 18:00 reopen),
    in daily ranges, inside one night session. The event points with the
    stretch; `fade` is the thin-market reversion."""
    if f["phase"][i] != (0 if p["phase"] == "asia" else 1):
        return None
    vwap, risk = f["day_vwap"][i], ctx["risk"][i]
    if not _ok(vwap) or not risk:
        return None
    gap = (bars[i][C] - vwap) / risk
    return 1 if gap > p["threshold"] else (-1 if gap < -p["threshold"] else None)


@family({"lookback": DRIFT_DAYS, "t_min": (1.5, 2.0, 2.5)}, exits=HOUR_EXITS)
def _hour_drift(i, bars, ctx, f, p):
    """The NEXT clock slot's own history: if its open-to-close return over the
    last N days has a t-statistic past `t_min`, trade that sign for a bar or
    few. Nothing is searched per hour -- the slot is chosen by its own past."""
    t = f["drift"][p["lookback"]][i]
    if not t == t or abs(t) < p["t_min"]:
        return None
    return 1 if t > 0 else -1


def build_all(full, spec, bar_minutes, bars):
    """The block and the drift table, built on `full` -- every bar the market
    printed -- and sampled at `bars`, the traded window's.

    The rules read sessions the traded window may not contain: an Asian range
    broken in a London window, the last cash close read at night. So the
    structure is built on the whole day and each window bar takes the reading
    of the same stamp. The rollover hour (17:00-18:00) is left out of the
    structure -- index and metal CFDs do not print in it, and letting FX open
    its "day" there would move every reopen reading an hour. A window bar in
    that hour reads NaN and fires nothing.
    """
    shift = spec["shift_hours"] * 3600
    full = [bar for bar in full
            if (((bar[TS] - shift) // 60) % 1440) // 60 != ROLLOVER // 60]
    out = build(full, spec, bar_minutes)
    out["drift"] = hour_drift(full, out["clock"], bar_minutes)
    where = {bar[TS]: i for i, bar in enumerate(full)}
    index = [where.get(bar[TS], -1) for bar in bars]
    return _sample(out, index)


def _sample(value, index):
    """Every series in the block, re-read at `index` (-1 is "no reading")."""
    if isinstance(value, dict):
        return {key: _sample(item, index) for key, item in value.items()}
    if isinstance(value, bytearray):
        return bytearray(value[i] if i >= 0 else 0 for i in index)
    if isinstance(value, array):
        missing = NAN if value.typecode == "d" else -1
        return array(value.typecode,
                     (value[i] if i >= 0 else missing for i in index))
    return value
