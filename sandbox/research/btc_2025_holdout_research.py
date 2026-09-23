"""Sealed BTC momentum, ORB, and trend search with a 2025+ holdout.

Selection may query only 2017-08-17 through 2024-12-31.  One winner per
family and one global winner are hash-sealed before ``oos`` may read 2025+.
Signals are the deliberately coarse causal families in
``btc_strategy_research``; this study changes the sample boundary and applies
the production-safe 1% risk budget with floor-to-0.01 sizing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import btc_strategy_research as base


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_2025_holdout_selection.json")
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp())
FULL_YEARS = tuple(range(2018, 2025))
RISK_FRACTION = 0.01
SELECTION_DD_LIMIT = 13.5
NEIGHBOUR_DD_LIMIT = 15.0


def hourly_bars(phase: str):
    where = "WHERE timestamp < '2025-01-01'" if phase == "select" else ""
    rows = data.query(
        "SELECT cast(timestamp as long) ts,first(open),max(high),min(low),"
        f"last(close),sum(volume) FROM btc_1m {where} "
        "SAMPLE BY 1h FILL(NONE) ALIGN TO CALENDAR"
    )
    return [
        (int(row[0]) // 1_000_000, *(float(value) for value in row[1:]))
        for row in rows
    ]


def safe_quantity(equity: float, price: float, stop: float) -> float:
    if equity <= 0.0 or price <= 0.0 or stop <= 0.0:
        return 0.0
    raw = min(
        equity * RISK_FRACTION / stop,
        equity / base.MARGIN / price,
    )
    return math.floor(raw / base.STEP) * base.STEP


def annual_returns(stat):
    equity = base.INITIAL
    out = {}
    for year in sorted(stat["years"], key=int):
        pnl = stat["years"][year]
        out[year] = 100.0 * pnl / equity if equity > 0.0 else -100.0
        equity += pnl
    return out


def passes(stat, dd_limit=SELECTION_DD_LIMIT, require_all_years=True):
    returns = annual_returns(stat)
    positive = sum(returns.get(str(year), -100.0) > 0.0 for year in FULL_YEARS)
    required = len(FULL_YEARS) if require_all_years else len(FULL_YEARS) - 1
    return (
        stat["trades"] >= 100
        and stat["pf"] >= 1.05
        and stat["max_dd_pct"] <= dd_limit
        and positive >= required
    )


def quality(stat):
    if not passes(stat):
        return -math.inf
    returns = annual_returns(stat)
    full = [returns[str(year)] for year in FULL_YEARS]
    return (
        min(full)
        + 0.5 * statistics.median(full)
        - statistics.pstdev(full)
        - 0.5 * stat["max_dd_pct"]
    )


def frozen(params):
    return tuple(sorted(params.items()))


def sealed(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(bars, indicators):
    # Make the imported engine use the exact production-safe sizing rule.  The
    # signal families and all execution logic remain unchanged.
    base.quantity = safe_quantity
    universe = base.candidates()
    results = {}
    for number, params in enumerate(universe, 1):
        results[frozen(params)] = base.backtest(bars, indicators, params, hi=IS_END)
        if number % 200 == 0:
            print(f"evaluated {number}/{len(universe)}", flush=True)

    ranked = []
    for params in universe:
        stat = results[frozen(params)]
        own = quality(stat)
        if not math.isfinite(own):
            continue
        neighbours = [
            results[frozen(item)]
            for item in base.family_neighbours(params, universe)
            if frozen(item) in results
        ]
        robust = [
            item for item in neighbours
            if passes(item, NEIGHBOUR_DD_LIMIT, require_all_years=False)
        ]
        if not neighbours or len(robust) < math.ceil(0.6 * len(neighbours)):
            continue
        neighbour_scores = [quality(item) for item in robust if passes(item)]
        plateau = statistics.median(neighbour_scores) if neighbour_scores else own
        ranked.append((min(own, plateau), params, stat, len(robust), len(neighbours)))
    ranked.sort(key=lambda item: item[0], reverse=True)

    print("\nTop robust 2017-2024 candidates:")
    for score, params, stat, robust, total in ranked[:20]:
        print(json.dumps({
            "score": round(score, 4),
            "robust_neighbours": f"{robust}/{total}",
            "params": params,
            "stats": stat,
            "annual_returns": {
                key: round(value, 2) for key, value in annual_returns(stat).items()
            },
        }, sort_keys=True))
    if not ranked:
        diagnostics = sorted(
            (
                sum(value > 0.0 for year, value in annual_returns(stat).items()
                    if int(year) in FULL_YEARS),
                -stat["max_dd_pct"],
                stat["pf"],
                params,
                stat,
            )
            for params in universe
            for stat in [results[frozen(params)]]
        )
        print("\nClosest cells:")
        for positive, _, _, params, stat in diagnostics[-12:][::-1]:
            print(json.dumps({
                "positive_full_years": positive,
                "params": params,
                "stats": stat,
            }, sort_keys=True))
        raise SystemExit("no candidate cleared the consistency and drawdown gates")

    family_winners = {}
    for family in ("momentum", "orb", "trend"):
        matches = [item for item in ranked if item[1]["family"] == family]
        if matches:
            winner = matches[0]
            family_winners[family] = {
                "score": round(winner[0], 6),
                "params": winner[1],
                "in_sample": winner[2],
                "robust_neighbours": f"{winner[3]}/{winner[4]}",
            }

    winner = ranked[0]
    payload = {
        "sealed": True,
        "protocol": {
            "in_sample": "2017-08-17 through 2024-12-31",
            "out_of_sample": "2025-01-01 through available 2026 data",
            "families": ["momentum", "orb", "trend"],
            "candidate_count": len(universe),
            "initial_balance": base.INITIAL,
            "entry_spread": base.SPREAD,
            "risk_fraction": RISK_FRACTION,
            "quantity_step": base.STEP,
            "margin": base.MARGIN,
            "selection_gate": (
                "all 2018-2024 years profitable; DD <=13.5%; PF >=1.05; "
                ">=100 trades"
            ),
            "plateau_gate": (
                ">=60% immediate family neighbours have at most one losing "
                "year and DD <=15%"
            ),
        },
        "global_winner_family": winner[1]["family"],
        "family_winners": family_winners,
    }
    sealed(payload)
    print(f"\nSEALED to {OUTPUT}")
    print(json.dumps(payload, indent=2, sort_keys=True))


def oos(bars, indicators):
    with open(OUTPUT, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("out_of_sample", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("2025 holdout selection seal mismatch")

    base.quantity = safe_quantity
    results = {}
    for family, candidate in payload["family_winners"].items():
        results[family] = base.backtest(
            bars,
            indicators,
            candidate["params"],
            lo=IS_END,
            hi=OOS_END,
        )
    print("LOCKED 2025-2026 OUT OF SAMPLE:")
    print(json.dumps(results, indent=2, sort_keys=True))
    payload["seal_sha256"] = expected
    payload["out_of_sample"] = results
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "oos"))
    args = parser.parse_args()
    bars = hourly_bars(args.phase)
    print(f"loaded {len(bars)} hourly bars")
    indicators = base.indicators(bars)
    if args.phase == "select":
        select(bars, indicators)
    else:
        oos(bars, indicators)


if __name__ == "__main__":
    main()
