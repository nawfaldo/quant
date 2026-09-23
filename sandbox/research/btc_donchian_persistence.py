"""Persistence-based regime filters for BTC Donchian. Fit 2020-2024, test 2025-2026.

`btc_donchian_regime.py` tried VIX, BTC realized volatility, and trend filters.
All failed, and the diagnostic was damning: across 24 rules the correlation
between in-sample and out-of-sample return/drawdown was **+0.077**. Those
features all measure the *magnitude* of movement (fear, volatility) or its
*direction* (above a moving average). A Donchian breakout does not need either.
It needs the tape to keep going after the channel breaks -- persistence.

The literature on regime-switching for trend following converges on three
estimators of exactly that, and none of them is a volatility measure:

  * **Hurst exponent** -- H > 0.5 persistent (trending), H < 0.5 anti-persistent
    (mean-reverting), H = 0.5 random walk. Estimated here by the variance-of-
    differences method on log price, which is the standard quick estimator and
    avoids the small-sample bias R/S has on short windows.
  * **Variance ratio** (Lo-MacKinlay) -- Var of q-period returns over q times the
    Var of 1-period returns. Above 1 means returns are positively autocorrelated
    and moves extend; below 1 means they reverse.
  * **Kaufman efficiency ratio** -- net move divided by the sum of absolute
    moves. Near 1 the market travelled in a straight line; near 0 it churned.

Two horizons are computed for each, because it is not obvious a priori whether
what matters is daily-scale persistence (does this week trend?) or intraday
(do 30-minute moves extend inside a session?). Both are lagged so a day is never
scored using its own bars.

PROTOCOL, unchanged and pre-registered: selection sees 2020-01..2024-12 only,
ranks on return/drawdown subject to keeping >=80% of unfiltered return and >=60%
of its trades, and 2025-2026 is read once. The in-sample/out-of-sample rank
correlation is reported again -- if it is near zero a second time with genuinely
different features, that is the finding, not the individual rules.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import btc_donchian_regime as base
from sandbox.research import btc_families_research as btc
from sandbox.research import btc_walkforward as wf


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_donchian_persistence_selection.json")


# --------------------------------------------------------------------------- #
# persistence estimators
# --------------------------------------------------------------------------- #


def hurst(log_prices):
    """Variance-of-differences Hurst on a log-price window.

    `std(logP[t+lag] - logP[t])` scales as `lag ** H`, so the slope of the
    log-log fit is H. Returns None when the window is degenerate (a flat or
    near-flat stretch gives a zero dispersion and no usable slope).
    """
    lags = [2, 4, 8, 16, 32]
    xs, ys = [], []
    for lag in lags:
        if len(log_prices) <= lag + 4:
            continue
        diffs = [log_prices[i + lag] - log_prices[i]
                 for i in range(len(log_prices) - lag)]
        spread = statistics.pstdev(diffs)
        if spread <= 0:
            continue
        xs.append(math.log(lag))
        ys.append(math.log(spread))
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denominator = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denominator if denominator else None


def variance_ratio(returns, q):
    """Lo-MacKinlay VR(q). >1 persistent, <1 mean-reverting."""
    n = len(returns)
    if n < q * 4:
        return None
    var1 = statistics.pvariance(returns)
    if var1 <= 0:
        return None
    aggregated = [sum(returns[i:i + q]) for i in range(0, n - q + 1)]
    if len(aggregated) < 4:
        return None
    return statistics.pvariance(aggregated) / (q * var1)


def efficiency_ratio(prices):
    """Kaufman: |net move| / summed absolute moves. 1 = straight line, 0 = churn."""
    if len(prices) < 3:
        return None
    path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
    return abs(prices[-1] - prices[0]) / path if path > 0 else None


# --------------------------------------------------------------------------- #
# features
# --------------------------------------------------------------------------- #


def session_closes_30m():
    """Last close of each in-session 30-minute bucket, with its epoch day."""
    bars, _ = btc.context("validate")
    return [(bar[btc.TS] // 86_400, bar[btc.C]) for bar in bars]


def daily_features():
    rows = data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as long) day,last(close) close "
        "FROM btc_1m ORDER BY day"
    )
    days = [int(r[0]) // 1_000_000 // 86_400 for r in rows]
    prices = [float(r[1]) for r in rows]
    logs = [math.log(p) for p in prices if p > 0]
    returns = [0.0] + [b - a for a, b in zip(logs, logs[1:])]

    intraday = session_closes_30m()

    out = {}
    # Intraday persistence per day, from the previous 20 sessions of 30m closes.
    by_day = {}
    for day, close in intraday:
        by_day.setdefault(day, []).append(close)
    session_days = sorted(by_day)
    intraday_hurst, intraday_er = {}, {}
    for index, day in enumerate(session_days):
        if index < 20:
            continue
        window = [c for prior in session_days[index - 20:index] for c in by_day[prior]]
        logs_w = [math.log(c) for c in window if c > 0]
        intraday_hurst[day] = hurst(logs_w)
        intraday_er[day] = efficiency_ratio(window)

    for index, day in enumerate(days):
        feature = {}
        if index >= 120:
            feature["hurst_120"] = hurst(logs[index - 120:index])
            feature["vr_5"] = variance_ratio(returns[index - 120:index], 5)
        if index >= 60:
            feature["hurst_60"] = hurst(logs[index - 60:index])
            feature["er_60"] = efficiency_ratio(prices[index - 60:index])
        if index >= 20:
            feature["er_20"] = efficiency_ratio(prices[index - 20:index])
        feature["hurst_intraday"] = intraday_hurst.get(day)
        feature["er_intraday"] = intraday_er.get(day)
        out[day] = feature
    return out


def multiplier(rule, params, f):
    """Exposure for a day. A missing reading always fails open."""
    if rule == "always":
        return 1.0
    value = f.get(params.get("field"))
    if value is None:
        return 1.0
    if rule == "above":
        return 1.0 if value > params["level"] else 0.0
    if rule == "below":
        return 1.0 if value < params["level"] else 0.0
    raise ValueError(rule)


RULES = [("always", [{}])]
for field, levels in (
    ("hurst_120", (0.45, 0.50, 0.55)),
    ("hurst_60", (0.45, 0.50, 0.55)),
    ("hurst_intraday", (0.45, 0.50, 0.55)),
    ("vr_5", (0.9, 1.0, 1.1)),
    ("er_60", (0.15, 0.25, 0.35)),
    ("er_20", (0.20, 0.30, 0.40)),
    ("er_intraday", (0.10, 0.15, 0.20)),
):
    RULES.append(("above", [{"field": field, "level": v} for v in levels]))


def tag_of(rule, params):
    return "always" if rule == "always" else f"{params['field']} > {params['level']}"


def evaluate(trades, features, lo, hi):
    rows = []
    for rule, grid in RULES:
        for params in grid:
            saved = base.multiplier
            base.multiplier = lambda r, p, f: multiplier(r, p, f)
            try:
                stat = base.replay(trades, features, rule, params, lo, hi)
            finally:
                base.multiplier = saved
            rows.append((tag_of(rule, params), rule, params, stat))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()

    bars, ctx = btc.context("validate")
    params = dict(json.load(open(btc.OUTPUT, encoding="utf-8"))["families"]["trend"]["params"])
    trades = wf.resolve("trend", bars, ctx, params)
    features = daily_features()

    lo, hi = ((base.IS_LO, base.IS_HI) if args.phase == "select"
              else (base.IS_HI, base.OOS_HI))
    label = ("IN SAMPLE 2020-2024" if args.phase == "select"
             else "OUT OF SAMPLE 2025-2026")
    rows = evaluate(trades, features, lo, hi)
    control = next(s for t, _, _, s in rows if t == "always")

    print(f"=== {label}: BTC Donchian + persistence filters ===")
    print(f"{'rule':<28}{'return':>10}{'dd':>8}{'ret/dd':>9}{'pf':>7}"
          f"{'trades':>8}{'+mo':>8}  eligible")
    print("-" * 92)
    for tag, rule, options, stat in rows:
        keeps = (
            stat["return_pct"] >= base.MIN_RETURN_SHARE * control["return_pct"]
            and stat["trades"] >= base.MIN_TRADE_SHARE * control["trades"]
        )
        flag = "" if rule == "always" else ("  ok" if keeps else "  cut")
        print(f"{tag:<28}{stat['return_pct']:>9.1f}%{stat['max_dd_pct']:>7.2f}%"
              f"{stat['ratio']:>9.2f}{stat['pf']:>7.3f}{stat['trades']:>8}"
              f"{stat['positive_months']:>5}/{stat['months']:<2}{flag}")

    if args.phase == "select":
        eligible = [
            r for r in rows
            if r[1] != "always"
            and r[3]["return_pct"] >= base.MIN_RETURN_SHARE * control["return_pct"]
            and r[3]["trades"] >= base.MIN_TRADE_SHARE * control["trades"]
        ]
        if not eligible:
            print("\n  nothing kept 80% of return and 60% of trades -- no rule sealed")
            return
        best = max(eligible, key=lambda r: r[3]["ratio"])
        print(f"\n  eligible: {len(eligible)}/{len(rows) - 1}")
        print(f"  SELECTED: {best[0]}")
        json.dump({"tag": best[0], "rule": best[1], "params": best[2],
                   "in_sample": best[3], "control": control},
                  open(OUTPUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    else:
        sealed = json.load(open(OUTPUT, encoding="utf-8"))
        chosen = next(s for t, _, _, s in rows if t == sealed["tag"])
        print(f"\n  pre-registered pick: {sealed['tag']}")
        print(f"    control  return {control['return_pct']:>7.1f}%  "
              f"dd {control['max_dd_pct']:>6.2f}%  ratio {control['ratio']:>6.2f}")
        print(f"    filtered return {chosen['return_pct']:>7.1f}%  "
              f"dd {chosen['max_dd_pct']:>6.2f}%  ratio {chosen['ratio']:>6.2f}")
        print(f"    -> {'BEATS' if chosen['ratio'] > control['ratio'] else 'LOSES TO'}"
              " the do-nothing control")

        # The question that matters more than any single rule.
        is_rows = evaluate(trades, features, base.IS_LO, base.IS_HI)
        pairs = [(i[3]["ratio"], o[3]["ratio"])
                 for i, o in zip(is_rows, rows) if i[1] != "always"]
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
        print(f"\n  in-sample vs out-of-sample ret/dd correlation across "
              f"{len(pairs)} rules: {cov / den if den else 0.0:+.3f}")


if __name__ == "__main__":
    main()
