"""Is the deep-window drawdown a property of the book, or of when you started?

A $400 cold start on 2022-08-01 drew 39.1% live / 25.1% sealed, while the same
book reached through 2020 drew 12.1% over a window that CONTAINS 2022-2026. The
difference is that a fresh small account is pinned at the broker minimum lot on
every sleeve, so its realised exposure has nothing to do with `risk_scale`
([[min-lot-pinned-sleeves-do-not-compound]]). By the time a 2020 start reaches
2022 it holds ~$2,000 and is sizing properly.

That explains ONE start date. This walks the start forward a quarter at a time
to separate the two candidate stories:

    if drawdown is high at EVERY early start   -> it is the cold start, general
    if it spikes only around 2022-08           -> that quarter is special

SEALED FILLS THROUGHOUT, on purpose. Exness serves no broker M1 before
2022-07-31 on the index and commodity CFDs, so a live run cannot be priced for
the early starts and mixing the two cost models across rows would make the
column incomparable. The live penalty is measured separately and is roughly
+1 to +14pp of drawdown depending on window.

THE WINDOWS ARE NESTED AND THE ORDER MATTERS. `sleeve_trades` memoises on the
lo/hi it is PASSED (None here), not on the resolved `ef.IS_END`, so a later
window silently reuses an earlier window's trade list. That is correct only when
the cached window is a SUPERSET, so the loop runs earliest start first and never
clears. Reversing the order would quietly produce nonsense.

    py -m sandbox.research._start_date_sensitivity
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

OUT_PATH = os.path.join(os.path.dirname(__file__),
    "start_date_sensitivity_live.json" if os.environ.get("LIVE","")=="1"
    else "start_date_sensitivity.json")

INITIAL = 500.0
TICK_COSTS = os.environ.get('LIVE','') == '1'
STARTS = [(y, m) for y in range(2020, 2026) for m in (1, 4, 7, 10)]


def build(members, initial, tick_costs=False):
    cs.TICK_COSTS = tick_costs
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
                           initial=initial, members_exact=tuple(members))
    return payload["book"]


def main():
    cs.MAPS_OVERRIDE = lx.maps_path("bars")
    # EXTERNAL nq sleeves excluded: `external_trades` caches per window and has
    # nothing for arbitrary sub-windows, which raises rather than returning an
    # empty book. Named so the row count is not mistaken for the whole canon.
    members = [m for m in cs.BOOK if m not in cs.EXTERNAL]
    keep = ef.IS_END
    rows = {}
    try:
        print(f"research canon, {len(members)} native sleeves "
              f"({len(cs.BOOK) - len(members)} EXTERNAL nq excluded)")
        print(f"${INITIAL:,.0f} cold start, {'LIVE' if TICK_COSTS else 'SEALED'} fills, "
              f"risk {cs.CANON_RISK_SCALE}, gross cap off")
        print(f"every run ends {lx._stamp(ef.OOS_END)}\n", flush=True)
        head = (f"{'start':>10}{'months':>8}{'return':>11}{'MTM dd':>9}"
                f"{'closed':>9}{'final $':>12}{'trades':>8}{'refused':>9}")
        print(head, flush=True)
        print("-" * len(head), flush=True)
        for year, month in STARTS:
            lo = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
            if lo >= ef.OOS_END:
                continue
            ef.IS_END = lo
            book = build(members, INITIAL, TICK_COSTS)
            months = round((ef.OOS_END - lo) / 86400 / 30.44)
            key = f"{year}-{month:02d}"
            rows[key] = {"months": months,
                         "return_pct": book["return_pct"],
                         "mtm_dd_pct": book["mtm_dd_pct"],
                         "max_dd_pct": book["max_dd_pct"],
                         "final": book["final"], "trades": book["trades"],
                         "refused": sum(book["below_broker_minimum"].values()),
                         "trough": str(book.get("mtm_dd_trough"))}
            r = rows[key]
            print(f"{key:>10}{months:>8}{r['return_pct']:>10.1f}%"
                  f"{r['mtm_dd_pct']:>8.1f}%{r['max_dd_pct']:>8.1f}%"
                  f"{r['final']:>12,.0f}{r['trades']:>8}{r['refused']:>9}",
                  flush=True)

        dds = [(v["mtm_dd_pct"], k) for k, v in rows.items()]
        worst = max(dds)
        best = min(dds)
        print(f"\nMTM drawdown across {len(rows)} start dates: "
              f"min {best[0]:.1f}% ({best[1]}), max {worst[0]:.1f}% "
              f"({worst[1]})", flush=True)
        early = [v["mtm_dd_pct"] for k, v in rows.items() if k < "2023"]
        late = [v["mtm_dd_pct"] for k, v in rows.items() if k >= "2023"]
        if early and late:
            print(f"  starts before 2023: mean {sum(early)/len(early):.1f}%   "
                  f"2023 and later: mean {sum(late)/len(late):.1f}%",
                  flush=True)
        print("\nA drawdown that is high at every early start is the cold "
              "start.\n  One that spikes at a single quarter is that quarter.",
              flush=True)
        with open(OUT_PATH, "w", encoding="utf-8") as handle:
            json.dump({"initial": INITIAL, "members": members,
                       "risk_scale": cs.CANON_RISK_SCALE,
                       "end": lx._stamp(ef.OOS_END), "starts": rows},
                      handle, indent=2)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        ef.IS_END = keep


if __name__ == "__main__":
    raise SystemExit(main())
