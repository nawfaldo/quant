"""Do the two drawdown drops survive a window they were not chosen on?

`btc:xma_ribbon` and `usdjpy:aroon` were removed on a greedy drawdown ranking
over 2025-01-02..2026-08-21 alone. That is the softest evidence in the current
canon: selecting members to minimise drawdown on one window partly manufactures
the band ([[selection-gate-manufactures-drawdown-and-consistency]]).

The Exness M1 import now reaches 2022-07-31 on the index and commodity CFDs --
the broker serves no deeper for those, so the common window is 2022-08 and NOT
the 2020 that was asked for. That still adds 2.5 years the drops never saw.

WHAT THIS IS AND IS NOT. 2022-2024 is the pool's own IN-SAMPLE period, so this
is not a holdout for the BOOK -- those sleeves were selected there. It is a
genuine out-of-window test of the two REMOVALS, which were decided on
2025-2026 only. A drop that helps on both windows is a different claim from one
that helps on the window that chose it.

Four books, both windows, sealed and live:

    25   canon before the drawdown drops
    24   without btc:xma_ribbon
    24   without usdjpy:aroon
    23   without both -- current canon

    py -m sandbox.research._drop_holdout
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

OUT_PATH = os.path.join(os.path.dirname(__file__), "drop_holdout.json")

DROPPED = ("btc:xma_ribbon", "usdjpy:aroon")
#: The window the drops were chosen on, so both can be printed side by side.
FITTED_START = "2025-01-02 09:00"


def build(members, lo, tick_costs):
    cs.TICK_COSTS = tick_costs
    cs.BROKER_STOPS = False
    cs.FORCE_MINIMUM_LOT = True
    cs.MIN_LOT_ALWAYS = True
    cs.UNCAPPED = True
    cs.GLOBAL_SIZING_CAP = 1500.0
    cs.MAX_DD_CONCENTRATION = None
    cs.FAIR_CAP = False
    cs._TICK_COST_CACHE.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        payload = cs.build(len(members), cs.MAX_DOWN_RHO, cs.MAX_LOSS_LIFT,
                           risk_scale=cs.CANON_RISK_SCALE,
                           gross_cap=cs.CANON_GROSS_CAP,
                           initial=cs.CANON_INITIAL,
                           members_exact=tuple(members))
    return payload["book"]


def main():
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    base23 = list(cs.BOOK)
    books = {
        "25 both kept": base23 + list(DROPPED),
        "24 -btc:xma_ribbon": base23 + ["usdjpy:aroon"],
        "24 -usdjpy:aroon": base23 + ["btc:xma_ribbon"],
        "23 both dropped": base23,
    }
    # The deep window is set by the members being replayed, so it is computed
    # from the WIDEST membership -- otherwise the 25-sleeve book would be
    # measured over a window derived from the 23-sleeve one and the comparison
    # would not be like for like.
    widest = sorted({m.split(":", 1)[0] for m in books["25 both kept"]
                     if m not in cs.EXTERNAL})
    deep = lx.tick_window_start(source="bars", symbols=widest)
    from datetime import datetime, timezone
    fitted = int(datetime.strptime(FITTED_START, "%Y-%m-%d %H:%M")
                 .replace(tzinfo=timezone.utc).timestamp())

    keep = ef.IS_END
    rows = {}
    try:
        for label, lo in (("deep 2022-2026", deep), ("fitted 2025-2026", fitted)):
            print(f"\n{'=' * 76}\n{label}: {lx._stamp(lo)} .. "
                  f"{lx._stamp(ef.OOS_END)} UTC\n{'=' * 76}", flush=True)
            head = (f"{'book':22}{'sealed ret':>12}{'sealed dd':>11}"
                    f"{'live ret':>11}{'live dd':>10}{'refused':>9}")
            print(head, flush=True)
            print("-" * len(head), flush=True)
            ef.IS_END = lo
            for name, members in books.items():
                sealed = build(members, lo, False)
                live = build(members, lo, True)
                rows[f"{label}|{name}"] = {
                    "sealed_return_pct": sealed["return_pct"],
                    "sealed_dd_pct": sealed["mtm_dd_pct"],
                    "live_return_pct": live["return_pct"],
                    "live_dd_pct": live["mtm_dd_pct"],
                    "refused": sum(live["below_broker_minimum"].values()),
                }
                r = rows[f"{label}|{name}"]
                print(f"{name:22}{r['sealed_return_pct']:>11.1f}%"
                      f"{r['sealed_dd_pct']:>10.1f}%"
                      f"{r['live_return_pct']:>10.1f}%"
                      f"{r['live_dd_pct']:>9.1f}%{r['refused']:>9}",
                      flush=True)

        print(f"\n{'=' * 76}\nVERDICT\n{'=' * 76}")
        for label in ("deep 2022-2026", "fitted 2025-2026"):
            a = rows[f"{label}|25 both kept"]
            b = rows[f"{label}|23 both dropped"]
            print(f"{label:18} dropping both: "
                  f"live dd {a['live_dd_pct']:.1f}% -> {b['live_dd_pct']:.1f}% "
                  f"({b['live_dd_pct'] - a['live_dd_pct']:+.1f}pp), "
                  f"return {a['live_return_pct']:.1f}% -> "
                  f"{b['live_return_pct']:.1f}% "
                  f"({b['live_return_pct'] - a['live_return_pct']:+.1f}pp)")
        print("\nA drop that lowers drawdown on BOTH windows is a property of "
              "the sleeve.\n  One that only helps on 2025-2026 is a property "
              "of the search that found it.")
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"deep_window": [lx._stamp(deep), lx._stamp(ef.OOS_END)],
                       "fitted_window": [FITTED_START, lx._stamp(ef.OOS_END)],
                       "dropped": list(DROPPED), "rows": rows}, handle,
                      indent=2)
        print(f"\nwrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
