"""IS-selected commodity baskets targeting 15% DD and calendar consistency."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox.research import commodity_families_research as study
from sandbox.research import commodity_portfolio as portfolio


def calendar_days(lo, hi):
    return portfolio.weekdays(lo, hi)


def return_path(series, keys, lo, hi, leverage):
    return [(day, leverage * statistics.fmean(series[key].get(day, 0.0)
                                               for key in keys))
            for day in calendar_days(lo, hi)]


def metrics(path, initial=study.INITIAL_BALANCE):
    equity = peak = initial; maximum_dd = 0.0; daily = []
    month_values = {}; year_values = {}
    for day, value in path:
        # The searched range stays far from ruin, but make the failure explicit.
        value = max(value, -1.0)
        equity *= 1.0 + value; peak = max(peak, equity)
        maximum_dd = max(maximum_dd, (peak-equity)/peak if peak > 0 else 1.0)
        daily.append(value)
        date = datetime.fromtimestamp(day*86_400, timezone.utc)
        month_values.setdefault(date.strftime("%Y-%m"), []).append(value)
        year_values.setdefault(str(date.year), []).append(value)
    def compounded(values):
        result = 1.0
        for value in values: result *= 1.0 + value
        return 100.0*(result-1.0)
    months = {key:round(compounded(values),2) for key,values in month_values.items()}
    years = {key:round(compounded(values),2) for key,values in year_values.items()}
    month_returns = list(months.values()); year_returns = list(years.values())
    month_sd = statistics.stdev(month_returns) if len(month_returns)>1 else 0.0
    monthly_sharpe = statistics.fmean(month_returns)/month_sd*math.sqrt(12) if month_sd else 0.0
    return {"return_pct":round(100*(equity/initial-1),2),
            "max_dd_pct":round(100*maximum_dd,2),
            "positive_months":sum(v>0 for v in month_returns),
            "total_months":len(month_returns),
            "positive_month_rate":round(100*sum(v>0 for v in month_returns)/len(month_returns),1),
            "worst_month_pct":round(min(month_returns),2),
            "monthly_sharpe":round(monthly_sharpe,3),
            "positive_years":sum(v>0 for v in year_returns),
            "total_years":len(year_returns),
            "worst_year_pct":round(min(year_returns),2),
            "monthly_returns":months,"yearly_returns":years}


def target_leverage(series, keys, target_dd, cap):
    lo, hi = 0.0, cap
    for _ in range(32):
        mid = (lo+hi)/2
        dd = metrics(return_path(series,keys,study.IS_START,study.IS_END,mid))["max_dd_pct"]
        if dd <= target_dd: lo = mid
        else: hi = mid
    return round(lo,4)


def analyze(target_dd=15.0, max_correlation=.25, leverage_cap=5.0):
    selected=portfolio.load(); keys=sorted(selected)
    corr={(a,b):abs(portfolio.correlation(selected[a]["is"],selected[b]["is"],
                                           study.IS_START,study.IS_END))
          for a,b in itertools.combinations(keys,2)}
    rows=[]
    for size in range(2,7):
        for combo in itertools.combinations(keys,size):
            symbols={selected[k]["symbol"] for k in combo}
            if len(symbols)<min(size,3): continue
            if max(sum(selected[k]["symbol"]==s for k in combo) for s in symbols)>2: continue
            pair_corr=[corr[tuple(sorted((a,b)))] for a,b in itertools.combinations(combo,2)]
            if max(pair_corr,default=0)>max_correlation: continue
            is_series={k:selected[k]["is"] for k in combo}; oos_series={k:selected[k]["oos"] for k in combo}
            leverage=target_leverage(is_series,combo,target_dd,leverage_cap)
            inside=metrics(return_path(is_series,combo,study.IS_START,study.IS_END,leverage))
            outside=metrics(return_path(oos_series,combo,study.IS_END,study.OOS_END,leverage))
            rows.append({"strategies":list(combo),"leverage":leverage,
                         "max_abs_is_corr":round(max(pair_corr,default=0),4),
                         "is":inside,"oos":outside})
    # Fully IS-determined ordering: yearly pass count, monthly hit rate, worst
    # year, monthly Sharpe, then total growth.
    rows.sort(key=lambda r:(r["is"]["positive_years"],
                            r["is"]["positive_month_rate"],
                            r["is"]["worst_year_pct"],
                            r["is"]["monthly_sharpe"],
                            r["is"]["return_pct"]),reverse=True)
    finalists=[]
    for row in rows:
        if row["is"]["positive_years"]==row["is"]["total_years"]:
            finalists.append(row)
        if len(finalists)>=10: break
    result={"protocol":{"target_is_dd_pct":target_dd,"leverage_cap":leverage_cap,
                        "max_abs_is_correlation":max_correlation,
                        "ranking":"IS positive years, positive-month rate, worst year, monthly Sharpe, return",
                        "weights":"equal before common leverage",
                        "warning":"daily sleeve overlay; shared MT5 margin and lot rounding not simulated"},
            "eligible_combinations":len(rows),"is_ranked_finalists":finalists}
    path=os.path.join(study.RESULTS,"commodity_consistency_target15.json")
    with open(path,"w",encoding="utf-8") as f: json.dump(result,f,indent=2,sort_keys=True); f.write("\n")
    for index,row in enumerate(finalists,1):
        print(index," + ".join(row["strategies"]),f"lev={row['leverage']:.3f}",
              f"IS={row['is']['return_pct']:+.1f}%/{row['is']['max_dd_pct']:.1f}% "
              f"months={row['is']['positive_months']}/{row['is']['total_months']} years={row['is']['positive_years']}/{row['is']['total_years']}",
              f"OOS={row['oos']['return_pct']:+.1f}%/{row['oos']['max_dd_pct']:.1f}% "
              f"months={row['oos']['positive_months']}/{row['oos']['total_months']} years={row['oos']['positive_years']}/{row['oos']['total_years']}")
    print(f"wrote {path}")


def analyze_full_sleeves(target_dd=15.0, tolerance=3.0, max_correlation=.25):
    """Every constituent keeps 100% of its standalone return stream.

    Summing N strategies is equivalent to ``return_path`` with leverage N,
    because that helper first takes their equal-weight mean.  No target-DD
    rescaling is performed; the DD band is a filter, not a fitted multiplier.
    """
    selected=portfolio.load(); keys=sorted(selected)
    corr={(a,b):abs(portfolio.correlation(selected[a]["is"],selected[b]["is"],
                                           study.IS_START,study.IS_END))
          for a,b in itertools.combinations(keys,2)}
    rows=[]
    for size in range(2,7):
        for combo in itertools.combinations(keys,size):
            symbols={selected[k]["symbol"] for k in combo}
            if len(symbols)<min(size,3): continue
            if max(sum(selected[k]["symbol"]==s for k in combo) for s in symbols)>2: continue
            pair_corr=[corr[tuple(sorted((a,b)))] for a,b in itertools.combinations(combo,2)]
            if max(pair_corr,default=0)>max_correlation: continue
            is_series={k:selected[k]["is"] for k in combo}; oos_series={k:selected[k]["oos"] for k in combo}
            inside=metrics(return_path(is_series,combo,study.IS_START,study.IS_END,size))
            if not target_dd-tolerance <= inside["max_dd_pct"] <= target_dd+tolerance: continue
            outside=metrics(return_path(oos_series,combo,study.IS_END,study.OOS_END,size))
            rows.append({"strategies":list(combo),"allocation_per_strategy_pct":100.0,
                         "gross_sleeve_exposure":size,
                         "max_abs_is_corr":round(max(pair_corr,default=0),4),
                         "is":inside,"oos":outside})
    rows.sort(key=lambda r:(r["is"]["positive_years"],r["is"]["positive_month_rate"],
                            r["is"]["worst_year_pct"],r["is"]["monthly_sharpe"],
                            r["is"]["return_pct"]),reverse=True)
    finalists=[r for r in rows if r["is"]["positive_years"]==r["is"]["total_years"]][:10]
    oos_near=[r for r in rows if target_dd-tolerance<=r["oos"]["max_dd_pct"]<=target_dd+tolerance]
    oos_near.sort(key=lambda r:(r["oos"]["positive_years"],r["oos"]["positive_month_rate"],
                                r["oos"]["worst_year_pct"],r["oos"]["monthly_sharpe"],
                                r["oos"]["return_pct"]),reverse=True)
    result={"protocol":{"allocation":"100% standalone return stream per strategy; returns summed",
                        "target_is_dd_band_pct":[target_dd-tolerance,target_dd+tolerance],
                        "max_abs_is_correlation":max_correlation,
                        "ranking":"IS positive years, positive-month rate, worst year, monthly Sharpe, return",
                        "warning":"independent full-account sleeves; shared MT5 equity, margin and lot rounding not simulated"},
            "eligible_near_target":len(rows),"is_ranked_finalists":finalists,
            "posthoc_oos_near_target":oos_near[:10]}
    path=os.path.join(study.RESULTS,"commodity_consistency_full_sleeves.json")
    with open(path,"w",encoding="utf-8") as f: json.dump(result,f,indent=2,sort_keys=True); f.write("\n")
    for index,row in enumerate(finalists,1):
        print(index," + ".join(row["strategies"]),
              f"IS={row['is']['return_pct']:+.1f}%/{row['is']['max_dd_pct']:.1f}% months={row['is']['positive_months']}/{row['is']['total_months']}",
              f"OOS={row['oos']['return_pct']:+.1f}%/{row['oos']['max_dd_pct']:.1f}% months={row['oos']['positive_months']}/{row['oos']['total_months']}")
    print("POSTHOC OOS NEAR TARGET")
    for index,row in enumerate(oos_near[:10],1):
        print(index," + ".join(row["strategies"]),
              f"IS={row['is']['return_pct']:+.1f}%/{row['is']['max_dd_pct']:.1f}% months={row['is']['positive_months']}/{row['is']['total_months']}",
              f"OOS={row['oos']['return_pct']:+.1f}%/{row['oos']['max_dd_pct']:.1f}% months={row['oos']['positive_months']}/{row['oos']['total_months']}")
    print(f"wrote {path}")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--target-dd",type=float,default=15.0); parser.add_argument("--dd-tolerance",type=float,default=3.0); parser.add_argument("--max-correlation",type=float,default=.25); parser.add_argument("--leverage-cap",type=float,default=5.0); parser.add_argument("--full-sleeves",action="store_true"); args=parser.parse_args()
    if args.full_sleeves: analyze_full_sleeves(args.target_dd,args.dd_tolerance,args.max_correlation)
    else: analyze(args.target_dd,args.max_correlation,args.leverage_cap)


if __name__=="__main__": main()
