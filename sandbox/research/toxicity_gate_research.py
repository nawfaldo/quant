"""Run the complete A4 toxicity-gate experiment on both mean reversers.

The underlying entry and bracket parameters stay frozen. The only optimized
question is whether a coarse, causal time-clocked VPIN proxy should switch off
an otherwise valid entry. Selection is anchored walk-forward and purged CV.

Day, time, side, cost, and sizing variants are fixed diagnostics. They are
persisted, but they cannot be promoted from this already-inspected sample.
"""

import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import subprocess

from sandbox import data
from sandbox import execution
from sandbox import metrics
from sandbox import purged_cv
from sandbox import search
from sandbox import strategies
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PROJECT_ROOT
from sandbox.toxicity_gate import ToxicityOverlay


BASE_STRATEGIES = ("Absorption Reversal", "Hourly Delta Reversal")
INITIAL = 1_000.0
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")


def git_sha():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def inside(fills, lo, hi):
    return [
        fill
        for fill in fills
        if (lo is None or fill.entry_ts >= lo)
        and (hi is None or fill.entry_ts < hi)
    ]


def base_fills(base, bars, context, ex):
    signals = []
    params = base.all_params()
    for group in base.groups():
        signals.extend(base.signals(bars, context, group, params))
    return execution.resolve(bars, signals, ex)


def score(fills, ex, lo, hi):
    selected = inside(fills, lo, hi)
    sized = execution.size(selected, ex)
    stat = metrics.stats(sized, initial=ex.initial, span=(lo, hi))
    points = [fill.points for fill in selected]
    stat["points"] = round(sum(points), 2)
    stat["edge"] = round(sum(points) / len(points), 4) if points else 0.0
    stat["edge_t"] = round(walkforward.edge_t(points), 3)
    return stat, sized


def with_baseline_fallback(result, baseline):
    """A fold with no supported gate keeps trading the unchanged strategy."""
    rows = []
    stitched = []
    stitched_points = []
    equity = result["initial"]
    for row, (_train_lo, _train_hi, test_lo, test_hi) in zip(
        result["rows"], walkforward.fold_windows()
    ):
        index, params, train, _test, plateau, _points = row
        fills = (
            baseline
            if params is None
            else result["cells"][search.freeze(params)]
        )
        stat, sized = walkforward.window_stats(
            fills, result["ex"], test_lo, test_hi, equity
        )
        points = walkforward.window_points(fills, test_lo, test_hi)
        equity += stat["pnl"]
        stitched.extend(sized)
        stitched_points.extend(points)
        rows.append((index, params, train, stat, plateau, points))
    result["rows"] = rows
    result["stitched"] = sorted(stitched)
    result["stitched_points"] = stitched_points
    return result


def matched_deltas(baseline, selected):
    """Per-baseline-trade points added by the gate (zero when retained)."""
    kept = Counter(selected)
    deltas = []
    for fill in baseline:
        if kept[fill]:
            kept[fill] -= 1
            deltas.append(0.0)
        else:
            deltas.append(-fill.points)
    return deltas


def selected_fills(result, baseline):
    out = []
    fold_deltas = []
    for row, (_train_lo, _train_hi, test_lo, test_hi) in zip(
        result["rows"], walkforward.fold_windows()
    ):
        params = row[1]
        candidate = (
            baseline
            if params is None
            else result["cells"][search.freeze(params)]
        )
        chosen = inside(candidate, test_lo, test_hi)
        control = inside(baseline, test_lo, test_hi)
        out.extend(chosen)
        overlay_points = sum(fill.points for fill in chosen)
        baseline_points = sum(fill.points for fill in control)
        fold_deltas.append(
            {
                "fold": row[0],
                "params": params,
                "overlay_points": round(overlay_points, 2),
                "baseline_points": round(baseline_points, 2),
                "delta_points": round(
                    overlay_points - baseline_points, 2
                ),
                "fallback": params is None,
            }
        )
    return out, fold_deltas


def annotate_cv(cv, result, baseline):
    blocks = []
    for block, (lo_iso, hi_iso) in zip(cv["blocks"], purged_cv.BLOCKS):
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        params = block["params"]
        candidate = (
            baseline
            if params is None
            else result["cells"][search.freeze(params)]
        )
        overlay_points = sum(
            fill.points for fill in inside(candidate, lo, hi)
        )
        baseline_points = sum(
            fill.points for fill in inside(baseline, lo, hi)
        )
        blocks.append(
            {
                **block,
                "overlay_points": round(overlay_points, 2),
                "baseline_points": round(baseline_points, 2),
                "delta_points": round(
                    overlay_points - baseline_points, 2
                ),
                "fallback": params is None,
            }
        )
    return blocks


def monthly_comparison(baseline_stat, overlay_stat):
    keys = sorted(
        set(baseline_stat["months"]) | set(overlay_stat["months"])
    )
    return {
        key: {
            "baseline": baseline_stat["months"].get(key, 0.0),
            "overlay": overlay_stat["months"].get(key, 0.0),
            "delta": round(
                overlay_stat["months"].get(key, 0.0)
                - baseline_stat["months"].get(key, 0.0),
                2,
            ),
        }
        for key in keys
    }


def improvement_folds(candidate, control):
    improved = 0
    rows = []
    for _index, (_train_lo, _train_hi, lo, hi) in enumerate(
        walkforward.fold_windows(), 1
    ):
        delta = sum(fill.points for fill in inside(candidate, lo, hi)) - sum(
            fill.points for fill in inside(control, lo, hi)
        )
        improved += delta > 0
        rows.append(round(delta, 2))
    return improved, rows


def diagnostic_filters(fills, ex, lo, hi):
    variants = {"gate_only": fills}
    for weekday, label in enumerate(DAYS):
        variants[f"skip_{label}"] = [
            fill
            for fill in fills
            if (fill.entry_ts // 86_400 + 3) % 7 != weekday
        ]
    variants.update(
        {
            "skip_open_0930_1100": [
                fill
                for fill in fills
                if not 570 <= (fill.entry_ts % 86_400) // 60 < 660
            ],
            "skip_midday_1100_1400": [
                fill
                for fill in fills
                if not 660 <= (fill.entry_ts % 86_400) // 60 < 840
            ],
            "skip_close_1400_1600": [
                fill
                for fill in fills
                if not 840 <= (fill.entry_ts % 86_400) // 60 < 960
            ],
            "long_only": [
                fill for fill in fills if fill.side == execution.LONG
            ],
            "short_only": [
                fill for fill in fills if fill.side == execution.SHORT
            ],
        }
    )
    out = {}
    for name, candidate in variants.items():
        stat, _sized = score(candidate, ex, lo, hi)
        improved, deltas = improvement_folds(candidate, fills)
        out[name] = {
            **stat,
            "folds_improved_vs_gate": improved,
            "fold_delta_points": deltas,
        }
    return out


def scaled_sizing(fills, ex, multipliers, lo, hi):
    selected = inside(fills, lo, hi)
    adjusted = [
        replace(fill, stop=fill.stop / max(0.01, multipliers(fill)))
        for fill in selected
    ]
    sized = execution.size(adjusted, ex)
    return metrics.stats(sized, initial=ex.initial, span=(lo, hi))


def sizing_diagnostics(fills, ex, bars, readings, cutoff, lo, hi):
    out = {}
    for risk in (0.0025, 0.005, 0.01):
        stat, _sized = score(fills, replace(ex, risk=risk), lo, hi)
        out[f"fixed_fraction_{100 * risk:.2f}%"] = stat

    index_by_ts = {bar[data.TS]: index for index, bar in enumerate(bars)}

    def toxicity_multiplier(fill):
        value = readings[index_by_ts[fill.entry_ts]]
        if value is None:
            return 1.0
        return max(0.5, min(1.0, 1.0 - 0.5 * value / cutoff))

    toxicity_stat = scaled_sizing(
        fills, ex, toxicity_multiplier, lo, hi
    )
    toxicity_stat["rule"] = (
        "linear 1.0x at zero toxicity to 0.5x at cutoff"
    )
    out["toxicity_tapered_0.50%"] = toxicity_stat

    atr = data.atr_by_day(bars, 20)
    training = sorted(
        value for day, value in atr.items() if day * 86_400 < lo
    )
    target = training[len(training) // 2]

    def atr_multiplier(fill):
        current = atr.get(fill.entry_ts // 86_400)
        return (
            max(0.5, min(1.5, target / current))
            if current is not None and current > 0.0
            else 1.0
        )

    atr_stat = scaled_sizing(fills, ex, atr_multiplier, lo, hi)
    atr_stat["target_atr"] = round(target, 4)
    atr_stat["rule"] = (
        "inverse 20-session ATR, clipped to 0.5x..1.5x"
    )
    out["causal_inverse_atr_0.50%"] = atr_stat
    return out


def cost_sensitivity(fills, ex, lo, hi):
    out = {}
    for spread in COSTS:
        # Spread changes per-unit points but not the already-resolved exit.
        repriced = [
            replace(fill, points=fill.points + ex.entry_cost - spread)
            for fill in fills
        ]
        stat, _sized = score(
            repriced, replace(ex, spread=spread), lo, hi
        )
        out[str(spread)] = stat
    return out


def promotion_gate(
    result, baseline_stat, overlay_stat, chosen, baseline, fold_deltas, cv_blocks
):
    deltas = matched_deltas(
        inside(baseline, *result["oos_span"]), chosen
    )
    ci_lo, ci_hi = walkforward.bootstrap_edge(deltas)
    t_stat = walkforward.edge_t(deltas)
    deflated = walkforward.deflated_t(t_stat, result["trials"])
    positive_folds = sum(row["delta_points"] > 0 for row in fold_deltas)
    cv_values = [row["delta_points"] for row in cv_blocks]
    cv_mean = sum(cv_values) / len(cv_values)
    checks = [
        {
            "name": "stitched OOS improvement > 0",
            "passed": overlay_stat["pnl"] > baseline_stat["pnl"]
            and sum(deltas) > 0,
            "detail": (
                f"${overlay_stat['pnl'] - baseline_stat['pnl']:+.2f}, "
                f"{sum(deltas):+.2f} matched points"
            ),
        },
        {
            "name": "folds improved >= 2/3",
            "passed": positive_folds
            >= math.ceil(2 * len(fold_deltas) / 3),
            "detail": f"{positive_folds}/{len(fold_deltas)}",
        },
        {
            "name": "matched improvement CI excludes 0",
            "passed": ci_lo > 0,
            "detail": (
                f"mean {sum(deltas) / max(1, len(deltas)):+.3f}, "
                f"95% CI [{ci_lo:+.3f}, {ci_hi:+.3f}], t={t_stat:.2f}"
            ),
        },
        {
            "name": (
                f"deflated matched t > 0 vs {result['trials']} "
                "cumulative trials"
            ),
            "passed": deflated > 0,
            "detail": f"{deflated:+.2f}",
        },
        {
            "name": "no CV block worse than -2x mean block",
            "passed": min(cv_values) >= -2 * abs(cv_mean),
            "detail": (
                f"worst {min(cv_values):+.2f}, mean {cv_mean:+.2f}, "
                f"positive {sum(value > 0 for value in cv_values)}/"
                f"{len(cv_values)}"
            ),
        },
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "matched_delta_points": [round(value, 4) for value in deltas],
    }


def serialise_folds(result):
    out = []
    for index, params, train, test, plateau, points in result["rows"]:
        out.append(
            {
                "fold": index,
                "params": params,
                "fallback_to_baseline": params is None,
                "train": train,
                "test": test,
                "plateau_t": plateau,
                "test_points": round(sum(points), 2),
            }
        )
    return out


def run_one(name, record_trials):
    base = strategies.get(name)
    base.execution = replace(base.execution, initial=INITIAL)
    overlay = ToxicityOverlay(base)
    overlay.execution = base.execution
    bars = data.load_bars(base.bars, base.symbol)
    context = overlay.context()
    baseline = base_fills(base, bars, context["base"], base.execution)

    wf = walkforward.run(
        overlay,
        initial=INITIAL,
        progress=True,
        record_trials=record_trials,
    )
    with_baseline_fallback(wf, baseline)
    median, stitched_stat = walkforward.report(wf)
    lo, hi = wf["oos_span"]
    baseline_stat, _baseline_sized = score(
        baseline, base.execution, lo, hi
    )
    chosen, fold_deltas = selected_fills(wf, baseline)

    fixed = median or dict(overlay.defaults)
    fixed_fills = wf["cells"][search.freeze(fixed)]
    fixed_stat, _fixed_sized = score(
        fixed_fills, base.execution, lo, hi
    )

    cv = purged_cv.run(
        overlay,
        cells=wf["cells"],
        ex=wf["ex"],
        progress=False,
        record_trials=record_trials,
    )
    cv_blocks = annotate_cv(cv, wf, baseline)

    filters = diagnostic_filters(
        fixed_fills, base.execution, lo, hi
    )
    sizing = sizing_diagnostics(
        fixed_fills,
        base.execution,
        bars,
        context["toxicity"][fixed["toxicity_lookback"]],
        fixed["toxicity_cutoff"],
        lo,
        hi,
    )
    if record_trials:
        trials.record(
            name,
            len(filters) - 1,
            "A4 fixed day/time/side filter diagnostics",
        )
        trials.record(
            name,
            len(sizing),
            "A4 fixed sizing diagnostics",
        )
    # Include the diagnostics just charged in the actual search haircut.
    wf["trials"] = trials.total(name)
    gate = promotion_gate(
        wf,
        baseline_stat,
        stitched_stat,
        chosen,
        baseline,
        fold_deltas,
        cv_blocks,
    )

    return {
        "strategy": name,
        "status": "promoted" if gate["passed"] else "rejected",
        "hypothesis": (
            "Mean-reversion entries should be disabled when the rolling "
            "time-clocked VPIN proxy shows unusually one-sided aggressive flow."
        ),
        "formula": (
            "mean(abs(trade_delta) / "
            "(aggressive_buy_volume + aggressive_sell_volume))"
        ),
        "timing": (
            "1/3/5 contiguous completed minutes ending immediately before "
            "the entry; missing readings fail open"
        ),
        "grid": overlay.grid,
        "execution": asdict(base.execution),
        "loaded_range": list(base.date_range),
        "bars": len(bars),
        "baseline_trades_full_sample": len(baseline),
        "oos_range": [
            walkforward.FOLDS[0][0],
            walkforward.FOLDS[-1][1],
        ],
        "cumulative_trials": trials.total(name),
        "walkforward": {
            "folds": serialise_folds(wf),
            "fold_improvement": fold_deltas,
            "stitched": stitched_stat,
            "baseline": baseline_stat,
            "monthly_comparison": monthly_comparison(
                baseline_stat, stitched_stat
            ),
            "selected_median": median,
            "fixed_median_or_default": fixed,
            "fixed_result": fixed_stat,
        },
        "purged_cv": {
            "blocks": cv_blocks,
            "mean_delta_points": round(
                sum(row["delta_points"] for row in cv_blocks)
                / len(cv_blocks),
                3,
            ),
        },
        "promotion_gate": gate,
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": cost_sensitivity(
            fixed_fills, base.execution, lo, hi
        ),
        "ml": {
            "run": False,
            "reason": (
                f"Only {len(baseline)} full-sample baseline trades; "
                "the requested threshold was thousands."
            ),
        },
        "notes": [
            (
                "A fold with no qualifying toxicity cell uses the unchanged "
                "base strategy; absence of evidence for a gate is not an "
                "instruction to stop trading."
            ),
            (
                "Day/time/side and sizing rows are diagnostics only and were "
                "not allowed to alter the promotion decision."
            ),
            (
                "Risk fraction scales exposure. It cannot change the "
                "per-unit edge of the gate."
            ),
        ],
    }


def print_result(result):
    wf = result["walkforward"]
    print(f"\n=== A4 {result['strategy']} ===")
    print(
        f"status={result['status']}  full trades="
        f"{result['baseline_trades_full_sample']}  "
        f"fixed={wf['fixed_median_or_default']}"
    )
    print(
        f"OOS baseline ${wf['baseline']['pnl']:+.2f} / "
        f"{wf['baseline']['trades']} trades; selected overlay "
        f"${wf['stitched']['pnl']:+.2f} / "
        f"{wf['stitched']['trades']} trades"
    )
    print("  month      baseline    overlay      delta")
    for month, row in wf["monthly_comparison"].items():
        print(
            f"  {month}  {row['baseline']:>10.2f} "
            f"{row['overlay']:>10.2f} {row['delta']:>10.2f}"
        )
    print("  promotion checks")
    for check in result["promotion_gate"]["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"    [{mark}] {check['name']}: {check['detail']}")


def run(out_path=None, record_trials=False):
    results = [run_one(name, record_trials) for name in BASE_STRATEGIES]
    full_count = sum(
        result["baseline_trades_full_sample"] for result in results
    )
    payload = {
        "candidate": "A4 Toxicity Gate",
        "generated_at_data_date": max(
            result["loaded_range"][1] for result in results
        ),
        "git_sha": git_sha(),
        "ml": {
            "run": False,
            "combined_full_sample_trades": full_count,
            "reason": (
                f"The two intended trade populations contain {full_count} "
                "trades combined, not thousands."
            ),
        },
        "results": results,
    }
    for result in results:
        print_result(result)
    if out_path:
        out_path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {out_path}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--record-trials",
        action="store_true",
        help="append this run's cells and diagnostics to trials.json",
    )
    args = parser.parse_args()
    run(args.out, args.record_trials)


if __name__ == "__main__":
    main()
