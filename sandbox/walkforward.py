"""Anchored walk-forward selection. OPTIMIZATION_PLAN.md Stage 4.

`search.sweep` scores every candidate on the whole sample and filters on a
holdout, which makes that holdout a second training set. This module replaces
that with a walk-forward where selection can only ever see the past:

  * folds are **anchored** -- the train window always starts at the first bar,
    so a winner has to survive every regime seen so far rather than quietly
    forgetting the ones that hurt;
  * one session of **embargo** sits between train and test;
  * selection runs inside the train window alone, the chosen cell is scored
    once on that fold's test window, and it is never revisited;
  * every headline number comes from the **stitched** test windows. In-sample
    figures are printed for reference and decide nothing.

Two Stage 3 requirements ride along here because they are properties of the
selection rule rather than of the grid:

  * **plateau width** -- the winning cell and *all* of its immediate grid
    neighbours must be profitable. A good cell ringed by losers is a hole in
    the surface, not an edge.
  * **deflated Sharpe** -- with N cells evaluated the best in-sample Sharpe is
    inflated by roughly sqrt(2 ln N) standard errors, and the deflated figure
    is what says whether an improvement is real or is the search finding what
    searches find.

The expensive half runs once: `execution.resolve` walks the bars per grid cell
over the whole sample, and every fold is then a timestamp slice of the
resulting fill list. Sizing is path dependent, so it is redone per segment from
that segment's own opening equity -- fills are per unit and carry no sizing.
"""
import itertools
import math
import random
from dataclasses import replace

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import search
from sandbox import trials
from sandbox.data import TS

def monthly_folds(first="2025-08", last="2026-08"):
    """`[(test_from, test_to)]`, one calendar month each, `last` exclusive."""
    out = []
    year, month = (int(p) for p in first.split("-"))
    last_year, last_month = (int(p) for p in last.split("-"))
    while (year, month) < (last_year, last_month):
        nxt = (year + 1, 1) if month == 12 else (year, month + 1)
        out.append((f"{year}-{month:02d}-01", f"{nxt[0]}-{nxt[1]:02d}-01"))
        year, month = nxt
    return out


#: (test_from, test_to). Anchored: every train window starts at the first bar.
#: 1-month test steps from 2025-08, so the first train window is ~5.5 months and
#: there are 12 folds. The previous layout (8-month minimum train, 2-month steps)
#: gave 5 folds and 10 OOS months, of which 7 were 2026 and one selected nothing
#: -- selection was scored almost entirely on one year. See Stage 4 of the plan:
#: an anchored walk-forward still cannot test its own first third, which is what
#: `purged_cv.py` exists to cover.
FOLDS = monthly_folds()

#: One session of embargo between the end of the train window and the start of
#: the test window. `execution.resolve` flattens at the session close, so a
#: single session is enough for no train trade to overlap a test bar.
EMBARGO_DAYS = 1

INITIAL = 1000.0

#: Selection floors inside a train window. Trade counts scale with the window,
#: so this is a rate rather than the sweep's absolute `--min-trades`.
MIN_TRADES_PER_MONTH = 6
MAX_LOSS_STREAK = 3
MAX_TOP_MONTH_SHARE = 0.5

#: Net points per trade must be at least this multiple of the round-trip
#: EXECUTION COST -- `Execution.entry_cost`, i.e. spread + slippage +
#: commission, not the spread alone. On a zero-spread commission account the
#: spread is 0.0, so reading it alone would turn this floor off entirely.
#:
#: The t-stat objective is `mean / sd * sqrt(n)`, which grows with the square
#: root of the trade count: given two cells of equal edge *quality* it prefers
#: the one that trades more, whether or not that edge survives the spread. On
#: Deep OFI Momentum it duly dropped the confirmation filter and loosened the
#: z-threshold, doubling trades from 1 602 to 3 383 while profit factor fell
#: from 1.094 to 0.931. This floor blocks that trade: at 3x, the spread is at
#: most a quarter of gross edge. It is set from the cost model rather than
#: fitted -- no value of it was tried against results before it was written down.
MIN_EDGE_MULTIPLE = 3.0

#: Train-window drawdown cap, as a fraction of starting equity. A risk limit,
#: applied as a floor and never ranked on: drawdown is a minimum statistic and
#: ranking on it selects the cell with the luckiest worst patch.
MAX_TRAIN_DD_FRACTION = 0.15


def fold_windows():
    """`[(train_lo, train_hi, test_lo, test_hi)]` as epoch seconds."""
    out = []
    for test_from, test_to in FOLDS:
        test_lo = metrics.split_ts(test_from)
        out.append((None, test_lo - EMBARGO_DAYS * 86_400, test_lo,
                    metrics.split_ts(test_to)))
    return out


def cell_fills(strategy, bars, context, ex, progress=True):
    """`{frozen params: [Fill]}` over the whole sample -- the one expensive pass."""
    by_group = search.group_fills(strategy, bars, context, ex, progress=progress)
    order = list(by_group)
    out = {}
    for combination in itertools.product(*(by_group[g] for g in order)):
        combo = {}
        fills = []
        for group_combo, group_fill in combination:
            combo.update(group_combo)
            fills.extend(group_fill)
        if strategy.valid(strategy.all_params(combo)):
            out[search.freeze(combo)] = fills
    return out


def window_stats(fills, ex, lo, hi, initial, span=None):
    """Stats for the trades entered in `[lo, hi)`, sized from `initial`."""
    inside = [f for f in fills if (lo is None or f.entry_ts >= lo)
              and (hi is None or f.entry_ts < hi)]
    sized = execution.size(inside, replace(ex, initial=initial))
    return metrics.stats(sized, initial=initial, span=span), sized


def months_between(lo, hi):
    return max(1.0, (hi - lo) / (365.25 * 86_400 / 12))


def window_points(fills, lo, hi):
    """Per-unit points for the trades entered in `[lo, hi)`."""
    return [f.points for f in fills
            if (lo is None or f.entry_ts >= lo) and (hi is None or f.entry_ts < hi)]


def edge_t(points):
    """t-stat of the per-trade edge: `mean / sd * sqrt(n)`.

    The selection objective. Monthly Sharpe compresses a fold's ~90 train trades
    into ~8 monthly buckets and then takes the Sharpe of those eight, discarding
    most of the sample before the comparison is made -- which is why picks
    wobbled between folds. This is the same quantity estimated from every trade.

    It is also directly comparable to the search haircut: under the null a t-stat
    has unit standard error, so `deflated_t` below simply subtracts
    `sqrt(2 ln N)` rather than having to estimate a standard error first.
    """
    n = len(points)
    if n < 2:
        return 0.0
    mean = sum(points) / n
    var = sum((p - mean) ** 2 for p in points) / (n - 1)
    return mean / math.sqrt(var) * math.sqrt(n) if var > 0 else 0.0


def select(strategy, cells, ex, lo, hi, initial, data_lo):
    """The train-window winner: floors, then plateau score, then plateau width.

    Returns `(key, stat, plateau, n_evaluated)` or `(None, ...)` when nothing
    clears. Nothing here reads a timestamp outside `[lo, hi)`.
    """
    stats = {}
    for key, fills in cells.items():
        stat = window_stats(fills, ex, lo, hi, initial)[0]
        points = window_points(fills, lo, hi)
        stat["edge_t"] = round(edge_t(points), 3)
        stat["edge_pts"] = round(sum(points) / len(points), 4) if points else 0.0
        stats[key] = stat
    # An anchored train window starts at the first bar, so its length -- and
    # therefore the trade count a candidate must produce -- grows fold by fold.
    floor = MIN_TRADES_PER_MONTH * months_between(lo if lo is not None else data_lo, hi)

    # Monthly consistency is a floor, not the objective: it rejects a candidate
    # that stalls and recovers in one burst, but it is too coarse to rank on.
    # The edge and drawdown floors are what stop the t-stat buying significance
    # with trade count -- see MIN_EDGE_MULTIPLE.
    passed = [key for key, stat in stats.items()
              if stat["trades"] >= floor
              and stat["pnl"] > 0
              and stat["max_loss_streak"] <= MAX_LOSS_STREAK
              and stat["top_month_share"] <= MAX_TOP_MONTH_SHARE
              # `entry_cost`, not `spread`: the gate asks that a cell's edge
              # clear its own execution cost by a margin, and cost is now the
              # sum of spread, slippage and commission. Reading `spread` alone
              # would compare against 0.0 on a zero-spread account and pass
              # everything.
              and stat["edge_pts"] >= MIN_EDGE_MULTIPLE * ex.entry_cost
              and stat["max_dd"] <= MAX_TRAIN_DD_FRACTION * initial]

    scored = []
    for key in passed:
        near = [n for n in search.neighbours(strategy, key) if n in stats]
        # Plateau *width*: a cell whose neighbourhood contains a loser is a
        # spike, and is refused however well it scores itself.
        if not near or any(stats[n]["pnl"] <= 0 for n in near):
            continue
        plateau = sum(stats[n]["edge_t"] for n in near) / len(near)
        scored.append((key, stats[key], plateau))

    scored.sort(key=lambda row: min(row[1]["edge_t"], row[2]), reverse=True)
    return (scored[0] if scored else (None, None, None)), len(stats), stats


def deflated_t(t, n_cells):
    """`t` minus the haircut for having picked the best of `n_cells`.

    A t-stat has unit standard error under the null, and the maximum of N such
    draws sits about `sqrt(2 ln N)` above zero even when every cell is
    worthless. `n_cells` must be the *cumulative* trial count (see `trials.py`),
    not one run's grid size: the search that matters is every search you ran.
    """
    return t - math.sqrt(2.0 * math.log(n_cells)) if n_cells >= 2 else t


def bootstrap_edge(points, draws=10_000, seed=0):
    """`(lo, hi)` 95% percentile-bootstrap CI for the mean per-trade edge.

    The Stage 6 gate that replaced eight monthly thresholds. With ~11 trades a
    month, a monthly series holds too few observations to test; the trade series
    holds every one, and a CI that straddles zero says so plainly.
    """
    n = len(points)
    if n < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        total = 0.0
        for _ in range(n):
            total += points[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return (means[int(0.025 * draws)], means[int(0.975 * draws)])


def deflated(msharpe, n_months, n_cells):
    """`msharpe` minus the haircut for having searched `n_cells` of them.

    The best of N noisy estimates is high by about sqrt(2 ln N) standard errors
    even when every cell is worthless; subtracting that is the honest version of
    the in-sample number.
    """
    if n_months < 2 or n_cells < 2:
        return msharpe
    se = math.sqrt((1.0 + 0.5 * msharpe ** 2) / n_months)
    return msharpe - math.sqrt(2.0 * math.log(n_cells)) * se


def median_params(keys, strategy):
    """Per-axis median of the folds' picks, snapped to the grid."""
    out = {}
    for axis in strategy.grid:
        values = sorted(dict(key)[axis] for key in keys)
        middle = values[len(values) // 2]
        out[axis] = min(strategy.grid[axis], key=lambda v: abs(v - middle))
    return out


def grid_steps_apart(strategy, axis, a, b):
    values = list(strategy.grid[axis])
    return abs(values.index(a) - values.index(b))


def run(strategy, initial=INITIAL, progress=True, record_trials=True):
    ex = replace(strategy.execution, initial=initial)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()

    print(f"{strategy.name}: {len(bars)} bars, initial ${initial:.0f}, "
          f"cost {ex.entry_cost}, {len(strategy.grid)} axes")
    cells = cell_fills(strategy, bars, context, ex)
    charged = (
        trials.record(
            strategy.name,
            len(cells),
            f"walkforward, {len(FOLDS)} folds, {len(strategy.grid)} axes",
        )
        if record_trials
        else trials.total(strategy.name)
    )
    print(f"  grid cells: {len(cells)}  cumulative trials: {charged}")

    data_lo = bars[0][TS]
    picks, stitched, stitched_points, rows = [], [], [], []
    equity = initial
    for index, (_, train_hi, test_lo, test_hi) in enumerate(fold_windows(), 1):
        (key, train_stat, plateau), n_cells, _all_stats = select(
            strategy, cells, ex, None, train_hi, initial, data_lo)
        if key is None:
            # No cell cleared, so the fold trades nothing. That is a real
            # outcome and its months stay in the series as flat ones -- drop
            # them and the stitched curve quietly omits its worst stretch.
            print(f"  fold {index}: no cell cleared the train filters -> flat")
            rows.append((index, None, None, None, None, []))
            continue
        test_stat, test_sized = window_stats(cells[key], ex, test_lo, test_hi, equity)
        test_points = window_points(cells[key], test_lo, test_hi)
        picks.append(key)
        stitched.extend(test_sized)
        stitched_points.extend(test_points)
        # Equity compounds across folds, as it does in the account being
        # modelled. Points are carried alongside because a compounding dollar
        # series cannot be compared fold to fold -- a later fold trades larger
        # for the same edge, which is one of the three things that manufactured
        # the apparent 2025/2026 split (Stage 1b).
        equity += test_stat["pnl"]
        rows.append((index, dict(key), train_stat, test_stat, plateau, test_points))
        if progress:
            print(f"  fold {index}: picked {dict(key)}  "
                  f"train t {train_stat['edge_t']:>5.2f} "
                  f"(deflated {deflated_t(train_stat['edge_t'], charged):>6.2f})"
                  f"  -> test pnl {test_stat['pnl']:>8.2f} "
                  f"pts {sum(test_points):>8.1f} trades {test_stat['trades']:>4}")

    return {"cells": cells, "ex": ex, "picks": picks, "rows": rows,
            "stitched": sorted(stitched), "n_cells": len(cells),
            "stitched_points": stitched_points, "trials": charged,
            "initial": initial, "strategy": strategy, "bars": bars,
            "context": context, "oos_span": (metrics.split_ts(FOLDS[0][0]),
                                             metrics.split_ts(FOLDS[-1][1]))}


FIELDS = ("pnl", "trades", "win_rate", "pf", "max_dd", "n_months", "pos_months",
          "worst_month", "best_month", "msharpe", "pos_rate", "max_loss_streak",
          "worst_quarter", "top_month_share")


def print_months(months, initial):
    equity = initial
    print(f"  {'month':>8} {'pnl':>9} {'equity':>10} {'%':>7}")
    for key, value in months.items():
        start = equity
        equity += value
        print(f"  {key:>8} {value:>9.2f} {equity:>10.2f} "
              f"{100 * value / start:>6.2f}%")


def report(result):
    strategy, initial = result["strategy"], result["initial"]
    axes = sorted(strategy.grid)

    print("\n--- parameter stability across folds " + "-" * 30)
    width = max(10, max(len(a) for a in axes))
    print("  fold  " + "  ".join(f"{a:>{width}}" for a in axes)
          + f"  {'test pnl':>9} {'test pts':>9} {'trades':>7}")
    for index, params, _train, test, _plateau, points in result["rows"]:
        if params is None:
            print(f"  {index:>4}  (nothing cleared)")
            continue
        print(f"  {index:>4}  " + "  ".join(f"{params[a]!s:>{width}}" for a in axes)
              + f"  {test['pnl']:>9.2f} {sum(points):>9.1f} {test['trades']:>7}")

    median = median_params(result["picks"], strategy) if result["picks"] else None
    if median:
        print("  med.  " + "  ".join(f"{median[a]!s:>{width}}" for a in axes))
        spread = {a: max(grid_steps_apart(strategy, a, dict(k)[a], median[a])
                         for k in result["picks"]) for a in axes}
        stable = sum(1 for k in result["picks"]
                     if all(grid_steps_apart(strategy, a, dict(k)[a], median[a]) <= 1
                            for a in axes))
        print(f"  max grid steps from median: {spread}")
        print(f"  folds within one step on every axis: {stable}/{len(result['picks'])}")

    stitched = metrics.stats(result["stitched"], initial=initial,
                             span=result["oos_span"])
    print("\n--- stitched out-of-sample (per-fold selection) " + "-" * 18)
    print("  " + "  ".join(f"{f}={stitched[f]}" for f in FIELDS))
    print_months(stitched["months"], initial)
    return median, stitched


def gate(result, stitched_stat, cv_blocks=None):
    """The Stage 6 gate: four performance tests, plus the search haircut.

    Everything the old twelve-row gate measured is still printed by `report`;
    it simply no longer decides. A dozen thresholds against eighteen monthly
    observations mostly test noise, so "nothing passes" carried no information
    about whether a strategy was any good.

    `cv_blocks` is `purged_cv.run(...)["blocks"]` when it has been run; without
    it the coverage gate cannot be evaluated and is reported as such rather
    than silently passing.
    """
    points = result["stitched_points"]
    folds = [row for row in result["rows"] if row[1] is not None]
    positive = sum(1 for row in folds if row[3]["pnl"] > 0)
    lo, hi = bootstrap_edge(points)
    t = edge_t(points)
    dt = deflated_t(t, result["trials"])

    checks = [
        ("stitched OOS PnL > 0", stitched_stat["pnl"] > 0,
         f"{stitched_stat['pnl']:+.2f} ({sum(points):+.1f} pts)"),
        (f"profitable folds >= 2/3 ({math.ceil(2 * len(folds) / 3)}/{len(folds)})",
         len(folds) > 0 and positive >= math.ceil(2 * len(folds) / 3),
         f"{positive}/{len(folds)}"),
        ("per-trade edge CI excludes 0", lo > 0,
         f"mean {sum(points) / max(1, len(points)):+.3f} pts, 95% CI "
         f"[{lo:+.3f}, {hi:+.3f}], t={t:.2f}"),
        (f"deflated t > 0 vs {result['trials']} cumulative trials", dt > 0,
         f"{dt:+.2f}"),
    ]
    if cv_blocks is None:
        checks.append(("purged-CV coverage", None, "not run"))
    else:
        values = [b["points"] for b in cv_blocks]
        mean = sum(values) / len(values)
        worst = min(values)
        checks.append(("no CV block worse than -2x mean block",
                       worst >= -2 * abs(mean),
                       f"worst {worst:+.1f} pts vs mean {mean:+.1f}"))

    print("\n--- Stage 6 gate " + "-" * 48)
    for name, ok, detail in checks:
        mark = "n/a " if ok is None else ("PASS" if ok else "FAIL")
        print(f"  [{mark}] {name:<46} {detail}")
    passed = all(ok for _, ok, _ in checks if ok is not None)
    unknown = any(ok is None for _, ok, _ in checks)
    print(f"  => {'PROMOTED' if passed and not unknown else 'NOT PROMOTED'}"
          f"{' (incomplete: run purged_cv)' if unknown and passed else ''}")
    return {"passed": passed and not unknown,
            "checks": [(n, o, d) for n, o, d in checks]}


def verify(result, params, label):
    """Score one fixed configuration over the whole out-of-sample period."""
    strategy, ex, initial = result["strategy"], result["ex"], result["initial"]
    key = search.freeze({a: params[a] for a in strategy.grid})
    lo, hi = result["oos_span"]
    stat, sized = window_stats(result["cells"][key], ex, lo, hi, initial,
                               span=result["oos_span"])
    print(f"\n--- {label}: {params} over {FOLDS[0][0]} .. {FOLDS[-1][1]} " + "-" * 8)
    print("  " + "  ".join(f"{f}={stat[f]}" for f in FIELDS))
    print_months(stat["months"], initial)
    return stat, sized


def control(result, strategy, params, label):
    """The compiled configuration over the same window -- the do-nothing control.

    A walk-forward that beats no benchmark has not shown that selecting
    parameters is better than leaving them alone, which is the question.
    """
    ex, initial = result["ex"], result["initial"]
    # The control is usually a *different* strategy from the candidate, so it
    # needs its own context -- the candidate's would be the wrong shape.
    context = strategy.context()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(result["bars"], context, group,
                                        strategy.all_params(params)))
    fills = execution.resolve(result["bars"], signals, ex)
    lo, hi = result["oos_span"]
    stat, sized = window_stats(fills, ex, lo, hi, initial, span=result["oos_span"])
    print(f"\n--- {label} over {FOLDS[0][0]} .. {FOLDS[-1][1]} " + "-" * 8)
    print("  " + "  ".join(f"{f}={stat[f]}" for f in FIELDS))
    print_months(stat["months"], initial)
    return stat, sized
