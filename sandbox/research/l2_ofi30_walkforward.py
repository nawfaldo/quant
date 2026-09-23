"""Would optimizing `ofi_deep@30m` have helped, or does tuning just fit noise?

The 2026 holdout is already spent on the frozen configuration, so a fresh
whole-sample sweep would produce a number with nothing left to check it.  This
answers the question the honest way instead: anchored walk-forward, parameters
re-selected inside each fold's training window only, each choice scored once on
the fold's test window, and the stitched result compared against the frozen
configuration run over the same test windows.

That comparison is the point.  If re-selection cannot beat leaving the
parameters alone -- the do-nothing control -- then optimizing this strategy
buys nothing, which is the same conclusion `nq_hourly_delta_reversal.rs`
records for its own three bracket structures.

Selection rule is fixed in advance: highest total net points in the training
window among cells with at least 30 training trades, ties broken toward the
larger z-cut.  No look at test data feeds back into it.

One approximation, stated because it matters: fills are resolved once over the
whole panel per cell and then partitioned by entry time, rather than re-running
the occupancy logic per window.  Only trades straddling a fold boundary differ.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from sandbox import execution, metrics
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research.l2_discovery import (
    cumulative, minute_of, raw_signals, session_of, slot_normalize,
)
from sandbox.research.l2_ofi30_strategy import ENTRY_LAST_MINUTE
from sandbox.research.l2_quote_side_strategy import EX, panel, ts_of

WINDOWS = (15, 30, 60)
CUTS = (2.0, 2.5, 3.0)
HOLDS = (15, 30, 60)
STOP, TARGET = 200.0, 0.0
MIN_TRAIN_TRADES = 30
FROZEN = (30, 2.5, 30)           # the configuration already spent on 2026

FOLDS = [
    ("2025-08-01", "2025-10-01"),
    ("2025-10-01", "2025-12-01"),
    ("2025-12-01", "2026-02-01"),
    ("2026-02-01", "2026-04-01"),
    ("2026-04-01", "2026-06-01"),
    ("2026-06-01", "2026-07-17"),
]


def build(rows, z, cut, hold):
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
        out.append(Signal(entry, side, STOP, TARGET, hold))
        busy_until = entry + hold
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",
                        default="sandbox/results/l2_ofi30_walkforward.json")
    args = parser.parse_args()

    rows = panel(ts_of("2025-02-12"), ts_of("2026-07-17"))
    bars = [r[1] for r in rows]
    ofi = raw_signals(rows)["ofi_deep"]
    print(f"panel {len(rows)} minutes, "
          f"{len({session_of(r[0]) for r in rows})} sessions")

    zs = {w: slot_normalize(cumulative(ofi, rows, w), rows) for w in WINDOWS}
    print(f"normalised {len(zs)} windows")

    # resolve every cell once over the whole panel
    cells = {}
    for w in WINDOWS:
        for cut in CUTS:
            for hold in HOLDS:
                fills = execution.resolve(bars, build(rows, zs[w], cut, hold), EX)
                cells[(w, cut, hold)] = fills
    print(f"resolved {len(cells)} cells")

    def slice_fills(fills, lo, hi):
        return [f for f in fills if lo <= f.entry_ts < hi]

    def total(fills):
        return sum(f.points for f in fills)

    results, stitched_sel, stitched_frozen = [], [], []
    print(f"\n{'fold test window':<26} {'selected cell':<20} {'train pts':>10} "
          f"{'test n':>7} {'test pts':>10} {'frozen n':>9} {'frozen pts':>11}")
    for start, end in FOLDS:
        lo, hi = ts_of(start), ts_of(end)
        best, best_pts = None, None
        for key, fills in cells.items():
            train = slice_fills(fills, 0, lo)
            if len(train) < MIN_TRAIN_TRADES:
                continue
            pts = total(train)
            if best_pts is None or pts > best_pts or (pts == best_pts
                                                      and key[1] > best[1]):
                best, best_pts = key, pts
        if best is None:
            continue
        test = slice_fills(cells[best], lo, hi)
        frozen = slice_fills(cells[FROZEN], lo, hi)
        stitched_sel.extend(test)
        stitched_frozen.extend(frozen)
        results.append({"test_from": start, "test_to": end,
                        "selected": list(best), "train_points": best_pts,
                        "test_trades": len(test), "test_points": total(test),
                        "frozen_trades": len(frozen),
                        "frozen_points": total(frozen)})
        print(f"{start + ' to ' + end:<26} {str(best):<20} {best_pts:>10.1f} "
              f"{len(test):>7} {total(test):>10.1f} {len(frozen):>9} "
              f"{total(frozen):>11.1f}")

    def summarise(name, fills):
        if not fills:
            print(f"{name:<26} no trades")
            return None
        stat = metrics.stats(execution.size(fills, EX), initial=EX.initial)
        edge = total(fills) / len(fills)
        print(f"{name:<26} {len(fills):>7} trades  {edge:>+7.3f} pts/trade  "
              f"{total(fills):>+9.1f} pts  pf {stat['pf']:<6} "
              f"{stat['pos_months']}/{stat['n_months']} months  "
              f"mSharpe {stat['msharpe']}")
        return {"trades": len(fills), "edge": edge, "points": total(fills),
                "pf": stat["pf"], "pos_months": stat["pos_months"],
                "n_months": stat["n_months"], "msharpe": stat["msharpe"],
                "max_dd": stat["max_dd"], "months": stat["months"]}

    print()
    sel = summarise("stitched, re-selected", stitched_sel)
    fro = summarise("stitched, frozen (control)", stitched_frozen)

    picks = [tuple(r["selected"]) for r in results]
    print(f"\nselected cells: {picks}")
    print(f"distinct selections: {len(set(picks))} of {len(picks)} folds")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"grid": {"windows": WINDOWS, "cuts": CUTS, "holds": HOLDS},
                   "cells": len(cells), "frozen": FROZEN, "folds": results,
                   "stitched_selected": sel, "stitched_frozen": fro},
                  fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
