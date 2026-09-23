"""Leakage-controlled research for liquidity-line Candidates C and D."""
from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import asdict, replace
from pathlib import Path

from sandbox import execution, metrics, search, trials, walkforward
from sandbox.liquidity_lines import iter_normalized_days
from sandbox.strategies.liquidity_line_candidates import (
    LiquidityLineMagnet,
    LiquidityLineReversal,
    resolve_day,
)

IS_FROM, IS_TO = "2025-02-12", "2026-01-01"
INITIAL, SPREAD = 1_000.0, 0.20
HORIZONS = (1, 5, 15, 30, 60)
EX = execution.Execution(initial=INITIAL, spread=SPREAD, session_end_min=960)
FOLDS = tuple((f"2025-{month:02d}-01",
               "2026-01-01" if month == 12 else f"2025-{month + 1:02d}-01")
              for month in range(8, 13))
BLOCKS = (
    ("2025-02-12", "2025-05-01"),
    ("2025-05-01", "2025-08-01"),
    ("2025-08-01", "2025-11-01"),
    ("2025-11-01", "2026-01-01"),
)


def combos(definition):
    axes = sorted(definition.grid)
    for values in itertools.product(*(definition.grid[axis] for axis in axes)):
        yield dict(zip(axes, values))


def point_months(fills, lo, hi):
    values = {}
    counts = {}
    for fill in fills:
        key = metrics.month_key(fill.entry_ts)
        values[key] = values.get(key, 0.0) + fill.points
        counts[key] = counts.get(key, 0) + 1
    padded = metrics.pad(values, lo, hi)
    return ({key: round(value, 2) for key, value in padded.items()},
            {key: counts.get(key, 0) for key in padded})


def summary(fills, lo, hi, ex=EX):
    kept = [fill for fill in fills if lo <= fill.entry_ts < hi]
    points = [fill.points for fill in kept]
    low, high = walkforward.bootstrap_edge(points, draws=5_000, seed=20260801)
    months, counts = point_months(kept, lo, hi)
    sized = execution.size(kept, replace(ex, initial=INITIAL))
    return {
        "trades": len(points),
        "net_points": round(sum(points), 2),
        "mean_points": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "edge_ci95": [round(low, 4), round(high, 4)],
        "point_months": months,
        "trade_months": counts,
        "sized": metrics.stats(sized, initial=INITIAL, span=(lo, hi)),
    }


def horizon_fills(rows, signals, horizon):
    fills = []
    free_at = -1
    for signal in signals:
        entry = rows[signal.entry_index]
        if entry.ts < free_at:
            continue
        target_ts = entry.ts + horizon * 60
        target = next((row for row in rows[signal.entry_index + 1:]
                       if row.ts >= target_ts), None)
        if target is None or target.ts // 86_400 != entry.ts // 86_400:
            continue
        sign = 1.0 if signal.side == execution.LONG else -1.0
        fills.append(execution.Fill(
            entry.ts, target.ts, signal.side,
            sign * (target.mid - entry.mid) - SPREAD,
            entry.mid, signal.stop,
        ))
        free_at = target.ts
    return fills


def first_pass(definitions):
    retained = {
        definition.name: {
            "candidate": [],
            "controls": {mode: [] for mode in control_modes(definition)},
            "horizons": {h: [] for h in HORIZONS},
        }
        for definition in definitions
    }
    sessions = 0
    for day, rows in iter_normalized_days("dbento", IS_FROM, IS_TO):
        sessions += 1
        for definition in definitions:
            bucket = retained[definition.name]
            signals = definition.signals(rows)
            bucket["candidate"].extend(resolve_day(rows, signals, SPREAD))
            for horizon in HORIZONS:
                bucket["horizons"][horizon].extend(horizon_fills(rows, signals, horizon))
            for mode in bucket["controls"]:
                control = definition.signals(rows, mode=mode)
                bucket["controls"][mode].extend(resolve_day(rows, control, SPREAD))
        if sessions % 20 == 0:
            print(f"kill-test scan: {sessions} sessions (latest {day})", flush=True)
    return retained, sessions


def control_modes(definition):
    if isinstance(definition, LiquidityLineReversal):
        return ("wall_touch", "non_large", "side_shuffled")
    return ("no_confirmation", "random_line", "pulled_before_entry")


def kill_decision(candidate, controls, horizons):
    reasons = []
    if candidate["mean_points"] <= 0:
        reasons.append("default bracket has non-positive net edge")
    positive_horizons = sum(row["mean_points"] > 0 for row in horizons.values())
    if positive_horizons < 3:
        reasons.append("directional effect is absent at most fixed horizons")
    best_control = max((row["mean_points"] for row in controls.values()), default=0.0)
    if candidate["mean_points"] <= best_control:
        reasons.append("candidate does not improve on its matched controls")
    return reasons


def grid_fills(definition):
    cells = {search.freeze(params): [] for params in combos(definition)}
    sessions = 0
    for day, rows in iter_normalized_days("dbento", IS_FROM, IS_TO):
        sessions += 1
        for key in cells:
            signals = definition.signals(rows, dict(key))
            cells[key].extend(resolve_day(rows, signals, SPREAD))
        if sessions % 20 == 0:
            print(f"{definition.name} grid: {sessions} sessions (latest {day})", flush=True)
    return cells


def train_pick(definition, cells, hi):
    lo = metrics.split_ts(IS_FROM)
    scored = {}
    for key, fills in cells.items():
        stat = summary(fills, lo, hi)
        scored[key] = stat
    eligible = []
    for key, stat in scored.items():
        if stat["trades"] < 30 or stat["net_points"] <= 0 or stat["mean_points"] < 3 * SPREAD:
            continue
        near = [n for n in search.neighbours(definition, key) if n in scored]
        if not near or any(scored[n]["net_points"] <= 0 for n in near):
            continue
        plateau = sum(scored[n]["edge_t"] for n in near) / len(near)
        eligible.append((key, stat, plateau))
    eligible.sort(key=lambda item: min(item[1]["edge_t"], item[2]), reverse=True)
    return eligible[0] if eligible else (None, None, None)


def optimize(definition, record_trials):
    cells = grid_fills(definition)
    if record_trials:
        trials.record(definition.name, len(cells), "2025 anchored Candidate C/D grid")
    picks, folds = [], []
    stitched = []
    for test_from, test_to in FOLDS:
        test_lo, test_hi = metrics.split_ts(test_from), metrics.split_ts(test_to)
        key, train, plateau = train_pick(definition, cells, test_lo - 86_400)
        test_fills = [] if key is None else [
            fill for fill in cells[key] if test_lo <= fill.entry_ts < test_hi
        ]
        if key is not None:
            picks.append(key)
            stitched.extend(test_fills)
        folds.append({
            "month": test_from[:7],
            "params": dict(key) if key else None,
            "train": train,
            "plateau_t": round(plateau, 4) if plateau is not None else None,
            "test": summary(test_fills, test_lo, test_hi),
        })
    if not picks:
        return {"cells": len(cells), "folds": folds, "frozen": None,
                "reason": "no anchored fold found a plateau-clearing cell"}
    frozen = walkforward.median_params(picks, definition)
    key = search.freeze(frozen)
    lo, hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    fixed = summary(cells[key], lo, hi)
    block_points = []
    for start, end in BLOCKS:
        blo, bhi = metrics.split_ts(start), metrics.split_ts(end)
        block_points.append(round(sum(fill.points for fill in cells[key]
                                      if blo <= fill.entry_ts < bhi), 2))
    promotes = (
        fixed["trades"] >= 30
        and fixed["net_points"] > 0
        and fixed["mean_points"] >= 3 * SPREAD
        and sum(value > 0 for value in block_points) >= 3
        and fixed["sized"]["max_dd"] <= 0.15 * INITIAL
        and fixed["sized"]["max_loss_streak"] <= 2
        and fixed["sized"]["top_month_share"] <= 0.40
    )
    return {
        "cells": len(cells),
        "folds": folds,
        "stitched_2025": summary(stitched, metrics.split_ts(FOLDS[0][0]), hi),
        "frozen": frozen,
        "fixed_2025": fixed,
        "regime_block_points": block_points,
        "eligible_for_2026": promotes,
    }


def markdown_report(result):
    lines = [
        "# Liquidity-line Candidates C and D",
        "",
        "NQ, $1,000 Forex account model, and a 0.20-point entry spread.",
        "The 2026 holdout remains sealed unless a candidate clears every 2025 gate.",
    ]
    for name, item in result["candidates"].items():
        stat = item["candidate_2025"]
        lines.extend([
            "", f"## {name}", "", f"Status: **{item['status']}**.", "",
            f"Default result: {stat['trades']:,} trades, {stat['net_points']:+,.2f} "
            f"net points, {stat['sized']['pnl']:+,.2f} sized dollars, "
            f"{stat['mean_points']:+.4f} points/trade.", "",
        ])
        if item["kill_reasons"]:
            lines.append("Reasons: " + "; ".join(item["kill_reasons"]) + ".")
            lines.append("")
        lines.extend([
            "| Month | Trades | Net points | Sized PnL |",
            "| --- | ---: | ---: | ---: |",
        ])
        months = stat["point_months"]
        dollars = stat["sized"]["months"]
        counts = stat["trade_months"]
        for month in months:
            lines.append(
                f"| {month} | {counts.get(month, 0)} | {months[month]:+.2f} | "
                f"${dollars.get(month, 0.0):+.2f} |"
            )
    lines.extend(["", "Machine-readable controls, confidence intervals, fixed-horizon "
                  "studies, grids, and trial accounting are in the JSON report.", ""])
    return "\n".join(lines)


def run(out_path, markdown_path, record_trials=False):
    definitions = (LiquidityLineReversal(), LiquidityLineMagnet())
    raw, sessions = first_pass(definitions)
    lo, hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    result = {
        "assumptions": {
            "instrument": "NQ", "initial": INITIAL,
            "account_model": "Forex", "entry_spread_points": SPREAD,
            "in_sample": [IS_FROM, IS_TO], "oos_evaluated": False,
            "execution": asdict(EX),
        },
        "line_reconstruction": {
            "source": "dbento", "sessions": sessions, "top_levels": 10,
            "normalization": "prior 20 completed source sessions by side/time/distance bucket",
        },
        "candidates": {},
    }
    for definition in definitions:
        bucket = raw[definition.name]
        candidate = summary(bucket["candidate"], lo, hi)
        controls = {name: summary(fills, lo, hi)
                    for name, fills in bucket["controls"].items()}
        horizons = {str(h): summary(fills, lo, hi)
                    for h, fills in bucket["horizons"].items()}
        reasons = kill_decision(candidate, controls, horizons)
        if record_trials:
            trials.record(definition.name, 1 + len(controls),
                          "2025 default kill test and matched controls")
        item = {
            "status": "killed_before_optimization" if reasons else "passed_kill_test",
            "kill_reasons": reasons,
            "defaults": definition.defaults,
            "grid": definition.grid,
            "candidate_2025": candidate,
            "controls_2025": controls,
            "fixed_horizons_2025": horizons,
            "optimization": None,
        }
        if not reasons:
            item["optimization"] = optimize(definition, record_trials)
            if not item["optimization"].get("eligible_for_2026", False):
                item["status"] = "rejected_after_2025_optimization"
            else:
                item["status"] = "frozen_pending_2026_reconstruction"
        result["candidates"][definition.name] = item

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    markdown_path = Path(markdown_path)
    markdown_path.write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({name: {"status": row["status"],
                            "reasons": row["kill_reasons"],
                            "candidate_2025": row["candidate_2025"]}
                      for name, row in result["candidates"].items()}, indent=2))
    print(f"wrote {out_path}")
    print(f"wrote {markdown_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="sandbox/results/liquidity_line_candidates_result.json")
    parser.add_argument("--markdown", default="sandbox/results/LIQUIDITY_LINE_CANDIDATES.md")
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    run(args.out, args.markdown, args.record_trials)


if __name__ == "__main__":
    main()
