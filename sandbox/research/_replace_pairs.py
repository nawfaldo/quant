"""Two out, two in: the actual replacement, tested as PAIRS.

WHY A PAIR AND NOT TWO SINGLES. `_replace_test` asked what one candidate does to
a 23-sleeve book. That is an ADDITION, and it cannot answer a replacement for two
removed members: the book ends up a sleeve short, and -- the part that matters --
two new members are not independent. Lots are recomputed at entry from a shared
balance, so each new sleeve shrinks the other's size, and two cells that each
look good alone can collide on the same symbol, the same hours, or the same
drawdown. The pair has to be priced as a pair.

THE SHORTLIST IS A COMPROMISE AND IT IS NAMED. Every pair over the whole
eligible pool is 861 books. So pairs are drawn from the cells that cleared a
bar as singles, which is a greedy narrowing: the best pair COULD in principle be
built from two cells that are mediocre alone and complementary together, and
this will not find it. What it will find is whether the obvious pairs collide.

    py -m sandbox.research._replace_pairs
"""
import platform

platform._wmi = None

import contextlib
import io
import itertools
import json
import os

from sandbox.research import cfd_families as ef
from sandbox.research import exness_combined_strategies as cs
from sandbox.research.fill_models import exness as lx

OUT_PATH = os.path.join(os.path.dirname(__file__), "replace_pairs.json")
SINGLE_PATH = os.path.join(os.path.dirname(__file__), "replace_test.json")

CUT = ("xniusd:cci", "uk100:gated_fade")

#: A cell joins the shortlist if, added alone, it paid for itself and did not
#: buy that return with much drawdown. Deliberately loose -- the point is to
#: catch collisions among plausible seats, not to pre-rank them.
MIN_SINGLE_RETURN_PP = 25.0
MAX_SINGLE_DD_PP = 2.5


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
    with open(SINGLE_PATH, encoding="utf-8") as handle:
        singles = json.load(handle)["added"]
    shortlist = sorted(k for k, v in singles.items()
                       if v["d_return_pp"] >= MIN_SINGLE_RETURN_PP
                       and v["d_dd_pp"] <= MAX_SINGLE_DD_PP)

    lo = lx.tick_window_start(source="bars")
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    keep = ef.IS_END
    base_members = [s for s in cs.BOOK if s not in CUT]
    pairs = list(itertools.combinations(shortlist, 2))
    try:
        ef.IS_END = lo
        print(f"window {lx._stamp(lo)} .. {lx._stamp(ef.OOS_END)} UTC, "
              f"LIVE fills", flush=True)
        print(f"out: {', '.join(CUT)}", flush=True)
        print(f"shortlist {len(shortlist)} cells -> {len(pairs)} pairs\n",
              flush=True)
        canon = build(list(cs.BOOK))
        base = build(base_members)
        print(f"canon as-is (25)      {canon['return_pct']:+8.1f}%   "
              f"MTM dd {canon['mtm_dd_pct']:.1f}%", flush=True)
        print(f"minus two   (23)      {base['return_pct']:+8.1f}%   "
              f"MTM dd {base['mtm_dd_pct']:.1f}%\n", flush=True)

        rows = {}
        head = (f"{'pair (two in)':58}{'return':>10}{'vs 23':>9}"
                f"{'MTM dd':>9}{'vs 23':>8}")
        print(head, flush=True)
        print("-" * len(head), flush=True)
        for a, b in pairs:
            book = build(base_members + [a, b])
            key = f"{a} + {b}"
            dr = book["return_pct"] - base["return_pct"]
            dd = book["mtm_dd_pct"] - base["mtm_dd_pct"]
            # The two singles' effects added together. The gap between this and
            # the pair's own delta IS the interaction -- negative means they
            # compete, positive means they complement.
            additive = (singles[a]["d_return_pp"] + singles[b]["d_return_pp"])
            rows[key] = {"members": [a, b], "return_pct": book["return_pct"],
                         "d_return_pp": dr, "mtm_dd_pct": book["mtm_dd_pct"],
                         "d_dd_pp": dd, "final": book["final"],
                         "additive_pp": additive,
                         "interaction_pp": dr - additive}
            print(f"{key:58}{book['return_pct']:>9.1f}%{dr:>+9.1f}"
                  f"{book['mtm_dd_pct']:>8.1f}%{dd:>+8.1f}", flush=True)

        print("\nBest pairs by return, with the interaction they carry:",
              flush=True)
        for key, row in sorted(rows.items(),
                               key=lambda kv: -kv[1]["d_return_pp"])[:12]:
            print(f"  {key:58}{row['d_return_pp']:>+8.1f}pp  "
                  f"dd {row['d_dd_pp']:>+5.1f}pp  "
                  f"interaction {row['interaction_pp']:>+7.1f}pp", flush=True)
        print("\n`interaction` is the pair's gain minus the two singles' gains. "
              "Negative means\n  they compete for the same balance and the pair "
              "is worth less than it looks.", flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"window": [lx._stamp(lo), lx._stamp(ef.OOS_END)],
                       "removed": list(CUT), "shortlist": shortlist,
                       "canon": {k: canon.get(k) for k in
                                 ("return_pct", "mtm_dd_pct", "final")},
                       "baseline": {k: base.get(k) for k in
                                    ("return_pct", "mtm_dd_pct", "final")},
                       "pairs": rows}, handle, indent=2)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
