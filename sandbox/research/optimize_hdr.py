"""The Hourly Delta Reversal optimization run, end to end and reproducible.

Order of operations follows OPTIMIZATION_PLAN.md:

  Stage 3  three tied axes (see `HourlyDeltaReversalTied`), plateau-width
           selection, deflated Sharpe -- all inside `walkforward.select`
  Stage 4  anchored walk-forward, selection confined to each train window
  Stage 6  the promotion gate, evaluated on stitched out-of-sample results

Everything is priced on a $1000 Forex NQ account at the `idk` environment's
0.2-point spread.
"""
import json
import subprocess
import sys
from dataclasses import replace

from sandbox import execution
from sandbox import metrics
from sandbox import search
from sandbox import strategies
from sandbox import walkforward as wf
from sandbox.paths import result_path

INITIAL = 1000.0
BENCHMARK = "Hourly Delta Reversal (fixed)"
CANDIDATE = "Hourly Delta Reversal"


def git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is nice to have, not required
        return "unknown"


def full_sample(strategy, params, label, initial):
    """One configuration over every bar available, months included."""
    ex = replace(strategy.execution, initial=initial)
    stat, sized = search.backtest(strategy, params, ex=ex)
    print(f"\n--- {label} -- full sample " + "-" * 30)
    print("  " + "  ".join(f"{f}={stat[f]}" for f in wf.FIELDS))
    wf.print_months(stat["months"], initial)
    return stat, sized


def fold_agreement(result):
    """How often each cell ranked first on train Sharpe, fold by fold.

    Stage 4's real output is parameter stability. The selector's pick can move
    for plateau reasons while the underlying surface does not, so this reports
    the surface directly.
    """
    strategy, ex = result["strategy"], result["ex"]
    print("\n--- top train cell per fold (surface stability) " + "-" * 18)
    tops = []
    for index, (_, train_hi, _, _) in enumerate(wf.fold_windows(), 1):
        stats = {k: wf.window_stats(f, ex, None, train_hi, result["initial"])[0]
                 for k, f in result["cells"].items()}
        top = max(stats.items(), key=lambda kv: kv[1]["msharpe"])
        tops.append(top[0])
        print(f"  fold {index}: {dict(top[0])}  train mSh {top[1]['msharpe']:.2f} "
              f"pnl {top[1]['pnl']:.1f}")
    unanimous = len(set(tops)) == 1
    print(f"  same cell on top in every fold: {unanimous}")
    return tops[0] if unanimous else None


GATES = [
    ("folds selected", lambda s, r: f"{len(r['picks'])}/5", lambda s, r: len(r["picks"]) == 5),
    ("trades total", lambda s, r: s["trades"], lambda s, r: s["trades"] >= 150),
    ("profitable folds", lambda s, r: r["profitable_folds"],
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
    ("param stability (>=4/5 within one step)", lambda s, r: r["stable"],
     lambda s, r: r["stable"] >= 4),
]


def gate(stitched, extras):
    print("\n--- Stage 6 promotion gate (stitched out-of-sample) " + "-" * 14)
    verdicts = []
    for label, value, test in GATES:
        ok = test(stitched, extras)
        verdicts.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label:<42} {value(stitched, extras)}")
    print(f"\n  VERDICT: {'PROMOTE' if all(verdicts) else 'DO NOT TRADE'} "
          f"({sum(verdicts)}/{len(verdicts)} gates)")
    return all(verdicts)


def main():
    print(f"git {git_sha()}  |  initial ${INITIAL:.0f}  |  Forex NQ  |  spread 0.2")

    benchmark = strategies.get(BENCHMARK)
    candidate = strategies.get(sys.argv[1] if len(sys.argv) > 1 else CANDIDATE)
    print(f"candidate: {candidate.name}")

    base_stat, _ = full_sample(benchmark, None, "compiled parameters (benchmark)",
                               INITIAL)

    result = wf.run(candidate, initial=INITIAL)
    median, stitched = wf.report(result)
    unanimous = fold_agreement(result)

    profitable = sum(1 for _, params, _, test, _ in result["rows"]
                     if params is not None and test["pnl"] > 0)
    in_sample = [row[2]["msharpe"] for row in result["rows"] if row[2] is not None]
    is_msharpe = sum(in_sample) / len(in_sample) if in_sample else 0.0
    stable = sum(1 for k in result["picks"]
                 if median and all(
                     wf.grid_steps_apart(candidate, a, dict(k)[a], median[a]) <= 1
                     for a in candidate.grid))
    extras = {
        "picks": result["picks"],
        "profitable_folds": profitable,
        "oos_is_ratio": (stitched["msharpe"] / is_msharpe) if is_msharpe else 0.0,
        "deflated": wf.deflated(stitched["msharpe"], max(1, stitched["n_months"]),
                                result["n_cells"]),
        "stable": stable,
    }
    print(f"\n  in-sample mean fold msharpe {is_msharpe:.2f}, "
          f"stitched OOS {stitched['msharpe']:.2f}, "
          f"cells evaluated {result['n_cells']}")

    wf.control(result, benchmark, None,
               "control: compiled parameters, same out-of-sample window")
    if median:
        wf.verify(result, median, "median configuration")
    if unanimous and (median is None or wf.search.freeze(median) != unanimous):
        wf.verify(result, dict(unanimous), "unanimous top-surface cell")

    passed = gate(stitched, extras)

    if unanimous:
        full_sample(candidate, dict(unanimous),
                    f"unanimous cell {dict(unanimous)} -- IN-SAMPLE, reference only",
                    INITIAL)

    out = {
        "git": git_sha(), "initial": INITIAL, "cells": result["n_cells"],
        "folds": wf.FOLDS, "embargo_days": wf.EMBARGO_DAYS,
        "grid": candidate.grid, "fixed": {k: v for k, v in candidate.defaults.items()
                                          if k not in candidate.grid},
        "picks": [dict(k) for k in result["picks"]],
        "median": median, "unanimous": dict(unanimous) if unanimous else None,
        "stitched_oos": stitched, "benchmark_full_sample": base_stat,
        "promoted": passed,
    }
    output = result_path("hourly_delta_reversal_walkforward.json")
    with output.open("w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()
