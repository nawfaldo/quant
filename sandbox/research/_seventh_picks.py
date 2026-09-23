"""Realised in-sample and holdout behaviour for named candidates.

The Monte Carlo ranks candidates on a distribution, which is the right guard and
the wrong thing to read when the question is "what did this actually do". This
prints the realised path per era, plus the sleeve's own per-trade edge year by
year, so a candidate that scores well on a 4.7-year median cannot hide a dead
holdout ([[refill-guarded-on-one-path-hides-its-tail]]).

Single process, no pool.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_picks \
        --picks hk50:level_confluence,ethusd:pullback
"""
from __future__ import annotations

import argparse
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

DEFAULT = ("hk50:level_confluence", "ethusd:pullback", "usdjpy:cci",
           "es:regime_breakout", "usdjpy:fracdiff")
YEARS = (2022, 2023, 2024, 2025, 2026)


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--picks", default=",".join(DEFAULT))
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    picks = [p for p in args.picks.replace(" ", "").split(",") if p]
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())

    print("STANDALONE EDGE BY YEAR (R = gross / distance, after live cost)")
    print(f"  {'sleeve':26}" + "".join(f"{y:>14}" for y in YEARS)
          + f"{'2026 vs prior':>15}")
    print(f"  {'':26}" + "".join(f"{'n':>5}{'sumR':>9}" for _ in YEARS)
          + f"{'ratio':>15}")
    for key in picks:
        by = {}
        for trade in state["logs"][key]:
            distance = trade.get("distance") or 0
            if distance <= 0:
                continue
            year = datetime.fromtimestamp(trade["exit_ts"],
                                          tz=timezone.utc).year
            by.setdefault(year, []).append(trade["gross"] / distance)
        line = f"  {key:26}"
        for year in YEARS:
            rs = by.get(year, [])
            line += f"{len(rs):>5}{sum(rs):>9.1f}"
        r26 = by.get(2026, [])
        early = [r for y in YEARS[:-1] for r in by.get(y, [])]
        if r26 and early and sum(early) / len(early) > 0:
            ratio = (sum(r26) / len(r26)) / (sum(early) / len(early))
            line += f"{ratio:>14.2f}x"
        else:
            line += f"{'-':>15}"
        print(line)

    windows = (("IS 22-25", stamp("2022-01-01"), stamp("2025-01-01")),
               ("OOS 25-26", stamp("2025-01-01"), stamp("2026-08-21")),
               ("2026 only", stamp("2026-01-01"), stamp("2026-08-21")))

    print(f"\nIN THE BOOK, realised, risk {args.risk}")
    print(f"  {'book':26}{'window':11}{'return':>11}{'MTMdd':>8}{'pos mo':>9}"
          f"{'mSh':>7}{'uw days':>9}{'worst mo':>10}{'skip':>6}")
    for label, add in [("canon 20", [])] + [(f"+{k}", [k]) for k in picks]:
        keys = list(ecs.BOOK) + add
        members = [state["members"][k] for k in keys]
        for name, lo, hi in windows:
            book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk)
            series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                          ecs.CANON_INITIAL)
            curve = [(ts - lo, v) for ts, v in book["marked"]]
            worst = min((r["return_pct"] for r in series.values()), default=0.0)
            print(f"  {label[:25]:26}{name:11}{book['return_pct']:>+10,.0f}%"
                  f"{book['mtm_dd_pct']:>7.1f}%{positive:>5}/{total:<3}"
                  f"{sharpe:>7.2f}{mc.underwater(curve):>9.1f}{worst:>+9.1f}%"
                  f"{sum(book['below_broker_minimum'].values()):>6}")


if __name__ == "__main__":
    main()
