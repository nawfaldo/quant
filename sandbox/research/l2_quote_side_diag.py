"""Why does a +15.8 point conditional mean trade at -1.6 points?

Three things stand between the study and the strategy.  Each is removed in turn
so the responsible one is identified rather than guessed:

* the bracket -- a 40-point stop truncates the 60-minute return that was
  actually measured;
* position limits -- the study averaged every qualifying minute, the strategy
  takes one position at a time and so samples a biased subset;
* the entry cutoff -- the study used all of RTH, the strategy stops at 14:30.

The last row reproduces the study's own quantity as a PnL: every qualifying
minute, no bracket, no position limit, exit exactly 60 minutes later.
"""
from __future__ import annotations

import math

import numpy as np

from sandbox import execution, metrics
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research.l2_discovery import minute_of, session_of
from sandbox.research.l2_quote_side_strategy import (
    ENTRY_LAST_MINUTE, EX, HOLD_MINUTES, Z_CUT, panel, quote_side_z, ts_of,
)


def signals(rows, z, stop, target, one_at_a_time, cutoff):
    out, busy_until = [], -1
    for i, (ts, _bar, _f) in enumerate(rows):
        if minute_of(ts) > cutoff or math.isnan(z[i]):
            continue
        if one_at_a_time and i <= busy_until:
            continue
        if z[i] >= Z_CUT:
            side = LONG
        elif z[i] <= -Z_CUT:
            side = SHORT
        else:
            continue
        out.append(Signal(i, side, stop, target, HOLD_MINUTES))
        busy_until = i + HOLD_MINUTES
    return out


def score(label, rows, sigs):
    bars = [r[1] for r in rows]
    fills = execution.resolve(bars, sigs, EX)
    if not fills:
        print(f"{label:<52} no fills")
        return
    pts = [f.points for f in fills]
    gross = [p + EX.entry_cost for p in pts]
    stat = metrics.stats(execution.size(fills, EX), initial=EX.initial)
    print(f"{label:<52} {len(fills):>6} {sum(gross) / len(gross):>+9.3f} "
          f"{sum(pts) / len(pts):>+9.3f} {sum(pts):>+10.1f} "
          f"{stat['pos_months']:>3}/{stat['n_months']:<3}")


def main():
    rows = panel(ts_of("2025-02-12"), ts_of("2026-01-01"))
    z = quote_side_z(rows)
    print(f"panel {len(rows)} minutes, {len({session_of(r[0]) for r in rows})} sessions")
    print(f"\n{'variant':<52} {'trades':>6} {'gross/tr':>9} {'net/tr':>9} "
          f"{'net pts':>10} {'months':>7}")

    score("bracket 40/60, one-at-a-time, cutoff 14:30",
          rows, signals(rows, z, 40.0, 60.0, True, ENTRY_LAST_MINUTE))
    score("bracket 40/60, overlapping, cutoff 14:30",
          rows, signals(rows, z, 40.0, 60.0, False, ENTRY_LAST_MINUTE))
    score("no target, stop 200, one-at-a-time, cutoff 14:30",
          rows, signals(rows, z, 200.0, 0.0, True, ENTRY_LAST_MINUTE))
    score("no target, stop 200, overlapping, cutoff 14:30",
          rows, signals(rows, z, 200.0, 0.0, False, ENTRY_LAST_MINUTE))
    score("no target, stop 200, overlapping, all RTH",
          rows, signals(rows, z, 200.0, 0.0, False, 955))

    # the study's own quantity, computed directly, for comparison
    close = np.array([r[1][3] for r in rows], dtype=float)
    hi, lo = [], []
    for i in range(len(rows)):
        j = i + HOLD_MINUTES
        if j >= len(rows) or session_of(rows[j][0]) != session_of(rows[i][0]):
            continue
        if math.isnan(z[i]):
            continue
        if z[i] >= Z_CUT:
            hi.append(close[j] - close[i])
        elif z[i] <= -Z_CUT:
            lo.append(close[i] - close[j])
    both = hi + lo
    print(f"\nstudy quantity, both legs, no costs or bracket: "
          f"{len(both)} observations, {sum(both) / len(both):+.3f} points each")
    print(f"  long leg  {len(hi):>6} obs  {sum(hi) / len(hi):+.3f}")
    print(f"  short leg {len(lo):>6} obs  {sum(lo) / len(lo):+.3f}")


if __name__ == "__main__":
    main()
