"""Every candidate book x risk x window, on one Monte Carlo grid.

WHY A GRID AND NOT MORE SWEEPS. The candidates were compared piecemeal -- canon
on three windows at one risk, the damper books on two windows at five risks --
so the one table that answers "which settings give the lowest tail" did not
exist and could not be assembled from the parts. This runs every combination at
the same path count so the numbers are commensurable.

THE RANKING KEY IS THE WORSE WINDOW. A book is only as safe as the era it is
about to trade, and the two eras disagree: the three drops that lower the
2022-2026 tail RAISE the 2025-2026 one ([[oos-drawdown-ignores-the-risk-dial]]).
So cells are ranked on max(p95) across IS, OOS and full rather than on the long
window, which would seat a book fitted to history the account has already
missed.

1,000 paths and not fewer: at 300 the p95 estimate is the 285th order statistic
of a heavy right tail and reads ~1.5 points LOW, which moved a whole frontier
when it was corrected.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_grid --paths 1000
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

OUT_PATH = os.path.join(mc.RESULTS, "_seventh_grid.json")

DAMPERS = ("eurjpy:gated_orb", "usoil:momentum", "usoil:aroon")
DROPS = ("ukoil:xma_cross", "jp225:volume_thrust", "ethusd:idio_break")

#: (label, drop, add) relative to `ecs.BOOK`.
BOOKS = (
    ("canon 26", (), ()),
    ("29 +3dmp", (), DAMPERS),
    ("26 -3+3dmp", DROPS, DAMPERS),
)
RISKS = (0.10, 0.13, 0.16, 0.19, 0.22)
ERAS = ("is", "oos", "full")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=(os.cpu_count() or 8))
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--risks", default="")
    args = parser.parse_args()
    live.arm()
    risks = ([float(v) for v in args.risks.split(",") if v] if args.risks
             else list(RISKS))

    out = {}
    for era in ERAS:
        jobs = [(drop, add, risk, seed)
                for _label, drop, add in BOOKS
                for risk in risks
                for seed in range(1, args.paths + 1)]
        print(f"\n=== era {era}: {len(jobs):,} replays ===", flush=True)
        rows = fr._run(jobs, args.workers, args.block_days, era, era)
        grouped = {}
        for row in rows:
            grouped.setdefault(
                (tuple(row["drop"]), tuple(row["add"]), row["risk"]),
                []).append(row)
        for key, group in grouped.items():
            out.setdefault(key, {})[era] = fr._band(group)

    table = []
    for label, drop, add in BOOKS:
        for risk in risks:
            band = out.get((tuple(drop), tuple(add), risk))
            if not band:
                continue
            worst = max(band[e]["p95_dd"] for e in ERAS)
            table.append({"book": label, "risk": risk, "worst_p95": worst,
                          "n": len(ecs.BOOK) - len(drop) + len(add),
                          **{f"{e}_{k}": band[e][k]
                             for e in ERAS
                             for k in ("p10_dd", "p50_dd", "p95_dd",
                                       "p10_ret", "p50_ret")}})
    table.sort(key=lambda r: r["worst_p95"])

    print(f"\n{'rank':>5}{'book':>12}{'risk':>6}{'worst p95':>11}"
          f"{'IS p95':>8}{'OOS p95':>9}{'full p95':>10}"
          f"{'IS ret':>10}{'OOS ret':>10}{'full ret':>12}")
    for index, row in enumerate(table, start=1):
        print(f"{index:>5}{row['book']:>12}{row['risk']:>6.2f}"
              f"{row['worst_p95']:>11.2f}{row['is_p95_dd']:>8.2f}"
              f"{row['oos_p95_dd']:>9.2f}{row['full_p95_dd']:>10.2f}"
              f"{row['is_p50_ret']:>9,.0f}%{row['oos_p50_ret']:>9,.0f}%"
              f"{row['full_p50_ret']:>11,.0f}%")

    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump({"paths": args.paths, "risks": risks,
                   "book": list(ecs.BOOK), "table": table}, handle, indent=2)
    print(f"\n{len(table)} cells -> {OUT_PATH}")


if __name__ == "__main__":
    main()
