"""Per-strategy daily exposure overlays for the five-member portfolio.

Donchian, VWAP, Hourly Delta Reversal and Deep OFI
Momentum share one $1,000 account. Each gets its *own* exposure multiplier,
chosen fresh every month, so a strategy can be switched off, halved, or left
alone independently of the others.

WHY WALK-FORWARD RATHER THAN A HOLDOUT. `btc_regime_overlay` could fit an
overlay on BTC's 2018-2024 and test it on 2025-2026. The NQ level-two strategies
have no such room: 17 months total, and all of it is already their out-of-sample
window, so an overlay fitted there and tested there is circular. The fix is the
protocol `OPTIMIZATION_PLAN.md` Stage 4 already specifies for this exact
problem -- an anchored walk-forward:

  * folds are one calendar month, stepping from 2025-08 (the plan's own start);
  * each fold's train window is anchored at the first bar and ends one session
    before the test month, so selection can only ever see the past;
  * the overlay is chosen inside the train window alone, per strategy, and that
    choice is scored once on the test month and never revisited;
  * every headline number comes from the stitched test months.

That makes the test honest without needing a holdout the NQ side does not have,
and it answers the question that actually matters -- *would I have picked a
useful overlay in real time?* -- rather than whether one exists with hindsight.

THE CONTROL IS THE POINT. Every fold also scores the do-nothing overlay. An
adaptive scheme that cannot beat leaving all five strategies switched on is a
more complicated way to earn less, and the plan says to report that either way.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data, execution, metrics, strategies
from sandbox.research import btc_families_research as btc
from sandbox.research import btc_regime_overlay as ov
from sandbox.research import btc_walkforward as wf
from sandbox.research import es_strategy_research as es


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_regime_result.json")
INITIAL = 1_000.0
STEP, MARGIN = 0.01, 0.25

BTC_MEMBERS = {"BTC Donchian": "trend", "BTC VWAP": "vwap"}
NQ_MEMBERS = ("Hourly Delta Reversal", "Deep OFI Momentum")
MEMBERS = list(BTC_MEMBERS) + list(NQ_MEMBERS)
MARKET = {name: ("btc" if name in BTC_MEMBERS else "nq") for name in MEMBERS}

#: The plan's fold layout: one-month test steps from 2025-08.
FOLD_FROM = "2025-08"
FOLD_TO = "2026-08"
EMBARGO = 86_400


def month_bounds(first, last):
    out = []
    year, month = (int(p) for p in first.split("-"))
    last_year, last_month = (int(p) for p in last.split("-"))
    while (year, month) < (last_year, last_month):
        nxt = (year + 1, 1) if month == 12 else (year, month + 1)
        out.append((
            int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp()),
            int(datetime(nxt[0], nxt[1], 1, tzinfo=timezone.utc).timestamp()),
        ))
        year, month = nxt
    return out


FOLDS = month_bounds(FOLD_FROM, FOLD_TO)


# --------------------------------------------------------------------------- #
# features, per market
# --------------------------------------------------------------------------- #


def daily_closes(table):
    rows = data.query(
        f"SELECT cast(timestamp_floor('d',timestamp) as long) day,last(close) close "
        f"FROM {table} ORDER BY day"
    )
    return [(int(r[0]) // 1_000_000 // 86_400, float(r[1])) for r in rows]


def market_features(table, train_hi):
    """`{day: {feature: value}}` for one market; every value causal for that day."""
    closes = daily_closes(table)
    days = [d for d, _ in closes]
    prices = [c for _, c in closes]
    returns = [0.0]
    for previous, current in zip(prices, prices[1:]):
        returns.append(math.log(current / previous) if previous > 0 else 0.0)

    ewma = ov.ewma_volatility(returns)
    ewma_pct = ov.trailing_percentile(ewma)
    sma = ov.trailing_mean(prices, 50)
    vix = ov.daily_vix()

    train = [r for d, r in zip(days, returns) if d * 86_400 < train_hi]
    model = ov.fit_hmm(train[1:]) if len(train) > 60 else None
    high = ov.filter_states(returns, model) if model else [None] * len(returns)

    out = {}
    for index, day in enumerate(days):
        previous_vix = next(
            (vix[day - back] for back in range(1, 6) if day - back in vix), None
        )
        out[day] = {
            "ewma": ewma[index],
            "ewma_pct": ewma_pct[index],
            "vix": previous_vix,
            "below_sma": None if sma[index] is None else prices[index - 1] < sma[index],
            "high_state": high[index],
        }
    return out


# --------------------------------------------------------------------------- #
# trades
# --------------------------------------------------------------------------- #


def load_trades():
    """`{member: [(entry_ts, points, stop, price)]}` plus each member's risk."""
    trades, risk = {}, {}
    bars, ctx = btc.context("validate")
    sealed = json.load(open(btc.OUTPUT, encoding="utf-8"))
    for name, family in BTC_MEMBERS.items():
        params = dict(sealed["families"][family]["params"])
        trades[name] = wf.resolve(family, bars, ctx, params)
        risk[name] = btc.RISK_FRACTION

    nq_bars = data.load_cached_level_two_bars("nq")
    for name in NQ_MEMBERS:
        strategy = strategies.get(name)
        ex = strategy.execution
        context = strategy.context()
        merged = strategy.all_params(strategy.defaults)
        signals = []
        for group in strategy.groups():
            signals.extend(strategy.signals(nq_bars, context, group, merged))
        trades[name] = [
            (f.entry_ts, f.points, f.stop, f.price)
            for f in execution.resolve(nq_bars, signals, ex)
        ]
        risk[name] = ex.risk
    return trades, risk


def replay(selection, trades, risk, features, lo, hi, initial=INITIAL):
    """Size every member's trades under its own overlay, on one shared balance."""
    events = []
    for name in MEMBERS:
        for entry_ts, points, stop, price in trades[name]:
            if lo <= entry_ts < hi:
                events.append((entry_ts, points, stop, price, name))
    events.sort()

    equity = peak = initial
    max_dd = 0.0
    pnls, months, by_member = [], {}, {name: 0.0 for name in MEMBERS}
    for entry_ts, points, stop, price, name in events:
        rule, params = selection[name]
        day = entry_ts // 86_400
        feature = features[MARKET[name]].get(day, {})
        scale = ov.multiplier(rule, params, feature)
        if scale <= 0.0:
            continue
        quantity = es.quantity(equity, price, stop, risk[name] * scale)
        if quantity < STEP:
            continue
        pnl = points * quantity
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 1.0)
        pnls.append(pnl)
        by_member[name] += pnl
        key = datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + pnl
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    values = list(months.values())
    return {
        "pnl": equity - initial,
        "equity": equity,
        "trades": len(pnls),
        "pf": (wins / losses) if losses else (999.0 if wins else 0.0),
        "max_dd_pct": 100.0 * max_dd,
        "months": months,
        "by_member": by_member,
        "monthly_sharpe": (statistics.fmean(values) / statistics.pstdev(values))
        if len(values) > 1 and statistics.pstdev(values)
        else 0.0,
    }


def score_member(name, trades, risk, features, rule, params, lo, hi):
    """Train-window quality for one member under one overlay, sized alone."""
    stat = replay({name: (rule, params)} | {n: ("always", {}) for n in MEMBERS if n != name},
                  {n: (trades[n] if n == name else []) for n in MEMBERS},
                  risk, features, lo, hi)
    return stat


def main():
    print("loading per-unit trades for five strategies...", flush=True)
    trades, risk = load_trades()
    for name in MEMBERS:
        print(f"  {name:<26} {len(trades[name]):>5} trades")

    # Features are built once with the HMM fitted on the earliest train window,
    # so no fold's model has seen its own test month.
    features = {
        "btc": market_features("btc_1m", FOLDS[0][0]),
        "nq": market_features("nq_1m", FOLDS[0][0]),
    }
    span_lo = min(t[0] for name in MEMBERS for t in trades[name])

    grid = [(rule, params) for rule, options in ov.RULES for params in options]
    print(f"\n{len(FOLDS)} monthly folds, {len(grid)} overlays per strategy, "
          f"chosen on train only\n")

    stitched_adaptive, stitched_control = {}, {}
    picks = {name: [] for name in MEMBERS}
    print(f"{'test month':>11} | {'adaptive':>10} {'control':>10} | picks")
    print("-" * 96)
    for lo, hi in FOLDS:
        train_hi = lo - EMBARGO
        selection = {}
        for name in MEMBERS:
            best, best_score = ("always", {}), -math.inf
            for rule, params in grid:
                stat = score_member(name, trades, risk, features, rule, params,
                                    span_lo, train_hi)
                if stat["trades"] < 20:
                    continue
                score = stat["monthly_sharpe"]
                if score > best_score:
                    best, best_score = (rule, params), score
            selection[name] = best
            picks[name].append(best[0])

        adaptive = replay(selection, trades, risk, features, lo, hi)
        control = replay({n: ("always", {}) for n in MEMBERS}, trades, risk,
                         features, lo, hi)
        month = datetime.fromtimestamp(lo, tz=timezone.utc).strftime("%Y-%m")
        stitched_adaptive[month] = adaptive["pnl"]
        stitched_control[month] = control["pnl"]
        tags = " ".join(f"{n.split()[-1][:4]}:{selection[n][0][:9]}" for n in MEMBERS)
        print(f"{month:>11} | {adaptive['pnl']:>10.2f} {control['pnl']:>10.2f} | {tags}")

    def summarise(series):
        values = list(series.values())
        equity, peak, dd = INITIAL, INITIAL, 0.0
        for v in values:
            equity += v
            peak = max(peak, equity)
            dd = max(dd, (peak - equity) / peak if peak > 0 else 1.0)
        return {
            "pnl": round(sum(values), 2),
            "positive": sum(1 for v in values if v > 0),
            "months": len(values),
            "monthly_dd_pct": round(100.0 * dd, 2),
            "monthly_sharpe": round(
                statistics.fmean(values) / statistics.pstdev(values), 3
            )
            if len(values) > 1 and statistics.pstdev(values)
            else 0.0,
        }

    a, c = summarise(stitched_adaptive), summarise(stitched_control)
    print("\n" + "=" * 96)
    print("STITCHED WALK-FORWARD (the only numbers that count)")
    print("=" * 96)
    print(f"  {'':<12}{'pnl':>10}{'+months':>10}{'month DD':>11}{'mSharpe':>10}")
    print(f"  {'adaptive':<12}{a['pnl']:>10.2f}{a['positive']:>6}/{a['months']:<3}"
          f"{a['monthly_dd_pct']:>10.2f}%{a['monthly_sharpe']:>10.2f}")
    print(f"  {'control':<12}{c['pnl']:>10.2f}{c['positive']:>6}/{c['months']:<3}"
          f"{c['monthly_dd_pct']:>10.2f}%{c['monthly_sharpe']:>10.2f}")
    print(f"\n  adaptive - control: {a['pnl'] - c['pnl']:+.2f}  "
          f"({'BEATS' if a['pnl'] > c['pnl'] else 'LOSES TO'} the do-nothing control)")
    print("\n  overlay stability per strategy (how often each was picked):")
    for name in MEMBERS:
        counts = {}
        for pick in picks[name]:
            counts[pick] = counts.get(pick, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        print(f"    {name:<26} " + "  ".join(f"{k} {v}/{len(FOLDS)}" for k, v in ranked))

    json.dump({"adaptive": stitched_adaptive, "control": stitched_control,
               "summary": {"adaptive": a, "control": c}},
              open(OUTPUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"\n  wrote {OUTPUT}")


if __name__ == "__main__":
    main()
