"""Two exit families the parameter search never tried: fixed points, and a clock.

`microprice_optimization` swept a bracket scaled by the 20-session ATR, plus a
time stop that only fires when that bracket has not. Two shapes were therefore
never tested, and both are things a practitioner would reach for first:

  HOLD   No stop and no target. Enter on the signal, leave exactly N minutes
         later. This is the cleanest measurement in the whole exercise: with no
         bracket there is no path dependence, so the mean points per trade *is*
         the signal's directional information at horizon N. A rule whose edge is
         real shows a hump -- rising to some horizon, decaying after it. A rule
         with no edge traces a flat line through zero at every N, and no
         bracket, filter or model built on top of it can do better.

  FIXED  Stop and target a constant number of points, the same distance in every
         volatility regime. The ATR-scaled version widens the bracket when the
         market is fast; the fixed version does not, and which is better is an
         empirical question rather than a settled one.

Protocol is unchanged from the parameter search: ranked on 2025 alone, 2026
sliced once at the end, the same drawdown and monthly-consistency gates, and
every cell charged to `trials.json`.

    py -B -m sandbox.research.microprice_exit_families --hold
    py -B -m sandbox.research.microprice_exit_families --fixed
    py -B -m sandbox.research.microprice_exit_families --hold --fixed \
        --record-trials
"""
from __future__ import annotations

import argparse
import itertools
import json
import math

import numpy as np

from sandbox import trials
from sandbox.paths import result_path
from sandbox.research import microprice_optimization as base

#: Minutes a HOLD position is kept. Spans the horizon the tilt EWMA plausibly
#: speaks to (one minute) out to a third of a session.
HOLD_MINUTES = (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90)

#: Fixed bracket, in points. NQ ticks at 0.25 and the spread charged is 0.2, so
#: the smallest stop here is already eight ticks -- below that the cost model
#: dominates whatever the rule does.
FIXED_GRID = {
    "stop_points": [2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0],
    "rr": [0.5, 1.0, 1.5, 2.0, 3.0],
    "time_stop": [20, 40],
}

#: Sizing for the HOLD family. A position with no stop has no risk distance to
#: divide by, so the risk-fraction mode is not available to it; a constant
#: fraction of equity is the neutral choice and keeps every horizon comparable.
HOLD_SIZING = {"mode": "fixed_frac", "fraction": 1.0}

#: What the HOLD family's drawdown is marked against, since it has no stop: the
#: 20-session ATR, as a stand-in for the adverse excursion a minute-scale
#: position can suffer. Marking against zero would make HOLD look artificially
#: calmer than the bracketed families it is compared with.
HOLD_MARK_FRACTION = 0.05


def hold_scan(state, candidates, lo, hi, initial):
    """Mean points per trade at each holding horizon, for every signal cell."""
    cache = base.PathCache(state, candidates, limit=len(HOLD_MINUTES) + 2)
    rows = []
    for values in itertools.product(*base.SIGNAL_GRID.values()):
        cell = dict(zip(base.SIGNAL_GRID, values))
        side = base.signal_mask(candidates, cell["halflife"], cell["tilt"],
                                cell["direction"], cell["max_spread"])
        if not (side != 0).any():
            continue
        for minutes in HOLD_MINUTES:
            resolved = cache(0.0, 0.0, minutes, None, bracket=False)
            trades = base.build_trades(candidates, side, side != 0, resolved,
                                       lo, hi)
            if trades is None or len(trades["ts"]) < base.MIN_TRADES:
                continue
            gross = trades["points"] + base.SPREAD
            deviation = float(np.std(gross, ddof=1))
            stop = HOLD_MARK_FRACTION * trades["atr"]
            pnls, closed, marked = base.equity_walk(
                trades["entry"], stop, trades["points"], trades["atr"],
                HOLD_SIZING, initial)
            stats = base.score(trades["ts"], pnls, closed, marked, initial)
            rows.append({
                **cell, "hold_minutes": minutes,
                "trades": int(len(gross)),
                "gross_points": round(float(np.mean(gross)), 4),
                "net_points": round(float(np.mean(trades["points"])), 4),
                "t": round(float(np.mean(gross)) / deviation
                           * math.sqrt(len(gross)), 3) if deviation > 0 else 0.0,
                "stats": base._trim(stats),
            })
    return rows


def fixed_scan(state, candidates, lo, hi, initial, verbose=True):
    """The compiled sizing over a fixed-point bracket grid, ranked in sample."""
    cache = base.PathCache(state, candidates)
    signal_cells = [dict(zip(base.SIGNAL_GRID, values))
                    for values in itertools.product(*base.SIGNAL_GRID.values())]
    results = []
    scanned = 0
    for stop_points, rr, time_stop in itertools.product(*FIXED_GRID.values()):
        resolved = cache(0.0, rr, time_stop, None, stop_points=stop_points)
        for signal in signal_cells:
            side = base.signal_mask(candidates, signal["halflife"],
                                    signal["tilt"], signal["direction"],
                                    signal["max_spread"])
            trades = base.build_trades(candidates, side, side != 0, resolved,
                                       lo, hi)
            scanned += 1
            if trades is None:
                continue
            stop = np.full(len(trades["ts"]), stop_points)
            pnls, closed, marked = base.equity_walk(
                trades["entry"], stop, trades["points"], trades["atr"],
                base.COMPILED["sizing"], initial)
            stats = base.score(trades["ts"], pnls, closed, marked, initial)
            if stats is None or stats["trades"] < base.MIN_TRADES:
                continue
            stats["gross_points_per_trade"] = round(
                float(np.mean(trades["points"] + base.SPREAD)), 4)
            stats["cell"] = {**signal, "stop_points": stop_points, "rr": rr,
                             "time_stop": time_stop}
            results.append(stats)
        if verbose:
            print(f"  stop={stop_points:g}pt rr={rr:g} ts={time_stop}: "
                  f"{len(results)} scored / {scanned} tried")
    return results, scanned


def score_fixed(state, candidates, cell, lo, hi, initial, scale=1.0):
    """One fixed-bracket cell over one window, optionally re-levered."""
    cache = base.PathCache(state, candidates, limit=4)
    side = base.signal_mask(candidates, cell["halflife"], cell["tilt"],
                            cell["direction"], cell["max_spread"])
    resolved = cache(0.0, cell["rr"], cell["time_stop"], None,
                     stop_points=cell["stop_points"])
    trades = base.build_trades(candidates, side, side != 0, resolved, lo, hi)
    if trades is None:
        return None
    stop = np.full(len(trades["ts"]), cell["stop_points"])
    sizing = {**base.COMPILED["sizing"], "scale": scale}
    pnls, closed, marked = base.equity_walk(
        trades["entry"], stop, trades["points"], trades["atr"],
        sizing, initial)
    stats = base.score(trades["ts"], pnls, closed, marked, initial)
    if stats is not None:
        stats["gross_points_per_trade"] = round(
            float(np.mean(trades["points"] + base.SPREAD)), 4)
    return stats


TARGET_DRAWDOWN = 0.15


def calibrate_scale(state, candidates, cell, lo, hi, initial,
                    target=TARGET_DRAWDOWN, low=0.1, high=200.0, steps=40):
    """Smallest exposure multiple whose drawdown reaches `target`, by bisection.

    Fitted on one window and reported on both. Drawdown is monotone in exposure
    up to the point where compounding and the lot step distort it, which is why
    this bisects the realised curve instead of assuming the linear scaling that
    holds only for small positions.

    This changes no trading decision -- same signals, same exits, same order --
    so it is a leverage statement, not a strategy change.
    """
    best = None
    for _ in range(steps):
        middle = (low + high) / 2.0
        stats = score_fixed(state, candidates, cell, lo, hi, initial,
                            scale=middle)
        drawdown = stats["max_drawdown"] if stats else 1.0
        if drawdown < target:
            low = middle
        else:
            high = middle
        if stats and (best is None
                      or abs(drawdown - target) < abs(best[1] - target)):
            best = (middle, drawdown)
        if high - low < 1e-3:
            break
    return best[0] if best else 1.0


def implied_leverage(state, candidates, cell, lo, hi, initial, scale):
    """Average notional as a multiple of equity, so the broker requirement is
    visible rather than implicit in a margin number."""
    side = base.signal_mask(candidates, cell["halflife"], cell["tilt"],
                            cell["direction"], cell["max_spread"])
    resolved = base.PathCache(state, candidates, limit=4)(
        0.0, cell["rr"], cell["time_stop"], None,
        stop_points=cell["stop_points"])
    trades = base.build_trades(candidates, side, side != 0, resolved, lo, hi)
    if trades is None:
        return 0.0
    stop = np.full(len(trades["ts"]), cell["stop_points"])
    quantity = np.minimum(initial * 0.01 / stop,
                          initial / base.MARGIN / trades["entry"]) * scale
    return float(np.mean(quantity * trades["entry"] / initial))


def print_hold(is_rows, oos_rows):
    """The edge-decay curve for the compiled signal, then the best of each N."""
    by_key = {}
    for row in oos_rows:
        by_key[(row["halflife"], row["tilt"], row["direction"],
                row["max_spread"], row["hold_minutes"])] = row

    header = (f"{'hold':>6}{'IS n':>8}{'IS gross':>10}{'IS t':>7}"
              f"{'OOS n':>8}{'OOS gross':>11}{'OOS t':>7}")
    print("\nEDGE DECAY, compiled signal (h1 / tilt 0.15 / continuation "
          "/ spread<=1.25)")
    print("No stop, no target: mean points per trade at each holding horizon.")
    print(header)
    print("-" * len(header))
    for row in sorted((r for r in is_rows
                       if r["halflife"] == 1 and r["tilt"] == 0.15
                       and r["direction"] == "continuation"
                       and r["max_spread"] == 1.25),
                      key=lambda r: r["hold_minutes"]):
        key = (1, 0.15, "continuation", 1.25, row["hold_minutes"])
        out = by_key.get(key)
        tail = (f"{out['trades']:>8}{out['gross_points']:>11.3f}{out['t']:>7.2f}"
                if out else f"{'-':>8}{'-':>11}{'-':>7}")
        print(f"{row['hold_minutes']:>6}{row['trades']:>8}"
              f"{row['gross_points']:>10.3f}{row['t']:>7.2f}{tail}")

    print("\nBEST SIGNAL CELL AT EACH HORIZON, by in-sample gross edge")
    print(f"{'hold':>6}  {'signal cell':30}{'IS n':>8}{'IS gross':>10}"
          f"{'IS t':>7}{'OOS gross':>11}{'OOS t':>7}")
    print("-" * 79)
    for minutes in HOLD_MINUTES:
        at = [r for r in is_rows if r["hold_minutes"] == minutes]
        if not at:
            continue
        best = max(at, key=lambda r: r["gross_points"])
        key = (best["halflife"], best["tilt"], best["direction"],
               best["max_spread"], minutes)
        out = by_key.get(key)
        label = (f"h{best['halflife']}/t{best['tilt']:g}/"
                 f"{'rev' if best['direction'] == 'reversion' else 'con'}/"
                 f"sp{best['max_spread']:g}")
        tail = (f"{out['gross_points']:>11.3f}{out['t']:>7.2f}" if out
                else f"{'-':>11}{'-':>7}")
        print(f"{minutes:>6}  {label:30}{best['trades']:>8}"
              f"{best['gross_points']:>10.3f}{best['t']:>7.2f}{tail}")

    peak = max(is_rows, key=lambda r: abs(r["t"]))
    print(f"\nlargest |t| anywhere in the HOLD scan: {peak['t']} at "
          f"{peak['hold_minutes']}m, h{peak['halflife']}/t{peak['tilt']:g}/"
          f"{peak['direction']} ({peak['trades']} trades)")
    strong = sum(1 for r in is_rows if abs(r["t"]) >= 2.0)
    print(f"{strong}/{len(is_rows)} horizon-and-signal combinations reach "
          f"|t| >= 2 in sample before costs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--fixed", action="store_true")
    parser.add_argument("--balance", type=float, default=base.INITIAL)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", default="microprice_exit_families_result.json")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--calibrate-drawdown", type=float, default=None,
                        help="re-lever the ranked fixed cells so their 2025 "
                             "drawdown reaches this fraction, then report 2026")
    args = parser.parse_args()
    if not (args.hold or args.fixed):
        args.hold = args.fixed = True

    is_lo, is_hi = base._epoch(base.IS_START), base._epoch(base.IS_END)
    oos_lo, oos_hi = base._epoch(base.OOS_START), base._epoch(base.OOS_END)

    print("loading nq level-two bars and features ...")
    state = base.load_state("nq")
    candidates = base.build_candidates(state)
    print(f"  {state['n']} bars, {len(candidates['signal'])} candidate signals")

    report = {"strategy": base.STRATEGY, "initial_balance": args.balance,
              "in_sample": [base.IS_START, base.IS_END],
              "out_of_sample": [base.OOS_START, base.OOS_END]}
    cells = 0

    if args.hold:
        print("\nHOLD family: no bracket, exit on the clock")
        is_rows = hold_scan(state, candidates, is_lo, is_hi, args.balance)
        oos_rows = hold_scan(state, candidates, oos_lo, oos_hi, args.balance)
        print_hold(is_rows, oos_rows)
        report["hold"] = {"in_sample": is_rows, "out_of_sample": oos_rows}
        cells += len(is_rows)

    if args.fixed:
        print("\nFIXED family: constant-point stop and target")
        results, scanned = fixed_scan(state, candidates, is_lo, is_hi,
                                      args.balance)
        cells += scanned
        ranked = base.rank(results, args.top)
        print(f"  {len(results)} cells scored, {len(ranked)} pass the gates")
        if not ranked:
            print("  nothing cleared the gates; showing the best monthly "
                  "Sharpes among rankable cells instead")
            ranked = base.best_effort(results, args.top)

        header = (f"{'cell':52}{'IS ret%':>9}{'mSh':>7}{'MDD%':>7}{'n':>6}"
                  f"{'pos':>6}{'str':>5}  {'OOS ret%':>9}{'mSh':>7}"
                  f"{'MDD%':>7}{'n':>6}{'pos':>6}{'str':>5}")
        print(f"\n{header}")
        print("-" * len(header))
        rows = []
        for stats in ranked:
            cell = stats["cell"]
            out = score_fixed(state, candidates, cell, oos_lo, oos_hi,
                              args.balance)
            label = (f"h{cell['halflife']}/t{cell['tilt']:g}/"
                     f"{'rev' if cell['direction'] == 'reversion' else 'con'}/"
                     f"sp{cell['max_spread']:g} sl{cell['stop_points']:g}pt/"
                     f"rr{cell['rr']:g}/ts{cell['time_stop']}")
            print(f"{label[:51]:52}{base._row(stats)}  {base._row(out)}")
            rows.append({"cell": base._serialisable(cell),
                         "in_sample": base._trim(stats),
                         "out_of_sample": base._trim(out)})
        if args.calibrate_drawdown is not None:
            target = args.calibrate_drawdown
            print(f"\nRE-LEVERED so 2025 drawdown reaches "
                  f"{100 * target:g}% (fitted on 2025 only)")
            print(f"{'cell':46}{'scale':>7}{'lev':>6}"
                  f"{'IS ret%':>9}{'IS DD%':>8}{'IS pos':>8}{'IS str':>7}"
                  f"  {'OOS ret%':>9}{'OOS DD%':>8}{'OOS pos':>8}{'OOS str':>8}")
            print("-" * 133)
            for row in rows:
                cell = row["cell"]
                scale = calibrate_scale(state, candidates, cell, is_lo, is_hi,
                                        args.balance, target)
                levered_is = score_fixed(state, candidates, cell, is_lo, is_hi,
                                         args.balance, scale)
                levered_oos = score_fixed(state, candidates, cell, oos_lo,
                                          oos_hi, args.balance, scale)
                leverage = implied_leverage(state, candidates, cell, is_lo,
                                            is_hi, args.balance, scale)
                label = (f"h{cell['halflife']}/t{cell['tilt']:g}/"
                         f"{'rev' if cell['direction'] == 'reversion' else 'con'}"
                         f"/sp{cell['max_spread']:g} sl{cell['stop_points']:g}pt/"
                         f"rr{cell['rr']:g}/ts{cell['time_stop']}")
                print(f"{label[:45]:46}{scale:>7.2f}{leverage:>6.1f}"
                      f"{levered_is['total_return_pct']:>9.1f}"
                      f"{100 * levered_is['max_drawdown']:>8.1f}"
                      f"{levered_is['pos_rate']:>8.2f}"
                      f"{levered_is['max_loss_streak']:>7}"
                      f"  {levered_oos['total_return_pct']:>9.1f}"
                      f"{100 * levered_oos['max_drawdown']:>8.1f}"
                      f"{levered_oos['pos_rate']:>8.2f}"
                      f"{levered_oos['max_loss_streak']:>8}")
                row["calibrated"] = {
                    "target_drawdown": target, "scale": round(scale, 3),
                    "implied_notional_leverage": round(leverage, 2),
                    "in_sample": base._trim(levered_is),
                    "out_of_sample": base._trim(levered_oos)}

        report["fixed"] = {"cells_scored": len(results), "top": rows}
        profitable = sum(1 for r in rows if r["out_of_sample"]
                         and r["out_of_sample"]["total_return_pct"] > 0)
        print(f"\n{profitable}/{len(rows)} of the ranked fixed-bracket cells "
              f"are profitable out of sample")

    charged = trials.total(base.STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(base.STRATEGY, cells,
                                "microprice exit families: hold horizons and "
                                "fixed-point brackets")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {base.STRATEGY!r}: {charged}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out)
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
