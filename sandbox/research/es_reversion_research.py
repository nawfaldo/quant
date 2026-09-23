"""ES reversion-anchored strategy families with a sealed 2025-2026 holdout.

A second, independent pass after ``es_strategy_research`` found nothing that
survived: its ORB and Donchian families cleared no in-sample cell, and its
momentum winner (buy-the-dip above the 20-day EMA) posted +66% in sample and
-5.8% out of sample. That search only ever measured price against *itself* --
a prior close, a channel, an EMA -- and every exit was a fixed reward multiple.

The families here change both. Each one anchors to a level the session actually
transacts around, and each may exit on elapsed time instead of a target:

  * ``vwap``      distance from the running session VWAP
  * ``gap``       the cash open against the prior cash close
  * ``overnight`` the Globex range that formed before the cash open

Protocol is otherwise identical to the first pass, so the two are comparable:
selection sees 2018-2024 only and is hash-sealed before ``validate`` may read
2025-2026; entries are restricted to 09:30-16:00 New York with a forced flatten
at the close; sizing compounds live equity, charges the whole 0.20-point spread
at entry, floors to the 0.01 step inside a 25% margin requirement, and lets
volatility targeting reduce but never increase risk.

The drawdown gate is 17%, raised from 14% at the user's direction on 2026-08-02.

TIME ZONE. ``es_1m`` is Chicago wall-clock, one hour behind ``nq_1m`` and the
level-two tables; see the module docstring of ``es_strategy_research``. Every
minute constant here is Chicago, and 08:30-15:00 is the requested 09:30-16:00
New York session.
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


OUTPUT = os.path.join(os.path.dirname(__file__), "es_reversion_selection.json")

INITIAL = es.INITIAL
SPREAD = es.SPREAD
STEP = es.STEP

IS_START, IS_END, OOS_END = es.IS_START, es.IS_END, es.OOS_END
FULL_IS_YEARS = es.FULL_IS_YEARS
SESSION_OPEN_MINUTE = es.SESSION_OPEN_MINUTE
SESSION_CLOSE_MINUTE = es.SESSION_CLOSE_MINUTE
BARS_PER_SESSION = es.BARS_PER_SESSION

#: The Globex session for a cash date opens at 17:00 Chicago the previous day.
GLOBEX_OPEN_MINUTE = 17 * 60

MIN_TRADES = 250
MIN_PROFIT_FACTOR = 1.05
SELECTION_DD_FLOOR = 8.0
SELECTION_DD_LIMIT = 17.0
ANNUAL_DD_LIMIT = 18.0
NEIGHBOUR_DD_LIMIT = 18.0

TS, O, H, L, C, V = es.TS, es.O, es.H, es.L, es.C, es.V


# --------------------------------------------------------------------------- #
# bars and session anchors
# --------------------------------------------------------------------------- #


def trade_day(ts):
    """The cash date a bar belongs to; 17:00 Chicago rolls to the next date."""
    day = ts // 86_400
    return day + 1 if ts % 86_400 // 60 >= GLOBEX_OPEN_MINUTE else day


def all_bars_30m(phase):
    """Every 30-minute bucket, overnight included. Oldest first."""
    upper = "AND timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM es_1m "
        f"WHERE timestamp >= '{es.WARMUP_START}' {upper} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    return [(int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
            for row in rows]


def session_anchors(full):
    """Per cash date: the pre-open Globex range and the prior cash close.

    Both are complete before the cash open, so a bar can read them the moment
    the session starts without seeing anything from its own future.
    """
    overnight = {}
    last_close = {}
    for bar in full:
        minute = bar[TS] % 86_400 // 60
        day = trade_day(bar[TS])
        if minute < SESSION_OPEN_MINUTE or minute >= GLOBEX_OPEN_MINUTE:
            entry = overnight.get(day)
            if entry is None:
                overnight[day] = [bar[H], bar[L]]
            else:
                entry[0] = max(entry[0], bar[H])
                entry[1] = min(entry[1], bar[L])
        elif minute < SESSION_CLOSE_MINUTE:
            last_close[day] = bar[C]

    ordered = sorted(last_close)
    prior_close = {}
    for index, day in enumerate(ordered):
        if index:
            prior_close[day] = last_close[ordered[index - 1]]
    return ({day: tuple(value) for day, value in overnight.items()}, prior_close)


def session_vwap(bars):
    """Running VWAP of the current cash session, per RTH bar.

    Includes the bar it is reported on, which is correct: a signal is formed on
    a *completed* bar and filled at the next open, so the bar's own volume is
    already public when the decision is made.
    """
    out = [None] * len(bars)
    day = None
    notional = volume = 0.0
    for index, bar in enumerate(bars):
        current = bar[TS] // 86_400
        if current != day:
            day, notional, volume = current, 0.0, 0.0
        typical = (bar[H] + bar[L] + bar[C]) / 3.0
        notional += typical * bar[V]
        volume += bar[V]
        out[index] = notional / volume if volume > 0 else None
    return out


def context(phase):
    full = all_bars_30m(phase)
    bars = [bar for bar in full
            if SESSION_OPEN_MINUTE <= bar[TS] % 86_400 // 60 <= SESSION_CLOSE_MINUTE]
    overnight, prior_close = session_anchors(full)
    closes = [bar[C] for bar in bars]
    ctx = {
        "atr": es.average_true_range(bars),
        "ema": {20 * BARS_PER_SESSION: es.ema(closes, 20 * BARS_PER_SESSION)},
        "vwap": session_vwap(bars),
        "overnight": overnight,
        "prior_close": prior_close,
        "volatility": es.trailing_annual_volatility(bars),
        "vix": es.vix_prior_by_bar(bars, phase),
    }
    return bars, ctx


# --------------------------------------------------------------------------- #
# axes
# --------------------------------------------------------------------------- #

EXIT_MODES = ("rr_1", "rr_2", "time_4", "time_8")

VWAP_AXES = {
    "direction": ("fade", "follow"),
    "threshold_atr": (0.5, 1.0, 1.5),
    "last_entry_minute": (12 * 60, 13 * 60 + 30),
    "stop_atr": (1.5, 2.5, 3.5),
    "exit_mode": EXIT_MODES,
    "trend": ("none", "ema_20d"),
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15),
}

GAP_AXES = {
    "direction": ("fade", "follow"),
    "threshold_atr": (0.25, 0.5, 1.0),
    "stop_atr": (1.5, 2.5, 3.5),
    "exit_mode": EXIT_MODES,
    "trend": ("none", "ema_20d"),
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15),
}

OVERNIGHT_AXES = {
    "direction": ("fade", "follow"),
    "buffer_atr": (0.0, 0.25),
    "last_entry_minute": (12 * 60, 13 * 60 + 30),
    "stop_atr": (1.5, 2.5, 3.5),
    "exit_mode": EXIT_MODES,
    "trend": ("none", "ema_20d"),
    "day_mode": ("all", "tue_thu"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.01, 0.015, 0.02),
    "vol_target": (None, 0.15),
}

AXES = {"vwap": VWAP_AXES, "gap": GAP_AXES, "overnight": OVERNIGHT_AXES}

CATEGORICAL = ("direction", "day_mode", "vix_mode", "trend", "exit_mode")


def candidates(axes):
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def exit_plan(mode, stop_distance):
    """`(reward_distance, max_bars)` for an exit mode; either may be None."""
    if mode.startswith("rr_"):
        return float(mode[3:]) * stop_distance, None
    return None, int(mode[5:])


def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    reference = ctx["ema"][20 * BARS_PER_SESSION][index]
    return price > reference if side == 1 else price < reference


# --------------------------------------------------------------------------- #
# signals
# --------------------------------------------------------------------------- #


def vwap_signal(index, bars, ctx, params):
    bar = bars[index]
    minute = bar[TS] % 86_400 // 60
    if minute > params["last_entry_minute"]:
        return None
    vwap = ctx["vwap"][index]
    atr = ctx["atr"][index]
    if vwap is None or atr is None or atr <= 0.0:
        return None
    deviation = (bar[C] - vwap) / atr
    threshold = params["threshold_atr"]
    side = -1 if deviation > threshold else 1 if deviation < -threshold else None
    if side is None:
        return None
    if params["direction"] == "follow":
        side = -side
    return side


def gap_signal(index, bars, ctx, params):
    """Formed on the completed first cash bar; filled at the next open."""
    bar = bars[index]
    minute = bar[TS] % 86_400 // 60
    if minute != SESSION_OPEN_MINUTE:
        return None
    previous = ctx["prior_close"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if previous is None or atr is None or atr <= 0.0:
        return None
    gap = (bar[O] - previous) / atr
    threshold = params["threshold_atr"]
    side = -1 if gap > threshold else 1 if gap < -threshold else None
    if side is None:
        return None
    if params["direction"] == "follow":
        side = -side
    return side


def overnight_signal(index, bars, ctx, params):
    bar = bars[index]
    minute = bar[TS] % 86_400 // 60
    if minute > params["last_entry_minute"]:
        return None
    window = ctx["overnight"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if window is None or atr is None or atr <= 0.0:
        return None
    high, low = window
    upper = high + params["buffer_atr"] * atr
    lower = low - params["buffer_atr"] * atr
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    if params["direction"] == "fade":
        side = -side
    return side


SIGNALS = {"vwap": vwap_signal, "gap": gap_signal, "overnight": overnight_signal}


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def backtest(family, bars, ctx, params, lo=IS_START, hi=IS_END):
    """Signals to sized trades over `[lo, hi)`, one position and one trade a day.

    Exit precedence matches the Rust strategies and the first ES pass: session
    flatten, then stop, then target, then the elapsed-time cap. A pending signal
    fills at the next bar's open and is dropped if the session turns over first.
    """
    equity = peak = INITIAL
    maximum_drawdown = 0.0
    trades = []
    position = pending = None
    traded_day = None
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
                if exit_price is None and position["max_bars"] is not None:
                    if index - position["index"] >= position["max_bars"]:
                        exit_price, reason = bar[O], "time"
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

        if position is None and pending is not None:
            if day == pending["day"] and minute < SESSION_CLOSE_MINUTE:
                amount = es.quantity(
                    equity, bar[O], pending["distance"], pending["risk_fraction"]
                )
                if amount >= STEP:
                    side = pending["side"]
                    entry = bar[O]
                    reward, max_bars = exit_plan(pending["exit_mode"],
                                                 pending["distance"])
                    position = {
                        "side": side, "entry": entry, "ts": ts, "index": index,
                        "quantity": amount,
                        "stop": entry - side * pending["distance"],
                        "target": None if reward is None else entry + side * reward,
                        "max_bars": max_bars,
                    }
                    traded_day = day
            pending = None

        if (position is None and pending is None and traded_day != day
                and SESSION_OPEN_MINUTE <= minute < SESSION_CLOSE_MINUTE
                and es.accepts_day(ts, params["day_mode"])
                and es.accepts_vix(ctx["vix"][index], params["vix_mode"])):
            side = signal_fn(index, bars, ctx, params)
            atr = ctx["atr"][index]
            realized = ctx["volatility"][index]
            if (side is not None and atr is not None and realized is not None
                    and accepts_trend(bar[C], ctx, index, side, params["trend"])):
                risk = params["risk_fraction"]
                if params["vol_target"] is not None and realized > 0.0:
                    risk *= min(1.0, params["vol_target"] / realized)
                pending = {
                    "side": side, "day": day, "distance": params["stop_atr"] * atr,
                    "risk_fraction": risk, "exit_mode": params["exit_mode"],
                }

    result = es.summarize(trades, maximum_drawdown, equity)
    result["annual"] = es.annual_detail(trades)
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
        and all(
            str(year) in annual
            and annual[str(year)]["pnl"] > 0.0
            and annual[str(year)]["max_dd_pct"] <= annual_limit
            for year in FULL_IS_YEARS
        )
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in FULL_IS_YEARS]
    return (
        100.0 * math.log(stat["final"] / INITIAL)
        + min(returns)
        + 0.25 * statistics.median(returns)
        - 0.5 * statistics.pstdev(returns)
    )


def neighbours(params, axes):
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
        nearby = [results[es.frozen(item)] for item in neighbours(params, axes)]
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
             for params in universe for stat in (results[es.frozen(params)],)),
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
    universe = candidates(AXES[family])
    results = {}
    for number, params in enumerate(universe, 1):
        results[es.frozen(params)] = backtest(family, bars, ctx, params)
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
        families[family] = select_family(family, evaluate_all(family, bars, ctx))
    payload = {
        "sealed": True,
        "protocol": {
            "bars": "es_1m causally aggregated to 30m; overnight buckets used only for anchors",
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
            "prior_pass": "es_strategy_research; its momentum winner failed 2025-2026 at -5.76%",
            "entry_spread": SPREAD,
            "initial_balance": INITIAL,
        },
        "families": families,
    }
    seal(payload)
    print(f"\nSEALED ES reversion selections to {OUTPUT}")


def validate(bars, ctx):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("ES reversion seal mismatch; rerun select before validate")
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
    bars, ctx = context(args.phase)
    print(f"loaded {len(bars)} RTH thirty-minute ES bars "
          f"({datetime.fromtimestamp(bars[0][TS], tz=timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(bars[-1][TS], tz=timezone.utc):%Y-%m-%d}); "
          f"{len(ctx['overnight'])} overnight ranges")
    if args.phase == "select":
        select(bars, ctx)
    else:
        validate(bars, ctx)


if __name__ == "__main__":
    main()
