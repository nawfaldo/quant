"""Greedy removal toward a drawdown target, at canon sizing, on live fills.

WHY MEMBERSHIP AND NOT SIZING. `_dd_target_sweep` moved both global dials across
the same 25 members and found the target reachable only at 248.8% against
canon's 852.7% -- 604 points for 3.5 of drawdown. It also found the two dials
are poor levers here: uncapped drawdown is 14.6% at EVERY risk from 0.05 to
0.135 (min-lot pinning means cutting risk cannot shrink a position already at
the broker minimum), and a gross cap of 8, 6 or 4 makes drawdown WORSE than no
cap at all.

WHAT THIS DOES. At fixed canon sizing, drop the one sleeve whose removal buys
the most drawdown per point of return surrendered, then repeat. Ratio and not
raw drawdown, because dropping the biggest earner always cuts drawdown and
always costs more than it is worth.

READ THIS BEFORE BELIEVING THE RESULT. Choosing members to hit a drawdown band
on one window MANUFACTURES that band -- the survivors are the sleeves that
happened not to be open at this window's trough, which is a statement about the
filter and not about the book
([[selection-gate-manufactures-drawdown-and-consistency]]). The window is also
inside the survivor pool's own screening period
([[exness-survivor-pool-is-oos-conditioned]]). So this produces a CANDIDATE
book, and the drawdown number attached to it is not evidence until it is
reproduced somewhere this search did not touch.

    py -m sandbox.research._dd_greedy_drop
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

OUT_PATH = os.path.join(os.path.dirname(__file__), "dd_greedy_drop.json")

TARGET_DD = 12.0
#: Stop rather than keep cutting: below this the book is too few sleeves to be
#: a book, and one sleeve's bad month becomes the whole record.
MIN_MEMBERS = 16


def build(members):
    cs.TICK_COSTS = True
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.MIN_LOT_ALWAYS = True
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
    members = list(cs.BOOK)
    trail = []
    try:
        ef.IS_END = lo
        book = build(members)
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills, canon sizing")
        print(f"start: {len(members)} sleeves  {book['return_pct']:+.1f}%  "
              f"MTM dd {book['mtm_dd_pct']:.1f}%\n", flush=True)

        while book["mtm_dd_pct"] > TARGET_DD and len(members) > MIN_MEMBERS:
            best = None
            for name in members:
                trial = build([m for m in members if m != name])
                d_dd = book["mtm_dd_pct"] - trial["mtm_dd_pct"]
                d_ret = book["return_pct"] - trial["return_pct"]
                if d_dd <= 0:
                    continue
                # Return surrendered per point of drawdown bought. Lower wins.
                cost = d_ret / d_dd
                if best is None or cost < best[0]:
                    best = (cost, name, trial, d_dd, d_ret)
            if best is None:
                print("no single removal lowers drawdown -- stopping",
                      flush=True)
                break
            cost, name, trial, d_dd, d_ret = best
            members = [m for m in members if m != name]
            book = trial
            trail.append({"dropped": name, "members": len(members),
                          "return_pct": book["return_pct"],
                          "mtm_dd_pct": book["mtm_dd_pct"],
                          "d_dd_pp": d_dd, "d_return_pp": d_ret,
                          "return_per_dd": cost})
            print(f"drop {name:30} -> {len(members):>2} sleeves  "
                  f"{book['return_pct']:>8.1f}%  dd {book['mtm_dd_pct']:>5.1f}%"
                  f"   (-{d_dd:.1f}pp dd for {d_ret:+.1f}pp return)",
                  flush=True)

        print(f"\nfinal: {len(members)} sleeves  {book['return_pct']:+.1f}%  "
              f"MTM dd {book['mtm_dd_pct']:.1f}%  closed {book['max_dd_pct']:.1f}%"
              f"  refused {sum(book['below_broker_minimum'].values())}")
        hot = max(book["by_sleeve"].items(),
                  key=lambda kv: kv[1].get("dd_event_share") or 0.0)
        print(f"worst drawdown concentration: {hot[0]} at "
              f"{100 * (hot[1].get('dd_event_share') or 0.0):.0f}%")
        print("\nremaining members:")
        for m in members:
            print(f"  {m}")
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                       "target_dd": TARGET_DD, "trail": trail,
                       "members": members,
                       "final": {k: book.get(k) for k in
                                 ("return_pct", "mtm_dd_pct", "max_dd_pct",
                                  "final", "trades")}}, handle, indent=2)
        print(f"\nwrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
