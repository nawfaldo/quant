"""Correlation and equal-weight portfolio analysis for passed commodity cells.

Correlations and basket selection use only each strategy's in-sample daily
returns.  The selected baskets are then reported on 2025-2026.  Returns are
daily-rebalanced equal-weight sleeve returns; this is a diversification study,
not a shared-margin execution simulator.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import statistics

from sandbox.research import commodity_families_research as study

PASS = {
    "xngusd": ("donchian", "gap", "momentum", "orb", "overnight", "zscore"),
    "xalusd": ("ma_cross", "orb", "overnight", "pdr", "zscore"),
    "xniusd": ("vwap", "zscore"),
}


def daily_returns(trades, initial=study.INITIAL_BALANCE):
    pnl = {}
    for trade in trades:
        day = trade["exit_ts"] // 86_400
        pnl[day] = pnl.get(day, 0.0) + trade["pnl"]
    equity = initial
    out = {}
    for day in sorted(pnl):
        out[day] = pnl[day] / equity if equity > 0 else -1.0
        equity += pnl[day]
    return out


def weekdays(lo, hi):
    return [day for day in range(lo // 86_400, hi // 86_400)
            if (day + 3) % 7 < 5]  # Unix epoch day zero was Thursday.


def correlation(left, right, lo, hi):
    days = weekdays(lo, hi)
    if len(days) < 2:
        return 0.0
    a = [left.get(day, 0.0) for day in days]
    b = [right.get(day, 0.0) for day in days]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va = sum((x-ma)**2 for x in a); vb = sum((x-mb)**2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((x-ma)*(y-mb) for x,y in zip(a,b)) / math.sqrt(va*vb)


def basket(series, keys, lo, hi, initial=study.INITIAL_BALANCE):
    days = weekdays(lo, hi)
    equity = peak = initial; maximum_dd = 0.0; returns = []
    gains = losses = 0.0
    for day in days:
        value = statistics.fmean(series[key].get(day, 0.0) for key in keys)
        pnl = equity * value; equity += pnl; peak = max(peak, equity)
        maximum_dd = max(maximum_dd, (peak-equity)/peak if peak > 0 else 1.0)
        returns.append(value)
        if value > 0: gains += value
        elif value < 0: losses -= value
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.fmean(returns)/sd*math.sqrt(252) if sd else 0.0
    return {"return_pct": round(100*(equity/initial-1),2),
            "max_dd_pct": round(100*maximum_dd,2),
            "daily_sharpe": round(sharpe,3),
            "daily_pf": round(gains/losses,3) if losses else None,
            "active_days": sum(any(series[k].get(d,0) for k in keys) for d in days)}


def load():
    selected = {}; contexts = {}
    for symbol, families in PASS.items():
        payload = json.load(open(study.output_path(symbol), encoding="utf-8"))
        bars, ctx = contexts.setdefault(symbol, study.context(symbol, "validate"))
        for family in families:
            params = payload["families"][family]["params"]
            key = f"{symbol}:{family}"
            inside = study.backtest(family,bars,ctx,params,lo=study.IS_START,
                                    hi=study.IS_END,include_trades=True)
            outside = study.backtest(family,bars,ctx,params,lo=study.IS_END,
                                     hi=study.OOS_END,include_trades=True)
            selected[key] = {"symbol":symbol,"family":family,
                             "is":daily_returns(inside["trade_log"]),
                             "oos":daily_returns(outside["trade_log"])}
    return selected


def analyze(max_correlation=0.25):
    selected = load(); keys = sorted(selected)
    corr = {a:{b:round(correlation(selected[a]["is"],selected[b]["is"],
                                   study.IS_START,study.IS_END),4)
               for b in keys} for a in keys}
    candidates = []
    for size in range(2,7):
        for combo in itertools.combinations(keys,size):
            # At least two symbols; three-sleeve baskets must use three symbols.
            symbols = {selected[k]["symbol"] for k in combo}
            if len(symbols) < min(size,3): continue
            counts = {symbol:sum(selected[k]["symbol"] == symbol for k in combo)
                      for symbol in symbols}
            if max(counts.values()) > 2: continue
            pairs = [abs(corr[a][b]) for a,b in itertools.combinations(combo,2)]
            if max(pairs,default=0.0) > max_correlation: continue
            oos_pairs = [abs(correlation(selected[a]["oos"], selected[b]["oos"],
                                         study.IS_END, study.OOS_END))
                         for a,b in itertools.combinations(combo,2)]
            is_stat=basket({k:selected[k]["is"] for k in combo},combo,
                           study.IS_START,study.IS_END)
            oos_stat=basket({k:selected[k]["oos"] for k in combo},combo,
                            study.IS_END,study.OOS_END)
            candidates.append({"strategies":list(combo),"max_abs_is_corr":round(max(pairs),4),
                               "max_abs_oos_corr":round(max(oos_pairs,default=0.0),4),
                               "is":is_stat,"oos":oos_stat})
    # Select without reading OOS: best IS Sharpe for each basket size, plus the
    # lowest-correlation basket for each size.
    picked=[]
    for size in range(2,7):
        rows=[r for r in candidates if len(r["strategies"])==size]
        if not rows: continue
        for row in (max(rows,key=lambda r:r["is"]["daily_sharpe"]),
                    min(rows,key=lambda r:r["max_abs_is_corr"])):
            if row not in picked: picked.append(row)
    result={"protocol":{"correlation_window":"2020-2024 available history",
                        "holdout":"2025-01-01 through 2026-08-06",
                        "weighting":"equal-weight daily-rebalanced strategy returns",
                        "max_abs_is_correlation":max_correlation,
                        "note":"research overlay; does not simulate shared MT5 margin"},
            "is_correlations":corr,"selected_combinations":picked,
            "eligible_combinations":len(candidates)}
    path=os.path.join(study.RESULTS,"commodity_uncorrelated_portfolio.json")
    with open(path,"w",encoding="utf-8") as f:
        json.dump(result,f,indent=2,sort_keys=True); f.write("\n")
    for row in picked:
        print(" + ".join(row["strategies"]),f"corr={row['max_abs_is_corr']:.3f}",
              f"IS={row['is']}",f"OOS={row['oos']}")
    print(f"wrote {path}")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--max-correlation",type=float,default=.25); args=parser.parse_args(); analyze(args.max_correlation)


if __name__=="__main__": main()
