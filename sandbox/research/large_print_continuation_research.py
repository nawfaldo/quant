"""2025-only optimization and sealed 2026 OOS for Large Print Continuation.

The entry hypothesis stays fixed: 100--190 lot directional-wick prints into a
balanced book.  Fifty bracket cells are selected on 2025 only.  Causal regime,
weekday, and coarse time filters are then diagnostics on that selected bracket;
at most one filter may be adopted.  Risk sizing is selected last and cannot
change the per-unit edge.  Only after every choice is frozen is 2026 scored.
"""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from sandbox import data, execution, metrics
from sandbox.data import TS
from sandbox.strategies.large_print_continuation import (
    LargePrintContinuation,
    _exit_index,
    candidate_rows,
)
from sandbox.execution import Signal

IS_FROM = "2025-02-01"
IS_TO = "2026-01-01"
OOS_FROM = "2026-01-01"
OOS_TO = "2026-08-01"
INITIAL = 1_000.0
STOP_GRID = (8.0, 12.0, 16.0, 20.0, 24.0)
TARGET_GRID = (8.0, 12.0, 20.0, 30.0, 40.0)
TIME_GRID = (15, 20)
TRAIN_BLOCKS = (
    ("2025-02-01", "2025-05-01"),
    ("2025-05-01", "2025-08-01"),
    ("2025-08-01", "2025-11-01"),
    ("2025-11-01", "2026-01-01"),
)


def inside(fills, lo, hi):
    return [fill for fill in fills if lo <= fill.entry_ts < hi]


def point_stats(fills, lo, hi):
    selected = inside(fills, lo, hi)
    points = [fill.points for fill in selected]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    return {
        "trades": len(points),
        "points": round(sum(points), 3),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(edge_t(points), 4),
        "pf_points": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
    }


def edge_t(points):
    if len(points) < 2:
        return 0.0
    mean = sum(points) / len(points)
    variance = sum((value - mean) ** 2 for value in points) / (len(points) - 1)
    return mean / math.sqrt(variance) * math.sqrt(len(points)) if variance > 0 else 0.0


def block_points(fills):
    return [
        round(sum(fill.points for fill in fills
                  if metrics.split_ts(lo) <= fill.entry_ts < metrics.split_ts(hi)), 3)
        for lo, hi in TRAIN_BLOCKS
    ]


def resolve_candidates(bars, rows, params, keep=None):
    """Apply a filter before occupancy, then reproduce strategy execution."""
    output = []
    free_from = -1
    for row in rows:
        if keep is not None and not keep(row):
            continue
        if row["index"] < free_from:
            continue
        output.append(Signal(
            row["index"], row["side"], params["stop"], params["target"],
            params["time_stop"],
        ))
        free_from = _exit_index(
            bars, row["index"], row["side"], params["stop"], params["target"],
            params["time_stop"], 960,
        )
    return execution.resolve(bars, output, LargePrintContinuation.execution)


def neighbours(key):
    stop, target, hold = key
    output = []
    for values, value, axis in (
        (STOP_GRID, stop, 0), (TARGET_GRID, target, 1), (TIME_GRID, hold, 2)
    ):
        index = values.index(value)
        for other in (index - 1, index + 1):
            if 0 <= other < len(values):
                row = [stop, target, hold]
                row[axis] = values[other]
                output.append(tuple(row))
    return output


def select_bracket(cells, lo, hi):
    """Require positive 2025 edge, 3/4 positive blocks, and a positive plateau."""
    ranked = []
    for key, fills in cells.items():
        stat = point_stats(fills, lo, hi)
        blocks = block_points(fills)
        near = [point_stats(cells[item], lo, hi)["edge_t"]
                for item in neighbours(key) if item in cells]
        plateau = sum(near) / len(near) if near else -999.0
        if (stat["trades"] >= 30 and stat["points"] > 0.0
                and sum(value > 0.0 for value in blocks) >= 3
                and near and min(near) > -0.25):
            ranked.append({
                "key": key,
                **stat,
                "blocks": blocks,
                "positive_blocks": sum(value > 0.0 for value in blocks),
                "plateau_t": round(plateau, 4),
                "robust_score": round(min(stat["edge_t"], plateau), 4),
            })
    ranked.sort(key=lambda row: (row["robust_score"], row["points"]), reverse=True)
    return ranked


def regime_context(bars):
    atr = data.atr_by_day(bars, 20)
    vix = data.vix_series(bars)
    atr_median = {}
    history = []
    for day in sorted({int(bar[TS]) // 86_400 for bar in bars}):
        current = atr.get(day)
        if current is not None and history:
            window = sorted(history[-60:])
            atr_median[day] = window[len(window) // 2]
        if current is not None:
            history.append(current)
    return atr, atr_median, vix


def filter_definitions(bars):
    atr, atr_median, vix = regime_context(bars)

    def facts(row):
        index = row["index"]
        timestamp = int(bars[index][TS])
        day = timestamp // 86_400
        minute = (timestamp % 86_400) // 60
        return {
            "day": day,
            "weekday": int((day + 3) % 7),
            "minute": minute,
            "atr": atr.get(day),
            "atr_median": atr_median.get(day),
            "vix": vix[index],
        }

    filters = {"baseline": lambda _row: True}
    for weekday, name in enumerate(("mon", "tue", "wed", "thu", "fri")):
        filters[f"skip_{name}"] = (
            lambda row, target=weekday: facts(row)["weekday"] != target
        )
    filters.update({
        "open_only": lambda row: facts(row)["minute"] < 690,
        "mid_only": lambda row: 690 <= facts(row)["minute"] < 840,
        "late_only": lambda row: facts(row)["minute"] >= 840,
        "skip_open": lambda row: facts(row)["minute"] >= 690,
        "skip_mid": lambda row: not 690 <= facts(row)["minute"] < 840,
        "skip_late": lambda row: facts(row)["minute"] < 840,
        "atr_below_trailing_median": lambda row: (
            facts(row)["atr"] is not None
            and facts(row)["atr_median"] is not None
            and facts(row)["atr"] < facts(row)["atr_median"]
        ),
        "atr_at_or_above_trailing_median": lambda row: (
            facts(row)["atr"] is not None
            and facts(row)["atr_median"] is not None
            and facts(row)["atr"] >= facts(row)["atr_median"]
        ),
        "vix_below_20": lambda row: 0.0 < facts(row)["vix"] < 20.0,
        "vix_at_or_above_20": lambda row: facts(row)["vix"] >= 20.0,
        "vix_below_25": lambda row: 0.0 < facts(row)["vix"] < 25.0,
        "vix_at_or_above_25": lambda row: facts(row)["vix"] >= 25.0,
    })
    return filters, facts


def select_filter(rows):
    baseline = rows["baseline"]
    ranked = []
    for name, row in rows.items():
        if name == "baseline" or row["trades"] < 24:
            continue
        deltas = [right - left for left, right in zip(baseline["blocks"], row["blocks"])]
        improved = sum(delta > 0.0 for delta in deltas)
        # One filter only, adopted only if it improves most independent 2025
        # blocks and the full 2025 point edge.
        if improved >= 3 and row["points"] > baseline["points"]:
            ranked.append({**row, "name": name, "improved_blocks": improved,
                           "block_deltas": [round(value, 3) for value in deltas]})
    ranked.sort(key=lambda row: (row["improved_blocks"], row["points"]), reverse=True)
    return ranked[0] if ranked else {**baseline, "name": "baseline",
                                      "improved_blocks": 0,
                                      "block_deltas": [0.0] * 4}


def sized_stats(fills, ex, lo, hi, multipliers=None):
    chosen = inside(fills, lo, hi)
    if multipliers is None:
        sized = execution.size(chosen, ex)
    else:
        sized = dynamic_size(chosen, ex, multipliers)
    return metrics.stats(sized, initial=ex.initial, span=(lo, hi))


def dynamic_size(fills, ex, multiplier):
    """Non-overlapping strategy fills with a causal quantity multiplier."""
    equity = ex.initial
    output = []
    for fill in sorted(fills, key=lambda item: item.entry_ts):
        raw = min(equity * ex.risk / fill.stop,
                  equity / ex.margin / fill.price) * ex.leverage
        quantity = math.floor(raw * multiplier(fill) / ex.step) * ex.step
        if quantity < ex.step:
            continue
        pnl = fill.points * quantity
        equity += pnl
        output.append((fill.entry_ts, pnl))
    return output


def sizing_study(fills, facts_by_ts, lo, hi, base_ex):
    output = {}
    for risk in (0.0025, 0.005, 0.01):
        name = f"fixed_fraction_{100 * risk:.2f}%"
        output[name] = sized_stats(fills, replace(base_ex, risk=risk), lo, hi)
    for leverage in (0.5, 0.75, 1.0):
        name = f"fixed_leverage_{leverage:.2f}x"
        output[name] = sized_stats(
            fills, replace(base_ex, risk=0.005, leverage=leverage), lo, hi
        )

    atr_values = sorted(value["atr"] for value in facts_by_ts.values()
                        if value["atr"] is not None and value["timestamp"] < hi)
    target = atr_values[len(atr_values) // 2] if atr_values else None

    def inverse_atr(fill):
        current = facts_by_ts.get(fill.entry_ts, {}).get("atr")
        if not current or not target:
            return 1.0
        return max(0.5, min(1.0, target / current))

    output["inverse_atr_capped_0.50_1.00x"] = sized_stats(
        fills, base_ex, lo, hi, inverse_atr
    )
    output["inverse_atr_capped_0.50_1.00x"]["training_target_atr"] = (
        round(target, 4) if target else None
    )
    return output, inverse_atr


def choose_sizing(rows):
    # Exposure is accepted only when 2025 is profitable and drawdown remains
    # below 10% of the $1,000 account.  Rank return/drawdown, not raw return.
    allowed = []
    for name, row in rows.items():
        if row["pnl"] > 0.0 and row["max_dd"] <= 100.0:
            ratio = row["pnl"] / max(row["max_dd"], 1.0)
            allowed.append((ratio, row["pnl"], name))
    return max(allowed)[2] if allowed else "fixed_leverage_0.50x"


def markdown(result):
    selected = result["selection"]
    train = result["train_selected"]
    oos = result["oos"]
    return "\n".join([
        "# NQ Large Print Continuation — 2025 train / 2026 OOS",
        "",
        f"Status: **{result['status']}**.",
        "",
        "The raw tape combines Databento and Bookmap with Bookmap precedence in",
        "overlapping minutes. Book balance is the completed second before each",
        "print; entries remain at the next minute open.",
        "",
        "## Frozen selection",
        "",
        f"- Stop: {selected['stop']} points",
        f"- Target: {selected['target']} points",
        f"- Time stop: {selected['time_stop']} minutes",
        f"- Filter: {selected['filter']}",
        f"- Sizing: {selected['sizing']}",
        "",
        "## Result",
        "",
        "| Window | PnL | Trades | PF | Win rate | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| 2025 train | {train['pnl']:+.2f} | {train['trades']} | {train['pf']:.3f} | {train['win_rate']:.1%} | {train['max_dd']:.2f} |",
        f"| 2026 OOS | {oos['pnl']:+.2f} | {oos['trades']} | {oos['pf']:.3f} | {oos['win_rate']:.1%} | {oos['max_dd']:.2f} |",
        "",
        "The 2026 row was evaluated only after the bracket, filter, and sizing",
        "choices had been frozen from 2025.",
        "",
    ])


def run(out_json=None, out_markdown=None):
    strategy = LargePrintContinuation()
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    base = strategy.all_params()
    base_rows = candidate_rows(
        bars, context["prints"], context["minute_sources"], base
    )
    is_lo, is_hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    oos_lo, oos_hi = metrics.split_ts(OOS_FROM), metrics.split_ts(OOS_TO)
    base_ex = replace(strategy.execution, initial=INITIAL)
    default_fills = resolve_candidates(bars, base_rows, base)
    default_result = {
        "train": sized_stats(default_fills, base_ex, is_lo, is_hi),
        "oos": sized_stats(default_fills, base_ex, oos_lo, oos_hi),
        "train_points": point_stats(default_fills, is_lo, is_hi),
        "oos_points": point_stats(default_fills, oos_lo, oos_hi),
    }

    cells = {}
    for stop in STOP_GRID:
        for target in TARGET_GRID:
            for hold in TIME_GRID:
                params = {**base, "stop": stop, "target": target, "time_stop": hold}
                cells[(stop, target, hold)] = resolve_candidates(bars, base_rows, params)
    ranked = select_bracket(cells, is_lo, is_hi)
    if ranked:
        bracket = ranked[0]
    else:
        key = (base["stop"], base["target"], base["time_stop"])
        fills = resolve_candidates(bars, base_rows, base)
        bracket = {"key": key, **point_stats(fills, is_lo, is_hi),
                   "blocks": block_points(fills), "positive_blocks": 0,
                   "plateau_t": 0.0, "robust_score": 0.0}
        cells[key] = fills

    stop, target, hold = bracket["key"]
    params = {**base, "stop": stop, "target": target, "time_stop": hold}
    filters, facts = filter_definitions(bars)
    filter_rows = {}
    filter_fills = {}
    for name, keep in filters.items():
        fills = resolve_candidates(bars, base_rows, params, keep)
        filter_fills[name] = fills
        filter_rows[name] = {
            **point_stats(fills, is_lo, is_hi),
            "blocks": block_points(fills),
        }
    chosen_filter = select_filter(filter_rows)
    fills = filter_fills[chosen_filter["name"]]

    facts_by_ts = {}
    for row in base_rows:
        details = facts(row)
        details["timestamp"] = row["entry_ts"]
        facts_by_ts[row["entry_ts"]] = details
    sizing, inverse_atr = sizing_study(fills, facts_by_ts, is_lo, is_hi, base_ex)
    sizing_name = choose_sizing(sizing)

    if sizing_name == "inverse_atr_capped_0.50_1.00x":
        train_sized = sized_stats(fills, base_ex, is_lo, is_hi, inverse_atr)
        oos_sized = sized_stats(fills, base_ex, oos_lo, oos_hi, inverse_atr)
    elif sizing_name.startswith("fixed_leverage_"):
        leverage = float(sizing_name.split("_")[-1][:-1])
        chosen_ex = replace(base_ex, risk=0.005, leverage=leverage)
        train_sized = sized_stats(fills, chosen_ex, is_lo, is_hi)
        oos_sized = sized_stats(fills, chosen_ex, oos_lo, oos_hi)
    else:
        risk = float(sizing_name.split("_")[-1][:-1]) / 100.0
        chosen_ex = replace(base_ex, risk=risk)
        train_sized = sized_stats(fills, chosen_ex, is_lo, is_hi)
        oos_sized = sized_stats(fills, chosen_ex, oos_lo, oos_hi)

    result = {
        "strategy": strategy.name,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "status": "promoted" if (
            oos_sized["pnl"] > 0.0 and oos_sized["pf"] > 1.0
            and oos_sized["max_dd"] <= 100.0
        ) else "rejected",
        "protocol": {
            "train": [IS_FROM, IS_TO],
            "sealed_oos": [OOS_FROM, OOS_TO],
            "bracket_cells": len(cells),
            "filter_candidates": len(filters),
            "only_one_filter_may_be_adopted": True,
        },
        "data": {
            "bars": len(bars),
            "raw_prints": len(context["prints"]),
            "raw_prints_by_source": {
                source: sum(row["source"] == source for row in context["prints"])
                for source in ("dbento", "bm")
            },
            "candidate_minutes": len(base_rows),
            "execution": asdict(base_ex),
        },
        "selection": {
            "stop": stop,
            "target": target,
            "time_stop": hold,
            "filter": chosen_filter["name"],
            "sizing": sizing_name,
        },
        "default_comparison": default_result,
        "bracket_ranking": ranked[:15],
        "filter_diagnostics": filter_rows,
        "selected_filter_diagnostics": chosen_filter,
        "sizing_diagnostics": sizing,
        "train_selected": train_sized,
        "oos": oos_sized,
        "train_selected_points": point_stats(fills, is_lo, is_hi),
        "oos_points": point_stats(fills, oos_lo, oos_hi),
        "selected_trade_sources": {
            window: {
                source: sum(
                    metrics.split_ts(lower) <= fill.entry_ts < metrics.split_ts(upper)
                    and next((row["source"] for row in base_rows
                              if row["entry_ts"] == fill.entry_ts), None) == source
                    for fill in fills
                )
                for source in ("dbento", "bm")
            }
            for window, lower, upper in (
                ("train", IS_FROM, IS_TO), ("oos", OOS_FROM, OOS_TO)
            )
        },
        "notes": [
            "Candle direction is never used.",
            "All regime values are known before entry.",
            "Risk-fraction variants can coincide because the Forex margin cap binds first.",
            "No 2026 result participates in parameter selection.",
        ],
    }
    print(json.dumps({
        "status": result["status"],
        "selection": result["selection"],
        "train": result["train_selected"],
        "oos": result["oos"],
    }, indent=2))
    if out_json:
        out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if out_markdown:
        out_markdown.write_text(markdown(result), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-markdown", type=Path)
    args = parser.parse_args()
    run(args.out_json, args.out_markdown)


if __name__ == "__main__":
    main()
