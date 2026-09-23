"""All six ES strategy families, ported to BTCUSD, with a 2025-2026 holdout.

The ES passes (`es_strategy_research`, `es_reversion_research`) are re-run here
against `btc_1m`. Same protocol, same gates, same sealing discipline, so the two
instruments are directly comparable:

  * ``orb``        opening-range breakout or fade
  * ``momentum``   intraday time-series momentum or fade
  * ``trend``      Donchian channel breakout
  * ``vwap``       distance from the running session VWAP
  * ``gap``        session open against the prior session close
  * ``overnight``  the range formed outside the session

TWO DELIBERATE CHANGES from the ES passes, both lessons from them:

1. **Trailing exits are in the grid from the start.** The ES sweep tested only
   fixed reward multiples and time stops, and a later dense sweep showed the
   trailing stop was the single biggest omission -- it flipped that candidate's
   out-of-sample per-trade edge from -0.32 to +0.81 points. `exit_mode` here
   carries `trail_*` alongside `rr_*` and `time_*`, and the `rr` axis extends to
   3.0 so a winner cannot sit on the boundary the way the ES ones did.

2. **Sizing is fixed, not searched.** `risk_fraction` and `vol_target` are pure
   monotone scaling knobs: they change how hard a signal is pressed, never
   whether it is real. Searching them multiplied the ES grid sixfold for no
   information and inflated the multiple-testing burden that later made its best
   in-sample t-statistic (2.53 over 52,560 cells) indistinguishable from noise.
   They are pinned below, and risk may be rescaled afterwards to suit a drawdown
   budget. This keeps the grid at ~14k cells rather than ~85k.

CLOCK. `btc_1m` is New York wall-clock, the same as `nq_1m` -- verified by
minute-return correlation against NQ (0.520 at shift 0, ~0.003 at +/-1h). ES is
the exception in this database, not the rule; see [es-1m-is-chicago-time]. So
09:30-16:00 is minutes 570-960 with no shift.

HOLDOUT CAVEAT. Unlike the ES runs, BTC's 2025 is **not** a pristine holdout:
earlier sealed families (`btc_orb`, `btc_rth_momentum`, `btc_consistency`) were
validated on 2024-2025, and `btc_orb.rs` says so itself. Only 2026 is genuinely
untouched. A 2025 result here is repeated validation and should be discounted
accordingly.
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
from sandbox.research import es_strategy_research as es


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_families_selection.json")

INITIAL = es.INITIAL
SPREAD = es.SPREAD
STEP = es.STEP

WARMUP_START = "2017-08-17"
IS_START, IS_END, OOS_END = es.IS_START, es.IS_END, es.OOS_END
FULL_IS_YEARS = es.FULL_IS_YEARS

#: New York minutes, matching btc_orb / btc_rth_momentum.
SESSION_OPEN_MINUTE = 9 * 60 + 30
SESSION_CLOSE_MINUTE = 16 * 60
BARS_PER_SESSION = 13
ATR_BARS = 2 * BARS_PER_SESSION
VOLATILITY_BARS = 20 * BARS_PER_SESSION
#: BTC trades every calendar day, so a year holds 365 sessions, not 252.
ANNUAL_PERIODS = 365.0 * BARS_PER_SESSION

#: Pinned rather than searched; see the module docstring.
RISK_FRACTION = 0.015
VOL_TARGET = 0.6

MIN_TRADES = 250
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 8.0
SELECTION_DD_LIMIT = 17.0
ANNUAL_DD_LIMIT = 18.0
NEIGHBOUR_DD_LIMIT = 18.0

TS, O, H, L, C, V = es.TS, es.O, es.H, es.L, es.C, es.V
TREND_PERIOD = 20 * BARS_PER_SESSION


# --------------------------------------------------------------------------- #
# bars and context
# --------------------------------------------------------------------------- #


def all_bars_30m(phase):
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM btc_1m "
        f"WHERE timestamp >= '{WARMUP_START}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    return [(int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
            for row in rows]


def trailing_annual_volatility(bars, periods=VOLATILITY_BARS):
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
            out[index] = math.sqrt(
                max(0.0, total_sq / periods - mean * mean) * ANNUAL_PERIODS
            )
    return out


def session_anchors(full):
    """Per session date: the out-of-session range, and the prior session close.

    BTC trades continuously, so the "overnight" window is not a halt -- it is
    simply the 16:00-09:30 stretch outside the traded session. That makes it a
    real, fully-formed price range rather than a gap, and the same is true of
    `prior_close`: the open does not jump to it, it drifts.
    """
    outside = {}
    last_close = {}
    for bar in full:
        minute = bar[TS] % 86_400 // 60
        day = bar[TS] // 86_400
        if minute >= SESSION_CLOSE_MINUTE:
            day += 1                      # evening belongs to the next session
        if minute < SESSION_OPEN_MINUTE or minute >= SESSION_CLOSE_MINUTE:
            entry = outside.get(day)
            if entry is None:
                outside[day] = [bar[H], bar[L]]
            else:
                entry[0] = max(entry[0], bar[H])
                entry[1] = min(entry[1], bar[L])
        else:
            last_close[day] = bar[C]
    ordered = sorted(last_close)
    prior = {day: last_close[ordered[i - 1]]
             for i, day in enumerate(ordered) if i}
    return {d: tuple(v) for d, v in outside.items()}, prior


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
    bars = [b for b in full
            if SESSION_OPEN_MINUTE <= b[TS] % 86_400 // 60 <= SESSION_CLOSE_MINUTE]
    outside, prior = session_anchors(full)
    closes = [b[C] for b in bars]
    channels = (BARS_PER_SESSION, 2 * BARS_PER_SESSION, 4 * BARS_PER_SESSION)
    return bars, {
        "atr": es.average_true_range(bars, periods=ATR_BARS),
        "ema": es.ema(closes, TREND_PERIOD),
        "high": {p: es.rolling_extreme([b[H] for b in bars], p, True) for p in channels},
        "low": {p: es.rolling_extreme([b[L] for b in bars], p, False) for p in channels},
        "vwap": session_vwap(bars),
        "overnight": outside,
        "prior_close": prior,
        "volatility": trailing_annual_volatility(bars),
        "vix": es.vix_prior_by_bar(bars, phase),
    }


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #

EXIT_MODES = ("rr_1", "rr_2", "rr_3", "time_4", "time_8", "trail_1.5", "trail_2.5")
COMMON = {
    "exit_mode": EXIT_MODES,
    "stop_atr": (1.5, 2.5, 3.5),
    "trend": ("none", "ema_20d"),
    "day_mode": ("all", "weekdays"),
    "vix_mode": ("none", "below_25"),
}

AXES = {
    "orb": {"direction": ("breakout", "fade"), "range_bars": (1, 2),
            "breakout_atr": (0.0, 0.25),
            "last_entry_minute": (13 * 60, 14 * 60 + 30), **COMMON},
    "momentum": {"direction": ("breakout", "fade"),
                 "signal_minute": (10 * 60, 11 * 60, 13 * 60),
                 "lookback": (6, 13, 26), "threshold_atr": (0.5, 1.0, 1.5), **COMMON},
    "trend": {"channel": (BARS_PER_SESSION, 2 * BARS_PER_SESSION,
                          4 * BARS_PER_SESSION),
              "last_entry_minute": (13 * 60, 14 * 60 + 30), **COMMON},
    "vwap": {"direction": ("fade", "follow"), "threshold_atr": (0.5, 1.0, 1.5),
             "last_entry_minute": (13 * 60, 14 * 60 + 30), **COMMON},
    "gap": {"direction": ("fade", "follow"), "threshold_atr": (0.25, 0.5, 1.0),
            **COMMON},
    "overnight": {"direction": ("fade", "follow"), "buffer_atr": (0.0, 0.25),
                  "last_entry_minute": (13 * 60, 14 * 60 + 30), **COMMON},
}

CATEGORICAL = ("direction", "day_mode", "vix_mode", "trend", "exit_mode")


def candidates(axes):
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def accepts_day(ts, mode):
    return mode != "weekdays" or (ts // 86_400 + 3) % 7 < 5


def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    return (price > ctx["ema"][index]) if side == 1 else (price < ctx["ema"][index])


def exit_plan(mode, stop_distance):
    """`(target_distance, max_bars, trail_multiple)`; unused legs are None."""
    if mode.startswith("rr_"):
        return float(mode[3:]) * stop_distance, None, None
    if mode.startswith("time_"):
        return None, int(mode[5:]), None
    return None, None, float(mode[6:])


# --------------------------------------------------------------------------- #
# signals
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


def trend_signal(index, bars, ctx, params, _state):
    bar = bars[index]
    if bar[TS] % 86_400 // 60 > params["last_entry_minute"]:
        return None
    atr = ctx["atr"][index]
    upper = ctx["high"][params["channel"]][index]
    lower = ctx["low"][params["channel"]][index]
    if atr is None or atr <= 0.0 or upper is None or lower is None:
        return None
    return 1 if bar[C] > upper else -1 if bar[C] < lower else None


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


SIGNALS = {"orb": orb_signal, "momentum": momentum_signal, "trend": trend_signal,
           "vwap": vwap_signal, "gap": gap_signal, "overnight": overnight_signal}


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END):
    """One position and one trade a session; flatten, stop, target, then time."""
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
        day, minute = ts // 86_400, ts % 86_400 // 60

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
                if price is None and position["max_bars"] is not None \
                        and index - position["index"] >= position["max_bars"]:
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
                points = side * (price - position["entry"]) - SPREAD
                pnl = points * position["quantity"]
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown,
                                       (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": position["ts"], "exit_ts": ts, "side": side,
                               "points": points, "pnl": pnl, "reason": reason})
                position = None

        if position is None and pending is not None:
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                quantity = es.quantity(equity, bar[O], pending["distance"],
                                       pending["risk_fraction"])
                if quantity >= STEP:
                    side, entry = pending["side"], bar[O]
                    target, max_bars, trail = exit_plan(params["exit_mode"],
                                                        pending["distance"])
                    position = {
                        "side": side, "entry": entry, "ts": ts, "index": index,
                        "quantity": quantity,
                        "stop": entry - side * pending["distance"],
                        "target": None if target is None else entry + side * target,
                        "max_bars": max_bars, "trail": trail, "best": entry,
                    }
                    traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE
                and accepts_day(ts, params["day_mode"])
                and es.accepts_vix(ctx["vix"][index], params["vix_mode"])):
            side = signal_fn(index, bars, ctx, params, state)
            atr, realized = ctx["atr"][index], ctx["volatility"][index]
            if (side is not None and atr is not None and realized is not None
                    and accepts_trend(bar[C], ctx, index, side, params["trend"])):
                risk = RISK_FRACTION
                if realized > 0.0:
                    risk *= min(1.0, VOL_TARGET / realized)
                pending = {"side": side, "day": day, "distance": params["stop_atr"] * atr,
                           "risk_fraction": risk}

    result = es.summarize(trades, maximum_drawdown, equity)
    result["annual"] = es.annual_detail(trades)
    points = [t["points"] for t in trades]
    if len(points) > 1:
        sd = statistics.stdev(points)
        result["t_stat"] = (round(statistics.fmean(points) / (sd / math.sqrt(len(points))), 2)
                            if sd else 0.0)
    return result


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def passes(stat, limit=SELECTION_DD_LIMIT, annual_limit=ANNUAL_DD_LIMIT):
    annual = stat["annual"]
    return (
        stat["trades"] >= MIN_TRADES
        and stat["pf"] >= MIN_PROFIT_FACTOR
        and stat["max_dd_pct"] <= limit
        and all(str(y) in annual and annual[str(y)]["pnl"] > 0.0
                and annual[str(y)]["max_dd_pct"] <= annual_limit
                for y in FULL_IS_YEARS)
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(y)]["return_pct"] for y in FULL_IS_YEARS]
    return (100.0 * math.log(stat["final"] / INITIAL) + min(returns)
            + 0.25 * statistics.median(returns) - 0.5 * statistics.pstdev(returns))


def neighbours(params, axes):
    out = []
    for axis, values in axes.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                out.append({**params, axis: values[other]})
    return out


def select_family(family, results):
    axes = AXES[family]
    universe = candidates(axes)
    ranked = []
    for params in universe:
        stat = results[es.frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        if not SELECTION_DD_FLOOR <= stat["max_dd_pct"] <= SELECTION_DD_LIMIT:
            continue
        bare = results.get(es.frozen({**params, "day_mode": "all", "vix_mode": "none"}))
        if bare is None or bare["pnl"] <= 0.0 or bare["pf"] < 1.0:
            continue
        nearby = [results[es.frozen(v)] for v in neighbours(params, axes)]
        robust = [x for x in nearby if passes(x, limit=NEIGHBOUR_DD_LIMIT)]
        if not nearby or len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(x) for x in robust if passes(x)]
        if not strict:
            continue
        ranked.append((own, params, stat, statistics.median(strict),
                       len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print(f"\nTop {family} cells (2018-2024 only):")
    for score, params, stat, plateau, robust, total in ranked[:6]:
        print(json.dumps({"score": round(score, 3), "plateau": round(plateau, 3),
                          "robust_neighbours": f"{robust}/{total}",
                          "params": params, "stats": stat}, sort_keys=True))
    if not ranked:
        print(f"  no {family} cell cleared the gates; closest by profitable years:")
        closest = sorted(
            ((sum(stat["annual"].get(str(y), {}).get("pnl", 0.0) > 0.0
                  for y in FULL_IS_YEARS), stat["pf"], params, stat)
             for params in universe for stat in (results[es.frozen(params)],)),
            key=lambda item: item[:2], reverse=True)
        for positive, _pf, params, stat in closest[:4]:
            print(json.dumps({"positive_years": positive, "params": params,
                              "stats": stat}, sort_keys=True))
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "plateau_score": round(plateau, 6),
            "robust_neighbours": f"{robust}/{total}"}


def evaluate_all(family, bars, ctx):
    universe = candidates(AXES[family])
    results = {}
    for number, params in enumerate(universe, 1):
        results[es.frozen(params)] = backtest(family, bars, ctx, params)
        if number % 1000 == 0:
            print(f"  {family}: evaluated {number}/{len(universe)}", flush=True)
    return results


def seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(bars, ctx):
    families = {f: select_family(f, evaluate_all(f, bars, ctx)) for f in AXES}
    payload = {
        "sealed": True,
        "protocol": {
            "bars": "btc_1m causally aggregated to 30m; out-of-session buckets used only for anchors",
            "clock": "btc_1m is New York wall-clock (verified against nq_1m); 09:30-16:00 = minutes 570-960",
            "session": "entries inside 09:30-16:00 only; forced flatten at the close",
            "in_sample": "2018-01-01 through 2024-12-31",
            "out_of_sample": "2025-01-01 through 2026-07-31",
            "holdout_caveat": "2025 is NOT pristine: earlier sealed BTC families were validated on 2024-2025. Only 2026 is untouched.",
            "candidate_counts": {f: len(candidates(AXES[f])) for f in AXES},
            "sizing": (f"fixed risk {RISK_FRACTION} of equity, vol target {VOL_TARGET}, "
                       "floored to 0.01 within 25% margin; not searched, so it can be "
                       "rescaled to a drawdown budget afterwards"),
            "selection_gate": (
                f"every {FULL_IS_YEARS[0]}-{FULL_IS_YEARS[-1]} year profitable; "
                f"selected DD {SELECTION_DD_FLOOR}-{SELECTION_DD_LIMIT}%; annual DD "
                f"<={ANNUAL_DD_LIMIT}%; >={MIN_TRADES} trades; PF >={MIN_PROFIT_FACTOR}; "
                "day/VIX filter must refine an already profitable unfiltered cell; "
                ">=60% of numeric neighbours robust"),
            "entry_spread": SPREAD, "initial_balance": INITIAL,
        },
        "families": families,
    }
    seal(payload)
    print(f"\nSEALED BTC family selections to {OUTPUT}")


def validate(bars, ctx):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("BTC selection seal mismatch; rerun select before validate")
    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        stat = backtest(family, bars, ctx, dict(winner["params"]),
                        lo=IS_END, hi=OOS_END)
        validation[family] = stat
        print(f"\n{family} 2025-2026 OUT OF SAMPLE:")
        print(json.dumps({k: v for k, v in stat.items() if k != "annual"},
                         sort_keys=True))
        for year, detail in sorted(stat["annual"].items()):
            print(f"  {year}: {detail['return_pct']:+7.2f}%  "
                  f"dd {detail['max_dd_pct']:5.2f}%  n={detail['trades']}")
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    bars, ctx = context(args.phase)
    print(f"loaded {len(bars)} session thirty-minute BTC bars "
          f"({datetime.fromtimestamp(bars[0][TS], tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc):%Y-%m-%d}); "
          f"{len(ctx['overnight'])} out-of-session ranges")
    print(f"candidate cells: {sum(len(candidates(a)) for a in AXES.values()):,}")
    if args.phase == "select":
        select(bars, ctx)
    else:
        validate(bars, ctx)


if __name__ == "__main__":
    main()
