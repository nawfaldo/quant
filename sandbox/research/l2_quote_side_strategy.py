"""Quote-side pressure as a tradeable strategy, scored against a matched control.

Signal: `(bid_add - ask_add) / total_adds` summed over the trailing 5 minutes,
z-scored against the same minute-of-day over the prior 20 sessions.  Long when
the quoting activity leans to the bid, short when it leans to the ask.

The conditional-mean study said +15.8 points long-short at a 60-minute horizon.
That is not a PnL: it trades both extremes simultaneously, ignores position
limits, and pays nothing.  This runs the signal through the same
`execution.resolve` every other strategy in the repo uses -- one position at a
time, 0.2 spread charged at entry -- and scores it against random entries drawn
from the same volatility bucket, because a no-information entry does not score
zero.

2025 is in-sample.  2026 is read only when `--holdout` is passed, once.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from sandbox import data, execution, metrics
from sandbox.data import TS, C, H, L
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research.l2_discovery import (
    EPS, RTH_END, RTH_START, cumulative, minute_of, session_of, slot_normalize,
)

WINDOW = 5
Z_CUT = 1.5
HOLD_MINUTES = 60
STOP, TARGET = 40.0, 60.0
ENTRY_LAST_MINUTE = 870          # 14:30, so a 60-minute hold finishes inside RTH
VOL_LOOKBACK = 5
EX = execution.Execution(initial=1000.0, session_end_min=955)
CONTROL_DRAWS = 200


def panel(from_ts, to_ts, symbol="nq"):
    bars = data.load_bars("level_two", symbol)
    feats = data.load_l2_features(symbol)
    rows = []
    for bar in bars:
        ts = bar[TS]
        if not (from_ts <= ts < to_ts):
            continue
        if not (RTH_START <= minute_of(ts) <= RTH_END):
            continue
        f = feats.get(ts)
        if f is None or not f["book_valid"]:
            continue
        rows.append((ts, bar, f))
    rows.sort(key=lambda r: r[0])
    return rows


def quote_side_z(rows):
    raw = np.zeros(len(rows))
    for i, (_ts, _bar, f) in enumerate(rows):
        adds = f["bid_add_volume"] + f["ask_add_volume"]
        raw[i] = (f["bid_add_volume"] - f["ask_add_volume"]) / (adds + EPS)
    return slot_normalize(cumulative(raw, rows, WINDOW), rows)


def build_signals(rows, z):
    """One position at a time: a new entry is skipped while one is open."""
    signals = []
    busy_until = -1
    for i, (ts, _bar, _f) in enumerate(rows):
        if i <= busy_until or minute_of(ts) > ENTRY_LAST_MINUTE:
            continue
        if math.isnan(z[i]):
            continue
        if z[i] >= Z_CUT:
            side = LONG
        elif z[i] <= -Z_CUT:
            side = SHORT
        else:
            continue
        signals.append(Signal(i, side, STOP, TARGET, HOLD_MINUTES))
        busy_until = i + HOLD_MINUTES
    return signals


def trailing_range(rows):
    vol = [None] * len(rows)
    for i in range(VOL_LOOKBACK, len(rows)):
        window = rows[i - VOL_LOOKBACK:i]
        if session_of(window[0][0]) != session_of(rows[i][0]):
            continue
        vol[i] = (max(b[1][H] for b in window) - min(b[1][L] for b in window))
    return vol


def matched_control(rows, bars, signals, vol, seed=99, cutoff=ENTRY_LAST_MINUTE):
    """Random entries drawn from the same volatility decile, same occupancy.

    `cutoff` must match the entry cutoff the signals were built with, or the
    control pool will not contain the minutes the strategy actually traded.
    """
    eligible = [i for i in range(len(rows))
                if vol[i] is not None and minute_of(rows[i][0]) <= cutoff]
    finite = sorted(eligible, key=lambda i: vol[i])
    decile_of = {}
    for rank, i in enumerate(finite):
        decile_of[i] = min(9, rank * 10 // max(1, len(finite)))
    pool = defaultdict(list)
    for i in finite:
        pool[decile_of[i]].append(i)

    wanted = defaultdict(int)
    longs = 0
    for s in signals:
        if vol[s.index] is None:
            continue
        wanted[decile_of[s.index]] += 1
        longs += s.side == LONG

    rng = random.Random(seed)
    draws = []
    for _ in range(CONTROL_DRAWS):
        picks = []
        for decile, count in wanted.items():
            picks.extend(rng.sample(pool[decile], min(count, len(pool[decile]))))
        picks.sort()
        fake, busy_until, k = [], -1, 0
        for i in picks:
            if i <= busy_until:
                continue
            side = LONG if k < longs else SHORT
            fake.append(Signal(i, side, STOP, TARGET, HOLD_MINUTES))
            busy_until = i + HOLD_MINUTES
            k += 1
        fills = execution.resolve(bars, fake, EX)
        if fills:
            draws.append(sum(f.points for f in fills) / len(fills))
    return draws


def report(label, rows, signals, vol, cutoff=ENTRY_LAST_MINUTE):
    bars = [r[1] for r in rows]
    fills = execution.resolve(bars, signals, EX)
    if not fills:
        print(f"{label}: no fills")
        return None
    pts = [f.points for f in fills]
    edge = sum(pts) / len(pts)
    stat = metrics.stats(execution.size(fills, EX), initial=EX.initial)
    draws = matched_control(rows, bars, signals, vol, cutoff=cutoff)
    cmean = statistics.mean(draws)
    csd = statistics.pstdev(draws)
    z = (edge - cmean) / csd if csd > 1e-9 else 0.0
    beat = 100 * sum(1 for d in draws if d < edge) / len(draws)

    print(f"\n=== {label}")
    print(f"  trades            {len(fills)}")
    print(f"  net points/trade  {edge:+.3f}")
    print(f"  total net points  {sum(pts):+.1f}")
    print(f"  sized pnl ($1000) {stat['pnl']:+.2f}   pf {stat['pf']}   "
          f"maxdd {stat['max_dd']}")
    print(f"  positive months   {stat['pos_months']}/{stat['n_months']}   "
          f"monthly sharpe {stat['msharpe']}")
    print(f"  vol-matched control {cmean:+.3f} +/- {csd:.3f} pts/trade")
    print(f"  edge vs control   {edge - cmean:+.3f} pts/trade  "
          f"(z {z:+.2f}, beats {beat:.1f}% of {len(draws)} draws)")
    print(f"  months            {stat['months']}")
    return {"trades": len(fills), "edge": edge, "control_mean": cmean,
            "control_sd": csd, "z": z, "beat_pct": beat, "stats": stat}


def ts_of(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", action="store_true",
                        help="also evaluate 2026 (read once, after freezing)")
    parser.add_argument("--out", default="sandbox/results/l2_quote_side.json")
    args = parser.parse_args()

    out = {"window": WINDOW, "z_cut": Z_CUT, "hold": HOLD_MINUTES,
           "stop": STOP, "target": TARGET, "cost": EX.entry_cost}

    rows = panel(ts_of("2025-02-12"), ts_of("2026-01-01"))
    print(f"in-sample panel: {len(rows)} minutes, "
          f"{len({session_of(r[0]) for r in rows})} sessions")
    z = quote_side_z(rows)
    vol = trailing_range(rows)
    out["in_sample_2025"] = report("2025 in-sample", rows, build_signals(rows, z), vol)

    if args.holdout:
        rows26 = panel(ts_of("2026-01-01"), ts_of("2026-07-17"))
        print(f"\nholdout panel: {len(rows26)} minutes, "
              f"{len({session_of(r[0]) for r in rows26})} sessions")
        z26 = quote_side_z(rows26)
        vol26 = trailing_range(rows26)
        out["holdout_2026"] = report("2026 holdout (read once)", rows26,
                                     build_signals(rows26, z26), vol26)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
