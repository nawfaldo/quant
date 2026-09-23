"""Grid sweep and robustness ranking over any registered strategy.

Nothing here knows what a parameter means. A strategy declares `grid` (axes),
`groups` (which axes each signal group depends on) and `valid` (constraints);
this module walks the space and ranks the results.

Two decompositions keep a six-axis sweep tractable:

  * signals and their per-unit outcomes never depend on account equity, so
    `execution.resolve` runs once per group combination rather than once per
    joint combination;
  * a position's exits depend only on the bars and its own bracket, so the
    fills of two groups can simply be concatenated before sizing.

Ranking deliberately avoids raw PnL. A candidate must clear volume and
consistency floors and be profitable in *both* a train and a later holdout
split, and is then scored on the weaker of its own monthly Sharpe and the
average of its grid neighbours — which favours broad plateaus over lucky cells.
"""
import itertools
import json
import os

from sandbox import data
from sandbox import execution
from sandbox import metrics

DEFAULT_SPLIT = "2026-05-01"
DEFAULT_MIN_TRADES = 120
DEFAULT_MIN_POS_MONTHS = 6
#: Consistency floors. Monthly Sharpe rewards evenness on average but tolerates a
#: strategy that stalls for a while and makes it back in one burst; these reject
#: that shape outright. A losing run longer than a quarter, or a strategy whose
#: single best month is more than half its gross profit, is not consistent
#: whatever its Sharpe says.
DEFAULT_MAX_LOSS_STREAK = 3
DEFAULT_MAX_TOP_MONTH_SHARE = 0.5


def _group_axes(strategy):
    """Group -> the swept axes it depends on, ignoring fixed parameters."""
    return {group: [name for name in names if name in strategy.grid]
            for group, names in strategy.groups().items()}


def _combos(strategy, names):
    """Every valid assignment of `names`, as dicts."""
    out = []
    for values in itertools.product(*(strategy.grid[name] for name in names)):
        combo = dict(zip(names, values))
        if strategy.valid(strategy.all_params(combo)):
            out.append(combo)
    return out


def group_fills(strategy, bars, context, ex, progress=True):
    """`{group: [(combo, fills), ...]}` — the expensive, reusable half of a sweep."""
    out = {}
    for group, names in _group_axes(strategy).items():
        combos = _combos(strategy, names)
        resolved = []
        for combo in combos:
            params = strategy.all_params(combo)
            signals = strategy.signals(bars, context, group, params)
            resolved.append((combo, execution.resolve(bars, signals, ex)))
        if progress:
            print(f"  group {group!r}: {len(resolved)} combinations")
        out[group] = resolved
    return out


def evaluate(strategy, bars=None, context=None, ex=None, split=None, progress=True):
    """Every joint combination, as `{frozen params: stats}`."""
    ex = ex or strategy.execution
    bars = bars if bars is not None else data.load_bars(strategy.bars, strategy.symbol)
    context = context if context is not None else strategy.context()
    split = split if split is not None else metrics.split_ts(DEFAULT_SPLIT)

    by_group = group_fills(strategy, bars, context, ex, progress)
    order = list(by_group)
    results = {}
    for combination in itertools.product(*(by_group[group] for group in order)):
        combo = {}
        fills = []
        for group_combo, group_fill in combination:
            combo.update(group_combo)
            fills.extend(group_fill)
        if not strategy.valid(strategy.all_params(combo)):
            continue
        results[freeze(combo)] = metrics.stats(execution.size(fills, ex), split=split,
                                               initial=ex.initial)
    if progress:
        print(f"  joint combinations: {len(results)}")
    return results


def freeze(combo):
    return tuple(sorted(combo.items()))


def neighbours(strategy, key):
    """Keys one grid step away on exactly one axis."""
    combo = dict(key)
    out = []
    for name, value in combo.items():
        values = list(strategy.grid.get(name, ()))
        if value not in values:
            continue
        index = values.index(value)
        for neighbour_index in (index - 1, index + 1):
            if not 0 <= neighbour_index < len(values):
                continue
            candidate = values[neighbour_index]
            variant = dict(combo)
            variant[name] = candidate
            out.append(freeze(variant))
    return out


def rank(strategy, results, min_trades=DEFAULT_MIN_TRADES,
         min_pos_months=DEFAULT_MIN_POS_MONTHS,
         max_loss_streak=DEFAULT_MAX_LOSS_STREAK,
         max_top_month_share=DEFAULT_MAX_TOP_MONTH_SHARE):
    """Filtered, plateau-scored candidates, best first. -> [(key, stats, plateau)]"""
    passed = [(key, stat) for key, stat in results.items()
              if stat["trades"] >= min_trades
              and stat["pos_months"] >= min_pos_months
              and stat["max_loss_streak"] <= max_loss_streak
              and stat["top_month_share"] <= max_top_month_share
              and stat.get("train", 1) > 0 and stat.get("hold", 1) > 0]

    scored = []
    for key, stat in passed:
        near = [results[n]["msharpe"] for n in neighbours(strategy, key) if n in results]
        plateau = sum(near) / len(near) if near else 0.0
        scored.append((key, stat, round(plateau, 3)))
    scored.sort(key=lambda row: min(row[1]["msharpe"], row[2]), reverse=True)
    return passed, scored


SUMMARY_FIELDS = ["pnl", "trades", "pf", "max_dd", "pos_months", "worst_month",
                  "msharpe", "pos_rate", "max_loss_streak", "worst_quarter",
                  "top_month_share", "train", "hold"]


def report(strategy, results, scored, passed_count, top=25, out_path=None,
           min_trades=DEFAULT_MIN_TRADES, min_pos_months=DEFAULT_MIN_POS_MONTHS,
           max_loss_streak=DEFAULT_MAX_LOSS_STREAK,
           max_top_month_share=DEFAULT_MAX_TOP_MONTH_SHARE):
    """Print the current-parameter baseline and the ranked table."""
    baseline_key = freeze({name: strategy.defaults[name] for name in strategy.grid})
    baseline = results.get(baseline_key)
    if baseline:
        print(f"\ncompiled params {ashort(strategy.defaults, strategy.grid)}:")
        print(" ", {field: baseline[field] for field in SUMMARY_FIELDS if field in baseline})
        print("  months", baseline["months"])
        if all(key != baseline_key for key, _, _ in scored):
            # Easy to misread the table otherwise: a floor tuned for one regime
            # can quietly exclude the parameters actually in production.
            print(f"  NOTE: excluded by the filters (trades>={min_trades}, "
                  f"pos_months>={min_pos_months}, max_loss_streak<={max_loss_streak}, "
                  f"top_month_share<={max_top_month_share}, train>0, hold>0) "
                  f"and so absent below")
    print(f"\npassing filters: {passed_count} / {len(results)}")

    axes = sorted(strategy.grid)
    width = max(12, max((len(a) for a in axes), default=12))
    print(f"\ntop {top} by robustness (own msharpe & plateau both high):")
    print("  ".join([f"{a:>{width}}" for a in axes])
          + f'  {"pnl":>7} {"tr":>4} {"pf":>5} {"dd":>6} {"pos":>3} '
            f'{"worst":>7} {"mSh":>6} {"plat":>6} {"str":>4} {"wQ":>7} {"top":>5} '
            f'{"train":>7} {"hold":>7}')
    for key, stat, plateau in scored[:top]:
        combo = dict(key)
        print("  ".join(f"{combo[a]!s:>{width}}" for a in axes)
              + f'  {stat["pnl"]:>7.0f} {stat["trades"]:>4} {stat["pf"]:>5.2f} '
                f'{stat["max_dd"]:>6.0f} {stat["pos_months"]:>3} '
                f'{stat["worst_month"]:>7.0f} {stat["msharpe"]:>6.2f} {plateau:>6.2f} '
                f'{stat["max_loss_streak"]:>4} {stat["worst_quarter"]:>7.0f} '
                f'{stat["top_month_share"]:>5.2f} '
                f'{stat.get("train", 0):>7.0f} {stat.get("hold", 0):>7.0f}')

    if out_path:
        with open(out_path, "w") as f:
            json.dump([{"params": dict(key), "plateau": plateau, **stat}
                       for key, stat, plateau in scored[:60]], f, indent=1)
        print(f"\nwrote {os.path.basename(out_path)} (top 60)")


def ashort(params, grid):
    return {name: params[name] for name in sorted(grid) if name in params}


def sweep(strategy, top=25, out_path=None, min_trades=DEFAULT_MIN_TRADES,
          min_pos_months=DEFAULT_MIN_POS_MONTHS,
          max_loss_streak=DEFAULT_MAX_LOSS_STREAK,
          max_top_month_share=DEFAULT_MAX_TOP_MONTH_SHARE, **kwargs):
    """Load, evaluate, rank and print. Returns the ranked list."""
    print(f"sweeping {strategy.name} over {len(strategy.grid)} axes ...")
    results = evaluate(strategy, **kwargs)
    passed, scored = rank(strategy, results, min_trades, min_pos_months,
                          max_loss_streak, max_top_month_share)
    report(strategy, results, scored, len(passed), top=top, out_path=out_path,
           min_trades=min_trades, min_pos_months=min_pos_months,
           max_loss_streak=max_loss_streak, max_top_month_share=max_top_month_share)
    return scored


def backtest(strategy, params=None, ex=None, bars=None, context=None, split=None):
    """One configuration end to end. Returns (stats, sized trades)."""
    ex = ex or strategy.execution
    bars = bars if bars is not None else data.load_bars(strategy.bars, strategy.symbol)
    context = context if context is not None else strategy.context()
    merged = strategy.all_params(params)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, merged))
    sized = execution.run(bars, signals, ex)
    return metrics.stats(sized, split=split, initial=ex.initial), sized
