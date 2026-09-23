"""Per-sleeve edge decay, measured in R-multiples and independent of sizing.

WHY R AND NOT P&L. A sleeve's dollar contribution inside the book is decided by
the shared balance, the lot floor and where in the compounding path its trades
fell, so a sleeve can look worse in a later year purely because it was sized
smaller. `gross / distance` is the trade's outcome in units of the risk it
actually took, which is the same quantity in 2022 and 2026 and on any balance.

`gross` is already net of the spread the live-execution maps charged, so this is
an AFTER-COST edge, not a gross one.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_decay
"""
from __future__ import annotations

import math
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_strategies as ecs

DAMPERS = ("eurjpy:gated_orb", "usoil:momentum", "usoil:aroon")


def year_of(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).year


def stats(rs):
    n = len(rs)
    if n == 0:
        return 0, 0.0, 0.0, 0.0
    mean = sum(rs) / n
    if n > 1:
        sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    return n, mean, sum(rs), t


def main():
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    keys = list(ecs.BOOK) + list(DAMPERS)
    years = (2022, 2023, 2024, 2025, 2026)

    rows = []
    for key in keys:
        log = state["logs"][key]
        by = {y: [] for y in years}
        for t in log:
            y = year_of(t["exit_ts"])
            if y in by and t.get("distance", 0) > 0:
                by[y].append(t["gross"] / t["distance"])
        cell = {"sleeve": key}
        for y in years:
            n, mean, total, tstat = stats(by[y])
            cell[y] = (n, mean, total, tstat)
        # Decay: mean R in 2026 against the 2022-2025 mean, and the sum of R
        # actually banked in 2026.
        early = [r for y in (2022, 2023, 2024, 2025) for r in by[y]]
        _n, e_mean, _s, _t = stats(early)
        n26, m26, s26, t26 = cell[2026]
        cell["early_mean"] = e_mean
        cell["ratio"] = (m26 / e_mean) if e_mean > 0 else float("nan")
        cell["r26"] = s26
        cell["n26"] = n26
        cell["t26"] = t26
        rows.append(cell)

    rows.sort(key=lambda c: c["r26"])
    print("R-multiple per trade by year (after live-execution cost)")
    print(f"{'sleeve':30}" + "".join(f"{y:>16}" for y in years)
          + f"{'2026 vs prior':>15}")
    print(f"{'':30}" + "".join(f"{'n':>6}{'sumR':>10}" for _ in years)
          + f"{'meanR ratio':>15}")
    for c in rows:
        line = f"{c['sleeve']:30}"
        for y in years:
            n, mean, total, _t = c[y]
            line += f"{n:>6}{total:>10.1f}"
        ratio = c["ratio"]
        line += f"{(ratio if ratio == ratio else 0):>14.2f}x"
        print(line)

    print("\nFAILING THE 2026 TEST (negative R banked in 2026):")
    for c in rows:
        if c["r26"] < 0:
            print(f"  {c['sleeve']:30} 2026 sumR {c['r26']:>7.1f} on "
                  f"{c['n26']:>4} trades, meanR {c[2026][1]:>+7.3f} "
                  f"(t {c['t26']:>+5.2f}) vs prior {c['early_mean']:>+.3f}")
    print("\nDECAYED (2026 mean R under half its 2022-2025 mean, but positive):")
    for c in rows:
        if c["r26"] >= 0 and c["ratio"] == c["ratio"] and c["ratio"] < 0.5:
            print(f"  {c['sleeve']:30} ratio {c['ratio']:.2f}x  "
                  f"2026 meanR {c[2026][1]:>+.3f} vs prior {c['early_mean']:>+.3f}")


if __name__ == "__main__":
    main()
