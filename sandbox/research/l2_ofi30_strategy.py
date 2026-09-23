"""The one survivor of the long-horizon search, run as a strategy.

`ofi_deep` summed over 30 minutes, z-scored per minute-of-day, held 30 minutes,
was the only cell whose long-short estimate rose monotonically with the entry
threshold while keeping ~200 sessions behind it: +7.7 / +14.5 / +18.9 / +20.8 /
+21.2 points at |z| cuts of 1.5 / 2.0 / 2.5 / 3.0 / 3.5, t peaking at 2.60.

That is a conditional mean, not a PnL.  The last time a conditional mean of this
size was taken at face value it traded at -1.56 points against a -0.10 control,
so it goes through `execution.resolve` at 0.2 spread and is scored against
random entries drawn from the same volatility decile.

2025 only.  The 2026 holdout is not read here.
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np

from sandbox.execution import LONG, SHORT, Signal
from sandbox.research.l2_discovery import (
    cumulative, minute_of, raw_signals, session_of, slot_normalize,
)
from sandbox.research.l2_quote_side_strategy import (
    matched_control, panel, report, trailing_range, ts_of,
)

WINDOW = 30
HOLD = 30
ENTRY_LAST_MINUTE = 900          # 15:00, so a 30-minute hold finishes in RTH
CUTS = (1.5, 2.0, 2.5, 3.0)
BRACKETS = ((25.0, 40.0), (40.0, 60.0), (200.0, 0.0))


def ofi_z(rows):
    raw = raw_signals(rows)["ofi_deep"]
    return slot_normalize(cumulative(raw, rows, WINDOW), rows)


def build(rows, z, cut, stop, target):
    """Signal completes in minute `i`; entry is the open of minute `i + 1`.

    `z[i]` uses minute `i`'s completed order flow, so entering at minute `i`'s
    own open would trade on the bar the signal is made of.
    """
    out, busy_until = [], -1
    for i, (ts, _bar, _f) in enumerate(rows):
        entry = i + 1
        if i <= busy_until or minute_of(ts) > ENTRY_LAST_MINUTE:
            continue
        if entry >= len(rows) or session_of(rows[entry][0]) != session_of(ts):
            continue
        if math.isnan(z[i]):
            continue
        if z[i] >= cut:
            side = LONG
        elif z[i] <= -cut:
            side = SHORT
        else:
            continue
        out.append(Signal(entry, side, stop, target, HOLD))
        busy_until = entry + HOLD
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_ofi30.json")
    args = parser.parse_args()

    rows = panel(ts_of("2025-02-12"), ts_of("2026-01-01"))
    print(f"in-sample panel: {len(rows)} minutes, "
          f"{len({session_of(r[0]) for r in rows})} sessions")
    z = ofi_z(rows)
    vol = trailing_range(rows)

    out = {"window": WINDOW, "hold": HOLD, "runs": []}
    for cut in CUTS:
        for stop, target in BRACKETS:
            label = (f"|z|>={cut}, stop {stop:.0f}"
                     + (f"/target {target:.0f}" if target else "/no target"))
            # entries land one minute after the signal completes
            got = report(label, rows, build(rows, z, cut, stop, target), vol,
                         cutoff=ENTRY_LAST_MINUTE + 1)
            if got:
                out["runs"].append({"cut": cut, "stop": stop, "target": target,
                                    **{k: v for k, v in got.items()
                                       if k != "stats"},
                                    "pnl": got["stats"]["pnl"],
                                    "pf": got["stats"]["pf"],
                                    "pos_months": got["stats"]["pos_months"]})

    print(f"\n{'variant':<40} {'trades':>7} {'net/tr':>9} {'control':>9} "
          f"{'edge':>8} {'z':>7}")
    for r in out["runs"]:
        tgt = f"/{r['target']:.0f}" if r["target"] else "/none"
        name = f"|z|>={r['cut']}, {r['stop']:.0f}{tgt}"
        print(f"{name:<40} {r['trades']:>7} {r['edge']:>+9.3f} "
              f"{r['control_mean']:>+9.3f} {r['edge'] - r['control_mean']:>+8.3f} "
              f"{r['z']:>+7.2f}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
