"""What the greedy's picks do out of sample, and their year-by-year edge.

The greedy in `_seventh_grow` ranks on the FULL window, so a pick could in
principle be earning its place in 2022-2024 and doing nothing since. This asks
each seated addition the two questions that would catch it: what it does on the
2025-2026 holdout and in 2026 alone, and whether its per-trade edge is still
there year by year.

Single process, no pool -- it runs beside the search without competing for
memory ([[multiprocessing-needs-a-main-guard]]).
"""
from __future__ import annotations
import math, pickle
from datetime import datetime, timezone
from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

PICKS = ["jp225:climax", "audjpy:regime_breakout"]


def main():
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())

    print("PER-TRADE EDGE BY YEAR (R = gross / distance, after live cost)")
    print(f"  {'sleeve':26}" + "".join(f"{y:>14}" for y in (2022, 2023, 2024, 2025, 2026)))
    print(f"  {'':26}" + "".join(f"{'n':>5}{'sumR':>9}" for _ in range(5)))
    for key in PICKS:
        by = {}
        for t in state["logs"][key]:
            d = t.get("distance") or 0
            if d <= 0:
                continue
            y = datetime.fromtimestamp(t["exit_ts"], tz=timezone.utc).year
            by.setdefault(y, []).append(t["gross"] / d)
        line = f"  {key:26}"
        for y in (2022, 2023, 2024, 2025, 2026):
            rs = by.get(y, [])
            line += f"{len(rs):>5}{sum(rs):>9.1f}"
        print(line)

    def ts(s):
        return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())

    W = [("IS 22-25", ts("2022-01-01"), ts("2025-01-01")),
         ("OOS 25-26", ts("2025-01-01"), ts("2026-08-21")),
         ("2026 only", ts("2026-01-01"), ts("2026-08-21"))]
    books = [("canon 20", []), ("+climax", PICKS[:1]), ("+both", PICKS)]
    print(f"\nREALISED, risk {ecs.CANON_RISK_SCALE}")
    print(f"  {'book':12}{'window':12}{'return':>11}{'MTMdd':>8}{'pos mo':>9}"
          f"{'mSh':>6}{'uw days':>9}{'worst mo':>10}")
    for label, add in books:
        keys = list(ecs.BOOK) + add
        members = [state["members"][k] for k in keys]
        for wname, lo, hi in W:
            b = fr._replay(members, lo=lo, hi=hi,
                           risk_scale=ecs.CANON_RISK_SCALE)
            series, sh, pos, tot = ecs.monthly(b["settled"], ecs.CANON_INITIAL)
            curve = [(t - lo, v) for t, v in b["marked"]]
            print(f"  {label:12}{wname:12}{b['return_pct']:>+10,.0f}%"
                  f"{b['mtm_dd_pct']:>7.1f}%{pos:>5}/{tot:<3}{sh:>6.2f}"
                  f"{mc.underwater(curve):>9.1f}"
                  f"{min((r['return_pct'] for r in series.values()), default=0):>+9.1f}%")


if __name__ == "__main__":
    main()
