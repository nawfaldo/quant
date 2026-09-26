"""Does the WAY `cfd_families` picks a winner decide how it does out of sample?

`cfd_families.choose` takes the one cell with the best `quality` out of a grid of
about 1,300 per family, subject to the gates. Under the live fill model 329 of
the 465 winners it has sealed lost money on the holdout (jp225 and hk50, whose
fills are known to be wrong, left out), and not one in-sample figure --
return, drawdown, trades, profit factor, t-stat, score, robust neighbours --
ranks them against their holdout result: every Spearman sits inside +/-0.06.
That is what taking the luckiest of N draws looks like, and it means the rule
that picks the cell is worth testing as hard as the cells.

So this scores EVERY cell of a family's grid on both windows, once, keeps the
two tables in the disk cache, and then asks which selection rule would have
done best on the holdout:

    current    `cfd_families.choose` ranking on `growth`, as every seal before
               2026-09-25 did
    tstat      `current`'s eligible cells, ranked by per-trade t-statistic
               instead of compounded growth
    sharpe     the same cells ranked by monthly Sharpe
    plateau    the passing cell whose neighbourhood grew most ON AVERAGE, so a
               lone spike ringed by losers loses to a broad hill
    entry      the passing cell whose ENTRY grew most on average across every
               exit, stop and filter it was tried with
    top5       the five best `choose`-eligible cells, equal weight
    eligible   every `choose`-eligible cell, equal weight
    breadth    `current`, taken only where most of the grid made money
    gated      `current`, taken only where the grid ranks its cells the same way
               in the early in-sample years as in the late ones
               (`inner_persistence` above zero) -- decided with no holdout data
    gated_plateau  `plateau` behind the same gate
    all        every cell in the grid, equal weight -- no selection at all

The ensembles are scored as equal slices of capital run side by side, so their
holdout return is the mean of their members' and their drawdown is quoted as
the members' mean, which overstates it.

The holdout is the one every wave has already been read against
([[exness-survivor-pool-is-oos-conditioned]]). That matters much less here than
it does for a cell: this chooses between a handful of RULES, each applied the
same way to every family, rather than between thousands of parameter sets.

    python -m sandbox.research.selection_study run --symbols usdjpy,ethusd \
        --bar-minutes 240 --groups swingbar --full-day --workers 3
    python -m sandbox.research.selection_study report --symbols usdjpy,ethusd \
        --bar-minutes 240 --groups swingbar --full-day
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import multiprocessing
import os
import statistics
import sys

# `EXNESS_FULL_DAY` is read when `cfd_families` is imported, so the swing grids
# have to set it first. A spawned worker inherits the environment.
if "--full-day" in sys.argv:
    os.environ["EXNESS_FULL_DAY"] = "1"

from sandbox.research import cfd_families as ef  # noqa: E402
from sandbox.research import es_strategy_research as es  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")

#: What a cell keeps from each window. `annual` is what `passes` and `quality`
#: read, so the in-sample side keeps it and `choose` runs on it unchanged.
KEEP_IS = ("return_pct", "max_dd_pct", "trades", "pf", "final", "pnl",
           "fill_rate", "annual", "monthly_sharpe", "edge_vs_drift_t_stat")
KEEP_OOS = ("return_pct", "max_dd_pct", "trades", "pf", "final", "pnl",
            "monthly_sharpe")

RULES = ("current", "tstat", "sharpe", "plateau", "entry", "top5", "eligible",
         "breadth", "gated", "gated_plateau", "all")


def inner_persistence(symbol, cells, results):
    """Does the grid agree WITH ITSELF inside the in-sample window?

    Each cell's growth over the first half of the in-sample years against its
    growth over the second half, Spearman over every cell of the family. It
    reads nothing past `IS_END`, so it can decide a selection -- which the
    holdout persistence it is a proxy for cannot."""
    years = ef.is_years(symbol)
    if len(years) < 2:
        return float("nan")
    cut = len(years) // 2

    def half(annual, span):
        return sum(100.0 * math.log(max(1e-6, 1.0 + (annual.get(str(y)) or {})
                                         .get("return_pct", 0.0) / 100.0))
                   for y in span)

    first, second = [], []
    for params in cells:
        annual = results[es.frozen(params)].get("annual") or {}
        first.append(half(annual, years[:cut]))
        second.append(half(annual, years[cut:]))
    return spearman(first, second)

#: Share of a family's grid that must have made money in sample for `breadth`
#: to take its `current` winner.
BREADTH = 0.5


def surface_path(symbol, bar, only):
    """Keyed on every switch that changes a cell's numbers. The swing and
    fastbar seals ran with `EXNESS_FILLS_ONLY=1` (sealed then as `live_only`),
    and without it the same cell books extra idealised trades before the broker
    table starts -- usdjpy 240m `sb_rsi` reads +111.3% instead of +98.3%."""
    tag = ef.scope_tag(only) or "all"
    flags = (("_fullday" if ef.FULL_DAY else "")
             + ("_fillsonly" if ef.FILLS_ONLY else "")
             + ("" if ef.MTM_PEAK else "_closedpeak"))
    return os.path.join(CACHE, f"selection_surface_{ef.BROKER}_{symbol}_"
                               f"{ef.label_bar(bar)}_{tag}{flags}.json.gz")


def run(symbol, bar, only, workers):
    """Every cell of every requested family, in sample and on the holdout."""
    ef.BAR_MINUTES = bar
    specs = ef.axes(symbol, bar, only)
    jobs = [(family, params, None, None)
            for family, axis in specs.items() for params in ef.candidates(axis)]
    ef.prewarm(symbol, "validate", bar)
    ef.prewarm(symbol, "select", bar)
    workers, _ = ef.fit_workers(symbol, "validate", bar, only, workers)
    print(f"{symbol} {ef.label_bar(bar)}: {len(jobs):,} cells over "
          f"{len(specs)} families, both windows, {workers} workers", flush=True)
    carried = ef.worker_specs(symbol)
    with multiprocessing.Pool(workers, ef._init_worker,
                              (symbol, "select", carried, bar, only)) as pool:
        inside = ef._dispatch(pool, jobs, workers, note="IS  ")
    with multiprocessing.Pool(workers, ef._init_worker,
                              (symbol, "validate", carried, bar, only,
                               (ef.IS_END, ef.OOS_END))) as pool:
        outside = ef._dispatch(pool, jobs, workers, note="OOS ")
    families = {}
    for family, axis in specs.items():
        rows = []
        for params in ef.candidates(axis):
            key = es.frozen(params)
            rows.append([params,
                         {k: inside[family][key].get(k) for k in KEEP_IS},
                         {k: outside[family][key].get(k) for k in KEEP_OOS}])
        families[family] = rows
    payload = {"symbol": symbol, "bar_minutes": bar, "full_day": ef.FULL_DAY,
               "fills_only": ef.FILLS_ONLY, "mtm_peak": ef.MTM_PEAK,
               "first_full_year": ef.INSTRUMENTS[symbol]["first_full_year"],
               "families": families}
    path = surface_path(symbol, bar, only)
    os.makedirs(CACHE, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    print(f"cached {os.path.normpath(path)}", flush=True)


# --------------------------------------------------------------------------- #
# selection rules
# --------------------------------------------------------------------------- #

def growth(stat):
    """In-sample log growth in percent: continuous, so a failing neighbour still
    counts for what it made rather than for minus infinity."""
    final = stat.get("final") or 0.0
    return 100.0 * math.log(final / ef.INITIAL_BALANCE) if final > 0 else -500.0


def entry_key(params):
    """The cell with its exit, stop and filters removed: the entry alone."""
    return es.frozen({k: v for k, v in params.items()
                      if k not in ef._FIRE_EXIT_AXES})


def pick(symbol, family, spec, results, bar):
    """`{rule: [params, ...]}` -- one cell for a picking rule, several for an
    ensemble, none where the rule selects nothing."""
    frozen = es.frozen
    cells = ef.candidates(spec)
    out = {rule: [] for rule in RULES}
    out["all"] = cells

    # `growth` by name: it is the rule every seal before 2026-09-25 used, and
    # `cfd_families` now defaults to `tstat` -- which is this study's `tstat`.
    winner = ef.choose(symbol, family, results, bar, rank="growth")
    if winner is not None:
        out["current"] = [winner["params"]]
        made = sum(results[frozen(p)]["pnl"] > 0 for p in cells) / len(cells)
        if made >= BREADTH:
            out["breadth"] = [winner["params"]]

    by_entry = {}
    for params in cells:
        by_entry.setdefault(entry_key(params), []).append(
            growth(results[frozen(params)]))
    entry_mean = {key: statistics.fmean(v) for key, v in by_entry.items()}

    passing, eligible = [], []
    for params in cells:
        stat = results[frozen(params)]
        own = ef.quality(symbol, stat)
        if not math.isfinite(own):
            continue
        bare = results.get(frozen({**params, "trend": "none",
                                   "vol_mode": spec["vol_mode"][0]}))
        if bare is None or bare["pnl"] <= 0 or bare["pf"] < 1:
            continue
        near = ef.neighbours(params, spec)
        hill = statistics.fmean([growth(stat)]
                                + [growth(results[frozen(p)]) for p in near])
        passing.append((hill, entry_mean[entry_key(params)], own, params))
        robust = [p for p in near
                  if ef.passes(symbol, results[frozen(p)], ef.NEIGHBOUR_DD)]
        if near and len(robust) >= math.ceil(.6 * len(near)):
            eligible.append((own, stat.get("edge_vs_drift_t_stat") or 0.0,
                             params, stat.get("monthly_sharpe") or 0.0))
    if passing:
        out["plateau"] = [max(passing, key=lambda row: (row[0], row[2]))[3]]
        out["entry"] = [max(passing, key=lambda row: (row[1], row[2]))[3]]
    # THE SELF-CONSISTENCY GATE: take a winner only from a grid whose early
    # in-sample years rank its cells the same way its late ones do.
    if inner_persistence(symbol, cells, results) > 0:
        out["gated"] = list(out["current"])
        out["gated_plateau"] = list(out["plateau"])
    if eligible:
        # The per-trade t-statistic rather than compounded growth: the same
        # gates and the same eligible set as `current`, ranked on how steady
        # the edge was instead of how much it made.
        out["tstat"] = [max(eligible, key=lambda row: (row[1], row[0]))[2]]
        out["sharpe"] = [max(eligible, key=lambda row: (row[3], row[0]))[2]]
    eligible.sort(key=lambda row: -row[0])
    out["top5"] = [row[2] for row in eligible[:5]]
    out["eligible"] = [row[2] for row in eligible]
    return out


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def spearman(x, y):
    def rank(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        for position, index in enumerate(order):
            out[index] = float(position)
        return out
    if len(x) < 3:
        return float("nan")
    rx, ry = rank(x), rank(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return cov / den if den else float("nan")


#: The shared axes every family is crossed with. `settings` asks, for each,
#: whether the value that did best IN SAMPLE is the one that did best on the
#: holdout -- an axis where it is not adds cells to the search and nothing else.
SHARED_AXES = ("exit_mode", "stop_day", "trend", "vol_mode", "direction")


def settings(surfaces):
    """Per shared axis: each value's mean in-sample growth and mean holdout
    return, pooled over every cell of every family, and how often the family's
    in-sample best value was also its holdout best."""
    for axis in SHARED_AXES:
        pooled, agree, asked = {}, 0, 0
        for families in surfaces:
            for rows in families.values():
                per = {}
                for params, inside, outside in rows:
                    if axis not in params:
                        continue
                    value = str(params[axis])
                    per.setdefault(value, ([], []))
                    per[value][0].append(growth(inside))
                    per[value][1].append(outside["return_pct"])
                    slot = pooled.setdefault(value, ([], []))
                    slot[0].append(growth(inside))
                    slot[1].append(outside["return_pct"])
                if len(per) > 1:
                    asked += 1
                    best_is = max(per, key=lambda v: statistics.fmean(per[v][0]))
                    best_oos = max(per, key=lambda v: statistics.fmean(per[v][1]))
                    agree += best_is == best_oos
        if len(pooled) < 2:
            continue
        print(f"\n  {axis}: in-sample best value was also the holdout best in "
              f"{agree}/{asked} families (chance {100 / len(pooled):.0f}%)")
        for value in sorted(pooled, key=lambda v: -statistics.fmean(pooled[v][1])):
            inside, outside = pooled[value]
            print(f"    {value:12} IS growth {statistics.fmean(inside):+7.1f}  "
                  f"OOS {statistics.fmean(outside):+6.1f}%  "
                  f"OOS>0 {100 * sum(v > 0 for v in outside) / len(outside):4.0f}%")


def report(symbols, bar, only):
    """Every rule's holdout record, per run and pooled."""
    pooled = {rule: [] for rule in RULES}
    signals = []
    surfaces = []
    for symbol in symbols:
        path = surface_path(symbol, bar, only)
        if not os.path.exists(path):
            print(f"{symbol} {ef.label_bar(bar)}: no cached surface -- `run` it first")
            continue
        payload = load(path)
        ef.BAR_MINUTES = bar
        ef.INSTRUMENTS[symbol]["first_full_year"] = payload["first_full_year"]
        specs = ef.axes(symbol, bar, only)
        surfaces.append({f: rows for f, rows in payload["families"].items()
                         if f in specs})
        per_run = {rule: [] for rule in RULES}
        for family, rows in payload["families"].items():
            if family not in specs:
                continue
            results, holdout = {}, {}
            for params, inside, outside in rows:
                key = es.frozen(ef.rehydrate(params))
                results[key], holdout[key] = inside, outside
            chosen = pick(symbol, family, specs[family], results, bar)
            for rule, members in chosen.items():
                if not members:
                    continue
                oos = [holdout[es.frozen(p)] for p in members]
                row = {"symbol": symbol, "family": family,
                       "ret": statistics.fmean(s["return_pct"] for s in oos),
                       "dd": statistics.fmean(s["max_dd_pct"] for s in oos),
                       "n": len(members)}
                per_run[rule].append(row)
                pooled[rule].append(row)
            if chosen["current"]:
                params = chosen["current"][0]
                cells = ef.candidates(specs[family])
                signals.append({
                    "oos": holdout[es.frozen(params)]["return_pct"],
                    "inner": inner_persistence(symbol, cells, results),
                    "breadth": sum(results[es.frozen(p)]["pnl"] > 0
                                   for p in cells) / len(cells),
                    "eligible": len(chosen["eligible"]),
                    "winner_growth": growth(results[es.frozen(params)]),
                    "entry_mean": statistics.fmean(
                        growth(results[es.frozen(p)]) for p in cells
                        if entry_key(p) == entry_key(params)),
                })
        print(f"\n{symbol} {ef.label_bar(bar)}  ({len(payload['families'])} families)")
        table(per_run)
    if len(symbols) > 1:
        print(f"\nPOOLED  {', '.join(symbols)}  {ef.label_bar(bar)}")
        table(pooled)
    paired(pooled)
    if len(signals) >= 5:
        print(f"\nwhat predicts a `current` winner's holdout return "
              f"({len(signals)} winners), Spearman:")
        oos = [s["oos"] for s in signals]
        for key in ("inner", "breadth", "eligible", "entry_mean",
                    "winner_growth"):
            print(f"  {key:14} {spearman([s[key] for s in signals], oos):+.3f}")
        print("\n  `current` winners kept by a breadth floor:")
        for floor in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6):
            kept = [s["oos"] for s in signals if s["breadth"] >= floor]
            if kept:
                print(f"    breadth >= {floor:.1f}  {len(kept):4} winners  mean OOS "
                      f"{statistics.fmean(kept):+6.1f}%  median "
                      f"{statistics.median(kept):+6.1f}%  OOS>0 "
                      f"{100 * sum(v > 0 for v in kept) / len(kept):3.0f}%")
    persistence(surfaces)
    print("\nshared axes, pooled over every cell:")
    settings(surfaces)
    return pooled


def persistence(surfaces):
    """Inside one family's grid, does a better in-sample cell do better on the
    holdout? Spearman over the cells, one number per family. Near zero means
    the surface carries no ranking information and no picking rule can help --
    only spreading over many cells can."""
    values = []
    for families in surfaces:
        for rows in families.values():
            x = [growth(inside) for _, inside, _ in rows]
            y = [outside["return_pct"] for _, _, outside in rows]
            rho = spearman(x, y)
            if rho == rho:
                values.append(rho)
    if values:
        print(f"\nin-sample rank vs holdout rank inside a family's grid "
              f"({len(values)} families): mean Spearman "
              f"{statistics.fmean(values):+.3f}, median "
              f"{statistics.median(values):+.3f}, positive in "
              f"{100 * sum(v > 0 for v in values) / len(values):.0f}%")


def paired(results):
    """Each rule against `current` on the families BOTH picked from, so a rule
    cannot look better by declining the hard ones."""
    base = {(r["symbol"], r["family"]): r["ret"] for r in results["current"]}
    print(f"\n  vs current, same families:  {'n':>4}{'mean diff':>11}{'t':>7}"
          f"{'wins':>7}")
    for rule in RULES:
        if rule == "current":
            continue
        diffs = [r["ret"] - base[(r["symbol"], r["family"])]
                 for r in results[rule] if (r["symbol"], r["family"]) in base]
        if len(diffs) < 3:
            continue
        sd = statistics.stdev(diffs)
        t = statistics.fmean(diffs) / (sd / math.sqrt(len(diffs))) if sd else 0.0
        print(f"  {rule:28}{len(diffs):>4}{statistics.fmean(diffs):>+10.1f}%"
              f"{t:>+7.2f}{100 * sum(d > 0 for d in diffs) / len(diffs):>6.0f}%")


def table(results):
    print(f"  {'rule':10}{'picked':>7}{'mean OOS':>10}{'median':>9}"
          f"{'OOS>0':>8}{'mean dd':>9}")
    for rule in RULES:
        rows = results[rule]
        if not rows:
            print(f"  {rule:10}{0:>7}")
            continue
        values = [r["ret"] for r in rows]
        print(f"  {rule:10}{len(rows):>7}{statistics.fmean(values):>+9.1f}%"
              f"{statistics.median(values):>+8.1f}%"
              f"{100 * sum(v > 0 for v in values) / len(values):>7.0f}%"
              f"{statistics.fmean(r['dd'] for r in rows):>8.1f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--bar-minutes", type=int, default=240)
    parser.add_argument("--groups", default=None)
    parser.add_argument("--families", default=None)
    parser.add_argument("--full-day", action="store_true",
                        help="EXNESS_FULL_DAY=1, as the swingbar seals ran")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    only = ef.expand_families(args.families, args.groups)
    symbols = [s for s in args.symbols.replace(" ", "").split(",") if s]
    for symbol in symbols:
        # The sealed swing and fastbar runs priced us500 on its weekend quote
        # with `--stale-spreads`; the live maps price nearly every bar anyway.
        ef.resolve(symbol, allow_stale=True)
    if args.command == "run":
        for symbol in symbols:
            run(symbol, args.bar_minutes, only, args.workers)
    else:
        report(symbols, args.bar_minutes, only)


if __name__ == "__main__":
    main()
