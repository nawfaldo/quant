"""The Deep OFI Momentum optimization run, end to end and reproducible.

Order of operations follows OPTIMIZATION_PLAN.md:

  Stage 3  three tied axes per variant (see `strategies/ofi_momentum.py`),
           plateau-width selection and the deflated Sharpe, both inside
           `walkforward.select`
  Stage 4  anchored walk-forward, selection confined to each train window
  Stage 5  structure, in `ofi_filters.py` and the `(confirmed)` variant
  Stage 6  the promotion gate, evaluated on stitched out-of-sample results

Four candidates are searched and all four are logged, because the count of
things tried is the input to the deflated-Sharpe haircut and unwritten it is a
guess: fixed brackets, ATR-scaled brackets, VIX-scaled brackets, and the fixed
form with aggressive-flow confirmation required.

Each is run through the gate twice:

  * **plateau selection** -- `walkforward.select`, i.e. the consistency floors
    and the requirement that every immediate grid neighbour also be profitable;
  * **relaxed selection** -- the best train monthly Sharpe and nothing else.

The second exists so that "nothing cleared the floors" cannot be mistaken for
"the floors were too strict". If the relaxed arm is also negative, the surface
has no profitable region to find rather than one the selector refused.

Everything is priced on a $1000 Forex NQ account at the `idk` environment's
0.2-point spread.
"""
import json
import subprocess
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf
from sandbox.paths import result_path

INITIAL = 1000.0
CANDIDATES = [
    "Deep OFI Momentum (tied)",
    "Deep OFI Momentum (atr)",
    "Deep OFI Momentum (vix)",
    "Deep OFI Momentum (confirmed)",
]
COMPILED = "Deep OFI Momentum"


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is nice to have, not required
        return "unknown"


def full_sample(strategy, params, label, initial=INITIAL):
    """One configuration over every bar available, months included."""
    ex = replace(strategy.execution, initial=initial)
    stat, _ = search.backtest(strategy, params, ex=ex)
    print(f"\n--- {label} -- full sample " + "-" * 26)
    print("  " + "  ".join(f"{f}={stat[f]}" for f in wf.FIELDS))
    wf.print_months(stat["months"], initial)
    return stat


def relaxed(strategy, cells, ex, initial):
    """Select each fold's train winner on monthly Sharpe alone.

    No consistency floor and no plateau-width requirement -- the weakest honest
    selector there is. Still walk-forward: the pick sees only its train window
    and is scored once on the test window.
    """
    picks, stitched, rows = [], [], []
    equity = initial
    for index, (_, train_hi, test_lo, test_hi) in enumerate(wf.fold_windows(), 1):
        stats = {k: wf.window_stats(f, ex, None, train_hi, initial)[0]
                 for k, f in cells.items()}
        key, train = max(stats.items(), key=lambda kv: kv[1]["msharpe"])
        test, sized = wf.window_stats(cells[key], ex, test_lo, test_hi, equity)
        equity += test["pnl"]
        picks.append(key)
        stitched.extend(sized)
        rows.append((index, dict(key), train, test))
        print(f"  fold {index}: {dict(key)}  train mSh {train['msharpe']:>5.2f} "
              f"pnl {train['pnl']:>7.1f}  -> test pnl {test['pnl']:>7.1f} "
              f"trades {test['trades']:>4}")
    return picks, stitched, rows


GATES = [
    ("folds selected = 5", lambda s, r: f"{r['n_picks']}/5", lambda s, r: r["n_picks"] == 5),
    ("trades total >= 150", lambda s, r: s["trades"], lambda s, r: s["trades"] >= 150),
    ("profitable folds >= 4", lambda s, r: f"{r['profitable_folds']}/5",
     lambda s, r: r["profitable_folds"] >= 4),
    ("pos_rate >= 0.70", lambda s, r: s["pos_rate"], lambda s, r: s["pos_rate"] >= 0.70),
    ("max_loss_streak <= 3", lambda s, r: s["max_loss_streak"],
     lambda s, r: s["max_loss_streak"] <= 3),
    ("worst_quarter > -3x avg month", lambda s, r: s["worst_quarter"],
     lambda s, r: s["worst_quarter"] > -3 * abs(s["pnl"] / max(1, s["n_months"]))),
    ("top_month_share <= 0.35", lambda s, r: s["top_month_share"],
     lambda s, r: s["top_month_share"] <= 0.35),
    ("monthly Sharpe >= 0.75", lambda s, r: s["msharpe"], lambda s, r: s["msharpe"] >= 0.75),
    ("OOS/IS msharpe >= 0.5", lambda s, r: round(r["oos_is_ratio"], 2),
     lambda s, r: r["oos_is_ratio"] >= 0.5),
    ("deflated Sharpe > 0", lambda s, r: round(r["deflated"], 2),
     lambda s, r: r["deflated"] > 0),
    ("param stability (>=4/5 within one step)", lambda s, r: f"{r['stable']}/5",
     lambda s, r: r["stable"] >= 4),
]


def gate(label, stitched, extras):
    print(f"\n--- Stage 6 gate: {label} " + "-" * 20)
    verdicts = []
    for name, value, test in GATES:
        ok = test(stitched, extras)
        verdicts.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<42} {value(stitched, extras)}")
    print(f"  VERDICT: {'PROMOTE' if all(verdicts) else 'DO NOT TRADE'} "
          f"({sum(verdicts)}/{len(verdicts)} gates)")
    return all(verdicts), sum(verdicts)


def extras_for(strategy, picks, rows, stitched, n_cells, initial):
    """The gate inputs that are not fields of the stitched stats bundle."""
    profitable = sum(1 for row in rows if row[-1] is not None and row[-1]["pnl"] > 0)
    in_sample = [row[2]["msharpe"] for row in rows if row[2] is not None]
    is_msharpe = sum(in_sample) / len(in_sample) if in_sample else 0.0
    median = wf.median_params(picks, strategy) if picks else None
    stable = sum(1 for k in picks
                 if median and all(wf.grid_steps_apart(strategy, a, dict(k)[a], median[a]) <= 1
                                   for a in strategy.grid))
    return median, {
        "n_picks": len(picks),
        "profitable_folds": profitable,
        "is_msharpe": is_msharpe,
        "oos_is_ratio": (stitched["msharpe"] / is_msharpe) if is_msharpe else 0.0,
        "deflated": wf.deflated(stitched["msharpe"], max(1, stitched["n_months"]), n_cells),
        "stable": stable,
    }


def run_candidate(name, log):
    strategy = strategies.get(name)
    print("\n" + "=" * 78)
    print(f"=== {name}")
    print("=" * 78)

    result = wf.run(strategy, initial=INITIAL)
    ex, cells = result["ex"], result["cells"]
    lo, hi = result["oos_span"]

    print("\n--- plateau selection (consistency floors + neighbour width) " + "-" * 5)
    median, extras = extras_for(strategy, result["picks"],
                                [(i, p, tr, te) for i, p, tr, te, _ in result["rows"]],
                                metrics.stats(result["stitched"], initial=INITIAL,
                                              span=result["oos_span"]),
                                result["n_cells"], INITIAL)
    plateau_oos = metrics.stats(result["stitched"], initial=INITIAL, span=result["oos_span"])
    print("  " + "  ".join(f"{f}={plateau_oos[f]}" for f in wf.FIELDS))
    wf.print_months(plateau_oos["months"], INITIAL)
    plateau_pass, plateau_score = gate(f"{name} / plateau selection",
                                       plateau_oos, extras)

    print("\n--- relaxed selection (best train monthly Sharpe, no floors) " + "-" * 5)
    picks, stitched, rows = relaxed(strategy, cells, ex, INITIAL)
    relaxed_oos = metrics.stats(stitched, initial=INITIAL, span=result["oos_span"])
    r_median, r_extras = extras_for(strategy, picks, rows, relaxed_oos,
                                    result["n_cells"], INITIAL)
    print("\n  stitched out-of-sample:")
    print("  " + "  ".join(f"{f}={relaxed_oos[f]}" for f in wf.FIELDS))
    wf.print_months(relaxed_oos["months"], INITIAL)
    relaxed_pass, relaxed_score = gate(f"{name} / relaxed selection",
                                       relaxed_oos, r_extras)

    if r_median:
        wf.verify(result, r_median, f"{name} median of relaxed picks")

    log[name] = {
        "cells": result["n_cells"],
        "grid": strategy.grid,
        "fixed": {k: v for k, v in strategy.defaults.items() if k not in strategy.grid},
        "plateau": {
            "picks": [dict(k) for k in result["picks"]],
            "median": median, "stitched_oos": plateau_oos,
            "gates_passed": plateau_score, "promoted": plateau_pass, **{
                k: v for k, v in extras.items() if k != "n_picks"}},
        "relaxed": {
            "picks": [dict(k) for k in picks],
            "median": r_median, "stitched_oos": relaxed_oos,
            "gates_passed": relaxed_score, "promoted": relaxed_pass, **{
                k: v for k, v in r_extras.items() if k != "n_picks"}},
    }
    return result["n_cells"]


def main():
    sha = git_sha()
    print(f"git {sha}  |  initial ${INITIAL:.0f}  |  Forex NQ  |  spread 0.2")
    compiled = strategies.get(COMPILED)
    print(f"data range: {data.bar_range(compiled.bars, compiled.symbol)}")

    base = full_sample(compiled, None, "compiled parameters (benchmark)")

    log = {}
    total_cells = sum(run_candidate(name, log) for name in CANDIDATES)

    print("\n" + "=" * 78)
    print(f"=== summary -- {total_cells} grid cells evaluated across "
          f"{len(CANDIDATES)} candidates")
    print("=" * 78)
    print(f"  {'candidate':<32} {'selection':<9} {'oos pnl':>9} {'trades':>7} "
          f"{'pf':>6} {'mSh':>6} {'gates':>6}")
    print(f"  {'compiled (full sample, in-sample)':<32} {'-':<9} "
          f"{base['pnl']:>9.2f} {base['trades']:>7} {base['pf']:>6.3f} "
          f"{base['msharpe']:>6.2f} {'-':>6}")
    for name in CANDIDATES:
        for arm in ("plateau", "relaxed"):
            row = log[name][arm]
            stat = row["stitched_oos"]
            print(f"  {name:<32} {arm:<9} {stat['pnl']:>9.2f} {stat['trades']:>7} "
                  f"{stat['pf']:>6.3f} {stat['msharpe']:>6.2f} "
                  f"{row['gates_passed']:>4}/11")

    promoted = [n for n in CANDIDATES
                if log[n]["plateau"]["promoted"] or log[n]["relaxed"]["promoted"]]
    print(f"\n  promoted: {promoted or 'none'}")

    out = {"git": sha, "initial": INITIAL, "spread": 0.2,
           "data_range": list(data.bar_range(compiled.bars, compiled.symbol)),
           "folds": wf.FOLDS, "embargo_days": wf.EMBARGO_DAYS,
           "cells_evaluated_total": total_cells,
           "compiled_full_sample": base, "candidates": log,
           "promoted": promoted}
    output = result_path("deep_ofi_momentum_walkforward.json")
    with output.open("w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()
