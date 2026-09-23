"""BTCUSD strategy-family research with a sealed 2024-2025 holdout.

The script deliberately has two phases. ``select`` may only read bars before
2024 and writes the winning configuration to ``btc_strategy_selection.json``.
``oos`` requires that file and evaluates exactly that configuration on the
previously untouched 2024-2025 bars.  This makes it harder to quietly turn the
out-of-sample period into another tuning set.

All signals are formed at an hourly close and filled at the following hourly
open. Stops are evaluated before targets when both occur within one candle.
The entire 0.20-point spread is charged once, at entry, matching the server
backtester. Position sizing uses 2% stop risk, the Forex 0.01 quantity step,
and the server's 25% margin requirement.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
from collections import deque
from datetime import datetime, timezone

from sandbox import data


INITIAL = 1_000.0
SPREAD = 0.2
RISK_FRACTION = 0.02
MARGIN = 0.25
STEP = 0.01
IS_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
OUTPUT = os.path.join(os.path.dirname(__file__), "btc_strategy_selection.json")

# Hour tuple: timestamp, open, high, low, close, volume.
TS, O, H, L, C, V = range(6)


def hourly_bars(phase):
    # The selector's SQL cannot return a holdout row. The OOS phase retains the
    # full history so its 336-hour EMA has exactly the same causal warm-up as the
    # already sealed run.
    where = "WHERE timestamp < '2024-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM btc_1m {where} "
        "SAMPLE BY 1h FILL(NONE) ALIGN TO CALENDAR"
    )
    return [
        (int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
        for row in rows
    ]


def rolling_extreme(values, period, maximum):
    """Previous-period extreme at each index; the current close is excluded."""
    out = [None] * len(values)
    queue = deque()
    for index, value in enumerate(values):
        while queue and queue[0] < index - period:
            queue.popleft()
        if queue:
            out[index] = values[queue[0]]
        while queue and ((value >= values[queue[-1]]) if maximum else
                         (value <= values[queue[-1]])):
            queue.pop()
        queue.append(index)
    return out


def ema(values, period):
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def indicators(bars):
    closes = [bar[C] for bar in bars]
    highs = [bar[H] for bar in bars]
    lows = [bar[L] for bar in bars]
    tr = []
    for index, bar in enumerate(bars):
        previous = closes[index - 1] if index else bar[O]
        tr.append(max(bar[H] - bar[L], abs(bar[H] - previous),
                      abs(bar[L] - previous)))
    atr = [None] * len(bars)
    total = 0.0
    for index, value in enumerate(tr):
        total += value
        if index >= 24:
            total -= tr[index - 24]
        if index >= 23:
            atr[index] = total / 24.0
    periods = (6, 12, 24, 48, 72, 168, 336, 720)
    return {
        "atr": atr,
        "ema": {period: ema(closes, period) for period in (72, 168, 336, 720)},
        "high": {period: rolling_extreme(highs, period, True) for period in periods},
        "low": {period: rolling_extreme(lows, period, False) for period in periods},
    }


def candidates():
    out = []
    # A range anchored to one of the three liquid global-session handoffs,
    # followed by a close-confirmed breakout. One trade at most per UTC day.
    for hour, duration, stop, reward, hold in itertools.product(
        (0, 8, 13), (2, 4, 6), (1.0, 1.5, 2.0), (1.5, 2.0, 3.0), (12, 24)
    ):
        out.append({"family": "orb", "hour": hour, "duration": duration,
                    "stop_atr": stop, "reward": reward, "hold": hold})

    # Once-daily time-series momentum, scaled by current hourly ATR and allowed
    # only on the same side of a slow EMA. The entry hour is deliberately coarse.
    for hour, lookback, threshold, trend, stop, reward, hold in itertools.product(
        (0, 8, 16), (24, 72, 168), (0.5, 1.0, 1.5), (168, 336),
        (1.5, 2.5), (1.5, 2.5), (24, 72)
    ):
        out.append({"family": "momentum", "hour": hour, "lookback": lookback,
                    "threshold": threshold, "trend": trend, "stop_atr": stop,
                    "reward": reward, "hold": hold})

    # Daily close-confirmed Donchian breakouts with an ATR trailing stop. There
    # is no target: winners are allowed to run until the trail or time cap.
    for hour, channel, trend, stop, trail, hold in itertools.product(
        (0, 8, 16), (72, 168, 336, 720), (168, 336),
        (2.0, 3.0), (2.0, 3.0, 4.0), (168, 336)
    ):
        out.append({"family": "trend", "hour": hour, "channel": channel,
                    "trend": trend, "stop_atr": stop, "trail_atr": trail,
                    "hold": hold})
    return out


def quantity(equity, price, stop):
    if equity <= 0 or price <= 0 or stop <= 0:
        return 0.0
    raw = min(equity * RISK_FRACTION / stop, equity / MARGIN / price)
    sized = math.floor(raw / STEP + 0.5) * STEP
    affordable = math.floor((equity / MARGIN / price) / STEP) * STEP
    return max(0.0, min(sized, affordable))


def signal(index, bars, ind, params, orb):
    bar = bars[index]
    hour = bar[TS] % 86_400 // 3_600
    atr = ind["atr"][index]
    if atr is None or atr <= 0:
        return None

    if params["family"] == "momentum":
        if hour != params["hour"]:
            return None
        lookback = params["lookback"]
        if index < lookback:
            return None
        move = bar[C] - bars[index - lookback][C]
        side = 1 if move > params["threshold"] * atr else (
            -1 if move < -params["threshold"] * atr else 0)
        trend = ind["ema"][params["trend"]][index]
        if (side == 1 and bar[C] <= trend) or (side == -1 and bar[C] >= trend):
            return None
        return side or None

    if params["family"] == "trend":
        if hour != params["hour"]:
            return None
        upper = ind["high"][params["channel"]][index]
        lower = ind["low"][params["channel"]][index]
        if upper is None or lower is None:
            return None
        trend = ind["ema"][params["trend"]][index]
        if bar[C] > upper and bar[C] > trend:
            return 1
        if bar[C] < lower and bar[C] < trend:
            return -1
        return None

    # ORB state is assembled from completed hourly candles. Signal at the first
    # matching close beyond the range, never while the range is still forming.
    day = bar[TS] // 86_400
    state = orb.get(day)
    if state is None:
        return None
    end = params["hour"] + params["duration"]
    if hour < end or state["traded"]:
        return None
    if bar[C] > state["high"]:
        state["traded"] = True
        return 1
    if bar[C] < state["low"]:
        state["traded"] = True
        return -1
    return None


def backtest(bars, ind, params, lo=None, hi=None):
    equity = peak = INITIAL
    max_drawdown = 0.0
    trades = []
    position = pending = None
    orb = {}

    for index, bar in enumerate(bars):
        ts = bar[TS]
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts >= hi:
            break
        hour = ts % 86_400 // 3_600

        if params["family"] == "orb":
            day = ts // 86_400
            state = orb.setdefault(day, {"high": -math.inf, "low": math.inf,
                                         "traded": False})
            if params["hour"] <= hour < params["hour"] + params["duration"]:
                state["high"] = max(state["high"], bar[H])
                state["low"] = min(state["low"], bar[L])

        if position is not None:
            side = position["side"]
            exit_price = reason = None
            stop_level = position["stop"]
            stop_hit = bar[L] <= stop_level if side == 1 else bar[H] >= stop_level
            if stop_hit:
                exit_price = min(bar[O], stop_level) if side == 1 else max(bar[O], stop_level)
                reason = "stop"
            elif position["target"] is not None:
                target = position["target"]
                target_hit = bar[H] >= target if side == 1 else bar[L] <= target
                if target_hit:
                    exit_price = max(bar[O], target) if side == 1 else min(bar[O], target)
                    reason = "target"
            if (exit_price is None
                    and ts - position["ts"] >= params["hold"] * 3_600):
                exit_price, reason = bar[O], "time"
            if exit_price is not None:
                points = side * (exit_price - position["entry"]) - SPREAD
                pnl = points * position["quantity"]
                equity += pnl
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({"entry_ts": position["ts"], "exit_ts": ts,
                               "side": side, "points": points, "pnl": pnl,
                               "quantity": position["quantity"], "reason": reason})
                position = None
            elif params["family"] == "trend":
                atr = ind["atr"][index]
                if atr is not None:
                    if side == 1:
                        position["best"] = max(position["best"], bar[C])
                        position["stop"] = max(position["stop"],
                            position["best"] - params["trail_atr"] * atr)
                    else:
                        position["best"] = min(position["best"], bar[C])
                        position["stop"] = min(position["stop"],
                            position["best"] + params["trail_atr"] * atr)

        if position is None and pending is not None:
            stop_distance = pending["stop_distance"]
            amount = quantity(equity, bar[O], stop_distance)
            if amount >= STEP:
                side = pending["side"]
                target = None if params["family"] == "trend" else (
                    bar[O] + side * stop_distance * params["reward"])
                position = {"side": side, "entry": bar[O], "ts": ts,
                            "index": index, "quantity": amount, "best": bar[O],
                            "stop": bar[O] - side * stop_distance, "target": target}
            pending = None

        if position is None and pending is None:
            side = signal(index, bars, ind, params, orb)
            atr = ind["atr"][index]
            if side is not None and atr is not None:
                pending = {"side": side,
                           "stop_distance": params["stop_atr"] * atr}

    if position is not None and bars:
        bar = bars[-1]
        points = position["side"] * (bar[C] - position["entry"]) - SPREAD
        pnl = points * position["quantity"]
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak > 0 else 1.0)
        trades.append({"entry_ts": position["ts"], "exit_ts": bar[TS],
                       "side": position["side"], "points": points, "pnl": pnl,
                       "quantity": position["quantity"], "reason": "end"})
    return summarize(trades, max_drawdown, equity)


def summarize(trades, max_drawdown, final_equity):
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    years = {}
    months = {}
    for trade in trades:
        dt = datetime.fromtimestamp(trade["entry_ts"], tz=timezone.utc)
        years[dt.year] = years.get(dt.year, 0.0) + trade["pnl"]
        key = f"{dt.year}-{dt.month:02d}"
        months[key] = months.get(key, 0.0) + trade["pnl"]
    monthly = list(months.values())
    mean = statistics.fmean(monthly) if monthly else 0.0
    std = statistics.pstdev(monthly) if len(monthly) > 1 else 0.0
    points = [trade["points"] for trade in trades]
    return {
        "pnl": round(final_equity - INITIAL, 2), "final": round(final_equity, 2),
        "trades": len(trades), "pf": round(wins / losses, 3) if losses else 999.0,
        "win_rate": round(sum(t["pnl"] > 0 for t in trades) / len(trades), 3)
                    if trades else 0.0,
        "max_dd_pct": round(100 * max_drawdown, 2),
        "monthly_sharpe": round(mean / std, 3) if std else 0.0,
        "mean_points": round(statistics.fmean(points), 2) if points else 0.0,
        "years": {str(year): round(pnl, 2) for year, pnl in sorted(years.items())},
        "positive_years": sum(pnl > 0 for year, pnl in years.items() if year >= 2018),
    }


def quality(stat):
    annual = [value for year, value in stat["years"].items() if int(year) >= 2018]
    if stat["trades"] < 36 or stat["pf"] <= 1.0 or len(annual) < 5:
        return -math.inf
    # Median year matters more than the exceptional bull-market year; drawdown
    # and losing years are explicit penalties rather than hidden in total PnL.
    return (statistics.median(annual) + 0.25 * min(annual)
            - 4.0 * stat["max_dd_pct"] + 25.0 * stat["positive_years"])


def family_neighbours(params, universe):
    same = [candidate for candidate in universe if candidate["family"] == params["family"]]
    axes = [key for key in params if key != "family"]
    values = {axis: sorted({candidate[axis] for candidate in same}) for axis in axes}
    result = []
    for axis in axes:
        sequence = values[axis]
        at = sequence.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(sequence):
                variant = dict(params)
                variant[axis] = sequence[other]
                result.append(variant)
    return result


def frozen(params):
    return tuple(sorted(params.items()))


def select(bars, ind):
    sample = [bar for bar in bars if bar[TS] < IS_END]
    universe = candidates()
    results = {}
    total = len(universe)
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(sample, ind, params, hi=IS_END)
        if number % 250 == 0:
            print(f"  evaluated {number}/{total}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        neighbours = [results.get(frozen(item)) for item in family_neighbours(params, universe)]
        neighbours = [item for item in neighbours if item is not None]
        profitable = sum(item["pnl"] > 0 for item in neighbours)
        if not neighbours or profitable < math.ceil(0.75 * len(neighbours)):
            continue
        plateau = statistics.median(quality(item) for item in neighbours)
        ranked.append((min(own, plateau), params, stat, plateau))
    ranked.sort(key=lambda row: row[0], reverse=True)

    print("\nTop robust in-sample candidates (2017-2023 only):")
    for score, params, stat, plateau in ranked[:15]:
        print(json.dumps({"score": round(score, 2), "plateau": round(plateau, 2),
                          "params": params, "stats": stat}, sort_keys=True))
    if not ranked:
        raise SystemExit("no candidate cleared the in-sample robustness rules")

    winner = ranked[0]
    protocol = {
        "data": "btc_1m aggregated causally to 1h",
        "in_sample": "2017-08-17 through 2023-12-31",
        "out_of_sample": "2024-01-01 through available 2025 data",
        "initial_balance": INITIAL, "entry_spread": SPREAD,
        "risk_fraction": RISK_FRACTION, "margin": MARGIN, "quantity_step": STEP,
        "candidate_count": len(universe),
        "selection_rule": "median annual PnL with worst-year/drawdown penalties and 75% profitable immediate neighbours",
    }
    payload = {"sealed": True, "protocol": protocol, "params": winner[1],
               "in_sample": winner[2], "score": round(winner[0], 4),
               "plateau_score": round(winner[3], 4)}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nSEALED winner to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def oos(bars, ind):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    seal = payload.pop("seal_sha256")
    payload.pop("out_of_sample", None)
    payload.pop("minute_level_server_validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != seal:
        raise SystemExit("selection seal does not match; rerun select before OOS")
    params = payload["params"]
    stat = backtest(bars, ind, params, lo=IS_END, hi=OOS_END)
    print("Locked parameters:", json.dumps(params, sort_keys=True))
    print("OUT OF SAMPLE 2024-2025:", json.dumps(stat, sort_keys=True))
    payload["seal_sha256"] = seal
    payload["out_of_sample"] = stat
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "oos"))
    args = parser.parse_args()
    bars = hourly_bars(args.phase)
    print(f"loaded {len(bars)} hourly bars")
    ind = indicators(bars)
    if args.phase == "select":
        select(bars, ind)
    else:
        oos(bars, ind)


if __name__ == "__main__":
    main()
