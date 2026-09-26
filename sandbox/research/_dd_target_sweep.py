"""Find a canon configuration whose LIVE marked drawdown sits at or under a target.

SIZING FIRST, MEMBERSHIP SECOND, AND NOT IN THE SAME GRID. Searching members and
risk together lets the search buy a drawdown number by dropping whichever sleeve
happened to be open at the trough, and the result is then a statement about the
filter rather than about the book ([[selection-gate-manufactures-drawdown-and-consistency]],
[[usoil-intraday-fails-twice]]). So this pass moves only the two global dials --
`risk_scale` and `gross_cap` -- across the SAME 25 members, which cannot select
anything.

DRAWDOWN IS MEASURED ON LIVE FILLS. A target met on sealed fills is not met on
the account: execution moved canon's marked drawdown by 1.2pp and the old book's
by 4.7pp, always upward.

`gross_cap` bounds SIMULTANEOUS notional in multiples of equity, and it is the
only risk lever that has ever cleared a random-refusal control
([[gross-exposure-cap-beats-its-null]]). It matters more than usual now:
`MIN_LOT_ALWAYS` lets a minimum lot exceed the 4x per-trade ceiling, so nothing
else bounds how much can be open at once.

    py -m sandbox.research._dd_target_sweep
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

OUT_PATH = os.path.join(os.path.dirname(__file__), "dd_target_sweep.json")

TARGET_DD = 12.0
RISKS = (0.05, 0.07, 0.09, 0.11, 0.135, 0.16, 0.20)
CAPS = (None, 8.0, 6.0, 4.0, 3.0, 2.0, 1.5)


def build(risk, cap, initial):
    cs.TICK_COSTS = True
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.MIN_LOT_ALWAYS = True
    cs.UNCAPPED = True
    cs.GLOBAL_SIZING_CAP = 1500.0
    cs.MAX_DD_CONCENTRATION = None
    cs.FAIR_CAP = False
    with contextlib.redirect_stdout(io.StringIO()):
        payload = cs.build(len(cs.BOOK), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
                           risk_scale=risk, gross_cap=cap, initial=initial,
                           members_exact=cs.BOOK)
    return payload["book"]


def main():
    lo = lx.tick_window_start(source="bars")
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    keep = ef.IS_END
    initial = cs.CANON_INITIAL
    rows = {}
    try:
        ef.IS_END = lo
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills, ${initial:,.0f}, {len(cs.BOOK)} sleeves")
        print(f"target MTM drawdown <= {TARGET_DD:.0f}%\n", flush=True)
        head = (f"{'risk':>7}{'cap':>7}{'return':>10}{'MTM dd':>9}"
                f"{'closed':>9}{'final $':>10}{'trades':>8}{'refused':>9}"
                f"{'worst sleeve share':>20}")
        print(head, flush=True)
        print("-" * len(head), flush=True)
        for risk in RISKS:
            for cap in CAPS:
                book = build(risk, cap, initial)
                hot = max(book["by_sleeve"].items(),
                          key=lambda kv: kv[1].get("dd_event_share") or 0.0)
                share = 100 * (hot[1].get("dd_event_share") or 0.0)
                refused = sum(book["below_broker_minimum"].values())
                key = f"{risk}|{cap}"
                rows[key] = {"risk_scale": risk, "gross_cap": cap,
                             "return_pct": book["return_pct"],
                             "mtm_dd_pct": book["mtm_dd_pct"],
                             "max_dd_pct": book["max_dd_pct"],
                             "final": book["final"], "trades": book["trades"],
                             "refused": refused,
                             "refused_by_gross_cap":
                                 book.get("refused_by_gross_cap", 0),
                             "worst_sleeve": hot[0],
                             "worst_share_pct": share}
                mark = "  <-- meets target" if book["mtm_dd_pct"] <= TARGET_DD else ""
                print(f"{risk:>7}{str(cap):>7}{book['return_pct']:>9.1f}%"
                      f"{book['mtm_dd_pct']:>8.1f}%{book['max_dd_pct']:>8.1f}%"
                      f"{book['final']:>10,.0f}{book['trades']:>8}"
                      f"{refused:>9}{hot[0].split(':')[0]:>12}"
                      f"{share:>7.0f}%{mark}", flush=True)

        ok = {k: v for k, v in rows.items() if v["mtm_dd_pct"] <= TARGET_DD}
        print(f"\n{len(ok)} of {len(rows)} cells meet the target.", flush=True)
        if ok:
            print("Best return among them:", flush=True)
            for k, v in sorted(ok.items(),
                               key=lambda kv: -kv[1]["return_pct"])[:8]:
                print(f"  risk {v['risk_scale']:<6} cap {str(v['gross_cap']):<6}"
                      f"  {v['return_pct']:>8.1f}%  dd {v['mtm_dd_pct']:>5.1f}%"
                      f"  refused {v['refused']}", flush=True)
        else:
            best = min(rows.values(), key=lambda v: v["mtm_dd_pct"])
            print(f"None. Lowest reachable on sizing alone is "
                  f"{best['mtm_dd_pct']:.1f}% at risk {best['risk_scale']}, "
                  f"cap {best['gross_cap']} ({best['return_pct']:.1f}%).\n"
                  f"  That is the point at which membership has to move.",
                  flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                       "target_dd": TARGET_DD, "initial": initial,
                       "members": list(cs.BOOK), "cells": rows}, handle,
                      indent=2)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
