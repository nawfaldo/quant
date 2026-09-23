"""Daily exposure overlays for the BTC strategies: can a regime filter help?

Sits on top of `btc_families_research`. The strategies are untouched; each trade
is scaled by an exposure multiplier decided *before* its entry day opens, so an
overlay can switch a strategy off (0.0), halve it, or leave it alone.

WHY THIS IS BTC-ONLY. An overlay has to be fitted somewhere and tested somewhere
else. BTC has 84 in-sample months (2018-2024) before its 2025-2026 holdout. The
NQ level-two strategies have 17 months in total and *all* of it is already their
out-of-sample window, so any regime rule fitted there and tested there is
circular. There is no honest overlay study for them on this data.

WHAT IS TESTED. Six overlays plus the control, all causal:

  * ``always``       multiplier 1.0 — the do-nothing control every other rule
                     must beat before it means anything
  * ``ewma_scale``   RiskMetrics EWMA (lambda 0.94) volatility forecast, scaled
                     inverse-vol toward a target: min(1, target / forecast)
  * ``ewma_off``     off when the EWMA forecast sits above its trailing
                     252-session percentile
  * ``vix_off``      off when the previous VIX close is at or above a threshold
  * ``trend_off``    off when BTC closed below its N-session average
  * ``markov_off``   two-state Gaussian HMM on daily returns; off in the
                     high-variance state
  * ``markov_scale`` same model, half exposure in the high-variance state

THE PRIOR IS POOR AND THE PROTOCOL SAYS SO. Seven rules times their thresholds
is a search, and `OPTIMIZATION_PLAN.md` Stage 5 is blunt about what that finds.
Every overlay is therefore reported against the control on the *same* trades,
the winner is taken on in-sample only, and 2025-2026 is read exactly once. An
overlay that cannot beat `always` out of sample is noise however good its
in-sample number looks.

CAUSALITY. Every feature for day D is built from sessions that closed strictly
before D: the EWMA is lagged a day, the VIX is the previous close, the SMA
excludes D, and the HMM is fitted on train returns only and then *filtered*
forward, which uses no future observation by construction.
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


OUTPUT = os.path.join(os.path.dirname(__file__), "btc_regime_selection.json")

MEMBERS = ("trend", "vwap")          # the two wired into the live runtime
IS_START, IS_END, OOS_END = btc.IS_START, btc.IS_END, btc.OOS_END
INITIAL = btc.INITIAL

#: RiskMetrics decay. Not fitted; it is the standard value and stays fixed so the
#: search is over the *rule*, not over a smoothing constant.
EWMA_LAMBDA = 0.94
TRADING_DAYS = 365.0


# --------------------------------------------------------------------------- #
# daily features
# --------------------------------------------------------------------------- #


def daily_closes():
    """`[(epoch day, close)]` per calendar day, oldest first."""
    rows = data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as long) day,last(close) close "
        "FROM btc_1m ORDER BY day"
    )
    return [(int(r[0]) // 1_000_000 // 86_400, float(r[1])) for r in rows]


def daily_vix():
    rows = data.query(
        "SELECT cast(timestamp_floor('d',timestamp) as long) day,last(close) close "
        "FROM vix_1d ORDER BY day"
    )
    return {int(r[0]) // 1_000_000 // 86_400: float(r[1]) for r in rows}


def ewma_volatility(returns):
    """RiskMetrics EWMA variance, annualized, lagged one day.

    `out[i]` is the forecast *for* day i built only from returns up to i-1, so a
    day is never sized using its own move.
    """
    out = [None] * len(returns)
    variance = None
    for index, value in enumerate(returns):
        out[index] = (
            None if variance is None else math.sqrt(variance * TRADING_DAYS)
        )
        variance = (
            value * value
            if variance is None
            else EWMA_LAMBDA * variance + (1.0 - EWMA_LAMBDA) * value * value
        )
    return out


def trailing_percentile(series, window=252):
    """Rank of each value within the `window` values before it, in [0, 1]."""
    out = [None] * len(series)
    for index in range(len(series)):
        if index < window:
            continue
        past = [v for v in series[index - window:index] if v is not None]
        value = series[index]
        if value is None or not past:
            continue
        out[index] = sum(1 for v in past if v <= value) / len(past)
    return out


def trailing_mean(values, window):
    out = [None] * len(values)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        if index >= window:
            out[index] = total / window
    return out


# --------------------------------------------------------------------------- #
# two-state Gaussian HMM
# --------------------------------------------------------------------------- #


def fit_hmm(returns, iterations=60):
    """Baum-Welch for a two-state Gaussian HMM. Returns (means, vars, A, pi).

    Deliberately small: two states, diagonal transitions initialised sticky, and
    a variance floor so a state cannot collapse onto a single observation. It is
    fitted on the train window alone and then only ever *filtered* forward.
    """
    n = len(returns)
    spread = statistics.pstdev(returns) or 1e-8
    mean = statistics.fmean(returns)
    means = [mean + spread, mean - spread]
    variances = [spread * spread, spread * spread * 4.0]
    transitions = [[0.95, 0.05], [0.05, 0.95]]
    initial = [0.5, 0.5]
    floor = 1e-12

    def density(x, state):
        var = max(variances[state], floor)
        return math.exp(-((x - means[state]) ** 2) / (2 * var)) / math.sqrt(
            2 * math.pi * var
        )

    for _ in range(iterations):
        # forward
        alpha = [[0.0, 0.0] for _ in range(n)]
        scale = [0.0] * n
        for state in range(2):
            alpha[0][state] = initial[state] * density(returns[0], state)
        scale[0] = sum(alpha[0]) or floor
        alpha[0] = [a / scale[0] for a in alpha[0]]
        for t in range(1, n):
            for state in range(2):
                alpha[t][state] = density(returns[t], state) * sum(
                    alpha[t - 1][prev] * transitions[prev][state] for prev in range(2)
                )
            scale[t] = sum(alpha[t]) or floor
            alpha[t] = [a / scale[t] for a in alpha[t]]
        # backward
        beta = [[1.0, 1.0] for _ in range(n)]
        for t in range(n - 2, -1, -1):
            for state in range(2):
                beta[t][state] = (
                    sum(
                        transitions[state][nxt]
                        * density(returns[t + 1], nxt)
                        * beta[t + 1][nxt]
                        for nxt in range(2)
                    )
                    / (scale[t + 1] or floor)
                )
        gamma = []
        for t in range(n):
            row = [alpha[t][s] * beta[t][s] for s in range(2)]
            total = sum(row) or floor
            gamma.append([v / total for v in row])
        # re-estimate
        for state in range(2):
            weight = sum(g[state] for g in gamma) or floor
            means[state] = sum(g[state] * r for g, r in zip(gamma, returns)) / weight
            variances[state] = max(
                floor,
                sum(
                    g[state] * (r - means[state]) ** 2 for g, r in zip(gamma, returns)
                )
                / weight,
            )
        for a in range(2):
            xi_total = [0.0, 0.0]
            for t in range(n - 1):
                for b in range(2):
                    xi_total[b] += (
                        alpha[t][a]
                        * transitions[a][b]
                        * density(returns[t + 1], b)
                        * beta[t + 1][b]
                        / (scale[t + 1] or floor)
                    )
            total = sum(xi_total) or floor
            transitions[a] = [v / total for v in xi_total]
        initial = gamma[0]

    # State 1 is defined as the high-variance one, so downstream rules read the
    # same thing whichever way EM happened to label them.
    if variances[0] > variances[1]:
        means.reverse()
        variances.reverse()
        transitions = [list(reversed(row)) for row in reversed(transitions)]
        initial = list(reversed(initial))
    return means, variances, transitions, initial


def filter_states(returns, model):
    """Causal P(high-variance state) for each day, lagged one day.

    `out[i]` uses returns up to i-1 only, so a day's exposure never depends on
    that day's own move.
    """
    means, variances, transitions, initial = model
    floor = 1e-12

    def density(x, state):
        var = max(variances[state], floor)
        return math.exp(-((x - means[state]) ** 2) / (2 * var)) / math.sqrt(
            2 * math.pi * var
        )

    out = [None] * len(returns)
    belief = list(initial)
    for index, value in enumerate(returns):
        predicted = [
            sum(belief[prev] * transitions[prev][state] for prev in range(2))
            for state in range(2)
        ]
        out[index] = predicted[1]
        updated = [predicted[state] * density(value, state) for state in range(2)]
        total = sum(updated) or floor
        belief = [v / total for v in updated]
    return out


# --------------------------------------------------------------------------- #
# overlays
# --------------------------------------------------------------------------- #


def build_features(train_hi):
    """`{day: {feature: value}}`, every feature causal for that day."""
    closes = daily_closes()
    days = [d for d, _ in closes]
    prices = [c for _, c in closes]
    returns = [0.0]
    for previous, current in zip(prices, prices[1:]):
        returns.append(math.log(current / previous) if previous > 0 else 0.0)

    ewma = ewma_volatility(returns)
    ewma_pct = trailing_percentile(ewma)
    sma50 = trailing_mean(prices, 50)
    vix = daily_vix()

    # The HMM sees train returns only; filtering afterwards is causal by
    # construction, so the OOS days are scored by a model that never saw them.
    train = [r for d, r in zip(days, returns) if d * 86_400 < train_hi]
    model = fit_hmm(train[1:]) if len(train) > 60 else None
    high_state = filter_states(returns, model) if model else [None] * len(returns)

    out = {}
    for index, day in enumerate(days):
        previous_vix = None
        for back in range(1, 6):
            if day - back in vix:
                previous_vix = vix[day - back]
                break
        out[day] = {
            "ewma": ewma[index],
            "ewma_pct": ewma_pct[index],
            "vix": previous_vix,
            "below_sma": (
                None if sma50[index] is None else prices[index - 1] < sma50[index]
            ),
            "high_state": high_state[index],
        }
    return out


def multiplier(rule, params, feature):
    """Exposure for one day under one overlay; None means "no reading, stay on"."""
    if rule == "always":
        return 1.0
    if rule == "ewma_scale":
        forecast = feature.get("ewma")
        if forecast is None or forecast <= 0:
            return 1.0
        return min(1.0, params["target"] / forecast)
    if rule == "ewma_off":
        pct = feature.get("ewma_pct")
        return 1.0 if pct is None else (0.0 if pct >= params["pct"] else 1.0)
    if rule == "vix_off":
        value = feature.get("vix")
        return 1.0 if value is None else (0.0 if value >= params["level"] else 1.0)
    if rule == "trend_off":
        below = feature.get("below_sma")
        return 1.0 if below is None else (0.0 if below else 1.0)
    if rule == "markov_off":
        p = feature.get("high_state")
        return 1.0 if p is None else (0.0 if p >= params["p"] else 1.0)
    if rule == "markov_scale":
        p = feature.get("high_state")
        return 1.0 if p is None else (0.5 if p >= params["p"] else 1.0)
    raise ValueError(rule)


RULES = [
    ("always", [{}]),
    ("ewma_scale", [{"target": t} for t in (0.4, 0.6, 0.8)]),
    ("ewma_off", [{"pct": p} for p in (0.7, 0.8, 0.9)]),
    ("vix_off", [{"level": v} for v in (20.0, 25.0, 30.0)]),
    ("trend_off", [{}]),
    ("markov_off", [{"p": p} for p in (0.5, 0.7)]),
    ("markov_scale", [{"p": p} for p in (0.5, 0.7)]),
]


def replay(trades, features, rule, params, lo, hi):
    """Size a per-unit trade list under one overlay, from a fresh $1,000."""
    equity = peak = INITIAL
    max_dd = 0.0
    pnls = []
    months = {}
    for entry_ts, points, stop, price in sorted(trades):
        if entry_ts < lo or entry_ts >= hi:
            continue
        day = entry_ts // 86_400
        scale = multiplier(rule, params, features.get(day, {}))
        if scale <= 0.0:
            continue
        risk = btc.RISK_FRACTION * scale
        quantity = es.quantity(equity, price, stop, risk)
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
    return {
        "return_pct": round(100.0 * (equity - INITIAL) / INITIAL, 2),
        "max_dd_pct": round(100.0 * max_dd, 2),
        "pf": round(wins / losses, 3) if losses else (999.0 if wins else 0.0),
        "trades": len(pnls),
        "positive_months": sum(1 for v in values if v > 0),
        "months": len(values),
        "monthly_sharpe": round(
            statistics.fmean(values) / statistics.pstdev(values), 3
        )
        if len(values) > 1 and statistics.pstdev(values)
        else 0.0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("select", "validate"))
    args = parser.parse_args()

    bars, ctx = btc.context("validate")
    features = build_features(IS_END)
    sealed = json.load(open(btc.OUTPUT, encoding="utf-8"))

    trades = {}
    for family in MEMBERS:
        params = dict(sealed["families"][family]["params"])
        trades[family] = wf.resolve(family, bars, ctx, params)
    pooled = [t for family in MEMBERS for t in trades[family]]

    lo, hi = (IS_START, IS_END) if args.phase == "select" else (IS_END, OOS_END)
    label = "IN SAMPLE 2018-2024" if args.phase == "select" else "OUT OF SAMPLE 2025-2026"
    control = replay(pooled, features, "always", {}, lo, hi)

    print(f"=== {label}: BTC Donchian + BTC VWAP, one $1,000 account ===")
    print(f"{'overlay':<24}{'return':>9}{'dd':>8}{'pf':>7}{'mSharpe':>9}"
          f"{'+mo':>8}{'trades':>8}   vs control")
    print("-" * 88)
    rows = []
    for rule, grid in RULES:
        for params in grid:
            stat = replay(pooled, features, rule, params, lo, hi)
            tag = rule + ("" if not params else " " + ",".join(
                f"{k}={v}" for k, v in params.items()))
            delta = stat["return_pct"] - control["return_pct"]
            rows.append((tag, stat, delta))
            print(f"{tag:<24}{stat['return_pct']:>8.1f}%{stat['max_dd_pct']:>7.1f}%"
                  f"{stat['pf']:>7.3f}{stat['monthly_sharpe']:>9.2f}"
                  f"{stat['positive_months']:>5}/{stat['months']:<2}"
                  f"{stat['trades']:>8}   {delta:>+8.1f}pp")
    beat = [r for r in rows if r[0] != "always" and r[2] > 0]
    print(f"\n  overlays beating the do-nothing control: {len(beat)}/{len(rows) - 1}")
    if args.phase == "select":
        best = max((r for r in rows if r[0] != "always"),
                   key=lambda r: r[1]["monthly_sharpe"])
        print(f"  best in-sample by monthly Sharpe: {best[0]}")
        json.dump({"best": best[0], "in_sample": best[1], "control": control},
                  open(OUTPUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
        print(f"  sealed to {OUTPUT}")


if __name__ == "__main__":
    main()
