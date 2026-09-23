"""BTC momentum constrained to the 09:30-16:00 session.

This is a new 30-minute research family, selected on 2017-2023 only. Positions
can be opened no earlier than 09:30 and are forcibly flattened at the 16:00
open. The score maximizes compounded return while requiring every full sample
year to be profitable and keeping realized drawdown at or below 13.5%, leaving
a small buffer beneath the requested 15% production limit.

Unlike the first BTC experiment, 2024-2025 has been observed by earlier strategy
families. It is therefore repeated validation, not a pristine statistical OOS.
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
from sandbox.research import btc_consistency_research as consistency
from sandbox.research import btc_strategy_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_rth_selection.json")
FULL_IS_YEARS = tuple(range(2018, 2024))
SELECTION_DD_LIMIT = 13.5
SELECTION_DD_FLOOR = 12.0
OPEN_MINUTE = 9 * 60 + 30
CLOSE_MINUTE = 16 * 60
STEP_SECONDS = 30 * 60

AXES = {
    "signal_minute": (10 * 60, 12 * 60, 14 * 60),
    "stop_atr": (3.5, 4.5, 5.5),
    "reward": (1.5, 2.5),
    "vix_mode": ("none", "below_25", "at_least_25"),
    "risk_fraction": (0.01, 0.015, 0.02, 0.025, 0.03),
    "vol_target": (None, 0.6, 0.8),
}


def bars_30m(phase):
    where = "WHERE timestamp < '2024-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM btc_1m {where} "
        "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
    )
    return [
        (int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
        for row in rows
    ]


def ema(values, period):
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def trailing_annual_volatility(bars, periods=672):
    """Causal realized volatility for 30-minute bars over the prior 14 days."""
    returns = [0.0]
    for previous, current in zip(bars, bars[1:]):
        returns.append(math.log(current[base.C] / previous[base.C]))
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
            out[index] = math.sqrt(variance * 365.0 * 48.0)
    return out


def context(bars, phase):
    closes = [bar[base.C] for bar in bars]
    true_ranges = []
    for index, bar in enumerate(bars):
        previous = closes[index - 1] if index else bar[base.O]
        true_ranges.append(max(
            bar[base.H] - bar[base.L],
            abs(bar[base.H] - previous),
            abs(bar[base.L] - previous),
        ))
    atr = [None] * len(bars)
    total = 0.0
    for index, value in enumerate(true_ranges):
        total += value
        if index >= 48:
            total -= true_ranges[index - 48]
        if index >= 47:
            atr[index] = total / 48.0
    return {
        "atr": atr,
        "ema": ema(closes, 672),  # 336 hours
        "volatility": trailing_annual_volatility(bars),
        "vix": consistency.vix_prior_by_bar(bars, phase),
    }


def candidates():
    return [dict(zip(AXES, values)) for values in itertools.product(*AXES.values())]


def signal(index, bars, ctx, params):
    if index < 144 or ctx["atr"][index] is None:  # 72 hours
        return None
    bar = bars[index]
    minute = bar[base.TS] % 86_400 // 60
    if minute != params["signal_minute"]:
        return None
    if datetime.fromtimestamp(bar[base.TS], tz=timezone.utc).weekday() >= 5:
        return None
    vix = ctx["vix"][index]
    if params["vix_mode"] == "below_25" and (vix is None or vix >= 25.0):
        return None
    if params["vix_mode"] == "at_least_25" and (vix is None or vix < 25.0):
        return None

    movement = bar[base.C] - bars[index - 144][base.C]
    # One hourly ATR is approximately 1.5 times the mean 30-minute true range.
    threshold = 1.5 * ctx["atr"][index]
    if movement > threshold and bar[base.C] > ctx["ema"][index]:
        return 1
    if movement < -threshold and bar[base.C] < ctx["ema"][index]:
        return -1
    return None


def quantity(equity, price, stop_distance, risk_fraction):
    risk_sized = equity * risk_fraction / stop_distance
    margin_sized = equity / base.MARGIN / price
    raw = min(risk_sized, margin_sized)
    rounded = math.floor(raw / base.STEP + 0.5) * base.STEP
    affordable = math.floor(margin_sized / base.STEP) * base.STEP
    return max(0.0, min(rounded, affordable))


def backtest(bars, ctx, params, lo=None, hi=None):
    equity = peak = base.INITIAL
    maximum_drawdown = 0.0
    trades = []
    position = pending = None
    traded_day = None

    for index, bar in enumerate(bars):
        ts = bar[base.TS]
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts >= hi:
            break
        day = ts // 86_400
        minute = ts % 86_400 // 60

        if position is not None:
            side = position["side"]
            exit_price = reason = None
            if minute >= CLOSE_MINUTE:
                exit_price, reason = bar[base.O], "session"
            else:
                stop = position["stop"]
                if ((side == 1 and bar[base.L] <= stop)
                        or (side == -1 and bar[base.H] >= stop)):
                    exit_price = min(bar[base.O], stop) if side == 1 else max(bar[base.O], stop)
                    reason = "stop"
                else:
                    target = position["target"]
                    if ((side == 1 and bar[base.H] >= target)
                            or (side == -1 and bar[base.L] <= target)):
                        exit_price = max(bar[base.O], target) if side == 1 else min(bar[base.O], target)
                        reason = "target"
            if exit_price is not None:
                points = side * (exit_price - position["entry"]) - base.SPREAD
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
            # A missing half hour may jump across the session boundary. Never
            # carry a pending entry outside the requested window.
            if day == pending["day"] and OPEN_MINUTE <= minute < CLOSE_MINUTE:
                amount = quantity(
                    equity, bar[base.O], pending["distance"], pending["risk_fraction"]
                )
                if amount >= base.STEP:
                    side = pending["side"]
                    position = {
                        "side": side, "entry": bar[base.O], "ts": ts, "quantity": amount,
                        "stop": bar[base.O] - side * pending["distance"],
                        "target": bar[base.O] + side * params["reward"] * pending["distance"],
                    }
                    traded_day = day
            pending = None

        if position is None and pending is None and traded_day != day:
            side = signal(index, bars, ctx, params)
            realised = ctx["volatility"][index]
            atr = ctx["atr"][index]
            if side is not None and realised is not None and atr is not None:
                risk = params["risk_fraction"]
                if params["vol_target"] is not None and realised > 0:
                    risk *= min(1.0, params["vol_target"] / realised)
                pending = {
                    "side": side, "day": day,
                    "distance": params["stop_atr"] * atr,
                    "risk_fraction": risk,
                }

    result = base.summarize(trades, maximum_drawdown, equity)
    result["annual"] = consistency.annual_detail(trades)
    return result


def passes(stat, limit=SELECTION_DD_LIMIT):
    annual = stat["annual"]
    return (
        stat["trades"] >= 300
        and stat["pf"] >= 1.05
        and stat["max_dd_pct"] <= limit
        and all(
            str(year) in annual
            and annual[str(year)]["pnl"] > 0
            and annual[str(year)]["max_dd_pct"] <= limit
            for year in FULL_IS_YEARS
        )
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in FULL_IS_YEARS]
    compounded = 100.0 * math.log(stat["final"] / base.INITIAL)
    return (
        compounded
        + min(returns)
        + 0.25 * statistics.median(returns)
        - 0.5 * statistics.pstdev(returns)
    )


def frozen(params):
    return tuple(sorted(params.items()))


def neighbours(params):
    out = []
    for axis, values in AXES.items():
        if axis == "vix_mode":
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                variant = dict(params)
                variant[axis] = values[other]
                out.append(variant)
    return out


def seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(bars, ctx):
    universe = candidates()
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(bars, ctx, params, hi=base.IS_END)
        if number % 200 == 0:
            print(f"  evaluated {number}/{len(universe)}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own) or stat["max_dd_pct"] < SELECTION_DD_FLOOR:
            continue
        nearby = [results[frozen(item)] for item in neighbours(params)]
        robust = [item for item in nearby if passes(item, limit=15.0)]
        if len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict = [quality(item) for item in robust if passes(item)]
        if not strict:
            continue
        plateau = statistics.median(strict)
        ranked.append((own, params, stat, plateau, len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print("\nTop RTH candidates (2017-2023 only):")
    for score, params, stat, plateau, robust, total in ranked[:15]:
        print(json.dumps({
            "score": round(score, 3), "plateau": round(plateau, 3),
            "robust_neighbours": f"{robust}/{total}", "params": params, "stats": stat,
        }, sort_keys=True))
    if not ranked:
        close = sorted(
            ((sum(stat["annual"].get(str(year), {}).get("pnl", 0) > 0
                  for year in FULL_IS_YEARS), -stat["max_dd_pct"], params, stat)
             for params in universe for stat in (results[frozen(params)],)),
            reverse=True, key=lambda item: item[:2],
        )
        print("\nClosest in-sample cells:")
        for positive, _dd, params, stat in close[:10]:
            print(json.dumps({"positive_years": positive, "params": params, "stats": stat},
                             sort_keys=True))
        raise SystemExit("no RTH candidate cleared the consistency/drawdown gates")

    winner = ranked[0]
    payload = {
        "sealed": True,
        "protocol": {
            "candidate_count": len(universe),
            "bars": "btc_1m causally aggregated to 30m",
            "session": "weekdays only; positions restricted to 09:30-16:00; 16:00 flatten",
            "in_sample": "2017-08-17 through 2023-12-31",
            "validation_reuse_warning": "2024-2025 was observed by earlier families and is not pristine OOS",
            "selection_gate": "every 2018-2023 year profitable; selected DD 12.0-13.5%; overall and annual DD <=13.5%; >=300 trades; PF >=1.05",
            "sizing": "equity compounded; nearest 0.01 within 25% margin; volatility can only reduce risk",
            "vix_rule": "previous calendar day's ^VIX close only",
            "entry_spread": base.SPREAD,
            "initial_balance": base.INITIAL,
        },
        "params": winner[1], "in_sample": winner[2],
        "score": round(winner[0], 6), "plateau_score": round(winner[3], 6),
        "robust_neighbours": f"{winner[4]}/{winner[5]}",
    }
    seal(payload)
    print(f"\nSEALED RTH winner to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def oos(bars, ctx):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    payload.pop("minute_level_server_validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("RTH selection seal mismatch")
    stat = backtest(bars, ctx, payload["params"], lo=base.IS_END, hi=base.OOS_END)
    print("Locked parameters:", json.dumps(payload["params"], sort_keys=True))
    print("2024-2025 REPEATED VALIDATION:", json.dumps(stat, sort_keys=True))
    payload["seal_sha256"] = expected
    payload["validation"] = stat
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "oos"))
    args = parser.parse_args()
    bars = bars_30m(args.phase)
    ctx = context(bars, args.phase)
    print(f"loaded {len(bars)} thirty-minute BTC bars")
    if args.phase == "select":
        select(bars, ctx)
    else:
        oos(bars, ctx)


if __name__ == "__main__":
    main()
