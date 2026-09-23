"""Test the five NQ sleeves against the decay rule and the current canon.

NQ WAS BARRED AS A SYMBOL, NOT FOUND WANTING. The 2026-09-03 removal was about
the data path -- `nq_1m` is a back-adjusted continuum and the sleeves reach real
prices only through the EXTERNAL level-two import
([[nq-is-barred-ustec-is-the-replacement]]) -- so their EDGE was never actually
screened the way the twenty survivors were. This asks the same two questions of
them: is the 2026 edge still there, and does the sleeve help the book.

`EXCLUDE_SYMBOLS` is bypassed deliberately and locally: the bar is a policy on
what `candidates()` offers, and this is the test that would justify changing it.
Nothing here writes to canon.

SINGLE PROCESS ON PURPOSE. Every NQ sleeve is EXTERNAL, so replaying one pulls a
full level-two price grid into the marking cache; a worker pool would hold one
copy per worker ([[multi-symbol-select-leaks-into-one-worker]]) and this machine
has already been taken down once today
([[multiprocessing-needs-a-main-guard]]). No pool is opened here at all.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_nq
"""
from __future__ import annotations

import argparse
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

NQ = ("nq:ofi", "nq:drift_vwap", "nq:volatility_breakout",
      "nq:level_confluence", "nq:sar")


def year_r(log):
    """`{year: [fractional return per trade]}`.

    EXTERNAL trades carry no `distance`, so the families' `gross / distance` R
    cannot be formed. `points * point_value * units_per_dollar` is the same
    quantity by another route: `units_per_dollar` is units per dollar of equity,
    so the product is the fraction of the account this trade moved at unit
    sizing -- comparable across years and independent of the balance.
    """
    by = {}
    for t in log:
        units = t.get("units_per_dollar")
        if units is None:
            continue
        year = datetime.fromtimestamp(t["exit_ts"], tz=timezone.utc).year
        by.setdefault(year, []).append(
            t["points"] * t["point_value"] * units)
    return by


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    window = live.window()
    mc.pin_external_window(window)

    print("importing the five NQ sleeves (EXTERNAL, level-two path)",
          flush=True)
    for key in NQ:
        log = ecs.external_trades(key, window=window)
        symbol, family = key.split(":", 1)
        state["logs"][key] = log
        state["members"][key] = {"symbol": symbol, "family": family,
                                 "external": True}
        print(f"  {key:26}{len(log):>6} trades", flush=True)

    sample = next((t for k in NQ for t in state["logs"][k]), None)
    print(f"\ntrade fields: {sorted(sample)}" if sample else "no trades")

    print("\nDECAY: fraction of equity moved per trade at unit sizing, by year")
    years = (2022, 2023, 2024, 2025, 2026)
    print(f"  {'sleeve':26}" + "".join(f"{y:>16}" for y in years)
          + f"{'2026 vs prior':>15}")
    print(f"  {'':26}" + "".join(f"{'n':>6}{'sumR':>10}" for _ in years)
          + f"{'ratio':>15}")
    for key in NQ:
        by = year_r(state["logs"][key])
        if not by:
            print(f"  {key:26}  no distance field -- R not computable")
            continue
        line = f"  {key:26}"
        for y in years:
            rs = by.get(y, [])
            line += f"{len(rs):>6}{sum(rs):>10.1f}"
        r26 = by.get(2026, [])
        early = [r for y in (2022, 2023, 2024, 2025) for r in by.get(y, [])]
        if r26 and early and sum(early) / len(early) > 0:
            ratio = (sum(r26) / len(r26)) / (sum(early) / len(early))
            line += f"{ratio:>14.2f}x"
        else:
            line += f"{'-':>15}"
        print(line)

    lo, hi = fr._bounds("full")

    def show(label, keys):
        book = fr._replay([state["members"][k] for k in keys],
                          lo=lo, hi=hi, risk_scale=args.risk)
        series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                      ecs.CANON_INITIAL)
        curve = [(ts - lo, v) for ts, v in book["marked"]]
        print(f"  {label[:27]:28}{len(keys):>4}{book['return_pct']:>+11,.0f}%"
              f"{book['mtm_dd_pct']:>7.1f}%{positive:>5}/{total:<3}"
              f"{sharpe:>6.2f}{mc.underwater(curve):>9.1f}"
              f"{min((r['return_pct'] for r in series.values()), default=0):>+9.1f}%"
              f"{sum(book['below_broker_minimum'].values()):>6}")

    print(f"\nIN THE BOOK, full window, risk {args.risk}")
    print(f"  {'change':28}{'n':>4}{'return':>12}{'MTMdd':>8}{'pos mo':>9}"
          f"{'mSh':>6}{'uw days':>9}{'worst mo':>10}{'skip':>6}")
    show("(canon)", list(ecs.BOOK))
    for key in NQ:
        show(f"+{key}", list(ecs.BOOK) + [key])
    show("+all five", list(ecs.BOOK) + list(NQ))

    # THE PINNING TEST. If a sleeve's drawdown contribution is the same at a
    # third of the risk, it is not being sized -- it is placing the broker
    # minimum every time and `risk_scale` cannot touch it
    # ([[min-lot-pinned-sleeves-do-not-compound]]).
    print("\nSAME BOOKS AT risk 0.05 -- if MTM dd barely moves, the sleeve "
          "is min-lot pinned")
    print(f"  {'change':28}{'n':>4}{'return':>12}{'MTMdd':>8}{'pos mo':>9}"
          f"{'mSh':>6}{'uw days':>9}{'worst mo':>10}{'skip':>6}")
    args.risk = 0.05
    show("(canon)", list(ecs.BOOK))
    for key in NQ:
        show(f"+{key}", list(ecs.BOOK) + [key])


if __name__ == "__main__":
    main()
