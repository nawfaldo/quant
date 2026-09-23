"""Sealed refinement of BTC Hourly Momentum for consistency and drawdown.

The core signal selected by ``btc_strategy_research`` is held fixed. This pass
tests only coarse execution regimes requested after that selection: three UTC
decision hours, broad weekday/weekend filters, a standard VIX 25 regime split,
and equity-compounding risk scaled down when trailing BTC volatility is above a
fixed annual target.

``select`` queries BTC and VIX only through 2023-12-31. It requires every full
2018-2023 calendar year to be profitable and caps both overall and within-year
realized drawdown at 12.5%, leaving a buffer beneath the requested 15% limit for
minute-level bracket resolution. ``oos`` requires the hash-sealed winner and
then evaluates 2024-2025 exactly once.
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
from sandbox.research import btc_strategy_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_consistency_selection.json")
FULL_IS_YEARS = tuple(range(2018, 2024))
MAX_SELECTION_DD = 12.5

CORE = {
    "family": "momentum",
    "lookback": 72,
    "threshold": 1.0,
    "trend": 336,
    "stop_atr": 2.5,
    "reward": 2.5,
    "hold": 24,
}

AXES = {
    "hour": (12, 16, 20),
    "day_mode": ("all", "weekdays", "weekends"),
    "vix_mode": ("none", "below_25", "at_least_25"),
    "risk_fraction": (0.0075, 0.01, 0.0125, 0.015),
    "vol_target": (None, 0.6, 0.8),
}


def candidates():
    return [dict(zip(AXES, values)) for values in itertools.product(*AXES.values())]


def vix_prior_by_bar(bars, phase):
    where = "WHERE timestamp < '2024-01-01'" if phase == "select" else ""
    rows = data.query(
        f"SELECT cast(timestamp as long) ts,close FROM vix_1d {where} ORDER BY timestamp"
    )
    daily = [(int(row[0]) // 1_000_000 // 86_400, float(row[1])) for row in rows]
    out = [None] * len(bars)
    cursor = 0
    latest = None
    for index, bar in enumerate(bars):
        day = bar[base.TS] // 86_400
        # Strictly earlier calendar date: Yahoo's daily value is known only
        # after that trading day closes. This also carries Friday over weekends.
        while cursor < len(daily) and daily[cursor][0] < day:
            latest = daily[cursor][1]
            cursor += 1
        out[index] = latest
    return out


def trailing_annual_volatility(bars, hours=168):
    returns = [0.0]
    for previous, current in zip(bars, bars[1:]):
        returns.append(math.log(current[base.C] / previous[base.C]))
    out = [None] * len(bars)
    total = total_sq = 0.0
    for index, value in enumerate(returns):
        total += value
        total_sq += value * value
        if index >= hours:
            old = returns[index - hours]
            total -= old
            total_sq -= old * old
        if index >= hours - 1:
            mean = total / hours
            variance = max(0.0, total_sq / hours - mean * mean)
            out[index] = math.sqrt(variance * 365.0 * 24.0)
    return out


def accepts(index, bars, vix, params):
    bar = bars[index]
    if bar[base.TS] % 86_400 // 3_600 != params["hour"]:
        return False
    weekday = datetime.fromtimestamp(bar[base.TS], tz=timezone.utc).weekday()
    if params["day_mode"] == "weekdays" and weekday >= 5:
        return False
    if params["day_mode"] == "weekends" and weekday < 5:
        return False
    reading = vix[index]
    if params["vix_mode"] == "below_25":
        return reading is not None and reading < 25.0
    if params["vix_mode"] == "at_least_25":
        return reading is not None and reading >= 25.0
    return True


def quantity(equity, price, stop_distance, risk_fraction):
    if equity <= 0 or price <= 0 or stop_distance <= 0:
        return 0.0
    risk_sized = equity * risk_fraction / stop_distance
    margin_sized = equity / base.MARGIN / price
    # Floor rather than round: a 0.01 minimum BTC quantity must never push the
    # estimated stop loss above the requested equity risk budget.
    return math.floor(min(risk_sized, margin_sized) / base.STEP) * base.STEP


def annual_detail(trades):
    by_year = {}
    for trade in sorted(trades, key=lambda item: item["exit_ts"]):
        year = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc).year
        by_year.setdefault(year, []).append(trade["pnl"])
    equity = base.INITIAL
    detail = {}
    first = min(by_year, default=2017)
    last = max(by_year, default=2023)
    for year in range(first, last + 1):
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


def backtest(bars, indicators, vix, volatility, params, lo=None, hi=None):
    signal_params = {**CORE, "hour": params["hour"]}
    equity = peak = base.INITIAL
    maximum_drawdown = 0.0
    trades = []
    position = pending = None

    for index, bar in enumerate(bars):
        ts = bar[base.TS]
        if lo is not None and ts < lo:
            continue
        if hi is not None and ts >= hi:
            break

        if position is not None:
            side = position["side"]
            exit_price = reason = None
            stop = position["stop"]
            if (side == 1 and bar[base.L] <= stop) or (side == -1 and bar[base.H] >= stop):
                exit_price = min(bar[base.O], stop) if side == 1 else max(bar[base.O], stop)
                reason = "stop"
            else:
                target = position["target"]
                if ((side == 1 and bar[base.H] >= target)
                        or (side == -1 and bar[base.L] <= target)):
                    exit_price = max(bar[base.O], target) if side == 1 else min(bar[base.O], target)
                    reason = "target"
            if exit_price is None and ts - position["ts"] >= CORE["hold"] * 3_600:
                exit_price, reason = bar[base.O], "time"
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
            amount = quantity(
                equity, bar[base.O], pending["stop_distance"], pending["risk_fraction"]
            )
            if amount >= base.STEP:
                side = pending["side"]
                distance = pending["stop_distance"]
                position = {
                    "side": side, "entry": bar[base.O], "ts": ts, "quantity": amount,
                    "stop": bar[base.O] - side * distance,
                    "target": bar[base.O] + side * CORE["reward"] * distance,
                }
            pending = None

        if position is None and pending is None and accepts(index, bars, vix, params):
            side = base.signal(index, bars, indicators, signal_params, {})
            atr = indicators["atr"][index]
            realised = volatility[index]
            if side is not None and atr is not None and realised is not None:
                risk = params["risk_fraction"]
                if params["vol_target"] is not None and realised > 0:
                    risk *= min(1.0, params["vol_target"] / realised)
                pending = {
                    "side": side,
                    "stop_distance": CORE["stop_atr"] * atr,
                    "risk_fraction": risk,
                }

    if position is not None:
        eligible = [bar for bar in bars if (lo is None or bar[base.TS] >= lo)
                    and (hi is None or bar[base.TS] < hi)]
        if eligible:
            bar = eligible[-1]
            points = position["side"] * (bar[base.C] - position["entry"]) - base.SPREAD
            pnl = points * position["quantity"]
            equity += pnl
            peak = max(peak, equity)
            maximum_drawdown = max(
                maximum_drawdown, (peak - equity) / peak if peak > 0 else 1.0
            )
            trades.append({
                "entry_ts": position["ts"], "exit_ts": bar[base.TS],
                "side": position["side"], "points": points, "pnl": pnl,
                "quantity": position["quantity"], "reason": "end",
            })

    result = base.summarize(trades, maximum_drawdown, equity)
    result["annual"] = annual_detail(trades)
    return result


def passes(stat, drawdown=MAX_SELECTION_DD):
    annual = stat["annual"]
    return (
        stat["trades"] >= 100
        and stat["pf"] >= 1.05
        and stat["max_dd_pct"] <= drawdown
        and all(
            str(year) in annual
            and annual[str(year)]["pnl"] > 0
            and annual[str(year)]["max_dd_pct"] <= drawdown
            for year in FULL_IS_YEARS
        )
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = [stat["annual"][str(year)]["return_pct"] for year in FULL_IS_YEARS]
    # Worst year and dispersion dominate. Total return cannot rescue a strategy
    # that stalls in one regime or produces its result in a single lucky year.
    return (
        min(returns)
        + 0.5 * statistics.median(returns)
        - statistics.pstdev(returns)
        - 0.5 * stat["max_dd_pct"]
    )


def frozen(params):
    return tuple(sorted(params.items()))


def neighbours(params):
    out = []
    for axis, values in AXES.items():
        # Day and VIX modes select disjoint samples; "weekends" is not a smooth
        # neighbour of "weekdays", nor is high VIX adjacent to low VIX. Their
        # protection against overfit is the full annual gate. Plateau stability
        # is meaningful only for the ordered schedule and sizing axes.
        if axis in ("day_mode", "vix_mode"):
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if not 0 <= other < len(values):
                continue
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


def select(bars, indicators, vix, volatility):
    universe = candidates()
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = backtest(
            bars, indicators, vix, volatility, params, hi=base.IS_END
        )
        if number % 100 == 0:
            print(f"  evaluated {number}/{len(universe)}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        nearby = [results[frozen(item)] for item in neighbours(params)]
        robust = [item for item in nearby if passes(item, drawdown=15.0)]
        if len(robust) < math.ceil(0.6 * len(nearby)):
            continue
        strict_scores = [quality(item) for item in robust if passes(item)]
        if not strict_scores:
            continue
        plateau = statistics.median(strict_scores)
        ranked.append((min(own, plateau), params, stat, plateau, len(robust), len(nearby)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print("\nTop consistency candidates (2017-2023 only):")
    for score, params, stat, plateau, robust, total in ranked[:15]:
        print(json.dumps({
            "score": round(score, 3), "plateau": round(plateau, 3),
            "robust_neighbours": f"{robust}/{total}", "params": params, "stats": stat,
        }, sort_keys=True))
    if not ranked:
        diagnostics = []
        for params in universe:
            stat = results[frozen(params)]
            annual = stat["annual"]
            positive = sum(
                annual.get(str(year), {}).get("pnl", 0.0) > 0 for year in FULL_IS_YEARS
            )
            worst_return = min(
                (annual.get(str(year), {}).get("return_pct", -100.0)
                 for year in FULL_IS_YEARS),
                default=-100.0,
            )
            diagnostics.append((positive, worst_return, -stat["max_dd_pct"], params, stat))
        diagnostics.sort(reverse=True, key=lambda item: item[:3])
        print("\nClosest cells, for in-sample gate diagnosis only:")
        for positive, worst_return, _negative_dd, params, stat in diagnostics[:12]:
            print(json.dumps({
                "positive_full_years": positive,
                "worst_year_return_pct": worst_return,
                "params": params,
                "stats": stat,
            }, sort_keys=True))
        raise SystemExit("no refinement cleared the annual consistency/drawdown gates")

    winner = ranked[0]
    payload = {
        "sealed": True,
        "protocol": {
            "core_signal": CORE,
            "candidate_count": len(universe),
            "in_sample": "2017-08-17 through 2023-12-31",
            "out_of_sample": "2024-01-01 through available 2025 data",
            "vix_rule": "previous calendar day's ^VIX close only",
            "sizing": "equity compounded; floor to 0.01; 25% margin; volatility can only reduce risk",
            "selection_gate": "every 2018-2023 year profitable; overall and annual DD <=12.5%; >=100 trades; PF >=1.05",
            "plateau_gate": "at least 60% of immediate coarse-grid neighbours profitable with DD <=15%",
            "entry_spread": base.SPREAD,
            "initial_balance": base.INITIAL,
        },
        "params": winner[1],
        "in_sample": winner[2],
        "score": round(winner[0], 6),
        "plateau_score": round(winner[3], 6),
        "robust_neighbours": f"{winner[4]}/{winner[5]}",
    }
    seal(payload)
    print(f"\nSEALED consistency winner to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def oos(bars, indicators, vix, volatility):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("out_of_sample", None)
    payload.pop("minute_level_server_validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("consistency selection seal mismatch")
    stat = backtest(
        bars, indicators, vix, volatility, payload["params"],
        lo=base.IS_END, hi=base.OOS_END,
    )
    print("Locked parameters:", json.dumps(payload["params"], sort_keys=True))
    print("OUT OF SAMPLE 2024-2025:", json.dumps(stat, sort_keys=True))
    payload["seal_sha256"] = expected
    payload["out_of_sample"] = stat
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "oos"))
    args = parser.parse_args()
    bars = base.hourly_bars(args.phase)
    indicators = base.indicators(bars)
    vix = vix_prior_by_bar(bars, args.phase)
    volatility = trailing_annual_volatility(bars)
    print(f"loaded {len(bars)} BTC hours and causal daily VIX context")
    if args.phase == "select":
        select(bars, indicators, vix, volatility)
    else:
        oos(bars, indicators, vix, volatility)


if __name__ == "__main__":
    main()
