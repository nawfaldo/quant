"""ES intraday strategy-family research with a sealed 2025-2026 holdout.

Three independent families compete on the same protocol: an opening-range
breakout, an intraday time-series momentum, and a Donchian trend follower with
an ATR trail. Selection sees 2018-2024 only and writes the winning cell to
``es_strategy_selection.json``; ``validate`` requires that hash-sealed file and
evaluates exactly those parameters on the previously untouched 2025-2026 bars.

TIME ZONE. ``es_1m`` is stamped in *Chicago* wall-clock, unlike ``nq_1m`` and the
level-two tables, which carry New York (see AGENT.md). The evidence is the
volume profile: the RTH open spike lands on 08:30, the cash-close ramp on 14:59,
the pit halt shows as a 15:15-15:30 gap, and the CME daily maintenance break is
hour 16 -- one hour before the same break in ``nq_1m``. Every minute constant
below is therefore Chicago, and ``SESSION_OPEN_MINUTE``/``SESSION_CLOSE_MINUTE``
are the requested 09:30-16:00 New York session expressed in that clock. Nothing
here converts timestamps; the bars are used exactly as stored.

Positions may only be opened inside the session and are forcibly flattened at
its close, so the strategy never carries overnight risk. Sizing compounds live
equity, charges the whole 0.20-point spread at entry, floors to the 0.01 Forex
quantity step inside a 25% margin requirement, and lets volatility targeting
reduce but never increase risk -- the same conventions as the sealed BTC
families and ``live_trade/src/backtest/engine.rs``.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data


OUTPUT = os.path.join(os.path.dirname(__file__), "es_strategy_selection.json")

INITIAL = 1_000.0
SPREAD = 0.2
MARGIN = 0.25
STEP = 0.01

WARMUP_START = "2016-01-01"
IS_START = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
FULL_IS_YEARS = tuple(range(2018, 2025))

#: Chicago minutes. 08:30 = 09:30 New York, 15:00 = 16:00 New York.
SESSION_OPEN_MINUTE = 8 * 60 + 30
SESSION_CLOSE_MINUTE = 15 * 60

BARS_PER_SESSION = 13          # 30-minute buckets from 08:30 through 14:30
ATR_BARS = 2 * BARS_PER_SESSION
VOLATILITY_BARS = 20 * BARS_PER_SESSION
ANNUAL_PERIODS = 252.0 * BARS_PER_SESSION

MIN_TRADES = 300
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 8.0
SELECTION_DD_LIMIT = 14.0
ANNUAL_DD_LIMIT = 15.0
NEIGHBOUR_DD_LIMIT = 15.0

# Bar tuple: timestamp, open, high, low, close, volume.
TS, O, H, L, C, V = range(6)


# --------------------------------------------------------------------------- #
# bars and context
# --------------------------------------------------------------------------- #


def bars_30m(phase):
    """RTH 30-minute ES bars, oldest first.

    The selector's SQL cannot return a holdout row. The validate phase keeps the
    full history so every indicator has exactly the same causal warm-up as the
    already sealed run.
    """
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM es_1m "
        f"WHERE timestamp >= '{WARMUP_START}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    out = []
    for row in rows:
        ts = int(row[0]) // 1_000_000
        minute = ts % 86_400 // 60
        if SESSION_OPEN_MINUTE <= minute <= SESSION_CLOSE_MINUTE:
            out.append((ts, *(float(value) for value in row[1:])))
    return out


def weekday(ts):
    """0 = Monday. Unix day zero was a Thursday."""
    return (ts // 86_400 + 3) % 7


def ema(values, period):
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def average_true_range(bars, periods=ATR_BARS):
    closes = [bar[C] for bar in bars]
    true_ranges = []
    for index, bar in enumerate(bars):
        previous = closes[index - 1] if index else bar[O]
        true_ranges.append(max(
            bar[H] - bar[L],
            abs(bar[H] - previous),
            abs(bar[L] - previous),
        ))
    out = [None] * len(bars)
    total = 0.0
    for index, value in enumerate(true_ranges):
        total += value
        if index >= periods:
            total -= true_ranges[index - periods]
        if index >= periods - 1:
            out[index] = total / periods
    return out


def trailing_annual_volatility(bars, periods=VOLATILITY_BARS):
    """Causal realized volatility over the prior 20 sessions, annualized."""
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


def rolling_extreme(values, period, maximum):
    """Extreme of the `period` bars *before* each index; the bar itself is out.

    Excluding the current bar is what makes a breakout test meaningful: a close
    can never exceed a channel that already counted it.
    """
    from collections import deque

    out = [None] * len(values)
    queue = deque()
    for index, value in enumerate(values):
        while queue and queue[0] <= index - 1 - period:
            queue.popleft()
        out[index] = values[queue[0]] if queue else None
        while queue and ((value >= values[queue[-1]]) if maximum
                         else (value <= values[queue[-1]])):
            queue.pop()
        queue.append(index)
    return out


def vix_prior_by_bar(bars, phase):
    """Previous calendar day's VIX close for each bar, else None.

    Strictly earlier date: a daily close is only known once that day has ended.
    This also carries Friday's print across the weekend.
    """
    upper = "WHERE timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        f"SELECT cast(timestamp as long) ts,close FROM vix_1d {upper} ORDER BY timestamp"
    )
    daily = [(int(row[0]) // 1_000_000 // 86_400, float(row[1])) for row in rows]
    out = [None] * len(bars)
    cursor = 0
    latest = None
    for index, bar in enumerate(bars):
        day = bar[TS] // 86_400
        while cursor < len(daily) and daily[cursor][0] < day:
            latest = daily[cursor][1]
            cursor += 1
        out[index] = latest
    return out


def context(bars, phase):
    closes = [bar[C] for bar in bars]
    highs = [bar[H] for bar in bars]
    lows = [bar[L] for bar in bars]
    channels = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION)
    return {
        "atr": average_true_range(bars),
        "ema": {
            20 * BARS_PER_SESSION: ema(closes, 20 * BARS_PER_SESSION),
            50 * BARS_PER_SESSION: ema(closes, 50 * BARS_PER_SESSION),
        },
        "high": {p: rolling_extreme(highs, p, True) for p in channels},
        "low": {p: rolling_extreme(lows, p, False) for p in channels},
        "volatility": trailing_annual_volatility(bars),
        "vix": vix_prior_by_bar(bars, phase),
    }


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #

TREND_PERIODS = {"ema_20d": 20 * BARS_PER_SESSION, "ema_50d": 50 * BARS_PER_SESSION}

ORB_AXES = {
    "direction": ("breakout", "fade"),
    "range_bars": (1, 2),                       # 30 or 60 minute opening range
    "breakout_atr": (0.0, 0.25),
    "last_entry_minute": (12 * 60, 13 * 60 + 30),
    "stop_atr": (1.5, 2.5, 3.5),
    "reward": (1.0, 1.5, 2.5),
    "trend": ("none", "ema_20d"),
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15, 0.20),
}

MOMENTUM_AXES = {
    "direction": ("breakout", "fade"),
    "signal_minute": (9 * 60, 10 * 60, 12 * 60),
    "lookback": (6, 13, 26),
    "threshold_atr": (0.5, 1.0, 1.5),
    "trend": ("none", "ema_20d"),
    "stop_atr": (1.5, 2.5, 3.5),
    "reward": (1.5, 2.5),
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15, 0.20),
}

TREND_AXES = {
    "channel": (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION),
    "last_entry_minute": (12 * 60, 13 * 60 + 30),
    "trend": ("none", "ema_20d"),
    "stop_atr": (1.5, 2.5, 3.5),
    "trail_atr": (None, 1.5, 2.5),              # None keeps a fixed 2.0 reward
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15, 0.20),
}

AXES = {"orb": ORB_AXES, "momentum": MOMENTUM_AXES, "trend": TREND_AXES}

#: Axes with no meaningful "one step away" cell, so they are not perturbed for
#: the plateau test. `direction` is a structural choice rather than a setting;
#: `day_mode` and `vix_mode` get their own "the filter must refine an edge that
#: already exists" test instead.
CATEGORICAL = ("direction", "day_mode", "vix_mode", "trend")


def candidates(axes):
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def accepts_day(ts, mode):
    return mode != "tue_thu" or 1 <= weekday(ts) <= 3


def accepts_vix(value, mode):
    return mode != "below_25" or (value is not None and value < 25.0)


def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    reference = ctx["ema"][TREND_PERIODS[mode]][index]
    return price > reference if side == 1 else price < reference


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #


def orb_signal(index, bars, ctx, params, state):
    bar = bars[index]
    ts = bar[TS]
    day = ts // 86_400
    minute = ts % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update({"day": day, "high": None, "low": None})

    range_end = SESSION_OPEN_MINUTE + 30 * params["range_bars"]
    if minute < range_end:
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
    if params["direction"] == "fade":
        side = -side
    if not accepts_trend(bar[C], ctx, index, side, params["trend"]):
        return None
    return {
        "side": side,
        "distance": params["stop_atr"] * atr,
        "reward": params["reward"],
        "trail": None,
    }


def momentum_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    minute = bar[TS] % 86_400 // 60
    if minute != params["signal_minute"] or index < params["lookback"]:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    movement = bar[C] - bars[index - params["lookback"]][C]
    threshold = params["threshold_atr"] * atr
    side = 1 if movement > threshold else -1 if movement < -threshold else None
    if side is None:
        return None
    if params["direction"] == "fade":
        side = -side
    if not accepts_trend(bar[C], ctx, index, side, params["trend"]):
        return None
    return {
        "side": side,
        "distance": params["stop_atr"] * atr,
        "reward": params["reward"],
        "trail": None,
    }


def trend_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    minute = bar[TS] % 86_400 // 60
    if minute > params["last_entry_minute"]:
        return None
    atr = ctx["atr"][index]
    if atr is None or atr <= 0.0:
        return None
    upper = ctx["high"][params["channel"]][index]
    lower = ctx["low"][params["channel"]][index]
    if upper is None or lower is None:
        return None
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None or not accepts_trend(bar[C], ctx, index, side, params["trend"]):
        return None
    return {
        "side": side,
        "distance": params["stop_atr"] * atr,
        "reward": 2.0,
        "trail": params["trail_atr"],
    }


SIGNALS = {"orb": orb_signal, "momentum": momentum_signal, "trend": trend_signal}


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def quantity(equity, price, stop_distance, risk_fraction):
    """Units to trade, from live equity. Floors so the stop cannot overrun risk."""
    if equity <= 0.0 or price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    margin_sized = equity / MARGIN / price
    raw = min(equity * risk_fraction / stop_distance, margin_sized)
    sized = math.floor(raw / STEP) * STEP
    affordable = math.floor(margin_sized / STEP) * STEP
    return max(0.0, min(sized, affordable))


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END):
    """Signals to sized trades over `[lo, hi)`.

    Ordering matches the Rust strategies: the session flatten is checked first,
    then the stop, then the target; a pending entry fills at the next bar's open;
    and at most one position is open at a time, one trade per session.
    """
    equity = peak = INITIAL
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
            exit_price = reason = None
            if minute >= SESSION_CLOSE_MINUTE:
                exit_price, reason = bar[O], "session"
            else:
                stop = position["stop"]
                if (side == 1 and bar[L] <= stop) or (side == -1 and bar[H] >= stop):
                    exit_price = min(bar[O], stop) if side == 1 else max(bar[O], stop)
                    reason = "stop"
                elif position["target"] is not None:
                    target = position["target"]
                    if (side == 1 and bar[H] >= target) or (side == -1 and bar[L] <= target):
                        exit_price = (max(bar[O], target) if side == 1
                                      else min(bar[O], target))
                        reason = "target"
            if exit_price is not None:
                points = side * (exit_price - position["entry"]) - SPREAD
                pnl = points * position["quantity"]
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(
                    maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
                )
                trades.append({
                    "entry_ts": position["ts"], "exit_ts": ts, "side": side,
                    "points": points, "pnl": pnl, "quantity": position["quantity"],
                    "reason": reason,
                })
                position = None
            elif position["trail"] is not None:
                atr = ctx["atr"][index]
                if atr is not None:
                    if side == 1:
                        position["best"] = max(position["best"], bar[C])
                        position["stop"] = max(
                            position["stop"], position["best"] - position["trail"] * atr
                        )
                    else:
                        position["best"] = min(position["best"], bar[C])
                        position["stop"] = min(
                            position["stop"], position["best"] + position["trail"] * atr
                        )

        if position is None and pending is not None:
            # A halted or missing bucket can jump the session boundary; never
            # carry a pending entry out of the window it was formed in.
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                amount = quantity(
                    equity, bar[O], pending["distance"], pending["risk_fraction"]
                )
                if amount >= STEP:
                    side = pending["side"]
                    entry = bar[O]
                    position = {
                        "side": side, "entry": entry, "ts": ts, "quantity": amount,
                        "stop": entry - side * pending["distance"],
                        "target": (None if pending["trail"] is not None else
                                   entry + side * pending["reward"] * pending["distance"]),
                        "trail": pending["trail"], "best": entry,
                    }
                    traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE
                and accepts_day(ts, params["day_mode"])
                and accepts_vix(ctx["vix"][index], params["vix_mode"])):
            setup = signal_fn(index, bars, ctx, params, state)
            realized = ctx["volatility"][index]
            if setup is not None and realized is not None:
                risk = params["risk_fraction"]
                if params["vol_target"] is not None and realized > 0.0:
                    risk *= min(1.0, params["vol_target"] / realized)
                pending = {**setup, "day": day, "risk_fraction": risk}

    result = summarize(trades, maximum_drawdown, equity)
    result["annual"] = annual_detail(trades)
    return result


def summarize(trades, maximum_drawdown, final_equity):
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    months = {}
    for trade in trades:
        dt = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        key = f"{dt.year}-{dt.month:02d}"
        months[key] = months.get(key, 0.0) + trade["pnl"]
    monthly = list(months.values())
    mean = statistics.fmean(monthly) if monthly else 0.0
    std = statistics.pstdev(monthly) if len(monthly) > 1 else 0.0
    return {
        "pnl": round(final_equity - INITIAL, 2),
        "final": round(final_equity, 2),
        "return_pct": round(100.0 * (final_equity - INITIAL) / INITIAL, 2),
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "win_rate": round(sum(t["pnl"] > 0 for t in trades) / len(trades), 3)
                    if trades else 0.0,
        "max_dd_pct": round(100.0 * maximum_drawdown, 2),
        "monthly_sharpe": round(mean / std, 3) if std else 0.0,
        "positive_months": sum(1 for value in monthly if value > 0),
        "n_months": len(monthly),
    }


def annual_detail(trades):
    by_year = {}
    for trade in sorted(trades, key=lambda item: item["exit_ts"]):
        year = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc).year
        by_year.setdefault(year, []).append(trade["pnl"])
    equity = INITIAL
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
            maximum_drawdown = max(
                maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
            )
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


def passes(stat, limit=SELECTION_DD_LIMIT, annual_limit=ANNUAL_DD_LIMIT):
    annual = stat["annual"]
    return (
        stat["trades"] >= MIN_TRADES
        and stat["pf"] >= MIN_PROFIT_FACTOR
        and stat["max_dd_pct"] <= limit
        and all(
            str(year) in annual
            and annual[str(year)]["pnl"] > 0.0
            and annual[str(year)]["max_dd_pct"] <= annual_limit
            for year in FULL_IS_YEARS
        )
    )


def quality(stat):
    """Compounded growth, penalised for an uneven or drawdown-heavy year mix."""
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in FULL_IS_YEARS]
    return (
        100.0 * math.log(stat["final"] / INITIAL)
        + min(returns)
        + 0.25 * statistics.median(returns)
        - 0.5 * statistics.pstdev(returns)
    )


def frozen(params):
    return tuple(sorted((key, str(value)) for key, value in params.items()))


def neighbours(params, axes):
    """One-step perturbations along the numeric axes only."""
    out = []
    for axis, values in axes.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                variant = dict(params)
                variant[axis] = values[other]
                out.append(variant)
    return out


def unfiltered(params):
    """The same cell with its day and VIX filters switched off."""
    return {**params, "day_mode": "all", "vix_mode": "none"}


def select_family(family, bars, ctx, results):
    axes = AXES[family]
    universe = candidates(axes)
    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        if not SELECTION_DD_FLOOR <= stat["max_dd_pct"] <= SELECTION_DD_LIMIT:
            continue

        # A day or VIX filter must refine an edge that is already there, not be
        # the edge. Without this a categorical mask can carve a losing cell into
        # a winning one purely by dropping the sessions that hurt it.
        bare = results.get(frozen(unfiltered(params)))
        if bare is None or bare["pnl"] <= 0.0 or bare["pf"] < 1.0:
            continue

        nearby = [results[frozen(item)] for item in neighbours(params, axes)]
        robust = [item for item in nearby if passes(item, limit=NEIGHBOUR_DD_LIMIT)]
        if not nearby or len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(item) for item in robust if passes(item)]
        if not strict:
            continue
        ranked.append((own, params, stat, statistics.median(strict),
                       len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print(f"\nTop {family} cells (2018-2024 only):")
    for score, params, stat, plateau, robust, total in ranked[:8]:
        print(json.dumps({
            "score": round(score, 3), "plateau": round(plateau, 3),
            "robust_neighbours": f"{robust}/{total}",
            "params": params, "stats": stat,
        }, sort_keys=True))
    if not ranked:
        print(f"  no {family} cell cleared the gates; closest by profitable years:")
        closest = sorted(
            ((sum(stat["annual"].get(str(y), {}).get("pnl", 0.0) > 0.0
                  for y in FULL_IS_YEARS), stat["pf"], params, stat)
             for params in universe for stat in (results[frozen(params)],)),
            key=lambda item: item[:2], reverse=True,
        )
        for positive, _pf, params, stat in closest[:5]:
            print(json.dumps({"positive_years": positive, "params": params,
                              "stats": stat}, sort_keys=True))
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {
        "params": params, "in_sample": stat, "score": round(score, 6),
        "plateau_score": round(plateau, 6),
        "robust_neighbours": f"{robust}/{total}",
    }


def evaluate_all(family, bars, ctx):
    axes = AXES[family]
    universe = candidates(axes)
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(family, bars, ctx, params)
        if number % 500 == 0:
            print(f"  {family}: evaluated {number}/{len(universe)}", flush=True)
    return results


def seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(bars, ctx):
    families = {}
    for family in AXES:
        results = evaluate_all(family, bars, ctx)
        families[family] = select_family(family, bars, ctx, results)

    payload = {
        "sealed": True,
        "protocol": {
            "bars": "es_1m causally aggregated to 30m, RTH buckets only",
            "clock": "es_1m is Chicago wall-clock; 08:30-15:00 here is 09:30-16:00 New York",
            "session": "entries inside 09:30-16:00 New York only; forced flatten at the close",
            "in_sample": "2018-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-07-29, untouched by selection",
            "candidate_counts": {f: len(candidates(AXES[f])) for f in AXES},
            "selection_gate": (
                f"every {FULL_IS_YEARS[0]}-{FULL_IS_YEARS[-1]} year profitable; "
                f"selected DD {SELECTION_DD_FLOOR}-{SELECTION_DD_LIMIT}%; "
                f"annual DD <={ANNUAL_DD_LIMIT}%; >={MIN_TRADES} trades; "
                f"PF >={MIN_PROFIT_FACTOR}; day/VIX filter must refine an already "
                "profitable unfiltered cell; >=60% of numeric neighbours robust"
            ),
            "sizing": ("equity compounded; floored to 0.01 within 25% margin; "
                       "volatility targeting can only reduce risk"),
            "vix_rule": "previous calendar day's VIX close only",
            "entry_spread": SPREAD,
            "initial_balance": INITIAL,
        },
        "families": families,
    }
    seal(payload)
    print(f"\nSEALED ES selections to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def validate(bars, ctx):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("ES selection seal mismatch; rerun select before validate")
    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = dict(winner["params"])
        params["vol_target"] = (None if params["vol_target"] in (None, "None")
                                else float(params["vol_target"]))
        stat = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END)
        validation[family] = stat
        print(f"\n{family} 2025-2026 OUT OF SAMPLE:")
        print(json.dumps(stat, indent=2, sort_keys=True))
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    bars = bars_30m(args.phase)
    ctx = context(bars, args.phase)
    print(f"loaded {len(bars)} RTH thirty-minute ES bars "
          f"({datetime.fromtimestamp(bars[0][TS], tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc):%Y-%m-%d})")
    if args.phase == "select":
        select(bars, ctx)
    else:
        validate(bars, ctx)


if __name__ == "__main__":
    main()
