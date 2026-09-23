"""Frozen `ofi_deep@30m` evaluated on the 2026 holdout, once.

Everything about the configuration is fixed by the caller's arguments before
this runs, and nothing here reads 2026 for selection.  The z-score warm-up
deliberately spans the whole history: `slot_normalize` only ever looks at
sessions strictly earlier than the one it is scoring, so loading 2025 as well
gives every 2026 minute a full 20-session normalisation window without letting
anything from the future in.  Signals are only built for timestamps inside the
holdout window.

Databento coverage ends 2026-07-16; the Bookmap handoff after that has a
different feature distribution and is excluded.
"""
from __future__ import annotations

import argparse
import json

from sandbox.research.l2_discovery import session_of
from sandbox.research.l2_ofi30_strategy import ENTRY_LAST_MINUTE, ofi_z
from sandbox.research.l2_ofi30_strategy import build as build_signals
from sandbox.research.l2_quote_side_strategy import (
    panel, report, trailing_range, ts_of,
)

WARMUP_FROM = "2025-02-12"
HOLDOUT_FROM = "2026-01-01"
HOLDOUT_TO = "2026-07-17"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cut", type=float, required=True)
    parser.add_argument("--stop", type=float, default=200.0)
    parser.add_argument("--target", type=float, default=0.0)
    parser.add_argument("--out", default="sandbox/results/l2_ofi30_holdout.json")
    args = parser.parse_args()

    rows = panel(ts_of(WARMUP_FROM), ts_of(HOLDOUT_TO))
    z = ofi_z(rows)
    vol = trailing_range(rows)

    start = ts_of(HOLDOUT_FROM)
    signals = [s for s in build_signals(rows, z, args.cut, args.stop, args.target)
               if rows[s.index][0] >= start]
    holdout_sessions = len({session_of(r[0]) for r in rows if r[0] >= start})
    print(f"panel {len(rows)} minutes; holdout {holdout_sessions} sessions "
          f"({HOLDOUT_FROM} to {HOLDOUT_TO}), {len(signals)} signals")

    label = (f"2026 holdout (read once): |z|>={args.cut}, stop {args.stop:.0f}"
             + (f"/target {args.target:.0f}" if args.target else "/no target"))
    got = report(label, rows, signals, vol, cutoff=ENTRY_LAST_MINUTE + 1)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"frozen": {"cut": args.cut, "stop": args.stop,
                              "target": args.target},
                   "holdout_from": HOLDOUT_FROM, "holdout_to": HOLDOUT_TO,
                   "sessions": holdout_sessions, "result": got},
                  fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
