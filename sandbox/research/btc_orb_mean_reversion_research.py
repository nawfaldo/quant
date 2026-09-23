"""Independent BTC RTH opening-range and mean-reversion research families.

Both families use the same sealed protocol as BTC RTH Momentum: selection sees
2017-2023 only, positions are restricted to weekday 09:30-16:00, sizing
compounds current equity, and volatility may reduce but never increase risk.
2024-2025 is repeated validation because earlier BTC families already exposed
that period.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics

from sandbox.research import btc_consistency_research as consistency
from sandbox.research import btc_rth_research as rth
from sandbox.research import btc_strategy_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_orb_mean_reversion_selection.json")
FULL_IS_YEARS = tuple(range(2018, 2024))
DD_FLOOR = 12.0
DD_LIMIT = 13.5

ORB_AXES = {
    "range_minutes": (30, 60),
    "last_entry_minute": (12 * 60, 14 * 60),
    "breakout_atr": (0.0, 0.25),
    "stop_atr": (3.5, 5.5),
    "reward": (1.5, 2.5),
    "trend": ("none", "ema_336h"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.015, 0.02, 0.025),
    "vol_target": (None, 0.6),
}

MEAN_AXES = {
    "signal_minute": (10 * 60, 12 * 60, 14 * 60),
    "mean_period": (48, 144),
    "deviation_atr": (1.5, 2.5),
    "stop_atr": (2.5, 4.5),
    "target_mode": ("mean", "rr_1_5"),
    "regime": ("none", "ema_slope"),
    "vix_mode": ("none", "below_25"),
    "risk_fraction": (0.015, 0.02, 0.025),
    "vol_target": (None, 0.6),
}


def candidates(axes):
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def context(bars, phase):
    ctx = rth.context(bars, phase)
    closes = [bar[base.C] for bar in bars]
    ctx["mean_48"] = rth.ema(closes, 48)
    ctx["mean_144"] = rth.ema(closes, 144)
    return ctx


def accepts_vix(value, mode):
    if mode == "below_25":
        return value is not None and value < 25.0
    return True


def weekday(bar):
    # Unix day zero was Thursday, so Monday maps to zero after adding three.
    return (bar[base.TS] // 86_400 + 3) % 7 < 5


def orb_signal(index, bars, ctx, params, state):
    bar = bars[index]
    day = bar[base.TS] // 86_400
    minute = bar[base.TS] % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update({"day": day, "high": None, "low": None})

    range_end = rth.OPEN_MINUTE + params["range_minutes"]
    if rth.OPEN_MINUTE <= minute < range_end:
        state["high"] = bar[base.H] if state["high"] is None else max(state["high"], bar[base.H])
        state["low"] = bar[base.L] if state["low"] is None else min(state["low"], bar[base.L])
        return None
    if (
        minute < range_end
        or minute > params["last_entry_minute"]
        or state["high"] is None
        or not weekday(bar)
    ):
        return None
    atr = ctx["atr"][index]
    if atr is None or not accepts_vix(ctx["vix"][index], params["vix_mode"]):
        return None
    upper = state["high"] + params["breakout_atr"] * atr
    lower = state["low"] - params["breakout_atr"] * atr
    side = 1 if bar[base.C] > upper else -1 if bar[base.C] < lower else None
    if side is None:
        return None
    if params["trend"] == "ema_336h":
        aligned = bar[base.C] > ctx["ema"][index] if side == 1 else bar[base.C] < ctx["ema"][index]
        if not aligned:
            return None
    return {
        "side": side,
        "distance": params["stop_atr"] * atr,
        "reward": params["reward"],
        "target": None,
    }


def mean_signal(index, bars, ctx, params, _state):
    if index < 48:
        return None
    bar = bars[index]
    minute = bar[base.TS] % 86_400 // 60
    if minute != params["signal_minute"] or not weekday(bar):
        return None
    atr = ctx["atr"][index]
    if atr is None or not accepts_vix(ctx["vix"][index], params["vix_mode"]):
        return None
    mean = ctx[f"mean_{params['mean_period']}"][index]
    deviation = bar[base.C] - mean
    threshold = params["deviation_atr"] * atr
    side = 1 if deviation < -threshold else -1 if deviation > threshold else None
    if side is None:
        return None
    if params["regime"] == "ema_slope":
        if index < 48:
            return None
        slope = ctx["ema"][index] - ctx["ema"][index - 48]
        if (side == 1 and slope <= 0.0) or (side == -1 and slope >= 0.0):
            return None
    return {
        "side": side,
        "distance": params["stop_atr"] * atr,
        "reward": 1.5,
        "target": mean if params["target_mode"] == "mean" else None,
    }


def backtest(family, bars, ctx, params, lo=None, hi=None):
    equity = peak = base.INITIAL
    maximum_drawdown = 0.0
    trades = []
    position = pending = None
    traded_day = None
    signal_state = {}
    signal_fn = orb_signal if family == "orb" else mean_signal

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
            if minute >= rth.CLOSE_MINUTE:
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
                maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({
                    "entry_ts": position["ts"], "exit_ts": ts, "side": side,
                    "points": points, "pnl": pnl, "quantity": position["quantity"],
                    "reason": reason,
                })
                position = None

        if position is None and pending is not None:
            if day == pending["day"] and rth.OPEN_MINUTE <= minute < rth.CLOSE_MINUTE:
                amount = rth.quantity(
                    equity, bar[base.O], pending["distance"], pending["risk_fraction"]
                )
                target = pending["target"]
                valid_target = target is None or (
                    (pending["side"] == 1 and target > bar[base.O])
                    or (pending["side"] == -1 and target < bar[base.O])
                )
                if amount >= base.STEP and valid_target:
                    side = pending["side"]
                    target = target if target is not None else (
                        bar[base.O] + side * pending["reward"] * pending["distance"]
                    )
                    position = {
                        "side": side, "entry": bar[base.O], "ts": ts, "quantity": amount,
                        "stop": bar[base.O] - side * pending["distance"], "target": target,
                    }
                    traded_day = day
            pending = None

        if position is None and pending is None and traded_day != day:
            setup = signal_fn(index, bars, ctx, params, signal_state)
            realised = ctx["volatility"][index]
            if setup is not None and realised is not None:
                risk = params["risk_fraction"]
                if params["vol_target"] is not None and realised > 0.0:
                    risk *= min(1.0, params["vol_target"] / realised)
                pending = {**setup, "day": day, "risk_fraction": risk}

    result = base.summarize(trades, maximum_drawdown, equity)
    result["annual"] = consistency.annual_detail(trades)
    return result


def passes(stat, limit=DD_LIMIT):
    annual = stat["annual"]
    return (
        stat["trades"] >= 250
        and stat["pf"] >= 1.05
        and stat["max_dd_pct"] <= limit
        and all(
            str(year) in annual
            and annual[str(year)]["pnl"] > 0.0
            and annual[str(year)]["max_dd_pct"] <= limit
            for year in FULL_IS_YEARS
        )
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in FULL_IS_YEARS]
    return (
        100.0 * math.log(stat["final"] / base.INITIAL)
        + min(returns)
        + 0.25 * statistics.median(returns)
        - 0.5 * statistics.pstdev(returns)
    )


def frozen(params):
    return tuple(sorted(params.items()))


def neighbours(params, axes):
    out = []
    for axis, values in axes.items():
        if axis == "vix_mode":
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                variant = dict(params)
                variant[axis] = values[other]
                out.append(variant)
    return out


def select_family(family, axes, bars, ctx):
    universe = candidates(axes)
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(family, bars, ctx, params, hi=base.IS_END)
        if number % 250 == 0:
            print(f"  {family}: evaluated {number}/{len(universe)}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own) or not DD_FLOOR <= stat["max_dd_pct"] <= DD_LIMIT:
            continue
        nearby = [results[frozen(item)] for item in neighbours(params, axes)]
        robust = [item for item in nearby if passes(item, limit=15.0)]
        if len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict_scores = [quality(item) for item in robust if passes(item)]
        if not strict_scores:
            continue
        ranked.append((
            own, params, stat, statistics.median(strict_scores), len(robust), len(nearby)
        ))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print(f"\nTop {family} candidates (2017-2023 only):")
    for score, params, stat, plateau, robust, total in ranked[:10]:
        print(json.dumps({
            "score": round(score, 3), "plateau": round(plateau, 3),
            "robust_neighbours": f"{robust}/{total}", "params": params, "stats": stat,
        }, sort_keys=True))
    if not ranked:
        closest = sorted(
            ((sum(stat["annual"].get(str(year), {}).get("pnl", 0.0) > 0.0
                  for year in FULL_IS_YEARS), -abs(stat["max_dd_pct"] - 12.75), params, stat)
             for params in universe for stat in (results[frozen(params)],)),
            reverse=True, key=lambda item: item[:2],
        )
        for positive, _distance, params, stat in closest[:5]:
            print(json.dumps({
                "positive_years": positive, "params": params, "stats": stat,
            }, sort_keys=True))
        return None
    score, params, stat, plateau, robust, total = ranked[0]
    return {
        "params": params, "in_sample": stat, "score": round(score, 6),
        "plateau_score": round(plateau, 6), "robust_neighbours": f"{robust}/{total}",
    }


def seal(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(bars, ctx):
    winners = {
        "orb": select_family("orb", ORB_AXES, bars, ctx),
        "mean_reversion": select_family("mean_reversion", MEAN_AXES, bars, ctx),
    }
    payload = {
        "sealed": True,
        "protocol": {
            "bars": "btc_1m causally aggregated to 30m",
            "candidate_counts": {
                "orb": len(candidates(ORB_AXES)),
                "mean_reversion": len(candidates(MEAN_AXES)),
            },
            "in_sample": "2017-08-17 through 2023-12-31",
            "validation_reuse_warning": "2024-2025 was observed by earlier BTC families and is not pristine OOS",
            "selection_gate": "every 2018-2023 year profitable; selected DD 12.0-13.5%; overall and annual DD <=13.5%; >=250 trades; PF >=1.05",
            "session": "weekdays only; positions restricted to 09:30-16:00; 16:00 flatten",
            "sizing": "equity compounded; nearest 0.01 within 25% margin; volatility can only reduce risk",
            "vix_rule": "previous calendar day's VIX close only",
            "entry_spread": base.SPREAD,
            "initial_balance": base.INITIAL,
        },
        "families": winners,
    }
    seal(payload)
    print(f"\nSEALED alternative-family selections to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def validate(bars, ctx):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    payload.pop("minute_level_server_validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("alternative-family selection seal mismatch")
    validation = {}
    for family, winner in payload["families"].items():
        if winner is not None:
            validation[family] = backtest(
                family, bars, ctx, winner["params"], lo=base.IS_END, hi=base.OOS_END
            )
            print(f"{family} 2024-2025 REPEATED VALIDATION:")
            print(json.dumps(validation[family], sort_keys=True))
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    bars = rth.bars_30m("select" if args.phase == "select" else "oos")
    ctx = context(bars, "select" if args.phase == "select" else "oos")
    print(f"loaded {len(bars)} thirty-minute BTC bars")
    if args.phase == "select":
        select(bars, ctx)
    else:
        validate(bars, ctx)


if __name__ == "__main__":
    main()
