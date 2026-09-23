"""Diligence on the winners of `drift_maroy_symbol_study`.

The sweep's verdict column answers one question -- did this symbol's in-sample
winner beat its own coin-flip control out of sample -- and five symbol/strategy
pairs answer it at all three cost settings.  That is necessary and nowhere near
sufficient.  This module runs the four checks that decide whether any of them is
worth anything:

  1. BUY AND HOLD.  A strategy that makes less than the instrument it trades is
     not a strategy.  Three of the five survivors lose to holding; ETHUSD is the
     only one that makes money while its underlying falls
     ([[buy-and-hold-is-the-second-mandatory-control]]).
  2. CONCENTRATION.  Share of OOS profit from the single best trade and the best
     month, plus the long/short split.  A result carried by one trade or one
     direction is a bet on a regime, not an edge
     ([[short-month-series-fakes-monthly-sharpe]]).
  3. LOT GRANULARITY.  Re-run at 10x the balance.  On a $1,000 account the 0.01
     lot floor can round trades away entirely, and the unfillable ones
     masquerade as risk control ([[lot-granularity-fakes-low-drawdown]]).
  4. A SECOND UNTOUCHED WINDOW.  2018-2019 is earlier than the in-sample fit and
     was never read by the search.  A candidate that dies there died the same way
     the gold momentum candidate did ([[xauusd-momentum-is-a-live-candidate]]).

Run from the repository root:

    py -B -m sandbox.research.drift_maroy_validate
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timezone

from sandbox import data, metrics
from sandbox.research import drift_maroy_symbol_study as study
from sandbox.research import maroy_intraday_momentum as maroy


OUTPUT = os.path.join(os.path.dirname(__file__), "..", "results",
                      "drift_maroy_validate_result.json")

#: The pairs that beat their own null out of sample at 0.2 points, 0.5 bp and
#: 2 bp.  Everything else in the sweep failed at at least one cost.
SURVIVORS = [("ethusd", "maroy"), ("ethusd", "drift"),
             ("de40", "maroy"), ("xptusd", "maroy"), ("xagusd", "maroy")]

#: Earlier than the in-sample fit and never read by the search.
PRIOR_FROM, PRIOR_TO = "2018-01-01", "2020-01-01"


def buy_and_hold(symbol, from_date, to_date):
    table = study.SYMBOLS[symbol]["table"]
    rows = data.query(
        f"SELECT first(close),last(close) FROM (SELECT close FROM {table} "
        f"WHERE timestamp >= '{from_date}' AND timestamp < '{to_date}')")
    first, last = rows[0]
    return round(100 * (last / first - 1.0), 2) if first else None


def concentration(trades):
    """How much of the profit came from one trade, one month, one direction."""
    if not trades:
        return {}
    total = sum(t["pnl"] for t in trades)
    gross = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    months = metrics.monthly([(t["entry_ts"], t["pnl"]) for t in trades])
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    return {
        "net_pnl": round(total, 2),
        "best_trade_share_of_gross": round(max(t["pnl"] for t in trades) / gross, 3)
            if gross > 0 else None,
        "best_month_share_of_gross": round(max(months.values()) / gross, 3)
            if gross > 0 and months else None,
        "pos_months": sum(1 for v in months.values() if v > 0),
        "n_months": len(months),
        "long_trades": len(longs), "short_trades": len(shorts),
        "long_pnl": round(sum(t["pnl"] for t in longs), 2),
        "short_pnl": round(sum(t["pnl"] for t in shorts), 2),
    }


def _run(symbol, strategy, params, window, days, balance, cost_bp, seed=None):
    """One evaluation at an arbitrary balance, returning summary and trades."""
    original = study.INITIAL_BALANCE
    study.INITIAL_BALANCE = balance
    maroy.INITIAL = balance
    try:
        per_year = study.SYMBOLS[symbol]["sessions_per_year"]
        if strategy == "maroy":
            maroy.COST_BPS = cost_bp
            result = maroy.run_config(study._maroy_sessions(symbol), params, symbol,
                                      study.maroy_policy(seed), window)
            trades = [{**t, "entry_ts": t["ts"],
                       "return_bp": study._maroy_return_bp(t)}
                      for t in result["trades"]]
        else:
            minutes = _minutes(symbol)
            session = study.SYMBOLS[symbol]["session"]
            bars_5m = study.aggregate(minutes, 5, session)
            states = study.drift_states(study.aggregate(minutes, 15), session,
                                        params["momentum"])
            trades = study.drift_backtest(bars_5m, states, session, params, window,
                                          cost_bp, seed)["trades"]
        return study.summarise(trades, window, per_year, days), trades
    finally:
        study.INITIAL_BALANCE = original
        maroy.INITIAL = original


_MINUTES = {}


def _minutes(symbol, warm_from="2017-01-01"):
    if symbol not in _MINUTES:
        _MINUTES[symbol] = study.load_minutes(symbol, warm_from)
    return _MINUTES[symbol]


def _days(symbol, strategy, window):
    if strategy == "maroy":
        return [s["day"] for s in study._maroy_sessions(symbol)
                if window[0] <= s["ts"] < window[1]]
    session = study.SYMBOLS[symbol]["session"]
    bars = study.aggregate(_minutes(symbol), 5, session)
    return sorted({b[study.TS] // 86_400 for b in bars
                   if window[0] <= b[study.TS] < window[1]})


def _prepare(symbol, strategy, cost_bp):
    """Register the symbol's session with the base module and warm its caches."""
    study.SYMBOLS[symbol]  # KeyError early if the pair is mistyped
    maroy.SESSIONS[symbol] = study.SYMBOLS[symbol]["session"]
    maroy.SESSIONS_PER_YEAR = study.SYMBOLS[symbol]["sessions_per_year"]
    maroy.SPREAD = study.ENTRY_SPREAD
    maroy.COST_BPS = cost_bp
    # `_maroy_sessions` and `_minutes` both need the earlier warm-up start, so
    # the prior-window check is not handed a cold noise profile.
    study._SESSION_CACHE.pop(symbol, None)
    _minutes(symbol)


def validate(symbol, strategy, winners, cost_bp):
    params = winners[(symbol, strategy)]
    _prepare(symbol, strategy, cost_bp)
    if strategy == "maroy":
        study._maroy_sessions(symbol)

    shift = study.shift_seconds(symbol)
    oos = (metrics.split_ts(study.OOS_FROM) + shift,
           metrics.split_ts(study.OOS_TO) + shift)
    prior = (metrics.split_ts(PRIOR_FROM) + shift,
             metrics.split_ts(PRIOR_TO) + shift)

    oos_days, prior_days = _days(symbol, strategy, oos), _days(symbol, strategy, prior)
    base_summary, base_trades = _run(symbol, strategy, params, oos, oos_days,
                                     study.INITIAL_BALANCE, cost_bp)
    scaled, _ = _run(symbol, strategy, params, oos, oos_days,
                     10 * study.INITIAL_BALANCE, cost_bp)
    prior_summary, _ = _run(symbol, strategy, params, prior, prior_days,
                            study.INITIAL_BALANCE, cost_bp)
    prior_null, _ = _run(symbol, strategy, params, prior, prior_days,
                         study.INITIAL_BALANCE, cost_bp, study.NULL_SEED)

    return {
        "symbol": symbol, "strategy": strategy, "params": params,
        "cost_bp": cost_bp,
        "oos": base_summary,
        "oos_buy_and_hold_pct": buy_and_hold(symbol, study.OOS_FROM, study.OOS_TO),
        "oos_concentration": concentration(base_trades),
        "oos_at_10x_balance": scaled,
        "prior_window": [PRIOR_FROM, PRIOR_TO],
        "prior_sessions": len(prior_days),
        "prior": prior_summary,
        "prior_null": prior_null,
        "prior_buy_and_hold_pct": buy_and_hold(symbol, PRIOR_FROM, PRIOR_TO),
    }


def load_winners(path):
    """`(symbol, strategy) -> the winning parameter dict` from a sweep report.

    The sweep records only the searched axes, so a Maroy winner is rehydrated
    with the two fields the search held constant for every cell -- `target_vol`
    is pinned at 1.0 so the paper's volatility scale-down stays inert, and the
    name is cosmetic.
    """
    with open(path) as handle:
        report = json.load(handle)
    out = {}
    for row in report["symbols"]:
        for strategy in ("drift", "maroy"):
            block = row.get(strategy) or {}
            if "real" not in block:
                continue
            params = dict(block["real"]["params"])
            if strategy == "maroy":
                params.setdefault("target_vol", 1.0)
                params.setdefault("name", row["symbol"])
                params.setdefault("family", params["exit_kind"])
            out[(row["symbol"], strategy)] = params
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", default=os.path.join(
        os.path.dirname(__file__), "..", "results",
        "drift_maroy_symbol_study_0.5bp.json"))
    parser.add_argument("--cost-bp", type=float, default=0.5)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    winners = load_winners(args.sweep)
    rows = []
    for symbol, strategy in SURVIVORS:
        if (symbol, strategy) not in winners:
            print(f"{symbol}/{strategy}: absent from {args.sweep}")
            continue
        rows.append(validate(symbol, strategy, winners, args.cost_bp))

    print(f"\n{'=' * 122}")
    print(f"SURVIVOR DILIGENCE at {args.cost_bp} bp     "
          f"OOS {study.OOS_FROM}..{study.OOS_TO}     "
          f"untouched prior window {PRIOR_FROM}..{PRIOR_TO}")
    print(f"{'=' * 122}")
    print(f"{'symbol/strat':18}{'OOS ret%':>10}{'B&H%':>9}{'10x ret%':>10}"
          f"{'best trade':>12}{'long PnL':>10}{'short PnL':>11}"
          f"{'prior%':>9}{'prior null%':>13}")
    print("-" * 122)
    for row in rows:
        c = row["oos_concentration"]
        best = c.get("best_trade_share_of_gross")
        print(f"{row['symbol'] + '/' + row['strategy']:18}"
              f"{row['oos']['return_pct']:>10.1f}"
              f"{row['oos_buy_and_hold_pct']:>9.1f}"
              f"{row['oos_at_10x_balance']['return_pct']:>10.1f}"
              f"{(f'{best:.0%}' if best is not None else '-'):>12}"
              f"{c.get('long_pnl', 0):>10.0f}{c.get('short_pnl', 0):>11.0f}"
              f"{row['prior']['return_pct']:>9.1f}"
              f"{row['prior_null']['return_pct']:>13.1f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump({"cost_bp": args.cost_bp, "sweep": args.sweep,
                   "survivors": rows}, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
