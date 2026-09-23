"""Fixed-point brackets and clock exits, for Hourly Delta Reversal and Deep OFI.

`microprice_exit_families` asked two questions of one strategy. This asks the
same two of the other two level-two strategies in `live_trade/src/strategies/idk`,
because neither has ever been tested against them either: both compile a bracket
scaled by the 20-session ATR, with a time stop that only fires when that bracket
has not.

  HOLD   No stop, no target, leave after exactly N minutes. With no bracket
         there is no path dependence, so the mean points per trade *is* the
         entry's directional information at horizon N.

  FIXED  A constant-point stop and target, the same distance in every regime.

WHAT IS HELD FIXED. Only the exit changes. Entries come from the registered
replica at its compiled parameters -- the same signals the Rust engine takes --
so a difference in the table is a difference in exit policy and nothing else.

THE TWO STRATEGIES ARE NOT STRUCTURED ALIKE, and the difference matters here.
`nq_hourly_delta_reversal.rs` holds a `Vec<OpenPosition>` and stacks: a new hour
may open a position while an earlier one is still live. `nq_ofi_momentum.rs`
holds at most one. Lengthening the hold therefore does very different things to
them -- it multiplies HDR's concurrent exposure and merely idles OFI -- so each
keeps its own convention, and OFI's single-position rule is re-applied per exit
cell by greedy selection over the resolved exits.

CANDIDATE POOL. OFI's replica bakes occupancy into `signals()`, so its pool is
harvested with `time_stop=1`, which frees every candidate except the one on the
immediately following bar. That is a superset of any real cell's entries and a
small, documented under-count rather than a silent one.

Protocol matches the rest of the exercise: ranked on 2025 alone, 2026 sliced
once at the end, $1,000 account, the `idk` environment's 0.2-point spread.

    py -B -m sandbox.research.exit_families
    py -B -m sandbox.research.exit_families --strategy "Hourly Delta Reversal"
    py -B -m sandbox.research.exit_families --record-trials
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import replace

import numpy as np

from sandbox import data, execution, metrics, strategies, trials
from sandbox.data import TS
from sandbox.execution import LONG, Signal
from sandbox.paths import result_path
from sandbox.research import microprice_optimization as base

INITIAL = 1_000.0
IS_START, IS_END = "2025-01-01", "2026-01-01"
OOS_START, OOS_END = "2026-01-01", "2027-01-01"
MIN_TRADES = 100
MIN_MONTHS = 8

#: `stacks` mirrors the Rust file: HDR keeps a vector of open positions, OFI a
#: single one. Getting this wrong silently changes what a long hold means.
TARGETS = {
    "Hourly Delta Reversal": {"stacks": True, "harvest": {}},
    "Deep OFI Momentum": {"stacks": False, "harvest": {"time_stop": 1}},
}

HOLD_MINUTES = (5, 10, 15, 30, 60, 120, 240, 390)

FIXED_GRID = {
    "stop_points": [4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0],
    "rr": [0.5, 1.0, 1.5, 2.0, 3.0],
    "time_stop": [30, 60, 120, 240],
}

#: A stop no bar can reach, so the HOLD family exits only on the clock or the
#: session flatten. Sizing then lands on the buying-power ceiling for every
#: trade, which is what makes the horizons comparable to each other.
UNREACHABLE_STOP = 1e6


def entry_pool(name):
    """`(bars, execution, [(index, side)])` -- the entry rule with no exit attached."""
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    params = strategy.all_params(TARGETS[name]["harvest"])
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    signals.sort(key=lambda s: s.index)
    return bars, ex, [(s.index, s.side) for s in signals]


#: Sizing denominator for the HOLD family. A position with no stop has no risk
#: distance, and dividing the risk budget by `UNREACHABLE_STOP` rounds every
#: trade to zero lots -- which is how the first run of this module produced an
#: empty HOLD table. On a $1,000 account any nominal below ~52 points lands on
#: the buying-power ceiling anyway, so every horizon carries the same exposure
#: and the column stays comparable across N.
HOLD_SIZE_STOP = 20.0


def sized_trades(bars, ex, signals, stacks, size_stop=None):
    """Resolve, apply occupancy when the strategy has it, and size on equity.

    Returns `[(entry_ts, pnl, quantity, stop, points)]`. Sizing repeats
    `execution.size` rather than calling it because the drawdown mark needs the
    quantity, which that function does not return; `--verify` pins the two
    against each other.
    """
    fills = execution.resolve(bars, signals, ex)
    if not fills:
        return []
    fills.sort(key=lambda f: f.entry_ts)

    if not stacks:
        kept, free_until = [], None
        for fill in fills:
            if free_until is not None and fill.entry_ts <= free_until:
                continue
            kept.append(fill)
            free_until = fill.exit_ts
        fills = kept

    equity = INITIAL
    queue = []
    out = []
    for fill in sorted(fills, key=lambda f: f.entry_ts):
        queue.sort()
        while queue and queue[0][0] <= fill.entry_ts:
            _exit_ts, pnl, entry_ts, quantity, stop, points = queue.pop(0)
            equity += pnl
            out.append((entry_ts, pnl, quantity, stop, points))
        risk_stop = size_stop if size_stop is not None else fill.stop
        raw = min(equity * ex.risk / risk_stop,
                  equity / ex.margin / fill.price) * ex.leverage
        quantity = math.floor(raw / ex.step) * ex.step
        if quantity < ex.step:
            continue
        queue.append((fill.exit_ts, fill.points * quantity, fill.entry_ts,
                      quantity, risk_stop, fill.points))
    for _exit_ts, pnl, entry_ts, quantity, stop, points in sorted(queue):
        equity += pnl
        out.append((entry_ts, pnl, quantity, stop, points))
    return sorted(out)


def score(trades, lo, hi):
    """Monthly consistency plus a conservatively marked drawdown."""
    window = [row for row in trades if lo <= row[0] < hi]
    if len(window) < MIN_TRADES:
        return None
    sized = [(row[0], row[1]) for row in window]
    stats = metrics.stats(sized, initial=INITIAL)
    if stats["n_months"] < MIN_MONTHS:
        return None

    equity = peak = INITIAL
    worst = 0.0
    for _ts, pnl, quantity, stop, _points in window:
        # What the account is worth with this position open and against it.
        marked = equity - min(stop, UNREACHABLE_STOP) * quantity
        worst = max(worst, (peak - min(marked, equity + pnl)) / peak)
        equity += pnl
        peak = max(peak, equity)

    stats["max_drawdown"] = round(worst, 4)
    stats["total_return_pct"] = round(100.0 * (equity / INITIAL - 1.0), 2)
    stats["final"] = round(equity, 2)
    points = np.array([row[4] for row in window])
    deviation = float(np.std(points, ddof=1))
    stats["gross_points_per_trade"] = round(float(np.mean(points)) + base.SPREAD, 4)
    stats["t"] = round(float(np.mean(points) + base.SPREAD) / deviation
                       * math.sqrt(len(points)), 3) if deviation > 0 else 0.0
    return stats


def hold_signals(pool, minutes):
    return [Signal(index, side, UNREACHABLE_STOP, 0.0, minutes)
            for index, side in pool]


def fixed_signals(pool, stop_points, rr, time_stop):
    return [Signal(index, side, stop_points, rr * stop_points, time_stop)
            for index, side in pool]


def run_hold(name, bars, ex, pool, stacks, is_lo, is_hi, oos_lo, oos_hi):
    header = (f"{'hold':>6}{'IS n':>8}{'IS gross':>10}{'IS t':>7}{'IS ret%':>9}"
              f"{'IS DD%':>8}{'OOS n':>8}{'OOS gross':>11}{'OOS t':>7}"
              f"{'OOS ret%':>10}{'OOS DD%':>9}")
    print(f"\n{name} -- HOLD (no bracket, exit on the clock)")
    print(header)
    print("-" * len(header))
    rows = []
    for minutes in HOLD_MINUTES:
        trades = sized_trades(bars, ex, hold_signals(pool, minutes), stacks,
                              size_stop=HOLD_SIZE_STOP)
        is_stats = score(trades, is_lo, is_hi)
        oos_stats = score(trades, oos_lo, oos_hi)
        if is_stats is None and oos_stats is None:
            continue
        left = (f"{is_stats['trades']:>8}{is_stats['gross_points_per_trade']:>10.3f}"
                f"{is_stats['t']:>7.2f}{is_stats['total_return_pct']:>9.1f}"
                f"{100 * is_stats['max_drawdown']:>8.1f}" if is_stats
                else f"{'-':>8}{'-':>10}{'-':>7}{'-':>9}{'-':>8}")
        right = (f"{oos_stats['trades']:>8}"
                 f"{oos_stats['gross_points_per_trade']:>11.3f}"
                 f"{oos_stats['t']:>7.2f}{oos_stats['total_return_pct']:>10.1f}"
                 f"{100 * oos_stats['max_drawdown']:>9.1f}" if oos_stats
                 else f"{'-':>8}{'-':>11}{'-':>7}{'-':>10}{'-':>9}")
        print(f"{minutes:>6}{left}{right}")
        rows.append({"hold_minutes": minutes, "in_sample": is_stats,
                     "out_of_sample": oos_stats})
    return rows


def run_fixed(name, bars, ex, pool, stacks, is_lo, is_hi, oos_lo, oos_hi, top):
    results = []
    scanned = 0
    for stop_points, rr, time_stop in itertools.product(*FIXED_GRID.values()):
        trades = sized_trades(bars, ex,
                              fixed_signals(pool, stop_points, rr, time_stop),
                              stacks)
        scanned += 1
        stats = score(trades, is_lo, is_hi)
        if stats is None:
            continue
        stats["cell"] = {"stop_points": stop_points, "rr": rr,
                         "time_stop": time_stop}
        stats["_trades"] = trades
        results.append(stats)

    passing = [s for s in results
               if s["max_drawdown"] <= base.MAX_DRAWDOWN
               and s["pos_rate"] >= base.MIN_POS_RATE
               and s["max_loss_streak"] <= base.MAX_LOSS_STREAK
               and s["top_month_share"] <= base.MAX_TOP_MONTH_SHARE
               and s["total_return_pct"] > 0]
    ranked = sorted(passing or results,
                    key=lambda s: s["msharpe"], reverse=True)[:top]

    header = (f"{'cell':30}{'IS ret%':>9}{'mSh':>7}{'DD%':>7}{'n':>6}{'pos':>6}"
              f"{'str':>5}{'IS gpt':>8}  {'OOS ret%':>9}{'mSh':>7}{'DD%':>7}"
              f"{'n':>6}{'pos':>6}{'str':>5}{'OOS gpt':>9}")
    print(f"\n{name} -- FIXED brackets ({len(results)} cells scored, "
          f"{len(passing)} pass the gates)")
    if not passing:
        print("  nothing cleared the gates; showing the best monthly Sharpes")
    print(header)
    print("-" * len(header))

    rows = []
    for stats in ranked:
        cell = stats["cell"]
        oos = score(stats.pop("_trades"), oos_lo, oos_hi)
        label = (f"sl{cell['stop_points']:g}pt/rr{cell['rr']:g}/"
                 f"ts{cell['time_stop']}")
        print(f"{label:30}{_row(stats)}  {_row(oos)}")
        rows.append({"cell": cell, "in_sample": _clean(stats),
                     "out_of_sample": _clean(oos)})
    for stats in results:
        stats.pop("_trades", None)
    profitable = sum(1 for r in rows if r["out_of_sample"]
                     and r["out_of_sample"]["total_return_pct"] > 0)
    print(f"  {profitable}/{len(rows)} profitable out of sample")
    return rows, scanned


def _row(stats):
    if not stats:
        return f"{'-':>9}{'-':>7}{'-':>7}{'-':>6}{'-':>6}{'-':>5}{'-':>8}"
    return (f"{stats['total_return_pct']:>9.1f}{stats['msharpe']:>7.2f}"
            f"{100 * stats['max_drawdown']:>7.1f}{stats['trades']:>6}"
            f"{stats['pos_rate']:>6.2f}{stats['max_loss_streak']:>5}"
            f"{stats['gross_points_per_trade']:>8.3f}")


def _clean(stats):
    if not stats:
        return None
    return {k: v for k, v in stats.items() if k not in ("cell", "_trades")}


def baseline(name, bars, ex, stacks, is_lo, is_hi, oos_lo, oos_hi):
    """The compiled strategy, exits and all, through the identical scorer.

    Without this the exit-family tables have nothing to be judged against: a
    cell that returns +8% means one thing beside a baseline of +2% and the
    opposite beside a baseline of +20%.
    """
    strategy = strategies.get(name)
    context = strategy.context()
    params = strategy.all_params()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    trades = sized_trades(bars, ex, signals, stacks)
    return score(trades, is_lo, is_hi), score(trades, oos_lo, oos_hi)


def verify(name, bars, ex, pool, stacks):
    """Pin the local sizer against `execution.size` on a plain bracket cell."""
    signals = fixed_signals(pool, 12.0, 2.0, 60)
    mine = sized_trades(bars, ex, signals, stacks)
    fills = execution.resolve(bars, signals, ex)
    if not stacks:
        kept, free_until = [], None
        for fill in sorted(fills, key=lambda f: f.entry_ts):
            if free_until is not None and fill.entry_ts <= free_until:
                continue
            kept.append(fill)
            free_until = fill.exit_ts
        fills = kept
    house = execution.size(fills, ex)
    same = (len(house) == len(mine)
            and all(a[0] == b[0] and abs(a[1] - b[1]) < 1e-9
                    for a, b in zip(sorted(house), sorted(
                        (row[0], row[1]) for row in mine))))
    print(f"  sizer matches execution.size for {name}: {same} "
          f"({len(house)} vs {len(mine)} trades)")
    return same


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", action="append", choices=list(TARGETS))
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--out", default="exit_families_result.json")
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    names = args.strategy or list(TARGETS)

    is_lo, is_hi = metrics.split_ts(IS_START), metrics.split_ts(IS_END)
    oos_lo, oos_hi = metrics.split_ts(OOS_START), metrics.split_ts(OOS_END)

    report = {"initial_balance": INITIAL, "in_sample": [IS_START, IS_END],
              "out_of_sample": [OOS_START, OOS_END], "strategies": {}}
    cells = 0
    for name in names:
        stacks = TARGETS[name]["stacks"]
        print(f"\n{'=' * 100}\n{name}  (stacks positions: {stacks})\n{'=' * 100}")
        bars, ex, pool = entry_pool(name)
        print(f"  {len(pool)} entry candidates, risk {100 * ex.risk:g}% of equity")
        verify(name, bars, ex, pool, stacks)
        base_is, base_oos = baseline(name, bars, ex, stacks, is_lo, is_hi,
                                     oos_lo, oos_hi)
        print(f"  compiled baseline (ATR bracket, nothing changed):")
        print(f"    2025 {_row(base_is)}")
        print(f"    2026 {_row(base_oos)}")

        hold = run_hold(name, bars, ex, pool, stacks, is_lo, is_hi,
                        oos_lo, oos_hi)
        fixed, scanned = run_fixed(name, bars, ex, pool, stacks, is_lo, is_hi,
                                   oos_lo, oos_hi, args.top)
        cells += len(hold) + scanned
        report["strategies"][name] = {
            "stacks": stacks, "entry_candidates": len(pool),
            "compiled_baseline": {"in_sample": _clean(base_is),
                                  "out_of_sample": _clean(base_oos)},
            "hold": hold, "fixed": fixed}

    charged = {}
    for name in names:
        count = cells // len(names)
        charged[name] = (trials.record(name, count, "exit families: hold "
                                       "horizons and fixed-point brackets")
                         if args.record_trials else trials.total(name) + count)
    print(f"\ncells evaluated: {cells}")
    for name, total in charged.items():
        print(f"cumulative trials charged to {name!r}: {total}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out)
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1, default=float)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
