"""Monte Carlo band for the decay-screened books, on both windows.

THE BOOKS. All three drop the seven sleeves that failed the 2026 edge test --
two negative in 2026 (`de40:consecutive`, `ethusd:idio_break`) and five decayed
to under half their prior mean R (`gbpusd:obv_break`, `ustec:key_reversal`,
`ustec:kendall`, `ustec:volatility_breakout`, `gbpjpy:fracdiff`) -- and then
refill by greedy forward selection under drawdown guards:

    CLEAN 20   the screen alone, plus the one damper that survived it
    SLOT4 24   + usdjpy:cci, jp225:climax, usdjpy:fracdiff, ethusd:cusum
    SLOT7 27   + usdjpy:rvol, usdjpy:gated_fade, ethusd:cci

`jp225:regime_breakout` is barred from the bench: it is `jp225:volatility_breakout`
with its regime gate switched off ([[inert-gate-clones-a-family]]).

THIS FILE EXISTS BECAUSE THE THROWAWAY THAT RAN IT FIRST HAD NO `__main__`
GUARD AND FORK-BOMBED THE MACHINE. `fr._run` opens a multiprocessing Pool, and
on Windows every spawned child re-imports the main module; unguarded, each child
re-created the pool and spawned more, recursively
([[multiprocessing-needs-a-main-guard]]). Worker count is deliberately modest
for the same reason -- each worker holds its own expansion of the frontier cache.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_clean_mc
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_clean_mc.json")

CUT = ("de40:consecutive", "ethusd:idio_break", "gbpusd:obv_break",
       "ustec:key_reversal", "ustec:kendall", "ustec:volatility_breakout",
       "gbpjpy:fracdiff")
CLEAN = ("eurjpy:gated_orb",)
SLOT4 = CLEAN + ("usdjpy:cci", "jp225:climax", "usdjpy:fracdiff",
                 "ethusd:cusum")
SLOT7 = SLOT4 + ("usdjpy:rvol", "usdjpy:gated_fade", "ethusd:cci")

BOOKS = (("CLEAN 20", CLEAN), ("SLOT4 24", SLOT4), ("SLOT7 27", SLOT7))
RISKS = (0.13, 0.19)
ERAS = ("full", "oos")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    args = parser.parse_args()
    live.arm()

    out = {}
    for era in ERAS:
        jobs = [(CUT, add, risk, seed)
                for _label, add in BOOKS
                for risk in RISKS
                for seed in range(1, args.paths + 1)]
        print(f"\n=== era {era}: {len(jobs):,} replays, "
              f"{args.workers} workers ===", flush=True)
        rows = fr._run(jobs, args.workers, args.block_days, era, era)
        grouped = {}
        for row in rows:
            grouped.setdefault((tuple(row["add"]), row["risk"]), []).append(row)
        for key, group in grouped.items():
            out.setdefault(key, {})[era] = fr._band(group)

    print(f"\n{'book':11}{'risk':>6}{'era':>6}{'p10':>8}{'p25':>8}{'p50':>8}"
          f"{'p75':>8}{'p90':>8}{'p95':>8}{'ret p10':>11}{'ret p50':>11}")
    table = []
    for label, add in BOOKS:
        for risk in RISKS:
            band = out.get((tuple(add), risk))
            if not band:
                continue
            for era in ERAS:
                b = band[era]
                print(f"{label:11}{risk:>6.2f}{era:>6}{b['p10_dd']:>8.2f}"
                      f"{b['p25_dd']:>8.2f}{b['p50_dd']:>8.2f}{b['p75_dd']:>8.2f}"
                      f"{b['p90_dd']:>8.2f}{b['p95_dd']:>8.2f}"
                      f"{b['p10_ret']:>10,.0f}%{b['p50_ret']:>10,.0f}%")
            table.append({"book": label, "risk": risk,
                          "n": len(ecs.BOOK) - len(CUT) + len(add),
                          "worst_p95": max(band[e]["p95_dd"] for e in ERAS),
                          **{f"{e}_{k}": band[e][k] for e in ERAS
                             for k in ("p10_dd", "p50_dd", "p95_dd",
                                       "p10_ret", "p50_ret")}})

    table.sort(key=lambda r: r["worst_p95"])
    print(f"\nRANKED BY WORST-WINDOW p95")
    print(f"{'book':11}{'risk':>6}{'n':>4}{'worst p95':>11}"
          f"{'full p95':>10}{'OOS p95':>9}{'full ret':>12}{'OOS ret':>10}")
    for row in table:
        print(f"{row['book']:11}{row['risk']:>6.2f}{row['n']:>4}"
              f"{row['worst_p95']:>11.2f}{row['full_p95_dd']:>10.2f}"
              f"{row['oos_p95_dd']:>9.2f}{row['full_p50_ret']:>11,.0f}%"
              f"{row['oos_p50_ret']:>9,.0f}%")

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"paths": args.paths, "cut": list(CUT),
                   "book": list(ecs.BOOK), "table": table}, handle, indent=2)
    print(f"\n-> {OUT_PATH}")


if __name__ == "__main__":
    main()
