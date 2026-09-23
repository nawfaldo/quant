"""Ten intraday strategy families on USOIL, with a sealed 2025-2026 holdout.

Same protocol as `es_strategy_research` and `btc_families_research`, so the
three instruments are directly comparable. Ten families compete, covering the
four requested categories plus one oil-specific event:

  breakout        ``orb``        opening-range breakout or fade
                  ``overnight``  break of the range formed outside the session
                  ``pdr``        break or fade of the prior session's range
  trend following ``donchian``   Donchian channel breakout with an ATR trail
                  ``ma_cross``   fast/slow EMA crossover
  momentum        ``momentum``   intraday time-series momentum or fade
                  ``gap``        session open against the prior session close
  mean reversion  ``vwap``       distance from the running session VWAP
                  ``zscore``     close against a rolling mean, in sigmas
  event           ``eia``        the Wednesday 10:30 inventory release

CLOCK. `usoil_1m` is **New York wall-clock**, established from the table
itself and not assumed: the daily maintenance break is the empty 17:00 hour
(ES, which is Chicago, breaks at 16:00 -- see [es-1m-is-chicago-time]); the
top volume minutes of 2021-2023 are 09:00, 10:30 and 14:27-14:29, which are
the NYMEX open, the EIA inventory release and the floor close in New York.
Nothing here converts timestamps; bars are used exactly as stored.

SESSION. 09:00-14:30 New York, the WTI pit session, not the 09:30-16:00 equity
session the other instruments use. The data says so: hourly volume runs
1650/2850/2810/2490 across 08:00-11:00 and collapses from 1799 in hour 14 to
690 in hour 15. Positions may only be opened inside that window and are
flattened at its close, so nothing carries overnight risk.

CONTRACT AND COSTS. Exness USOIL is 1000 barrels a lot with a 0.01 lot minimum,
so quantity is carried in **barrels** and the size step is 10 barrels. A one
dollar move is one dollar a barrel. The cost model is the 0.20 spread charged
wholly at entry, as specified; Zero-account commission is 6.25 USD a lot, i.e.
0.00625 a barrel, which this spread already dwarfs, so ignoring it is
conservative rather than optimistic. `validate` reports each winner's
break-even spread because 0.20 on a ~70 dollar barrel is ~28bp, a heavy toll
for an intraday rule.

WHY SELECTION RUNS ON 10,000 AND REPORTS ON 1,000. The requested account is
1,000 dollars. At that balance the 10-barrel step is the binding constraint,
not the signal: a 1.5% risk budget is 15 dollars, so a cell whose stop is wider
than 1.50 a barrel cannot round up to a single step and silently takes *no
trades at all*, while cells just inside the boundary trade at an effective risk
that is whatever the rounding leaves. Ranking 14k cells under that regime ranks
lot arithmetic, not edge -- the failure recorded in
[[lot-granularity-fakes-low-drawdown]]. Selection therefore runs at 10,000,
where the step is smooth, and every selected cell is then reported at **both**
10,000 and the requested 1,000 so the deployable figure is the one you read.

ROLLING CFD CAVEAT. `usoil_1m` is a broker CFD that tracks the front month and
jumps at each contract roll. Rolls happen outside the session, so they cannot
touch an intraday trade, but they *do* enter the `gap` and `overnight`
families' inputs as price moves that carry no information. Treat a winner in
those two families with more suspicion than the other eight.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import es_strategy_research as es


OUTPUT = os.path.join(os.path.dirname(__file__), "usoil_families_selection.json")

#: Selection balance. See the module docstring: the requested 1,000 is reported,
#: never ranked on.
SELECT_BALANCE = 10_000.0
REPORT_BALANCES = (10_000.0, 1_000.0)

SPREAD = 0.2            # dollars a barrel, charged wholly at entry
MARGIN = 0.25           # self-imposed notional cap; Exness itself allows far more
STEP = 10.0             # barrels; 0.01 lot of a 1000 barrel contract

WARMUP_START = "2016-01-01"
IS_START = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 7, tzinfo=timezone.utc).timestamp())
FULL_IS_YEARS = tuple(range(2018, 2025))

#: New York minutes: the WTI pit session.
SESSION_OPEN_MINUTE = 9 * 60           # 09:00
SESSION_CLOSE_MINUTE = 14 * 60 + 30    # 14:30, the flatten bucket

BARS_PER_SESSION = 12                  # 30-minute buckets, 09:00 through 14:30
ATR_BARS = 2 * BARS_PER_SESSION
VOLATILITY_BARS = 20 * BARS_PER_SESSION
LONG_VOLATILITY_BARS = 100 * BARS_PER_SESSION
ANNUAL_PERIODS = 252.0 * BARS_PER_SESSION

#: WTI realised volatility sits near 35-45% in a normal year, so this throttles
#: 2020 and the 2022 invasion months rather than the whole sample.
VOL_TARGET = 0.40

#: Declared before any result was seen. Requiring all seven years profitable --
#: the ES and BTC gate -- is not survivable on an instrument whose sample
#: contains April 2020, so six of seven is the floor here, and it is fixed.
MIN_POSITIVE_YEARS = 6
MIN_TRADES = 300
#: The inventory release fires once a week by construction, so it cannot reach
#: the daily families' trade count and is not asked to.
FAMILY_MIN_TRADES = {"eia": 150}
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 8.0
SELECTION_DD_LIMIT = 20.0
ANNUAL_DD_LIMIT = 22.0
NEIGHBOUR_DD_LIMIT = 22.0

TS, O, H, L, C, V = range(6)


# --------------------------------------------------------------------------- #
# bars and context
# --------------------------------------------------------------------------- #


def all_bars_30m(phase):
    """Every 30-minute bucket from the warm-up start, oldest first.

    Out-of-session buckets are kept because the overnight and gap anchors are
    built from them; the traded list is filtered out of this one.

    The selector's SQL cannot return a holdout row. `validate` keeps the full
    history so every indicator warms up exactly as it did in the sealed run.
    """
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    sql = (
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        "last(close),sum(volume) FROM usoil_1m "
        f"WHERE timestamp >= '{WARMUP_START}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    key = f"{sql}:{data._table_fingerprint(['usoil_1m'])}"

    def build():
        return [[int(row[0]) // 1_000_000, *(float(value) for value in row[1:])]
                for row in data.query(sql)]

    # Keyed on whether the holdout is withheld, not on the phase name: `edge`
    # and `validate` read exactly the same rows, and giving them separate cache
    # entries only makes the second one re-query for nothing.
    scope = "is" if phase == "select" else "full"
    return [tuple(row) for row in data._cached(f"usoil_30m_{scope}", key, build)]


def in_session(ts):
    return SESSION_OPEN_MINUTE <= ts % 86_400 // 60 <= SESSION_CLOSE_MINUTE


def trailing_annual_volatility(bars, periods):
    """Causal realised volatility over the prior `periods` buckets, annualised."""
    returns = [0.0]
    for previous, current in zip(bars, bars[1:]):
        returns.append(math.log(current[C] / previous[C]))
    out = [None] * len(bars)
    total = total_sq = 0.0
    for index, value in enumerate(returns):
        total += value
        total_sq += value * value
        if index >= periods:
            old = returns[index - periods]
            total -= old
            total_sq -= old * old
        if index >= periods - 1:
            mean = total / periods
            variance = max(0.0, total_sq / periods - mean * mean)
            out[index] = math.sqrt(variance * ANNUAL_PERIODS)
    return out


def rolling_mean_sigma(values, period):
    """`(mean, sigma)` of the `period` values ending at each index, inclusive."""
    means = [None] * len(values)
    sigmas = [None] * len(values)
    total = total_sq = 0.0
    for index, value in enumerate(values):
        total += value
        total_sq += value * value
        if index >= period:
            old = values[index - period]
            total -= old
            total_sq -= old * old
        if index >= period - 1:
            mean = total / period
            means[index] = mean
            sigmas[index] = math.sqrt(max(0.0, total_sq / period - mean * mean))
    return means, sigmas


def session_anchors(full):
    """Per session day: the out-of-session range, and the prior pit close.

    The evening belongs to the session it precedes, so the "overnight" window
    for day D is 14:30 on D-1 through 09:00 on D. `prior_close` is the last
    *in-session* close before that, i.e. the 14:00-14:30 bucket, rather than the
    14:30-15:00 flatten bucket, so it is the pit close a trader would quote.
    """
    outside = {}
    pit_close = {}
    for bar in full:
        minute = bar[TS] % 86_400 // 60
        day = bar[TS] // 86_400
        if minute >= SESSION_CLOSE_MINUTE:
            day += 1                      # the evening belongs to the next session
        if minute < SESSION_OPEN_MINUTE or minute >= SESSION_CLOSE_MINUTE:
            entry = outside.get(day)
            if entry is None:
                outside[day] = [bar[H], bar[L]]
            else:
                entry[0] = max(entry[0], bar[H])
                entry[1] = min(entry[1], bar[L])
        else:
            pit_close[bar[TS] // 86_400] = bar[C]
    ordered = sorted(pit_close)
    prior = {day: pit_close[ordered[index - 1]]
             for index, day in enumerate(ordered) if index}
    return {day: tuple(value) for day, value in outside.items()}, prior


def prior_session_range(bars):
    """`{day: (high, low)}` of the *previous* traded session."""
    per_day = {}
    for bar in bars:
        day = bar[TS] // 86_400
        entry = per_day.get(day)
        if entry is None:
            per_day[day] = [bar[H], bar[L]]
        else:
            entry[0] = max(entry[0], bar[H])
            entry[1] = min(entry[1], bar[L])
    ordered = sorted(per_day)
    return {day: tuple(per_day[ordered[index - 1]])
            for index, day in enumerate(ordered) if index}


def session_vwap(bars):
    out = [None] * len(bars)
    day = None
    notional = volume = 0.0
    for index, bar in enumerate(bars):
        current = bar[TS] // 86_400
        if current != day:
            day, notional, volume = current, 0.0, 0.0
        notional += (bar[H] + bar[L] + bar[C]) / 3.0 * bar[V]
        volume += bar[V]
        out[index] = notional / volume if volume > 0 else None
    return out


def context(phase):
    full = all_bars_30m(phase)
    bars = [bar for bar in full if in_session(bar[TS])]
    closes = [bar[C] for bar in bars]
    outside, prior = session_anchors(full)
    channels = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION)
    means = {}
    sigmas = {}
    for period in ZSCORE_PERIODS:
        means[period], sigmas[period] = rolling_mean_sigma(closes, period)
    short = trailing_annual_volatility(bars, VOLATILITY_BARS)
    long = trailing_annual_volatility(bars, LONG_VOLATILITY_BARS)
    return bars, {
        "atr": es.average_true_range(bars, periods=ATR_BARS),
        "ema": {period: es.ema(closes, period)
                for period in set(TREND_PERIODS.values())},
        "fast": {p: es.ema(closes, p) for p in MA_FAST},
        "slow": {p: es.ema(closes, p) for p in MA_SLOW},
        "high": {p: es.rolling_extreme([b[H] for b in bars], p, True) for p in channels},
        "low": {p: es.rolling_extreme([b[L] for b in bars], p, False) for p in channels},
        "mean": means,
        "sigma": sigmas,
        "vwap": session_vwap(bars),
        "overnight": outside,
        "prior_close": prior,
        "prior_range": prior_session_range(bars),
        "volatility": short,
        # "Calm" is short-horizon volatility under its own long-horizon level:
        # self-referential, so it needs no external index, and causal on both legs.
        "calm": [None if s is None or l is None or l <= 0.0 else s < l
                 for s, l in zip(short, long)],
    }


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #

ZSCORE_PERIODS = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 5 * BARS_PER_SESSION)
MA_FAST = (BARS_PER_SESSION, 2 * BARS_PER_SESSION)
MA_SLOW = (5 * BARS_PER_SESSION, 10 * BARS_PER_SESSION, 20 * BARS_PER_SESSION)

#: 12:00 and 13:30 New York; both leave room for at least two buckets before the
#: flatten, so a late entry is still a trade rather than an instant close.
LAST_ENTRY = (12 * 60, 13 * 60 + 30)

#: Eleven exits: four reward multiples, three holding lengths (1h / 2h / 6h at
#: 30-minute buckets) and three trail widths.
EXIT_MODES = ("rr_1", "rr_1.5", "rr_2", "rr_3",
              "time_2", "time_4", "time_12",
              "trail_1.0", "trail_1.5", "trail_2.5")
COMMON = {
    "exit_mode": EXIT_MODES,
    "stop_atr": (1.0, 1.5, 2.5, 3.5, 5.0),
    "trend": ("none", "ema_20d", "ema_50d"),
    "vol_mode": ("none", "calm", "active"),
}

#: Trend filter references, by EMA period in buckets.
TREND_PERIODS = {"ema_20d": 20 * BARS_PER_SESSION, "ema_50d": 50 * BARS_PER_SESSION}

AXES = {
    "orb": {"direction": ("breakout", "fade"), "range_bars": (1, 2),
            "breakout_atr": (0.0, 0.25), "last_entry_minute": LAST_ENTRY, **COMMON},
    "overnight": {"direction": ("breakout", "fade"), "buffer_atr": (0.0, 0.25),
                  "last_entry_minute": LAST_ENTRY, **COMMON},
    "pdr": {"direction": ("breakout", "fade"), "buffer_atr": (0.0, 0.25),
            "last_entry_minute": LAST_ENTRY, **COMMON},
    "donchian": {"channel": (BARS_PER_SESSION, 2 * BARS_PER_SESSION,
                             4 * BARS_PER_SESSION),
                 "last_entry_minute": LAST_ENTRY, **COMMON},
    "ma_cross": {"fast": MA_FAST, "slow": MA_SLOW,
                 "last_entry_minute": LAST_ENTRY, **COMMON},
    "momentum": {"direction": ("breakout", "fade"),
                 "signal_minute": (10 * 60, 11 * 60, 13 * 60),
                 "lookback": (4, 12, 24), "threshold_atr": (0.5, 1.0, 1.5), **COMMON},
    "gap": {"direction": ("fade", "follow"), "threshold_atr": (0.25, 0.5, 1.0),
            **COMMON},
    "vwap": {"direction": ("fade", "follow"), "threshold_atr": (0.5, 1.0, 1.5),
             "last_entry_minute": LAST_ENTRY, **COMMON},
    "zscore": {"direction": ("fade", "follow"), "period": ZSCORE_PERIODS,
               "threshold_z": (1.5, 2.0, 2.5), "last_entry_minute": LAST_ENTRY,
               **COMMON},
    "eia": {"direction": ("follow", "fade"), "signal_minute": (10 * 60 + 30, 11 * 60),
            "threshold_atr": (0.5, 1.0), **COMMON},
}

CATEGORICAL = ("direction", "vol_mode", "trend", "exit_mode", "fast", "slow")


def candidates(axes):
    cells = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    return [cell for cell in cells if valid(cell)]


def valid(params):
    """Reject impossible corners. Only `ma_cross` has one: fast must be faster."""
    if "fast" in params and "slow" in params:
        return params["fast"] < params["slow"]
    return True


def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    reference = ctx["ema"][TREND_PERIODS[mode]][index]
    return price > reference if side == 1 else price < reference


def accepts_vol(ctx, index, mode):
    """`calm` and `active` are complements, so a family that only works in one
    of them shows up as a matched pair rather than as a single lucky mask."""
    if mode == "none":
        return True
    if mode == "calm":
        return ctx["calm"][index] is True
    return ctx["calm"][index] is False


def exit_plan(mode, stop_distance):
    """`(target_distance, max_bars, trail_multiple)`; unused legs are None."""
    if mode.startswith("rr_"):
        return float(mode[3:]) * stop_distance, None, None
    if mode.startswith("time_"):
        return None, int(mode[5:]), None
    return None, None, float(mode[6:])


# --------------------------------------------------------------------------- #
# signals
#
# Every signal reads bar `index`'s *close* and the entry then fills at bar
# `index + 1`'s open, which the backtest loop enforces through `pending`. A
# signal that consumed its own bar's close and entered on that same bar would
# manufacture the fake edge recorded in [[entry-must-be-next-bar-open]].
# --------------------------------------------------------------------------- #


def orb_signal(index, bars, ctx, params, state):
    bar = bars[index]
    day = bar[TS] // 86_400
    minute = bar[TS] % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update({"day": day, "high": None, "low": None})
    end = SESSION_OPEN_MINUTE + 30 * params["range_bars"]
    if minute < end:
        state["high"] = bar[H] if state["high"] is None else max(state["high"], bar[H])
        state["low"] = bar[L] if state["low"] is None else min(state["low"], bar[L])
        return None
    if minute > params["last_entry_minute"] or state["high"] is None:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    upper = state["high"] + params["breakout_atr"] * atr
    lower = state["low"] - params["breakout_atr"] * atr
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def overnight_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    window = ctx["overnight"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if window is None or atr is None or atr <= 0.0:
        return None
    upper = window[0] + params["buffer_atr"] * atr
    lower = window[1] - params["buffer_atr"] * atr
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def pdr_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    window = ctx["prior_range"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if window is None or atr is None or atr <= 0.0:
        return None
    upper = window[0] + params["buffer_atr"] * atr
    lower = window[1] - params["buffer_atr"] * atr
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def donchian_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    atr = ctx["atr"][index]
    upper = ctx["high"][params["channel"]][index]
    lower = ctx["low"][params["channel"]][index]
    if atr is None or atr <= 0.0 or upper is None or lower is None:
        return None
    return 1 if bar[C] > upper else -1 if bar[C] < lower else None


def ma_cross_signal(index, bars, ctx, params, _state):
    """Trade the crossing bar only, not every bar the fast line stays above."""
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"] or index == 0:
        return None
    if ctx["atr"][index] is None:
        return None
    fast = ctx["fast"][params["fast"]]
    slow = ctx["slow"][params["slow"]]
    now = fast[index] - slow[index]
    before = fast[index - 1] - slow[index - 1]
    if before <= 0.0 < now:
        return 1
    if before >= 0.0 > now:
        return -1
    return None


def momentum_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 != params["signal_minute"] or index < params["lookback"]:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    move = bar[C] - bars[index - params["lookback"]][C]
    threshold = params["threshold_atr"] * atr
    side = 1 if move > threshold else -1 if move < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def gap_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 != SESSION_OPEN_MINUTE:
        return None
    previous = ctx["prior_close"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if previous is None or atr is None or atr <= 0.0:
        return None
    drift = (bar[O] - previous) / atr
    threshold = params["threshold_atr"]
    side = -1 if drift > threshold else 1 if drift < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


def vwap_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    vwap, atr = ctx["vwap"][index], ctx["atr"][index]
    if vwap is None or atr is None or atr <= 0.0:
        return None
    deviation = (bar[C] - vwap) / atr
    threshold = params["threshold_atr"]
    side = -1 if deviation > threshold else 1 if deviation < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


def zscore_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    period = params["period"]
    mean = ctx["mean"][period][index]
    sigma = ctx["sigma"][period][index]
    if mean is None or sigma is None or sigma <= 0.0 or ctx["atr"][index] is None:
        return None
    z = (bar[C] - mean) / sigma
    threshold = params["threshold_z"]
    side = -1 if z > threshold else 1 if z < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


def eia_signal(index, bars, ctx, params, _state):
    """The Wednesday 10:30 EIA petroleum status report.

    The release is at 10:30 exactly, so the 10:30-11:00 bucket *is* the
    reaction. `signal_minute` picks whether the rule reads that bucket or waits
    one more, and either way the entry is the following bucket's open.

    Wednesday only. A Monday holiday pushes the release to Thursday a handful
    of times a year; those are simply not traded rather than guessed at.
    """
    bar = bars[index]
    if es.weekday(bar[TS]) != 2 or bar[TS] % 86_400 // 60 != params["signal_minute"]:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    move = bar[C] - bar[O]
    threshold = params["threshold_atr"] * atr
    side = 1 if move > threshold else -1 if move < -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


SIGNALS = {
    "orb": orb_signal, "overnight": overnight_signal, "pdr": pdr_signal,
    "donchian": donchian_signal, "ma_cross": ma_cross_signal,
    "momentum": momentum_signal, "gap": gap_signal, "vwap": vwap_signal,
    "zscore": zscore_signal, "eia": eia_signal,
}


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


#: The eight sizing schemes. `riskvol_1.5pct` is the pinned default every other
#: phase uses; the rest exist so "did you try different sizing" has an answer.
#:
#:   risk_*      stop-based: quantity = equity * pct / stop distance
#:   riskvol_*   the same, throttled by min(1, VOL_TARGET / realised vol)
#:   notional_*  fixed fractional: quantity = equity * leverage / price,
#:               ignoring the stop entirely
#:   flat        one minimum lot, always; no compounding at all
#:
#: `flat` is the diagnostic rather than a proposal: its equity curve is a plain
#: dollar sum, so it isolates the trade sequence from every compounding effect.
SIZE_MODES = ("riskvol_1.5pct", "risk_1pct", "risk_2pct", "riskvol_1pct",
              "riskvol_2pct", "notional_1x", "notional_2x", "flat")
DEFAULT_SIZE = "riskvol_1.5pct"


def quantity(mode, equity, price, stop_distance, realized):
    """Barrels to trade under `mode`, floored to the 10-barrel lot step.

    Every mode is capped by the same margin ceiling, so they differ in how they
    scale a trade, never in what the account can afford.
    """
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    ceiling = equity / MARGIN / price
    if mode == "flat":
        return STEP if ceiling >= STEP else 0.0
    family, level = mode.split("_")
    if family == "notional":
        raw = equity * float(level.rstrip("x")) / price
    else:
        fraction = float(level.rstrip("pct")) / 100.0
        if family == "riskvol" and realized and realized > 0.0:
            fraction *= min(1.0, VOL_TARGET / realized)
        raw = equity * fraction / stop_distance
    return max(0.0, math.floor(min(raw, ceiling) / STEP) * STEP)


def random_side(ts, seed):
    """A deterministic coin flip for `ts`, for the null control.

    Deterministic rather than drawn from an RNG so it is identical across
    worker processes and reproducible between runs, and derived from the bar
    timestamp so two different cells that fire on the same bar agree — which is
    what preserves the real signals' correlation structure across the grid.
    """
    digest = hashlib.md5(f"{seed}:{ts}".encode()).digest()
    return 1 if digest[0] & 1 else -1


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
             initial=SELECT_BALANCE, spread=SPREAD, size_mode=DEFAULT_SIZE,
             null_seed=None):
    """Signals to sized trades over `[lo, hi)`.

    Ordering matches the Rust strategies and the ES/BTC passes: the session
    flatten is checked first, then the stop, then the target, then the time
    stop; a pending entry fills at the next bar's open; at most one position is
    open at a time and at most one trade is taken per session.
    """
    equity = peak = initial
    maximum_drawdown = 0.0
    trades = []
    position = pending = None
    traded_day = None
    state = {}
    signal_fn = SIGNALS[family]

    for index, bar in enumerate(bars):
        ts = bar[TS]
        if ts < lo:
            continue
        if ts >= hi:
            break
        day = ts // 86_400
        minute = ts % 86_400 // 60

        if position is not None:
            side = position["side"]
            price = reason = None
            if minute >= SESSION_CLOSE_MINUTE:
                price, reason = bar[O], "session"
            else:
                stop = position["stop"]
                if (side == 1 and bar[L] <= stop) or (side == -1 and bar[H] >= stop):
                    price = min(bar[O], stop) if side == 1 else max(bar[O], stop)
                    reason = "stop"
                elif position["target"] is not None:
                    target = position["target"]
                    if (side == 1 and bar[H] >= target) or (side == -1 and bar[L] <= target):
                        price = max(bar[O], target) if side == 1 else min(bar[O], target)
                        reason = "target"
                if (price is None and position["max_bars"] is not None
                        and index - position["index"] >= position["max_bars"]):
                    price, reason = bar[O], "time"
            if price is None:
                if position["trail"] is not None and ctx["atr"][index]:
                    atr = ctx["atr"][index]
                    if side == 1:
                        position["best"] = max(position["best"], bar[C])
                        position["stop"] = max(
                            position["stop"], position["best"] - position["trail"] * atr)
                    else:
                        position["best"] = min(position["best"], bar[C])
                        position["stop"] = min(
                            position["stop"], position["best"] + position["trail"] * atr)
            else:
                gross = side * (price - position["entry"])
                points = gross - spread
                pnl = points * position["quantity"]
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": position["ts"], "exit_ts": ts, "side": side,
                               "points": points, "gross": gross, "pnl": pnl,
                               "quantity": position["quantity"], "reason": reason})
                position = None

        if position is None and pending is not None:
            # A missing bucket can jump the session boundary; never carry a
            # pending entry out of the window that formed it.
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                amount = quantity(size_mode, equity, bar[O], pending["distance"],
                                  pending["realized"])
                if amount >= STEP:
                    side, entry = pending["side"], bar[O]
                    target, max_bars, trail = exit_plan(params["exit_mode"],
                                                        pending["distance"])
                    position = {
                        "side": side, "entry": entry, "ts": ts, "index": index,
                        "quantity": amount,
                        "stop": entry - side * pending["distance"],
                        "target": None if target is None else entry + side * target,
                        "max_bars": max_bars, "trail": trail, "best": entry,
                    }
                    traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE
                and accepts_vol(ctx, index, params["vol_mode"])):
            side = signal_fn(index, bars, ctx, params, state)
            # The null control keeps *when* the rule fires and replaces only
            # *which way* it bets. Trade count, timing, session mix, stop width
            # and exit logic are therefore untouched, so any in-sample return
            # it produces is manufactured purely by choosing the best cell.
            if side is not None and null_seed is not None:
                side = random_side(bars[index][TS], null_seed)
            atr, realized = ctx["atr"][index], ctx["volatility"][index]
            if (side is not None and atr is not None and realized is not None
                    and accepts_trend(bar[C], ctx, index, side, params["trend"])):
                # The realised volatility travels with the pending entry; which
                # sizing scheme consumes it is `size_mode`'s business, not the
                # signal's.
                pending = {"side": side, "day": day,
                           "distance": params["stop_atr"] * atr,
                           "realized": realized}

    result = summarize(trades, maximum_drawdown, equity, initial)
    result["annual"] = annual_detail(trades, initial)
    # Two per-trade means, and the distinction is the whole point on this
    # instrument. `net` is after the spread and is what the account earns.
    # `gross` is before it and is the only measure of whether the entry rule
    # predicts direction at all -- so `gross` is also, by construction, the
    # break-even spread: the rule dies at exactly the cost that equals it.
    gross = [trade["gross"] for trade in trades]
    net = [trade["points"] for trade in trades]
    if len(net) > 1:
        deviation = statistics.stdev(gross)
        result["gross_points_per_trade"] = round(statistics.fmean(gross), 4)
        result["net_points_per_trade"] = round(statistics.fmean(net), 4)
        result["breakeven_spread"] = round(statistics.fmean(gross), 4)
        result["gross_t_stat"] = (
            round(statistics.fmean(gross) / (deviation / math.sqrt(len(gross))), 2)
            if deviation else 0.0)
    return result


def summarize(trades, maximum_drawdown, final_equity, initial):
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    months = {}
    for trade in trades:
        moment = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        key = f"{moment.year}-{moment.month:02d}"
        months[key] = months.get(key, 0.0) + trade["pnl"]
    monthly = list(months.values())
    mean = statistics.fmean(monthly) if monthly else 0.0
    deviation = statistics.pstdev(monthly) if len(monthly) > 1 else 0.0
    return {
        "pnl": round(final_equity - initial, 2),
        "final": round(final_equity, 2),
        "return_pct": round(100.0 * (final_equity - initial) / initial, 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "win_rate": round(sum(t["pnl"] > 0 for t in trades) / len(trades), 3)
                    if trades else 0.0,
        "max_dd_pct": round(100.0 * maximum_drawdown, 2),
        "monthly_sharpe": round(mean / deviation, 3) if deviation else 0.0,
        "positive_months": sum(1 for value in monthly if value > 0),
        "n_months": len(monthly),
    }


def annual_detail(trades, initial):
    by_year = {}
    for trade in sorted(trades, key=lambda item: item["exit_ts"]):
        year = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc).year
        by_year.setdefault(year, []).append(trade["pnl"])
    equity = initial
    detail = {}
    if not by_year:
        return detail
    for year in range(min(by_year), max(by_year) + 1):
        start = peak = equity
        maximum_drawdown = 0.0
        pnl = 0.0
        for value in by_year.get(year, ()):
            pnl += value
            equity += value
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown,
                                   (peak - equity) / peak if peak > 0 else 1.0)
        detail[str(year)] = {
            "pnl": round(pnl, 2),
            "return_pct": round(100.0 * pnl / start, 2) if start > 0 else -100.0,
            "max_dd_pct": round(100.0 * maximum_drawdown, 2),
            "trades": len(by_year.get(year, ())),
        }
    return detail


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def passes(family, stat, limit=SELECTION_DD_LIMIT, annual_limit=ANNUAL_DD_LIMIT):
    annual = stat["annual"]
    positive = sum(1 for year in FULL_IS_YEARS
                   if annual.get(str(year), {}).get("pnl", 0.0) > 0.0)
    worst = max((annual.get(str(year), {}).get("max_dd_pct", 100.0)
                 for year in FULL_IS_YEARS), default=100.0)
    return (
        stat["trades"] >= FAMILY_MIN_TRADES.get(family, MIN_TRADES)
        and stat["pf"] >= MIN_PROFIT_FACTOR
        and stat["max_dd_pct"] <= limit
        and positive >= MIN_POSITIVE_YEARS
        and worst <= annual_limit
    )


def quality(family, stat):
    """Compounded growth, penalised for an uneven or drawdown-heavy year mix."""
    if not passes(family, stat):
        return -math.inf
    returns = [stat["annual"].get(str(year), {}).get("return_pct", 0.0)
               for year in FULL_IS_YEARS]
    return (100.0 * math.log(stat["final"] / SELECT_BALANCE)
            + min(returns)
            + 0.25 * statistics.median(returns)
            - 0.5 * statistics.pstdev(returns))


def neighbours(params, axes):
    """One-step perturbations along the numeric axes only."""
    out = []
    for axis, values in axes.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                variant = {**params, axis: values[other]}
                if valid(variant):
                    out.append(variant)
    return out


def select_family(family, results, quiet=False):
    axes = AXES[family]
    universe = candidates(axes)
    ranked = []
    for params in universe:
        stat = results[es.frozen(params)]
        own = quality(family, stat)
        if not math.isfinite(own):
            continue
        if not SELECTION_DD_FLOOR <= stat["max_dd_pct"] <= SELECTION_DD_LIMIT:
            continue
        # A regime filter must refine an edge that is already there, not be the
        # edge: without this a volatility mask can carve a losing cell into a
        # winning one purely by dropping the periods that hurt it.
        bare = results.get(es.frozen({**params, "vol_mode": "none", "trend": "none"}))
        if bare is None or bare["pnl"] <= 0.0 or bare["pf"] < 1.0:
            continue
        nearby = [results[es.frozen(item)] for item in neighbours(params, axes)]
        robust = [item for item in nearby
                  if passes(family, item, limit=NEIGHBOUR_DD_LIMIT)]
        if not nearby or len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(family, item) for item in robust if passes(family, item)]
        if not strict:
            continue
        ranked.append((own, params, stat, statistics.median(strict),
                       len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    if quiet:
        if not ranked:
            return None
        score, params, stat, plateau, robust, total = ranked[0]
        return {"params": params, "in_sample": stat, "score": round(score, 6),
                "plateau_score": round(plateau, 6),
                "robust_neighbours": f"{robust}/{total}"}

    print(f"\nTop {family} cells (2018-2024 only, {SELECT_BALANCE:,.0f} account):")
    for score, params, stat, plateau, robust, total in ranked[:6]:
        print(json.dumps({"score": round(score, 3), "plateau": round(plateau, 3),
                          "robust_neighbours": f"{robust}/{total}",
                          "params": params,
                          "stats": {k: v for k, v in stat.items() if k != "annual"}},
                         sort_keys=True))
    if not ranked:
        print(f"  no {family} cell cleared the gates; closest by profitable years:")
        closest = sorted(
            ((sum(stat["annual"].get(str(y), {}).get("pnl", 0.0) > 0.0
                  for y in FULL_IS_YEARS), stat["pf"], stat["trades"], params, stat)
             for params in universe for stat in (results[es.frozen(params)],)),
            key=lambda item: item[:3], reverse=True)
        for positive, _pf, _n, params, stat in closest[:4]:
            print(json.dumps({"positive_years": positive, "params": params,
                              "stats": {k: v for k, v in stat.items()
                                        if k != "annual"}}, sort_keys=True))
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "plateau_score": round(plateau, 6),
            "robust_neighbours": f"{robust}/{total}"}


# --------------------------------------------------------------------------- #
# parallel evaluation
# --------------------------------------------------------------------------- #

_WORKER = {}


def _init_worker(phase):
    _WORKER["bars"], _WORKER["ctx"] = context(phase)


def _evaluate_cell(job):
    # The spread travels with the job rather than in a module global: Windows
    # spawns workers by re-importing this module, so a global set in the parent
    # would silently revert to its default in every worker.
    family, params, spread, null_seed = job
    return es.frozen(params), backtest(family, _WORKER["bars"], _WORKER["ctx"],
                                       params, spread=spread,
                                       null_seed=null_seed)


def evaluate_all(family, phase, pool, spread, null_seed=None):
    universe = candidates(AXES[family])
    jobs = [(family, params, spread, null_seed) for params in universe]
    results = {}
    for number, (key, stat) in enumerate(
            pool.imap_unordered(_evaluate_cell, jobs, chunksize=16), 1):
        results[key] = stat
        if number % 1000 == 0:
            print(f"  {family}: evaluated {number}/{len(universe)}", flush=True)
    return results


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #


def output_path(spread):
    """A sealed file per cost assumption; a rerun at another spread is a
    different experiment and must not overwrite the first one's seal."""
    if spread == SPREAD:
        return OUTPUT
    return OUTPUT.replace(".json", f"_spread{spread:.2f}.json")


def seal(payload, path):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(phase, workers, spread=SPREAD):
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        families = {family: select_family(
            family, evaluate_all(family, phase, pool, spread)) for family in AXES}
    payload = {
        "sealed": True,
        "protocol": {
            "bars": "usoil_1m causally aggregated to 30m; out-of-session buckets "
                    "used only for the overnight and gap anchors",
            "clock": "usoil_1m is New York wall-clock, established from the empty "
                     "17:00 maintenance hour and the 09:00 / 10:30 / 14:27-14:29 "
                     "volume peaks; no timestamp is converted",
            "session": "09:00-14:30 New York (the WTI pit session); entries inside "
                       "it only, forced flatten at the close",
            "in_sample": "2018-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-08-06, untouched by selection",
            "candidate_counts": {f: len(candidates(AXES[f])) for f in AXES},
            "selection_gate": (
                f">={MIN_POSITIVE_YEARS} of {len(FULL_IS_YEARS)} in-sample years "
                f"profitable; selected DD {SELECTION_DD_FLOOR}-{SELECTION_DD_LIMIT}%; "
                f"annual DD <={ANNUAL_DD_LIMIT}%; >={MIN_TRADES} trades "
                f"({FAMILY_MIN_TRADES}); PF >={MIN_PROFIT_FACTOR}; the volatility "
                "and trend filters must refine an already profitable unfiltered "
                "cell; >=60% of numeric neighbours robust"),
            "sizing": (f"{DEFAULT_SIZE}, floored to the {STEP:.0f}-barrel lot "
                       f"step inside {MARGIN:.0%} margin. Held fixed during "
                       "selection because sizing cannot change gross points a "
                       "trade; the `sizing` phase sweeps all of "
                       f"{list(SIZE_MODES)} over the selected cells instead."),
            "select_balance": SELECT_BALANCE,
            "report_balances": list(REPORT_BALANCES),
            "balance_note": (
                "Selection runs at 10,000 because at the requested 1,000 the "
                "10-barrel lot step, not the signal, decides which cells trade "
                "at all. Every winner is reported at both balances."),
            "contract": "Exness USOIL: 1000 barrels a lot, 0.01 lot minimum, so "
                        "quantity is in barrels and the step is 10 barrels",
            "entry_spread": spread,
            "cost_note": "charged wholly at entry. Zero-account commission "
                         "(6.25 a lot = 0.00625 a barrel) is inside the 0.20 "
                         "figure, so that run is the conservative side.",
        },
        "families": families,
    }
    path = output_path(spread)
    seal(payload, path)
    print(f"\nSEALED USOIL family selections (spread {spread}) to {path}")


BREAKEVEN_SPREADS = (0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)


def validate(phase, workers, spread=SPREAD):
    del workers
    bars, ctx = context(phase)
    path = output_path(spread)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("USOIL selection seal mismatch; rerun select before validate")

    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        entry = {}
        for balance in REPORT_BALANCES:
            stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                            initial=balance, spread=spread)
            entry[f"oos_{balance:.0f}"] = stat
            print(f"\n{family} 2025-2026 OUT OF SAMPLE @ {balance:,.0f}:")
            print(json.dumps({k: v for k, v in stat.items() if k != "annual"},
                             sort_keys=True))
            for year, detail in sorted(stat["annual"].items()):
                print(f"  {year}: {detail['return_pct']:+7.2f}%  "
                      f"dd {detail['max_dd_pct']:5.2f}%  n={detail['trades']}")
        # Break-even spread: the cost model is the single biggest assumption
        # here, so report where the rule dies rather than only whether it lives
        # at 0.20.
        curve = {}
        for level in BREAKEVEN_SPREADS:
            # `level`, not `spread`: this loop used to rebind the argument, so
            # every family after the first was scored at 0.50 instead of the
            # requested cost.
            stat = backtest(family, bars, ctx, params, lo=IS_START, hi=OOS_END,
                            initial=SELECT_BALANCE, spread=level)
            curve[f"{level:.2f}"] = {
                "pnl": stat["pnl"], "pf": stat["pf"],
                "gross_points_per_trade": stat.get("gross_points_per_trade"),
            }
        entry["spread_curve_full_sample"] = curve
        print(f"  spread curve (full sample, {SELECT_BALANCE:,.0f}): " +
              ", ".join(f"{k}->{v['pf']}" for k, v in curve.items()))
        validation[family] = entry

    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _edge_cell(job):
    """One cell's cost-free edge over the FULL sample, both windows separately.

    Run at spread zero and at a fixed balance: this measures the entry rule, so
    sizing, compounding and the lot step are all noise here.
    """
    family, params = job
    inside = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                      lo=IS_START, hi=IS_END, spread=0.0)
    outside = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                       lo=IS_END, hi=OOS_END, spread=0.0)
    return family, params, inside, outside


def edge_scan(phase, workers):
    """Does any family predict direction *before* costs, and by how much?

    The selection pass answered "is this profitable at a 0.20 spread" and got
    ten noes. That answer conflates two very different failures -- no edge, and
    an edge smaller than the toll -- and only the second one is fixable by
    changing venue or timeframe. This separates them.

    Nothing is selected here. The printed maximum is the best of N draws and is
    reported *with* N so it can be read against that.
    """
    print(f"\nCOST-FREE EDGE SCAN (spread 0.00, {SELECT_BALANCE:,.0f} account)")
    print("gross = mean points a trade before the spread; it is also the "
          "break-even spread.\n")
    header = (f"{'family':<10} {'cells':>6} {'best gross':>11} {'t':>6} {'n':>6} "
              f"{'median':>8} {'>0.20':>6} {'OOS gross':>10}")
    print(header)
    print("-" * len(header))
    summary = {}
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        for family in AXES:
            jobs = [(family, params) for params in candidates(AXES[family])]
            rows = []
            for _f, params, inside, outside in pool.imap_unordered(
                    _edge_cell, jobs, chunksize=16):
                if inside["trades"] < FAMILY_MIN_TRADES.get(family, MIN_TRADES):
                    continue
                rows.append((inside.get("gross_points_per_trade", 0.0),
                             inside.get("gross_t_stat", 0.0), inside["trades"],
                             outside.get("gross_points_per_trade", 0.0), params))
            if not rows:
                print(f"{family:<10} {len(jobs):>6}   (no cell reached the trade floor)")
                summary[family] = None
                continue
            rows.sort(key=lambda row: row[0], reverse=True)
            best = rows[0]
            median = statistics.median(row[0] for row in rows)
            above = sum(1 for row in rows if row[0] > SPREAD)
            print(f"{family:<10} {len(rows):>6} {best[0]:>11.4f} {best[1]:>6.2f} "
                  f"{best[2]:>6} {median:>8.4f} {above:>6} {best[3]:>10.4f}")
            summary[family] = {
                "cells_scored": len(rows),
                "best_gross_points_per_trade": best[0],
                "best_gross_t_stat": best[1],
                "best_trades": best[2],
                "best_oos_gross_points_per_trade": best[3],
                "median_gross_points_per_trade": round(median, 4),
                "cells_above_spread": above,
                "best_params": best[4],
            }
    path = os.path.join(os.path.dirname(__file__), "usoil_edge_scan.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"spread_for_comparison": SPREAD,
                   "note": "no selection is made here; the maximum is the best "
                           "of `cells_scored` draws and must be read against it",
                   "families": summary}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {path}")


def sizing_sweep(phase, spread):
    """Every sizing scheme against each sealed winner, in sample and out.

    Kept out of the selection grid on purpose. Sizing is a monotone rescaling
    of an already-fixed trade sequence: it cannot change which bar a position
    exits on, so `gross_points_per_trade` is *identical* across every row here.
    What it can change is compounding, drawdown, and whether the lot step
    swallows a trade -- so a family whose gross edge is negative cannot be
    rescued by any row, and one whose gross edge is positive might be.

    Reading the table: `flat` holds one minimum lot and never compounds, so its
    equity curve is a plain dollar sum and its return column is not comparable
    with the others; it is there to show the trade sequence unmixed with
    position growth.
    """
    bars, ctx = context(phase)
    path = output_path(spread)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    out = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        print(f"\n{family}  (spread {spread}, sizing sweep over the sealed cell)")
        print(f"{'size_mode':<16} {'IS ret%':>9} {'IS dd%':>7} {'OOS ret%':>9} "
              f"{'OOS dd%':>8} {'OOS n':>6} {'OOS gross':>10}")
        rows = {}
        for mode in SIZE_MODES:
            inside = backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
                              initial=SELECT_BALANCE, spread=spread,
                              size_mode=mode)
            outside = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                               initial=SELECT_BALANCE, spread=spread,
                               size_mode=mode)
            print(f"{mode:<16} {inside['return_pct']:>9.2f} "
                  f"{inside['max_dd_pct']:>7.2f} {outside['return_pct']:>9.2f} "
                  f"{outside['max_dd_pct']:>8.2f} {outside['trades']:>6} "
                  f"{outside.get('gross_points_per_trade', 0.0):>10.4f}")
            rows[mode] = {"in_sample": inside, "out_of_sample": outside}
        out[family] = rows

    destination = path.replace(".json", "_sizing.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"spread": spread, "size_modes": list(SIZE_MODES),
                   "note": "gross points a trade is invariant across size "
                           "modes by construction; only compounding, drawdown "
                           "and lot-step truncation differ",
                   "families": out}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


#: Families the null control re-selects. The three biggest in-sample winners,
#: so the control is run against the strongest claims rather than the weakest.
NULL_FAMILIES = ("orb", "zscore", "pdr")


def why(phase, workers, spread=0.0):
    """Why is the holdout so much worse than the fit? Three candidate answers.

    A. **The holdout is underpowered.** 1.6 years and ~100-300 trades may
       simply be too few to resolve an edge of the size claimed, in which case
       the out-of-sample number is uninformative rather than damning.
    B. **The regime changed.** 2025-2026 oil could be structurally different
       from 2018-2024, in which case the rule might be real but mis-timed.
    C. **The search manufactured the fit.** If running the identical selection
       over *randomised* trade directions also produces a large in-sample
       return, then the in-sample number carries no information at all and the
       out-of-sample number is simply the honest one.

    These are distinguishable, and they are not equally actionable, so the
    module tests all three rather than asserting the last.
    """
    bars, ctx = context(phase)
    with open(output_path(spread), encoding="utf-8") as handle:
        payload = json.load(handle)

    # ---- A. power -------------------------------------------------------- #
    print("\nA. IS THE HOLDOUT BIG ENOUGH TO SEE THE CLAIMED EDGE?")
    print(f"{'family':<10} {'IS gross':>9} {'IS se':>7} {'OOS gross':>10} "
          f"{'OOS se':>7} {'OOS n':>6} {'drop':>7} {'t(drop)':>8} {'signif':>7}")
    for family, winner in sorted(payload["families"].items()):
        if winner is None:
            continue
        params = dict(winner["params"])
        inside = backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END,
                          spread=spread)
        outside = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                           spread=spread)
        ig, it = inside["gross_points_per_trade"], inside["gross_t_stat"]
        og, ot = outside["gross_points_per_trade"], outside["gross_t_stat"]
        ise = abs(ig / it) if it else float("nan")
        ose = abs(og / ot) if ot else float("nan")
        drop = ig - og
        combined = math.sqrt(ise ** 2 + ose ** 2)
        t_drop = drop / combined if combined else 0.0
        print(f"{family:<10} {ig:>9.4f} {ise:>7.4f} {og:>10.4f} {ose:>7.4f} "
              f"{outside['trades']:>6} {drop:>7.4f} {t_drop:>8.2f} "
              f"{'yes' if abs(t_drop) > 1.96 else 'NO':>7}")
    print("  'NO' means the fall from fit to holdout is inside sampling error:")
    print("  the holdout cannot resolve an edge that size, either way.")

    # ---- B. regime ------------------------------------------------------- #
    print("\nB. DID THE MARKET CHANGE?  (session bars, per calendar year)")
    print(f"{'year':<6} {'bars':>6} {'mean |ret| bp':>14} {'ann vol %':>10} "
          f"{'mean ATR':>9} {'ATR/price %':>12}")
    per_year = {}
    for index, bar in enumerate(bars):
        year = datetime.fromtimestamp(bar[TS], tz=timezone.utc).year
        entry = per_year.setdefault(year, {"ret": [], "atr": [], "px": []})
        if index and bars[index - 1][C] > 0:
            entry["ret"].append(math.log(bar[C] / bars[index - 1][C]))
        if ctx["atr"][index]:
            entry["atr"].append(ctx["atr"][index])
            entry["px"].append(bar[C])
    for year in sorted(per_year):
        entry = per_year[year]
        if len(entry["ret"]) < 2 or not entry["atr"]:
            continue
        deviation = statistics.pstdev(entry["ret"])
        atr = statistics.fmean(entry["atr"])
        price = statistics.fmean(entry["px"])
        print(f"{year:<6} {len(entry['ret']):>6} "
              f"{1e4 * statistics.fmean([abs(r) for r in entry['ret']]):>14.2f} "
              f"{100 * deviation * math.sqrt(ANNUAL_PERIODS):>10.1f} "
              f"{atr:>9.3f} {100 * atr / price:>12.3f}")

    # ---- C. null control ------------------------------------------------- #
    print("\nC. NULL CONTROL — identical selection, randomised trade direction")
    print("   Same bars, same cells, same gates, same ranking. Only the side of")
    print("   each trade is a coin flip, so any in-sample return below is")
    print("   produced by the search itself and by nothing else.\n")
    print(f"{'family':<10} {'seed':>5} {'selected?':>10} {'IS ret%':>9} "
          f"{'IS gross':>9} {'IS t':>6} {'OOS ret%':>9} {'OOS gross':>10}")
    null_rows = {}
    with multiprocessing.Pool(workers, _init_worker, (phase,)) as pool:
        for family in NULL_FAMILIES:
            real = payload["families"].get(family)
            if real is not None:
                print(f"{family:<10} {'real':>5} {'yes':>10} "
                      f"{real['in_sample']['return_pct']:>9.2f} "
                      f"{real['in_sample']['gross_points_per_trade']:>9.4f} "
                      f"{real['in_sample']['gross_t_stat']:>6.2f}")
            for seed in (1, 2, 3):
                results = evaluate_all(family, phase, pool, spread, null_seed=seed)
                chosen = select_family(family, results, quiet=True)
                if chosen is None:
                    print(f"{family:<10} {seed:>5} {'no cell':>10}")
                    null_rows.setdefault(family, []).append(None)
                    continue
                stat = chosen["in_sample"]
                outside = backtest(family, bars, ctx, dict(chosen["params"]),
                                   lo=IS_END, hi=OOS_END, spread=spread,
                                   null_seed=seed)
                print(f"{family:<10} {seed:>5} {'yes':>10} "
                      f"{stat['return_pct']:>9.2f} "
                      f"{stat['gross_points_per_trade']:>9.4f} "
                      f"{stat['gross_t_stat']:>6.2f} "
                      f"{outside['return_pct']:>9.2f} "
                      f"{outside.get('gross_points_per_trade', 0.0):>10.4f}")
                null_rows.setdefault(family, []).append(
                    {"params": chosen["params"], "in_sample": stat,
                     "out_of_sample": outside})

    destination = os.path.join(os.path.dirname(__file__), "usoil_why.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"spread": spread, "null_seeds": [1, 2, 3],
                   "null_families": list(NULL_FAMILIES),
                   "null_control": null_rows}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase",
                        choices=("select", "validate", "edge", "sizing", "why"))
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--spread", type=float, default=SPREAD,
                        help="dollars a barrel charged at entry; a value other "
                             "than the default seals to its own file")
    args = parser.parse_args()
    bars, _ctx = context(args.phase)
    print(f"loaded {len(bars)} session thirty-minute USOIL bars "
          f"({datetime.fromtimestamp(bars[0][TS], tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc):%Y-%m-%d})")
    print(f"candidate cells: {sum(len(candidates(a)) for a in AXES.values()):,} "
          f"across {len(AXES)} families; {args.workers} workers")
    if args.phase == "select":
        select(args.phase, args.workers, args.spread)
    elif args.phase == "edge":
        edge_scan(args.phase, args.workers)
    elif args.phase == "sizing":
        sizing_sweep(args.phase, args.spread)
    elif args.phase == "why":
        why(args.phase, args.workers, args.spread)
    else:
        validate(args.phase, args.workers, args.spread)


if __name__ == "__main__":
    main()
