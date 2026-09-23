"""A better regime filter for BTC Donchian, fitted 2020-2024, tested 2025-2026.

The compiled VIX<25 gate came from `btc_families_research`; tightening it to 20
was tried and reverted after a direct 2020-2026 run showed it cost 31% of return
*and* raised max drawdown. This searches for something that actually helps.

TWO CHANGES OF METHOD, both from that failure:

1. **A longer fitting window.** The earlier overlay work fitted on 2018-2024 and
   judged on 2025-2026; the filter it chose only helped in the judging window.
   Here selection sees 2020-01..2024-12 and the test window is untouched by the
   selection rule.

2. **A selection criterion that cannot be gamed by trading less.** Ranking on
   monthly Sharpe is what picked the losing filter: a gate that removes most of
   the strategy flatters every ratio while destroying the business. Selection
   here maximises return / max-drawdown *subject to keeping at least 80% of the
   unfiltered return and 60% of its trades*. A filter has to earn its ratio, not
   buy it by switching off.

FEATURES. Motivated by the decay analysis rather than picked at random: Donchian's
strongest years (2023, 2025) ran at ~44% BTC realised volatility and its weakest
(2021, 2022) at 65-81%, so BTC's *own* volatility is the natural candidate and
VIX -- an equity gauge -- is the incumbent to beat. All are causal: each day's
value is built from sessions that closed strictly before it.

HONEST CAVEAT. 2025-2026 has already been read several times for these
strategies in other studies, so it is repeated validation rather than a pristine
holdout. Treat a pass here as weaker evidence than the arithmetic suggests, and
the walk-forward failure recorded in `btc_donchian.rs` still stands.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import btc_families_research as btc
from sandbox.research import btc_walkforward as wf
from sandbox.research import es_strategy_research as es


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_donchian_regime_selection.json")
INITIAL = btc.INITIAL

IS_LO = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
IS_HI = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_HI = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())

#: Selection floors. A filter must keep most of the business it is filtering.
MIN_RETURN_SHARE = 0.80
MIN_TRADE_SHARE = 0.60


# --------------------------------------------------------------------------- #
# features
# --------------------------------------------------------------------------- #


def daily_features():
    rows = data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as long) day,last(close) close "
        "FROM btc_1m ORDER BY day"
    )
    days = [int(r[0]) // 1_000_000 // 86_400 for r in rows]
    prices = [float(r[1]) for r in rows]
    returns = [0.0]
    for previous, current in zip(prices, prices[1:]):
        returns.append(math.log(current / previous) if previous > 0 else 0.0)

    def rolling_vol(window):
        out = [None] * len(returns)
        for index in range(len(returns)):
            if index < window:
                continue
            # Strictly prior sessions: the current day's own move is excluded.
            past = returns[index - window:index]
            out[index] = 100.0 * statistics.pstdev(past) * math.sqrt(365)
        return out

    rv20, rv60 = rolling_vol(20), rolling_vol(60)

    def sma(window):
        out = [None] * len(prices)
        total = 0.0
        for index, value in enumerate(prices):
            total += value
            if index >= window:
                total -= prices[index - window]
            if index >= window:
                out[index] = total / window
        return out

    sma50, sma200 = sma(50), sma(200)

    def percentile(series, window=252):
        out = [None] * len(series)
        for index in range(len(series)):
            past = [v for v in series[max(0, index - window):index] if v is not None]
            if series[index] is None or len(past) < window // 2:
                continue
            out[index] = sum(1 for v in past if v <= series[index]) / len(past)
        return out

    rv_pct = percentile(rv20)

    vix_rows = data.query(
        "SELECT cast(timestamp as long) ts,close FROM vix_1d ORDER BY timestamp"
    )
    vix = {int(r[0]) // 1_000_000 // 86_400: float(r[1]) for r in vix_rows}

    out = {}
    for index, day in enumerate(days):
        previous_vix = next(
            (vix[day - back] for back in range(1, 6) if day - back in vix), None
        )
        out[day] = {
            "rv20": rv20[index],
            "rv60": rv60[index],
            "rv_pct": rv_pct[index],
            "rv_ratio": (rv20[index] / rv60[index])
            if rv20[index] and rv60[index]
            else None,
            "vix": previous_vix,
            "above_sma50": (
                None if sma50[index] is None else prices[index - 1] > sma50[index]
            ),
            "above_sma200": (
                None if sma200[index] is None else prices[index - 1] > sma200[index]
            ),
        }
    return out


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


def multiplier(rule, params, f):
    """Exposure for a day. Missing readings fail open, never closed."""
    if rule == "always":
        return 1.0
    if rule == "btc_vol_below":
        v = f.get("rv20")
        return 1.0 if v is None else (1.0 if v < params["level"] else 0.0)
    if rule == "btc_vol_pct_below":
        v = f.get("rv_pct")
        return 1.0 if v is None else (1.0 if v < params["pct"] else 0.0)
    if rule == "btc_vol_contracting":
        v = f.get("rv_ratio")
        return 1.0 if v is None else (1.0 if v < params["ratio"] else 0.0)
    if rule == "btc_vol_scale":
        v = f.get("rv20")
        if not v or v <= 0:
            return 1.0
        return min(1.0, params["target"] / v)
    if rule == "vix_below":
        v = f.get("vix")
        return 1.0 if v is None else (1.0 if v < params["level"] else 0.0)
    if rule == "above_sma200":
        v = f.get("above_sma200")
        return 1.0 if v is None else (1.0 if v else 0.0)
    if rule == "above_sma50":
        v = f.get("above_sma50")
        return 1.0 if v is None else (1.0 if v else 0.0)
    raise ValueError(rule)


RULES = [
    ("always", [{}]),
    ("btc_vol_below", [{"level": x} for x in (40, 45, 50, 55, 60, 70)]),
    ("btc_vol_pct_below", [{"pct": x} for x in (0.5, 0.6, 0.7, 0.8, 0.9)]),
    ("btc_vol_contracting", [{"ratio": x} for x in (0.9, 1.0, 1.1)]),
    ("btc_vol_scale", [{"target": x} for x in (35, 45, 55, 65)]),
    ("vix_below", [{"level": x} for x in (18, 20, 25, 30)]),
    ("above_sma200", [{}]),
    ("above_sma50", [{}]),
]


def replay(trades, features, rule, params, lo, hi):
    equity = peak = INITIAL
    max_dd = 0.0
    pnls, months = [], {}
    for entry_ts, points, stop, price in sorted(trades):
        if entry_ts < lo or entry_ts >= hi:
            continue
        scale = multiplier(rule, params, features.get(entry_ts // 86_400, {}))
        if scale <= 0.0:
            continue
        quantity = es.quantity(equity, price, stop, btc.RISK_FRACTION * scale)
        if quantity < btc.STEP:
            continue
        pnl = points * quantity
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 1.0)
        pnls.append(pnl)
        key = datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + pnl
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    values = list(months.values())
    ret = 100.0 * (equity - INITIAL) / INITIAL
    dd = 100.0 * max_dd
    return {
        "return_pct": round(ret, 1),
        "max_dd_pct": round(dd, 2),
        "ratio": round(ret / dd, 2) if dd > 0 else 0.0,
        "pf": round(wins / losses, 3) if losses else 0.0,
        "trades": len(pnls),
        "positive_months": sum(1 for v in values if v > 0),
        "months": len(values),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()

    bars, ctx = btc.context("validate")
    sealed = json.load(open(btc.OUTPUT, encoding="utf-8"))
    params = dict(sealed["families"]["trend"]["params"])
    trades = wf.resolve("trend", bars, ctx, params)
    features = daily_features()

    lo, hi = (IS_LO, IS_HI) if args.phase == "select" else (IS_HI, OOS_HI)
    label = "IN SAMPLE 2020-2024" if args.phase == "select" else "OUT OF SAMPLE 2025-2026"
    control = replay(trades, features, "always", {}, lo, hi)

    print(f"=== {label}: BTC Donchian, $1,000, sealed geometry ===")
    print(f"{'rule':<28}{'return':>10}{'dd':>8}{'ret/dd':>9}{'pf':>7}"
          f"{'trades':>8}{'+mo':>8}   vs control")
    print("-" * 96)
    rows = []
    for rule, grid in RULES:
        for options in grid:
            stat = replay(trades, features, rule, options, lo, hi)
            tag = rule + ("" if not options else " " + ",".join(
                f"{k}={v}" for k, v in options.items()))
            keeps = (
                stat["return_pct"] >= MIN_RETURN_SHARE * control["return_pct"]
                and stat["trades"] >= MIN_TRADE_SHARE * control["trades"]
            )
            rows.append((tag, rule, options, stat, keeps))
            flag = "" if rule == "always" else ("  ok" if keeps else "  cut")
            print(f"{tag:<28}{stat['return_pct']:>9.1f}%{stat['max_dd_pct']:>7.2f}%"
                  f"{stat['ratio']:>9.2f}{stat['pf']:>7.3f}{stat['trades']:>8}"
                  f"{stat['positive_months']:>5}/{stat['months']:<2}"
                  f"{stat['ratio'] - control['ratio']:>+8.2f}{flag}")

    if args.phase == "select":
        eligible = [r for r in rows if r[1] != "always" and r[4]]
        if not eligible:
            print("\n  no rule kept 80% of return and 60% of trades; "
                  "nothing beats leaving the strategy alone")
            return
        best = max(eligible, key=lambda r: r[3]["ratio"])
        print(f"\n  eligible rules: {len(eligible)}/{len(rows) - 1}")
        print(f"  SELECTED (best return/drawdown among eligible): {best[0]}")
        json.dump({"rule": best[1], "params": best[2], "in_sample": best[3],
                   "control": control}, open(OUTPUT, "w", encoding="utf-8"),
                  indent=2, sort_keys=True)
        print(f"  sealed to {OUTPUT}")
    else:
        sel = json.load(open(OUTPUT, encoding="utf-8"))
        tag = sel["rule"] + ("" if not sel["params"] else " " + ",".join(
            f"{k}={v}" for k, v in sel["params"].items()))
        chosen = replay(trades, features, sel["rule"], sel["params"], lo, hi)
        print(f"\n  pre-registered pick: {tag}")
        print(f"    control  return {control['return_pct']:>7.1f}%  "
              f"dd {control['max_dd_pct']:>6.2f}%  ratio {control['ratio']:>6.2f}")
        print(f"    filtered return {chosen['return_pct']:>7.1f}%  "
              f"dd {chosen['max_dd_pct']:>6.2f}%  ratio {chosen['ratio']:>6.2f}")
        verdict = "BEATS" if chosen["ratio"] > control["ratio"] else "LOSES TO"
        print(f"    -> {verdict} the do-nothing control on return/drawdown")


if __name__ == "__main__":
    main()
