"""What `MIN_LOT_ALWAYS` buys and what it costs, at the balances actually held.

Two books, three balances, both with `de40:consecutive` seated -- the sleeve the
4x policy refuses at $400 and the broker margins for $10.66.

READ `refused` AND `peak gross` TOGETHER. Turning the policy off removes
refusals by definition; the question is what exposure replaces them. `peak
gross` is the largest simultaneous notional the book carried, in multiples of
equity at that moment, and it is the number the gross cap would bound if it were
on ([[gross-exposure-cap-beats-its-null]]).

    py -m sandbox.research._min_lot_always
"""
import platform

platform._wmi = None

import collections
import contextlib
import io

from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research import exness_live_execution as lx

BALANCES = (392.25, 450.0, 500.0)


def run(members, initial, always):
    cs.MIN_LOT_ALWAYS = always
    cs.TICK_COSTS = True
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.UNCAPPED = True
    cs.GLOBAL_SIZING_CAP = 1500.0
    cs.MAX_DD_CONCENTRATION = None
    cs.FAIR_CAP = False
    refused = collections.Counter()
    orig = cs.size

    def spy(sleeve, equity, trade, *a, **k):
        lots, money = orig(sleeve, equity, trade, *a, **k)
        if lots <= 0:
            refused[sleeve] += 1
        return lots, money

    cs.size = spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            book = cs.build(len(members), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
                            risk_scale=cs.CANON_RISK_SCALE,
                            gross_cap=cs.CANON_GROSS_CAP,
                            initial=initial,
                            members_exact=tuple(members))["book"]
    finally:
        cs.size = orig
        cs.MIN_LOT_ALWAYS = False
    return book, refused


def main():
    lo = lx.tick_window_start(source="bars")
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    members = [s for s in cs.BOOK if s != "uk100:xma_cross"]
    members.append("de40:consecutive")
    keep = ef.IS_END
    try:
        ef.IS_END = lo
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills, de40:consecutive seated\n")
        head = (f"{'balance':>9}{'policy':>12}{'return':>10}{'MTM dd':>9}"
                f"{'closed':>9}{'final $':>10}{'refused':>9}  refused where")
        print(head, flush=True)
        print("-" * (len(head) + 8), flush=True)
        for initial in BALANCES:
            for always in (False, True):
                book, refused = run(members, initial, always)
                where = ", ".join(f"{k.split(':')[0]}x{v}"
                                  for k, v in refused.most_common(3)) or "-"
                print(f"{initial:>9,.0f}{'min-lot on' if always else '4x cap':>12}"
                      f"{book['return_pct']:>9.1f}%{book['mtm_dd_pct']:>8.1f}%"
                      f"{book['max_dd_pct']:>8.1f}%{book['final']:>10,.0f}"
                      f"{sum(refused.values()):>9}  {where}", flush=True)
        print("\n`min-lot on` places a minimum lot whenever the BROKER can "
              "margin it, so the 4x\n  policy shrinks positions but never "
              "refuses one. Compare the drawdown\n  columns, not the return: "
              "extra notional shows up there first.", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
