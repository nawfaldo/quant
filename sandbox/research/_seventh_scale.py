"""Why a cold-start era out-returns the same months inside a long run.

THE PUZZLE. IS (36 months) makes +1,101% and OOS (20 months) makes +673%, each
from its own $500 -- so OOS compounds at roughly twice the annual rate. Chaining
the two multiples gives +9,180%, but the continuous 2022-2026 run returns
+5,879%. The same trades, in the same order, are worth less inside the long run.

THE CAUSE IS THE LOT FLOOR, AND IT CUTS BOTH WAYS. At $500 a forced minimum lot
is a large share of equity, so a sleeve that wants 0.004 lots places 0.01 and
carries more risk -- and more return -- than the risk fraction asked for
([[four-hundred-dollars-selects-the-sleeves-for-you]],
[[lot-granularity-fakes-low-drawdown]]). As the balance compounds the requested
size clears the floor on its own and the boost fades, so percentage returns
normalise downward.

THE TEST. Replay the SAME holdout window from progressively larger starting
balances. If the lot floor is what inflates the cold number, return per year
falls monotonically as the start rises, and drawdown falls with it.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_scale
"""
from __future__ import annotations

import argparse
import math
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

STARTS = (500.0, 1_000.0, 2_000.0, 6_000.0, 20_000.0, 60_000.0)


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    members = [state["members"][k] for k in ecs.BOOK]

    windows = (("IS 22-25", "2022-01-01", "2025-01-01"),
               ("OOS 25-26", "2025-01-01", "2026-08-21"),
               ("full 22-26", "2022-01-01", "2026-08-21"))

    print(f"canon {len(ecs.BOOK)} sleeves, risk {args.risk}")
    print(f"{'window':12}{'start $':>10}{'return':>11}{'x':>8}{'per year':>10}"
          f"{'MTMdd':>8}{'trades':>8}{'skipped':>9}")
    for name, lo_text, hi_text in windows:
        lo, hi = stamp(lo_text), stamp(hi_text)
        years = (hi - lo) / (365.25 * 86_400)
        for start in STARTS:
            book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk,
                              initial=start)
            growth = book["final"] / start
            per_year = (growth ** (1.0 / years) - 1.0) * 100.0 if growth > 0 \
                else float("nan")
            print(f"{name:12}{start:>10,.0f}{book['return_pct']:>+10,.0f}%"
                  f"{growth:>8.2f}{per_year:>9,.0f}%{book['mtm_dd_pct']:>7.1f}%"
                  f"{book['trades']:>8,}"
                  f"{sum(book['below_broker_minimum'].values()):>9,}")
        print()

    lo_is, hi_is = stamp("2022-01-01"), stamp("2025-01-01")
    lo_oos, hi_oos = stamp("2025-01-01"), stamp("2026-08-21")
    is_book = fr._replay(members, lo=lo_is, hi=hi_is, risk_scale=args.risk,
                         initial=ecs.CANON_INITIAL)
    chained = fr._replay(members, lo=lo_oos, hi=hi_oos, risk_scale=args.risk,
                         initial=is_book["final"])
    cold = fr._replay(members, lo=lo_oos, hi=hi_oos, risk_scale=args.risk,
                      initial=ecs.CANON_INITIAL)
    full = fr._replay(members, lo=lo_is, hi=hi_oos, risk_scale=args.risk,
                      initial=ecs.CANON_INITIAL)
    print("THE SAME HOLDOUT MONTHS, COLD versus CARRIED")
    print(f"  cold from ${ecs.CANON_INITIAL:,.0f}      "
          f"{cold['return_pct']:>+9,.0f}%   dd {cold['mtm_dd_pct']:.1f}%   "
          f"final ${cold['final']:,.0f}")
    print(f"  carried from ${is_book['final']:,.0f}  "
          f"{chained['return_pct']:>+9,.0f}%   dd {chained['mtm_dd_pct']:.1f}%"
          f"   final ${chained['final']:,.0f}")
    print(f"\n  IS x OOS if the two multiples chained: "
          f"{100 * ((is_book['final'] / ecs.CANON_INITIAL) * (cold['final'] / ecs.CANON_INITIAL) - 1):+,.0f}%")
    print(f"  IS then the holdout actually carried:  "
          f"{100 * (chained['final'] / ecs.CANON_INITIAL - 1):+,.0f}%")
    print(f"  the continuous 2022-2026 run:          "
          f"{full['return_pct']:+,.0f}%")


if __name__ == "__main__":
    main()
