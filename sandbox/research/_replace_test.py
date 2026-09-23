"""What each eligible candidate does to the book that would seat it.

THE QUESTION IS NOT "IS THIS CELL GOOD". `_live_screen` already answered that,
and its answer is a pass/fail, not a ranking -- the pool was chosen on this same
window so the RETURNS there are conditioned
([[exness-survivor-pool-is-oos-conditioned]]).

The question here is what a cell does to a SHARED BALANCE it has to compete for.
Lots are recomputed at entry from the running equity, so a new member shrinks
every other member's size, and a cell with a fine standalone return can still
cost the book money. That is measurable and it is not conditioned in the same
way, because it is a difference between two books over the same trades.

BASELINE IS THE BOOK WITH THE TWO FAILURES ALREADY REMOVED. Seating a
replacement into a book that still contains `xniusd:cci` would measure the
replacement against a book nobody intends to run.

    py -m sandbox.research._replace_test
"""
import platform

platform._wmi = None

import contextlib
import io
import json
import os

from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_live_execution as lx

OUT_PATH = os.path.join(os.path.dirname(__file__), "replace_test.json")
SCREEN_PATH = os.path.join(os.path.dirname(__file__), "live_screen.json")

CUT = ("xniusd:cci", "uk100:gated_fade")


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
    with open(SCREEN_PATH, encoding="utf-8") as handle:
        screen = json.load(handle)["candidates"]
    eligible = sorted(k for k, v in screen.items()
                      if v["survives"] and not v["in_book"])

    lo = lx.tick_window_start(source="bars")
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    keep = ef.IS_END
    base_members = [s for s in cs.BOOK if s not in CUT]
    try:
        ef.IS_END = lo
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills", flush=True)
        print(f"baseline: canon minus {', '.join(CUT)} "
              f"({len(base_members)} sleeves)", flush=True)
        base = build(base_members)
        print(f"  {base['return_pct']:+.1f}%   MTM dd {base['mtm_dd_pct']:.1f}%"
              f"   final ${base['final']:,.0f}\n", flush=True)

        rows = {}
        head = (f"{'candidate added':30}{'return':>10}{'vs base':>9}"
                f"{'MTM dd':>9}{'vs base':>9}{'final $':>10}")
        print(head, flush=True)
        print("-" * len(head), flush=True)
        for name in eligible:
            book = build(base_members + [name])
            dr = book["return_pct"] - base["return_pct"]
            dd = book["mtm_dd_pct"] - base["mtm_dd_pct"]
            rows[name] = {"return_pct": book["return_pct"], "d_return_pp": dr,
                          "mtm_dd_pct": book["mtm_dd_pct"], "d_dd_pp": dd,
                          "final": book["final"],
                          "solo_live_pct": screen[name]["live_return_pct"],
                          "cost_bp": screen[name]["cost_bp"]}
            print(f"{name:30}{book['return_pct']:>9.1f}%{dr:>+9.1f}"
                  f"{book['mtm_dd_pct']:>8.1f}%{dd:>+9.1f}"
                  f"{book['final']:>10,.0f}", flush=True)

        print("\nRanked by return added, then by drawdown:", flush=True)
        best = sorted(rows.items(), key=lambda kv: -kv[1]["d_return_pp"])[:10]
        for name, row in best:
            print(f"  {name:30}{row['d_return_pp']:>+7.1f}pp   "
                  f"dd {row['d_dd_pp']:>+5.1f}pp   "
                  f"solo {row['solo_live_pct']:>+7.1f}%", flush=True)
        print("\nA cell that adds return AND lowers drawdown is the only "
              "unambiguous seat.\n  Anything that buys return with drawdown is "
              "a risk decision, not a free one.", flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                       "removed": list(CUT),
                       "baseline": {k: base.get(k) for k in
                                    ("return_pct", "mtm_dd_pct", "final",
                                     "trades")},
                       "added": rows}, handle, indent=2)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
