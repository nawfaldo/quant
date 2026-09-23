"""Original S&P 500 first-to-last-half-hour momentum, with sealed OOS.

Gao, Han, Li, and Zhou (JFE 2018) document that the return from the prior close
through the first half hour predicts the final half-hour return.  Their second
specification adds the penultimate half-hour.  This module tests those rules on
ES without reusing the broader noise-band strategy's 2025-2026 result.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics

from sandbox.research import es_noise_momentum_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "es_close_momentum_selection.json")

AXES = {
    "signal": ("first", "sum", "agree"),
    "opening_move_multiple": (0.0, 0.5, 1.0, 1.5),
    "side_mode": ("both", "long", "short"),
    "stop_atr": (None, 0.15, 0.25, 0.35),
    "vol_target": (None, 0.15, 0.20),
}
CATEGORICAL = {"signal", "side_mode"}

MIN_TRADES = 500
MIN_PF = 1.05
MAX_DD = 18.0
MIN_YEAR = 3.0
MAX_CV = 0.75
MAX_YEAR_SHARE = 0.30


def candidates():
    return [dict(zip(AXES, values)) for values in itertools.product(*AXES.values())]


def frozen(params):
    return tuple(sorted((key, str(value)) for key, value in params.items()))


def signal_side(session, params):
    first = session.bars[0][base.C] / session.prior_close - 1.0
    penultimate = session.bars[-2][base.C] / session.bars[-2][base.O] - 1.0
    sigma = session.sigma[10][base.SESSION_OPEN_MINUTE]
    if sigma is None or abs(first) < params["opening_move_multiple"] * sigma:
        return None
    if params["signal"] == "first":
        value = first
    elif params["signal"] == "sum":
        value = first + penultimate
    else:
        if first * penultimate <= 0.0:
            return None
        value = first
    side = 1 if value > 0.0 else -1 if value < 0.0 else None
    return side if side is not None and base.accepts_side(side, params["side_mode"]) else None


def backtest(sessions, params, lo=base.IS_START, hi=base.IS_END, spread=base.SPREAD):
    equity = peak = base.INITIAL
    maximum_drawdown = 0.0
    trades = []
    for session in sessions:
        ts = session.day * 86_400
        if ts < lo or ts >= hi or session.prior_close is None:
            continue
        if session.atr is None or session.volatility is None:
            continue
        side = signal_side(session, params)
        if side is None:
            continue
        risk = base.BASE_RISK_FRACTION
        if params["vol_target"] is not None and session.volatility > 0.0:
            risk *= min(1.0, params["vol_target"] / session.volatility)
        sizing_distance = (params["stop_atr"] if params["stop_atr"] is not None
                           else 0.25) * session.atr
        amount = base.quantity(equity, session.bars[-1][base.O], sizing_distance,
                               risk, spread)
        if amount < base.MINIMUM_QUANTITY:
            continue
        bar = session.bars[-1]
        entry = bar[base.O] + side * spread
        raw_exit = bar[base.C]
        reason = "close"
        if params["stop_atr"] is not None:
            stop = bar[base.O] - side * sizing_distance
            stopped = ((side == 1 and bar[base.L] <= stop)
                       or (side == -1 and bar[base.H] >= stop))
            if stopped:
                raw_exit = min(bar[base.O], stop) if side == 1 else max(bar[base.O], stop)
                reason = "stop"
        adverse = bar[base.L] if side == 1 else bar[base.H]
        marked = equity + base._trade_pnl(side, entry, adverse, amount)
        maximum_drawdown = max(maximum_drawdown,
                               (peak - marked) / peak if peak > 0 else 1.0)
        pnl = base._trade_pnl(side, entry, raw_exit, amount)
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown,
                               (peak - equity) / peak if peak > 0 else 1.0)
        trades.append({
            "entry_ts": bar[base.TS], "exit_ts": bar[base.TS] + 30 * 60,
            "side": side, "quantity": amount, "pnl": pnl, "reason": reason,
        })
    return base.summarize(trades, equity, maximum_drawdown)


def passes(stat, neighbour=False):
    annual = stat["annual"]
    consistency = stat["consistency"]
    return (stat["trades"] >= MIN_TRADES and stat["pf"] >= MIN_PF
            and stat["max_dd_pct"] <= MAX_DD + (2.0 if neighbour else 0.0)
            and all(str(year) in annual and annual[str(year)]["return_pct"] >= MIN_YEAR
                    for year in base.IS_YEARS)
            and consistency["years_above_5pct"] >= 6
            and consistency["annual_return_cv"] <= MAX_CV
            and consistency["max_single_year_return_share"] <= MAX_YEAR_SHARE)


def quality(stat):
    if not passes(stat):
        return -math.inf
    annual = [stat["annual"][str(year)]["return_pct"] for year in base.IS_YEARS]
    return (25.0 * math.log(stat["final"] / base.INITIAL) + 3.0 * min(annual)
            + statistics.median(annual) - 1.5 * statistics.pstdev(annual)
            - 0.5 * stat["max_dd_pct"])


def neighbours(params):
    result = []
    for axis, values in AXES.items():
        if axis in CATEGORICAL:
            continue
        at = values.index(params[axis])
        for other in (at - 1, at + 1):
            if 0 <= other < len(values):
                changed = dict(params)
                changed[axis] = values[other]
                result.append(changed)
    return result


def write_sealed(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(sessions):
    universe = candidates()
    results = {frozen(params): backtest(sessions, params) for params in universe}
    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        nearby = [results[frozen(item)] for item in neighbours(params)]
        robust = [item for item in nearby if passes(item, neighbour=True)]
        if len(robust) < math.ceil(0.60 * len(nearby)):
            continue
        strict = [quality(item) for item in robust if passes(item)]
        if not strict:
            continue
        plateau = statistics.median(strict)
        ranked.append((min(own, plateau), own, plateau, params, stat,
                       len(robust), len(nearby)))
    ranked.sort(reverse=True, key=lambda row: (row[0], (row[1] + row[2]) / 2.0))
    print("Top original close-momentum cells (2017-2024 only):")
    for robust_score, own, plateau, params, stat, robust, total in ranked[:10]:
        print(json.dumps({"robust_score": round(robust_score, 4),
                          "score": round(own, 4), "plateau": round(plateau, 4),
                          "robust_neighbours": f"{robust}/{total}",
                          "params": params, "stats": stat}, sort_keys=True))
    if not ranked:
        closest = sorted(((sum(stat["annual"].get(str(year), {}).get("pnl", 0) > 0
                               for year in base.IS_YEARS), stat["pf"], params, stat)
                          for params in universe
                          for stat in (results[frozen(params)],)), reverse=True,
                         key=lambda row: row[:2])
        for positive, _pf, params, stat in closest[:5]:
            print(json.dumps({"positive_years": positive, "params": params,
                              "stats": stat}, sort_keys=True))
        with open(OUTPUT, "w", encoding="utf-8") as handle:
            json.dump({
                "sealed": False,
                "verdict": "REJECTED_IN_SAMPLE",
                "protocol": {
                    "source_rule": "Gao-Han-Li-Zhou first/penultimate half-hour predicts last half-hour",
                    "in_sample": "2017-2024", "candidate_count": len(universe),
                    "spread": base.SPREAD,
                    "consistency_gate": "all years >=3%, 6/8 >=5%, CV <=0.75, max year share <=30%",
                },
                "closest": [{"positive_years": positive, "params": params,
                             "stats": stat}
                            for positive, _pf, params, stat in closest[:5]],
            }, handle, indent=2, sort_keys=True)
            handle.write("\n")
        raise SystemExit("no original close-momentum cell cleared consistency gates")
    robust_score, own, plateau, params, stat, robust, total = ranked[0]
    write_sealed({
        "sealed": True,
        "protocol": {
            "source_rule": "Gao-Han-Li-Zhou first/penultimate half-hour predicts last half-hour",
            "in_sample": "2017-2024", "out_of_sample": "2025-2026",
            "candidate_count": len(universe), "spread": base.SPREAD,
            "sizing": "1% compounded equity stop risk; Exness regular US500",
            "consistency_gate": "all years >=3%, 6/8 >=5%, CV <=0.75, max year share <=30%",
        },
        "winner": {"params": params, "in_sample": stat,
                   "robust_score": round(robust_score, 6), "score": round(own, 6),
                   "plateau_score": round(plateau, 6),
                   "robust_neighbours": f"{robust}/{total}"},
    })


def validate(sessions):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("close-momentum selection seal mismatch")
    params = payload["winner"]["params"]
    validation = {f"spread_{spread:.2f}": backtest(
        sessions, params, base.IS_END, base.OOS_END, spread
    ) for spread in (0.20, 0.40, 0.80)}
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(validation, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()
    sessions = base.build_sessions(base.bars_30m(args.phase), args.phase)
    print(f"loaded {len(sessions)} sessions")
    if args.phase == "select":
        select(sessions)
    else:
        validate(sessions)


if __name__ == "__main__":
    main()
