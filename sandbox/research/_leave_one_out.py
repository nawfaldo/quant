"""Leave-one-out on the canon book, priced the way the account actually fills.

WHY NOT STANDALONE RETURN. A sleeve's own return says what it made in its own
account. It does not say what the BOOK loses without it, and the two disagree
sharply: lots are recomputed from a shared balance, so removing a sleeve frees
budget for every other one, and a sleeve that damps a drawdown is worth more
than its return ([[drop-test-not-standalone-return-values-a-sleeve]],
[[low-loss-lift-sleeves-are-drawdown-dampers]]).

So this rebuilds the whole book 26 times -- once whole, once without each member
-- on LIVE fills, and reports what each removal does to return and to marked
drawdown. `cost` is what keeping the sleeve is worth: positive means the book is
worse without it.

ONE PROCESS ON PURPOSE. `sleeve_trades` memoises per (sleeve, window, cost
model), so the backtests are paid once and the 26 rebuilds are 26 replays.

    py -m sandbox.research._leave_one_out
"""
import platform

platform._wmi = None

import contextlib
import io
import json
import os

from sandbox.research import cfd_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research.fill_models import exness as lx

OUT_PATH = os.path.join(os.path.dirname(__file__), "leave_one_out.json")


def build(members):
    cs.TICK_COSTS = True
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.UNCAPPED = True
    cs.GLOBAL_SIZING_CAP = 1500.0
    cs.MAX_DD_CONCENTRATION = None
    cs.FAIR_CAP = False
    with contextlib.redirect_stdout(io.StringIO()):
        payload = cs.build(len(members), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
                           risk_scale=cs.CANON_RISK_SCALE,
                           gross_cap=cs.CANON_GROSS_CAP,
                           initial=cs.CANON_INITIAL,
                           members_exact=tuple(members))
    return payload["book"]


def main():
    lo = lx.tick_window_start(source="bars")
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    keep = ef.IS_END
    full = list(cs.BOOK)
    try:
        ef.IS_END = lo
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills", flush=True)
        base = build(full)
        print(f"whole book: {base['return_pct']:+.1f}%  "
              f"MTM dd {base['mtm_dd_pct']:.1f}%  "
              f"final ${base['final']:,.0f}\n", flush=True)

        rows = {}
        head = (f"{'dropped sleeve':30}{'return':>10}{'cost':>10}"
                f"{'MTM dd':>9}{'dd change':>11}{'final $':>10}")
        print(head, flush=True)
        print("-" * len(head), flush=True)
        for name in full:
            book = build([s for s in full if s != name])
            cost = base["return_pct"] - book["return_pct"]
            dd = book["mtm_dd_pct"] - base["mtm_dd_pct"]
            rows[name] = {"return_pct": book["return_pct"],
                          "cost_pp": cost,
                          "mtm_dd_pct": book["mtm_dd_pct"],
                          "dd_change_pp": dd,
                          "final": book["final"]}
            print(f"{name:30}{book['return_pct']:>9.1f}%{cost:>+10.1f}"
                  f"{book['mtm_dd_pct']:>8.1f}%{dd:>+11.1f}"
                  f"{book['final']:>10,.0f}", flush=True)

        print("\n`cost` is what KEEPING the sleeve is worth: positive means the "
              "book is worse\n  without it. Negative means dropping it helps. "
              "`dd change` is the same\n  sign convention on marked drawdown -- "
              "negative is an improvement.", flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                       "whole_book": {k: base.get(k) for k in
                                      ("return_pct", "mtm_dd_pct", "final",
                                       "trades")},
                       "dropped": rows}, handle, indent=2)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
