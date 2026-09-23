"""Search the Creamer port for a cell that trades often enough to be measured.

`creamer_orderflow_research.py` established the problem: at the published
defaults the setup fires 4 times in 376 sessions, which is far below the count
at which any number is readable. This file searches for a cell that trades
enough to have a measurable edge, and then tries to prove that edge is not an
artefact of having searched.

    2025-02-12 .. 2026-01-01    IN SAMPLE -- everything is selected here
    2026-01-01 .. 2026-08-13    OUT OF SAMPLE -- read once, at the end

FIVE RULES THIS SEARCH FOLLOWS, each of them paid for by an earlier study:

  * SIZING IS NEVER IN THE GRID. Risk fraction, leverage and the account are
    fixed at the strategy's own values throughout. A sizing knob inside a
    selection grid picks the cell whose bets happened to land, not the cell
    whose signal was right ([[usoil-intraday-fails-twice]]).
  * THE UNTOUCHED DEFAULTS ARE A CONTESTANT. On the ORB study the defaults beat
    24 of 25 optimised cells out of sample; a search that cannot beat the thing
    it started from has found nothing ([[orb-optimization-collapsed-out-of-sample]]).
  * ENTRY AND EXIT ARE SEARCHED SEPARATELY. Stage 1 moves only the gates that
    decide WHEN the setup fires, with the exit frozen. Stage 2 freezes that
    entry and moves only the exit. Searched jointly, a good bracket rescues a
    bad entry and the result cannot be attributed.
  * TRAILING EXITS ARE IN THE STAGE-2 GRID, always
    ([[trailing-exits-beat-fixed-targets]]).
  * THE WINNER FACES TWO NULLS IN BOTH WINDOWS -- random direction and random
    timing. A raw out-of-sample return is not evidence on its own; only the
    margin over the null is ([[coin-flip-control-beats-real-signals]]).

RANKING IS ON THE IN-SAMPLE t-STATISTIC of points per trade, not on return and
not on monthly Sharpe. Return ranks the cell that bet biggest, and a monthly
Sharpe over a ten-month window is decided by two or three months
([[short-month-series-fakes-monthly-sharpe]]). The t-statistic asks the only
question a search can answer: is this cell's per-trade edge distinguishable
from zero, given how often it traded?

PARTICIPATION IS FIXED OFF in stage 1. It is the one gate that can only ever
remove trades, and the brief was more of them; it is put back on the winner
afterwards as a single before/after check rather than as a search dimension.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import time
from dataclasses import replace

from sandbox import data, execution, metrics, paths
from sandbox.data import TS
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research import combined_book
from sandbox.strategies.creamer_orderflow import CreamerOrderflow

INITIAL = 1_000.0
IS = ("2025-01-01", "2026-01-01")
OOS = ("2026-01-01", "2026-12-31")
TRIALS = 500
SEED = 20260815

#: A cell that does not trade this often in sample is not a candidate whatever
#: its edge looks like -- the brief was volume of trades, and a thin cell's
#: t-statistic is unstable anyway. Roughly one trade per two sessions.
MIN_IS_TRADES = 120
#: How many of the best in-sample cells get their out-of-sample column read. The
#: SPREAD of this group is the overfit diagnostic: if the top cells disagree
#: wildly out of sample, the ranking was noise.
TOP_N = 20

#: STAGE 1 -- entry gates only. Every axis here changes how often the setup
#: fires; none of them touches the bracket.
ENTRY_GRID = {
    #: last minute a position may be opened. 660 is the talk's own "first hour
    #: and a half"; the wider two ask what that rule costs.
    "entry_to": [660, 780, 915],
    "max_entries": [2, 4, 8],
    #: how hard the absorbed side had to be hitting
    "aggression_z": [0.5, 1.0, 1.5],
    #: how still price had to stay while it was hit
    "move_fraction": [0.55, 0.75, 1.0],
    "window": [2, 3, 5],
    "min_leg_atr": [0.5, 1.0, 2.0],
    "location": ["none", "holds_prior"],
    "flip": ["delta", "both"],
}

#: STAGE 2 -- the bracket, on the frozen stage-1 entry.
EXIT_GRID = {
    "target_mode": ["rr", "poc", "swing"],
    "rr": [1.0, 1.5, 2.0, 3.0],
    "trail_r": [0.0, 0.5, 1.0, 1.5],
    "time_stop": [15, 30, 45, 90],
}

#: Held at the strategy's own values in stage 1 so the entry is judged on a
#: constant bracket.
FROZEN_EXIT = {"target_mode": "rr", "rr": 1.5, "trail_r": 0.0, "time_stop": 45}


def account():
    combined_book.apply_cost_model("pro")
    return replace(combined_book.nq_execution(CreamerOrderflow().execution),
                   initial=INITIAL)


def window_bounds(window):
    return metrics.split_ts(window[0]), metrics.split_ts(window[1])


def score(fills, ex, window):
    """Per-unit and account figures for the fills entered inside `window`.

    Sizing restarts at `ex.initial` for each window rather than carrying the
    other window's balance in, so the two columns are comparable.
    """
    lo, hi = window_bounds(window)
    inside = [f for f in fills if lo <= f.entry_ts < hi]
    if not inside:
        return {"trades": 0, "points_per_trade": 0.0, "t": 0.0, "pf": 0.0,
                "win_rate": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0,
                "mde_points": None}
    points = [f.points for f in inside]
    mean = sum(points) / len(points)
    sd = (sum((p - mean) ** 2 for p in points) / len(points)) ** 0.5
    stats = metrics.stats(execution.size(inside, ex), initial=ex.initial)
    return {
        "trades": len(inside),
        "points_per_trade": round(mean, 3),
        "t": round(mean / (sd / math.sqrt(len(points))), 2) if sd else 0.0,
        "mde_points": round(2.8 * sd / math.sqrt(len(points)), 2),
        "pf": stats["pf"],
        "win_rate": stats["win_rate"],
        "return_pct": round(100.0 * stats["pnl"] / ex.initial, 2),
        "max_dd_pct": round(100.0 * stats["max_dd"] / ex.initial, 2),
    }


def cells(grid):
    keys = sorted(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, values))


def evaluate(strategy, bars, context, ex, overrides):
    params = strategy.all_params(overrides)
    signals = strategy.signals(bars, context, "all", params)
    return signals, execution.resolve(bars, signals, ex)


def sweep(strategy, bars, context, ex, grid, base, label):
    """Every cell in `grid`, scored in both windows. Selection reads IS only."""
    total = 1
    for values in grid.values():
        total *= len(values)
    print(f"\n{label}: {total:,} cells")
    started = time.time()
    out = []
    for n, overrides in enumerate(cells(grid), 1):
        merged = {**base, **overrides}
        _signals, fills = evaluate(strategy, bars, context, ex, merged)
        out.append({"overrides": overrides, "params": merged,
                    "is": score(fills, ex, IS), "oos": score(fills, ex, OOS)})
        if n % 250 == 0 or n == total:
            rate = (time.time() - started) / n
            print(f"    {n:>6,}/{total:,}  {rate * (total - n):>6.0f}s left")
    return out


def ranked(results):
    """In-sample survivors, best t-statistic first. OOS is never read here."""
    alive = [r for r in results if r["is"]["trades"] >= MIN_IS_TRADES]
    return sorted(alive, key=lambda r: r["is"]["t"], reverse=True)


def print_table(rows, title):
    print(f"\n{title}")
    print(f"    {'#':>3}{'IS n':>7}{'IS pts':>9}{'IS t':>7}{'IS ret%':>9}"
          f"{'OOS n':>7}{'OOS pts':>9}{'OOS t':>7}{'OOS ret%':>10}  cell")
    for i, row in enumerate(rows, 1):
        ins, oos = row["is"], row["oos"]
        detail = ", ".join(f"{k}={v}" for k, v in sorted(row["overrides"].items()))
        print(f"    {i:>3}{ins['trades']:>7}{ins['points_per_trade']:>9.3f}"
              f"{ins['t']:>7.2f}{ins['return_pct']:>9.2f}"
              f"{oos['trades']:>7}{oos['points_per_trade']:>9.3f}"
              f"{oos['t']:>7.2f}{oos['return_pct']:>10.2f}  {detail}")


def yield_vs_edge(rows):
    """Does trading more often cost edge? Correlation of IS n against IS pts.

    This is the brief's own question. If the correlation is negative, every
    knob that buys trade count is being paid for in per-trade edge, and "more
    trades" and "better strategy" are opposite directions rather than the same
    one.
    """
    if len(rows) < 3:
        return {}
    ns = [r["is"]["trades"] for r in rows]
    edges = [r["is"]["points_per_trade"] for r in rows]
    mean_n, mean_e = sum(ns) / len(ns), sum(edges) / len(edges)
    cov = sum((n - mean_n) * (e - mean_e) for n, e in zip(ns, edges))
    var_n = sum((n - mean_n) ** 2 for n in ns)
    var_e = sum((e - mean_e) ** 2 for e in edges)
    return {
        "cells": len(rows),
        "trades_range": [min(ns), max(ns)],
        "edge_range": [round(min(edges), 3), round(max(edges), 3)],
        "correlation": round(cov / math.sqrt(var_n * var_e), 3)
        if var_n > 0 and var_e > 0 else 0.0,
    }


def decay(rows):
    """How the in-sample ranking survived contact with the holdout.

    The number that matters is not the winner's OOS return but how many of the
    top cells kept their sign. A ranking built on noise scatters.
    """
    if not rows:
        return {}
    oos = [r["oos"]["points_per_trade"] for r in rows]
    positive = sum(1 for v in oos if v > 0)
    return {"cells": len(rows), "oos_positive": positive,
            "oos_positive_pct": round(100.0 * positive / len(rows), 1),
            "oos_median_points": round(sorted(oos)[len(oos) // 2], 3),
            "is_median_points": round(
                sorted(r["is"]["points_per_trade"] for r in rows)[len(rows) // 2], 3)}


# --------------------------------------------------------------------------- #
# null controls
# --------------------------------------------------------------------------- #
def points_per_trade(fills, window):
    lo, hi = window_bounds(window)
    inside = [f.points for f in fills if lo <= f.entry_ts < hi]
    return sum(inside) / len(inside) if inside else 0.0


def coin_flip(bars, signals, ex, rng, window):
    draws = []
    for _ in range(TRIALS):
        fake = [replace(s, side=LONG if rng.random() < 0.5 else SHORT)
                for s in signals]
        draws.append(points_per_trade(execution.resolve(bars, fake, ex), window))
    return draws


def random_entry(bars, signals, ex, rng, params, window):
    lo_min, hi_min = params["entry_from"], params["entry_to"]
    lo, hi = window_bounds(window)
    pool = [i for i, bar in enumerate(bars)
            if lo <= bar[TS] < hi and lo_min <= (bar[TS] % 86_400) // 60 <= hi_min
            and i + 1 < len(bars)]
    live = [s for s in signals if lo <= bars[s.index][TS] < hi]
    if not live or len(pool) < len(live):
        return []
    longs = sum(1 for s in live if s.side == LONG)
    brackets = [(s.stop, s.target, s.max_minutes, s.trail) for s in live]
    draws = []
    for _ in range(TRIALS):
        picks = sorted(rng.sample(pool, len(live)))
        fake = []
        for k, index in enumerate(picks):
            stop, target, minutes, trail = brackets[k % len(brackets)]
            fake.append(Signal(index, LONG if k < longs else SHORT, stop, target,
                               minutes, trail))
        draws.append(points_per_trade(execution.resolve(bars, fake, ex), window))
    return draws


def verdict(actual, draws):
    if not draws:
        return None
    mean = sum(draws) / len(draws)
    sd = (sum((d - mean) ** 2 for d in draws) / len(draws)) ** 0.5
    return {"null_mean": round(mean, 3), "null_sd": round(sd, 3),
            "z": round((actual - mean) / sd, 2) if sd else 0.0,
            "beats_pct": round(100.0 * sum(1 for d in draws if d < actual)
                               / len(draws), 1)}


def nulls(strategy, bars, context, ex, overrides, rng):
    params = strategy.all_params(overrides)
    signals, fills = evaluate(strategy, bars, context, ex, overrides)
    out = {}
    for name, window in (("is", IS), ("oos", OOS)):
        actual = points_per_trade(fills, window)
        out[name] = {
            "points_per_trade": round(actual, 3),
            "coin_flip": verdict(actual, coin_flip(bars, signals, ex, rng, window)),
            "random_entry": verdict(
                actual, random_entry(bars, signals, ex, rng, params, window)),
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="creamer_orderflow_optimize.json")
    args = parser.parse_args()

    strategy = CreamerOrderflow()
    ex = account()
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    rng = random.Random(SEED)

    print(f"NQ level-two, ${INITIAL:,.0f} Forex, Exness {combined_book.COST_MODEL}, "
          f"entry cost {ex.entry_cost:.3f} pts")
    print(f"  in sample {IS[0]}..{IS[1]}   out of sample {OOS[0]}..{OOS[1]}")
    print(f"  selection reads IN SAMPLE ONLY; min {MIN_IS_TRADES} IS trades")

    out = {"initial": INITIAL, "cost_model": combined_book.COST_MODEL,
           "entry_cost_points": round(ex.entry_cost, 3),
           "is": IS, "oos": OOS, "min_is_trades": MIN_IS_TRADES,
           "trials": TRIALS, "seed": SEED}

    # ---- baseline: the untouched defaults ------------------------------- #
    _s, fills = evaluate(strategy, bars, context, ex, {})
    out["defaults"] = {"is": score(fills, ex, IS), "oos": score(fills, ex, OOS)}
    print(f"\nBASELINE, untouched defaults: "
          f"IS {out['defaults']['is']['trades']} trades / "
          f"{out['defaults']['is']['points_per_trade']:+.3f} pts, "
          f"OOS {out['defaults']['oos']['trades']} trades / "
          f"{out['defaults']['oos']['points_per_trade']:+.3f} pts")

    # ---- stage 1: entry -------------------------------------------------- #
    stage1 = sweep(strategy, bars, context, ex, ENTRY_GRID,
                   {**FROZEN_EXIT, "participation_mult": 0}, "STAGE 1 (entry)")
    alive = ranked(stage1)
    print(f"\n    {len(alive):,} of {len(stage1):,} cells cleared "
          f"{MIN_IS_TRADES} in-sample trades")
    if not alive:
        raise SystemExit("no cell traded often enough to select from")
    # RANKING BY t FINDS THE LEAST BAD WHEN NOTHING IS GOOD. The sort is
    # descending on a signed statistic, so a grid in which every cell loses
    # still produces a "top 20" -- and a reader who skips the sign will take it
    # for a shortlist of winners. Say so where it cannot be missed.
    if alive[0]["is"]["t"] <= 0:
        print("\n    *** NO PROFITABLE CELL IN SAMPLE. Every one of the "
              f"{len(alive):,} cells that traded")
        print("    *** often enough LOST money in 2025. The ranking below is "
              "least-bad-first,")
        print("    *** not a shortlist of candidates, and stage 2 is tuning "
              "the exit of a")
        print("    *** losing entry. Read it as a measurement, not a search "
              "result.")
    out["profitable_in_sample"] = alive[0]["is"]["t"] > 0

    print_table(alive[:TOP_N], f"STAGE 1, top {TOP_N} by in-sample t")
    out["stage1_top"] = alive[:TOP_N]
    out["yield_vs_edge"] = yield_vs_edge(alive)
    relation = out["yield_vs_edge"]
    if relation:
        print(f"\n    YIELD vs EDGE across the {relation['cells']} survivors: "
              f"correlation {relation['correlation']:+.3f}")
        print(f"      trade count {relation['trades_range'][0]}"
              f"..{relation['trades_range'][1]}, "
              f"per-trade edge {relation['edge_range'][0]:+.2f}"
              f"..{relation['edge_range'][1]:+.2f} pts")
    out["stage1_decay"] = decay(alive[:TOP_N])
    out["stage1_decay_all"] = decay(alive)

    busiest = max(alive, key=lambda r: r["is"]["trades"])
    print_table([busiest], "STAGE 1, the busiest surviving cell")
    out["stage1_busiest"] = busiest

    entry = alive[0]["params"]

    # ---- stage 2: exits -------------------------------------------------- #
    stage2 = sweep(strategy, bars, context, ex, EXIT_GRID, entry,
                   "STAGE 2 (exits, entry frozen)")
    alive2 = ranked(stage2)
    if not alive2:
        raise SystemExit("frozen entry lost its trade count under every exit")
    print_table(alive2[:TOP_N], f"STAGE 2, top {TOP_N} by in-sample t")
    out["stage2_top"] = alive2[:TOP_N]
    out["stage2_decay"] = decay(alive2[:TOP_N])

    chosen = alive2[0]["params"]
    out["chosen"] = chosen

    # ---- participation, put back on the winner --------------------------- #
    _s, with_gate = evaluate(strategy, bars, context, ex,
                             {**chosen, "participation_mult": 1.0})
    out["chosen_with_participation"] = {"is": score(with_gate, ex, IS),
                                        "oos": score(with_gate, ex, OOS)}

    # ---- the winner, against both nulls, in both windows ----------------- #
    print("\nWINNER")
    for key, value in sorted(chosen.items()):
        if strategy.defaults.get(key) != value:
            print(f"    {key:<22} {value}   (default {strategy.defaults.get(key)})")
    _s, chosen_fills = evaluate(strategy, bars, context, ex, chosen)
    out["chosen_is"] = score(chosen_fills, ex, IS)
    out["chosen_oos"] = score(chosen_fills, ex, OOS)
    print_table([{"overrides": {}, "is": out["chosen_is"],
                  "oos": out["chosen_oos"]}], "    scored")

    print("\nNULLS  (500 draws each, both windows)")
    out["nulls"] = nulls(strategy, bars, context, ex, chosen, rng)
    print(f"    {'window':<8}{'pts/tr':>9}{'flip mean':>11}{'flip z':>8}"
          f"{'rand mean':>11}{'rand z':>8}")
    for name in ("is", "oos"):
        block = out["nulls"][name]
        flip, rand = block["coin_flip"], block["random_entry"]
        print(f"    {name:<8}{block['points_per_trade']:>9.3f}"
              f"{flip['null_mean']:>11.3f}{flip['z']:>8.2f}"
              f"{(rand or {}).get('null_mean', 0):>11.3f}"
              f"{(rand or {}).get('z', 0):>8.2f}")

    print("\nVERDICT INPUTS")
    # The defaults' per-trade figure is quoted WITH its trade count because on
    # a handful of trades it is a single outcome, not a rate.
    print(f"    defaults OOS pts/trade      "
          f"{out['defaults']['oos']['points_per_trade']:+.3f} "
          f"on {out['defaults']['oos']['trades']} trades")
    print(f"    chosen   OOS pts/trade      "
          f"{out['chosen_oos']['points_per_trade']:+.3f} "
          f"on {out['chosen_oos']['trades']} trades "
          f"(MDE {out['chosen_oos']['mde_points']})")
    print(f"    top-{TOP_N} cells positive OOS    "
          f"{out['stage2_decay'].get('oos_positive')}/{TOP_N}")
    print(f"    stage-1 survivors positive  "
          f"{out['stage1_decay_all'].get('oos_positive')}/"
          f"{out['stage1_decay_all'].get('cells')}")

    path = paths.result_path(args.json)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
