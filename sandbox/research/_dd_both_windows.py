"""Find a configuration under a drawdown target on BOTH windows at once.

WHY BALANCE IS THE FIRST AXIS AND NOT RISK. On the deep window the 25-sleeve
book draws 39.1% at $400 and 14.5% at $2,000, with the trough seven weeks in.
That is not the book misbehaving -- it is 23 sleeves each pinned at the broker
MINIMUM lot on an account too small to size any of them properly, so the
realised exposure has nothing to do with `risk_scale`. The same reason makes
risk nearly inert down there: uncapped drawdown was 14.6% at every risk from
0.05 to 0.135 on the fitted window ([[min-lot-pinned-sleeves-do-not-compound]]).

Once the balance is large enough that positions are sized rather than floored,
`risk_scale` starts working again. So the grid is balance x risk, and both
windows are required to pass -- a cell that only clears on 2025-2026 is the
artifact this search exists to avoid ([[selection-gate-manufactures-drawdown-and-consistency]]).

MEMBERS ARE HELD FIXED at the 25 that survived the execution swap, INCLUDING
`btc:xma_ribbon` and `usdjpy:aroon`. Their removal cost 512pp of return for
0.9pp of drawdown on the deep window and is being treated as refuted.

    py -m sandbox.research._dd_both_windows
"""
import platform

platform._wmi = None

import contextlib
import io
import json
import os
from datetime import datetime, timezone

from sandbox.research import cfd_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research.fill_models import exness as lx

OUT_PATH = os.path.join(os.path.dirname(__file__), "dd_both_windows.json")

TARGET_DD = 13.0
BALANCES = (400.0, 800.0, 1200.0, 1600.0, 2000.0, 3000.0)
RISKS = (0.05, 0.09, 0.135, 0.20)
FITTED_START = "2025-01-02 09:00"


def build(members, initial, risk):
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
                           risk_scale=risk, gross_cap=cs.CANON_GROSS_CAP,
                           initial=initial, members_exact=tuple(members))
    return payload["book"]


def main():
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    members = list(cs.BOOK) + ["btc:xma_ribbon", "usdjpy:aroon"]
    widest = sorted({m.split(":", 1)[0] for m in members
                     if m not in cs.EXTERNAL})
    deep = lx.tick_window_start(source="bars", symbols=widest)
    fitted = int(datetime.strptime(FITTED_START, "%Y-%m-%d %H:%M")
                 .replace(tzinfo=timezone.utc).timestamp())
    keep = ef.IS_END
    rows = {}
    try:
        print(f"deep   {lx._stamp(deep)} .. {lx._stamp(ef.OOS_END)}")
        print(f"fitted {FITTED_START} .. {lx._stamp(ef.OOS_END)}")
        print(f"{len(members)} sleeves, target MTM dd <= {TARGET_DD:.0f}% on "
              f"BOTH, live fills\n", flush=True)
        head = (f"{'balance':>9}{'risk':>7}{'deep ret':>11}{'deep dd':>10}"
                f"{'fit ret':>10}{'fit dd':>9}{'refused':>9}  verdict")
        print(head, flush=True)
        print("-" * (len(head) + 6), flush=True)
        for initial in BALANCES:
            for risk in RISKS:
                ef.IS_END = deep
                d = build(members, initial, risk)
                ef.IS_END = fitted
                f = build(members, initial, risk)
                ok = (d["mtm_dd_pct"] <= TARGET_DD
                      and f["mtm_dd_pct"] <= TARGET_DD)
                refused = (sum(d["below_broker_minimum"].values())
                           + sum(f["below_broker_minimum"].values()))
                rows[f"{initial}|{risk}"] = {
                    "initial": initial, "risk_scale": risk,
                    "deep_return_pct": d["return_pct"],
                    "deep_dd_pct": d["mtm_dd_pct"],
                    "deep_final": d["final"],
                    "fitted_return_pct": f["return_pct"],
                    "fitted_dd_pct": f["mtm_dd_pct"],
                    "fitted_final": f["final"],
                    "refused": refused, "passes": ok}
                print(f"{initial:>9,.0f}{risk:>7}{d['return_pct']:>10.1f}%"
                      f"{d['mtm_dd_pct']:>9.1f}%{f['return_pct']:>9.1f}%"
                      f"{f['mtm_dd_pct']:>8.1f}%{refused:>9}"
                      f"{'  <-- PASSES' if ok else ''}", flush=True)

        good = {k: v for k, v in rows.items() if v["passes"]}
        print(f"\n{len(good)} of {len(rows)} cells clear {TARGET_DD:.0f}% on "
              f"both windows.", flush=True)
        if good:
            print("Ranked by deep return:", flush=True)
            for k, v in sorted(good.items(),
                               key=lambda kv: -kv[1]["deep_return_pct"]):
                print(f"  ${v['initial']:>6,.0f} risk {v['risk_scale']:<6} "
                      f"deep {v['deep_return_pct']:>8.1f}% dd "
                      f"{v['deep_dd_pct']:>5.1f}%   fitted "
                      f"{v['fitted_return_pct']:>7.1f}% dd "
                      f"{v['fitted_dd_pct']:>5.1f}%", flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"target_dd": TARGET_DD, "members": members,
                       "deep_window": [lx._stamp(deep), lx._stamp(ef.OOS_END)],
                       "fitted_window": [FITTED_START, lx._stamp(ef.OOS_END)],
                       "cells": rows}, handle, indent=2)
        print(f"\nwrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
