"""Which Maroy exit families combine on one account without stacking drawdown.

Every exit type shares an entry rule, so they trade the same signal and their
losing days are not independent by construction. The question is which subsets
are *sufficiently* independent that blending them cuts drawdown per unit of
return -- and that is a correlation question, not a ranking question. A pair of
strategies both making 30% at 8% drawdown is worth nothing extra if they draw
down on the same days.

METHOD. Each strategy runs standalone under its stage-optimised policy, which
yields a daily equity curve. Because every sizing rule here is fractional, those
daily returns are scale-invariant, so a portfolio holding weight w_i in each
sleeve returns sum(w_i * r_i) each day. Subsets are enumerated, equal-weighted,
compounded, and then levered by a single scalar so that in-sample max drawdown
lands on the 15% budget. Ranking is by in-sample return at that fixed risk, so
every candidate is compared at the same drawdown and the winner is the one that
converts diversification into return rather than into a smaller number.

WHAT THIS MODEL ASSUMES, and where it is optimistic:
  * Sleeves are independent books on a shared balance. Real concurrent positions
    share margin, and two sleeves long at once carry more directional exposure
    than the daily-return sum implies. Correlations here are between *daily*
    results, so intraday overlap is averaged away rather than modelled.
  * Levering the blend by a scalar assumes each sleeve can actually be sized up
    by that factor. The 0.01 lot floor says otherwise on a small account, so
    every survivor is re-run at $10,000 as a check.
  * Selection sees 2020-2024 only; 2025-2026 is scored once per reported combo.
    With thousands of subsets enumerated, the top of an in-sample table is a
    weak signal -- the correlation structure is the durable finding, not the
    identity of the single best quadruple.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import statistics

from sandbox.research import maroy_intraday_momentum as base
from sandbox.research import maroy_optimization as opt


OUTPUT = os.path.join(os.path.dirname(__file__), "maroy_portfolio_result.json")
SOURCE = os.path.join(os.path.dirname(__file__), "maroy_optimization_rth.json")

MAX_SLEEVES = 4


def load_policies(path=SOURCE):
    with open(path) as handle:
        report = json.load(handle)
    out = []
    for family in report["families"]:
        policy = {key: (set(value) if key in ("hours", "weekdays") else value)
                  for key, value in family["policy"].items()}
        if "regime" in policy:
            policy["regime"] = {k: tuple(v) for k, v in policy["regime"].items()}
        out.append((family["strategy"], policy))
    return out


def daily_returns(sessions, config, session_name, policy, window):
    """Day-keyed fractional returns from a standalone run."""
    result = base.run_config(sessions, config, session_name, policy, window)
    curve = result["curve"]
    out = {}
    for (_prev_day, _ts, previous), (day, _ts2, current) in zip(curve, curve[1:]):
        out[day] = (current / previous - 1.0) if previous > 0 else 0.0
    return out


def blend(streams, weights, days):
    return [sum(w * stream.get(day, 0.0) for stream, w in zip(streams, weights))
            for day in days]


def curve_stats(returns):
    equity, peak, drawdown = 1.0, 1.0, 0.0
    for value in returns:
        equity *= (1.0 + value)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 0.0)
        if equity <= 0:
            return {"total_return_pct": -100.0, "max_drawdown_pct": 100.0, "sharpe": -9.9}
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns) if returns else 0.0
    return {
        "total_return_pct": 100.0 * (equity - 1.0),
        "max_drawdown_pct": 100.0 * drawdown,
        "sharpe": (mean / volatility * math.sqrt(base.SESSIONS_PER_YEAR)
                   if volatility else 0.0),
    }


def lever_to_target(returns, target=opt.MDD_TARGET, high=12.0):
    """Smallest-error scalar putting max drawdown on `target`.

    Drawdown is monotone in the scalar for a fixed return path, so a bisection
    is exact here in a way it is not when re-sizing inside the backtest.
    """
    low = 0.0
    if curve_stats(returns)["max_drawdown_pct"] <= 1e-9:
        return 1.0
    for _ in range(40):
        mid = (low + high) / 2
        if curve_stats([r * mid for r in returns])["max_drawdown_pct"] > target:
            high = mid
        else:
            low = mid
    return low


def correlation(a, b):
    if len(a) < 2:
        return 0.0
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    sd_a, sd_b = statistics.pstdev(a), statistics.pstdev(b)
    if sd_a == 0 or sd_b == 0:
        return 0.0
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / len(a)
    return cov / (sd_a * sd_b)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    from sandbox.research import btc_donchian_regime
    features = btc_donchian_regime.daily_features()
    sessions = base.load_sessions("rth")

    names, is_streams, oos_streams = [], [], []
    for name, policy in load_policies():
        policy = {**policy, "regime_features": features}
        config = opt.config_for(name)
        in_sample = daily_returns(sessions, config, "rth", policy, opt.IN_SAMPLE)
        if not any(in_sample.values()):
            continue
        names.append(name)
        is_streams.append(in_sample)
        oos_streams.append(daily_returns(sessions, config, "rth", policy,
                                         opt.OUT_OF_SAMPLE))
        print(f"loaded {name}")

    is_days = sorted({day for stream in is_streams for day in stream})
    oos_days = sorted({day for stream in oos_streams for day in stream})
    is_series = [[stream.get(day, 0.0) for day in is_days] for stream in is_streams]

    print(f"\n{'=' * 100}\nIN-SAMPLE DAILY RETURN CORRELATION (2020-2024)\n{'=' * 100}")
    print(f"{'':34}" + "".join(f"{i:>5}" for i in range(len(names))))
    matrix = [[correlation(is_series[i], is_series[j]) for j in range(len(names))]
              for i in range(len(names))]
    for i, name in enumerate(names):
        print(f"{i:>2} {name[:31]:31}" + "".join(f"{matrix[i][j]:>5.2f}"
                                                 for j in range(len(names))))

    # Standalone, each levered to the same 15% budget, so the portfolio table is
    # comparable against a single sleeve rather than against its unlevered self.
    singles = {}
    for i, name in enumerate(names):
        scalar = lever_to_target(is_series[i])
        singles[i] = {
            "scalar": scalar,
            "is": curve_stats([r * scalar for r in is_series[i]]),
            "oos": curve_stats([oos_streams[i].get(d, 0.0) * scalar for d in oos_days]),
        }

    combos = []
    for size in range(1, MAX_SLEEVES + 1):
        for subset in itertools.combinations(range(len(names)), size):
            weights = [1.0 / size] * size
            blended = [sum(w * is_series[i][d] for i, w in zip(subset, weights))
                       for d in range(len(is_days))]
            scalar = lever_to_target(blended)
            if scalar <= 0:
                continue
            stats = curve_stats([r * scalar for r in blended])
            oos_blend = [sum(w * oos_streams[i].get(day, 0.0)
                             for i, w in zip(subset, weights)) for day in oos_days]
            combos.append({
                "members": [names[i] for i in subset],
                "size": size, "scalar": scalar,
                "is": stats,
                "oos": curve_stats([r * scalar for r in oos_blend]),
                "mean_correlation": (
                    statistics.fmean([matrix[i][j] for i, j in
                                      itertools.combinations(subset, 2)])
                    if size > 1 else 1.0),
            })

    combos.sort(key=lambda c: c["is"]["total_return_pct"], reverse=True)
    print(f"\n{'=' * 116}\nBEST COMBINATIONS, each levered to 15% in-sample MDD "
          f"({len(combos)} enumerated)\n{'=' * 116}")
    print(f"{'n':>2} {'sleeves':52}{'corr':>6}{'IS ret%':>10}{'IS MDD':>8}"
          f"{'OOS ret%':>10}{'OOS MDD':>9}{'OOS Sh':>8}")
    print("-" * 116)
    for combo in combos[:args.top]:
        label = " + ".join(m.replace("Boundary with different exit", "BwDE")
                           .replace("Boundary & ", "B&")
                           .replace("VWAP & Ladder", "V&L") for m in combo["members"])
        print(f"{combo['size']:>2} {label[:52]:52}{combo['mean_correlation']:>6.2f}"
              f"{combo['is']['total_return_pct']:>10.1f}{combo['is']['max_drawdown_pct']:>8.1f}"
              f"{combo['oos']['total_return_pct']:>10.1f}{combo['oos']['max_drawdown_pct']:>9.1f}"
              f"{combo['oos']['sharpe']:>8.2f}")

    with open(args.out, "w") as handle:
        json.dump({"names": names, "correlation": matrix,
                   "singles": {names[i]: v for i, v in singles.items()},
                   "combos": combos[:200]}, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
