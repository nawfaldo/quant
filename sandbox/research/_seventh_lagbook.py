"""The canon book at the modelled delay against the server-true delay."""
from __future__ import annotations
import argparse, os
from datetime import datetime, timezone
from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_families as ef
from sandbox.research import exness_live_execution as le
from sandbox.research import _seventh_serverlag as sl


def run(maps, label, lo):
    cs.MAPS_OVERRIDE = maps
    cs.TICK_COSTS = True
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.UNCAPPED = True
    cs.GLOBAL_SIZING_CAP = 1500.0
    cs.MAX_DD_CONCENTRATION = None
    cs.FAIR_CAP = False
    cs._TICK_COST_CACHE.clear()
    cs._SLEEVE_MEMO.clear()
    sealed_is_end = ef.IS_END
    try:
        ef.IS_END = lo
        out = cs.build(len(cs.BOOK), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
                       risk_scale=cs.CANON_RISK_SCALE,
                       gross_cap=cs.CANON_GROSS_CAP,
                       initial=cs.CANON_INITIAL, members_exact=cs.BOOK)
    finally:
        ef.IS_END = sealed_is_end
    return label, out["book"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="2025-01-01")
    args = parser.parse_args()
    lo = int(datetime.fromisoformat(args.start)
             .replace(tzinfo=timezone.utc).timestamp())
    results = [run(le.maps_path("bars"), "modelled +79s", lo),
               run(sl.SERVER_MAPS, "measured +3s", lo)]
    print(f"\n{'=' * 78}\nENTRY DELAY: MODELLED (79s) vs MEASURED (~3s)   "
          f"{args.start} .. {le._stamp(ef.OOS_END)}\n{'=' * 78}")
    print(f"  {'':24}{'modelled':>14}{'measured':>14}{'delta':>12}")
    a, b = results[0][1], results[1][1]
    for field, name, unit in (("return_pct", "return", "%"),
                              ("mtm_dd_pct", "MTM dd", "%"),
                              ("max_dd_pct", "closed dd", "%"),
                              ("final", "final equity", ""),
                              ("trades", "trades", "")):
        x, y = a.get(field), b.get(field)
        if x is None or y is None:
            continue
        print(f"  {name:24}{x:>13,.1f}{unit:1}{y:>13,.1f}{unit:1}"
              f"{y - x:>+11,.1f}{unit:1}")
    print(f"\n  {'sleeve':30}{'modelled $':>12}{'measured $':>12}{'delta':>10}")
    for name in cs.BOOK:
        x = a["by_sleeve"].get(name, {}).get("pnl", 0.0)
        y = b["by_sleeve"].get(name, {}).get("pnl", 0.0)
        print(f"  {name:30}{x:>12,.0f}{y:>12,.0f}{y - x:>+10,.0f}")


if __name__ == "__main__":
    main()
