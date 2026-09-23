"""Minimal chronological 80/20 validation for NQ Whale Digestion.

Only the emergency-stop distance is searched. The signal definition, book
threshold, size band, 15-minute horizon, costs, and 0.25% fixed-fraction sizing
are frozen before the split. The final 75 sessions never participate in the
choice. A positive holdout is not enough for promotion: it must also clear a
cost-relative edge floor and a bootstrap uncertainty gate.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from sandbox import data, execution, metrics, walkforward
from sandbox.strategies.whale_digestion import WhaleDigestion, whale_rows

SPLIT_DATE = "2026-04-15"
INITIAL = 1_000.0
MIN_HOLDOUT_TRADES = 30
MIN_PF = 1.10
MIN_EDGE = 0.60  # three times the configured 0.20-point round-trip cost


def point_stats(fills):
    points = [fill.points for fill in fills]
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    low, high = walkforward.bootstrap_edge(points, draws=10_000, seed=83)
    return {
        "trades": len(points),
        "points": round(sum(points), 3),
        "edge": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "pf_points": round(wins / losses, 4) if losses else (999.0 if wins else 0.0),
        "bootstrap_mean_95_ci": [round(low, 4), round(high, 4)],
    }


def score(fills, ex, lo, hi):
    selected = [fill for fill in fills if lo <= fill.entry_ts < hi]
    sized = execution.size(selected, ex)
    return {
        **point_stats(selected),
        "sized": metrics.stats(sized, initial=ex.initial, span=(lo, hi)),
    }


def markdown(result):
    selected = result["selected_stop"]
    train = result["train_cells"][str(int(selected))]
    hold = result["holdout"]
    return "\n".join([
        "# NQ Whale Digestion — chronological 80/20",
        "",
        f"Status: **{result['status']}**.",
        "",
        f"The first {result['split']['train_sessions']} sessions selected one of",
        f"three emergency stops. The final {result['split']['holdout_sessions']}",
        "sessions were evaluated once and did not influence selection.",
        "",
        "## Frozen strategy",
        "",
        f"- Emergency stop: {selected:.0f} points",
        "- Profit target: none",
        "- Time exit: 15 minutes",
        "- Risk: 0.25% of live equity, Forex 0.01-lot floor",
        "",
        "## Result",
        "",
        "| Window | Trades | Net points | Edge | Point PF | Sized PnL | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 80% train | {train['trades']} | {train['points']:+.2f} | {train['edge']:+.3f} | {train['pf_points']:.3f} | {train['sized']['pnl']:+.2f} | {train['sized']['max_dd']:.2f} |",
        f"| 20% holdout | {hold['trades']} | {hold['points']:+.2f} | {hold['edge']:+.3f} | {hold['pf_points']:.3f} | {hold['sized']['pnl']:+.2f} | {hold['sized']['max_dd']:.2f} |",
        "",
        "Promotion requires at least 30 holdout trades, PF 1.10, mean edge",
        "0.60 points, and a bootstrap lower bound above zero.",
        "",
    ])


def run(out_json=None, out_markdown=None):
    strategy = WhaleDigestion()
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    split = metrics.split_ts(SPLIT_DATE)
    lo, hi = int(bars[0][data.TS]), int(bars[-1][data.TS]) + 60
    days = sorted({int(bar[data.TS]) // 86_400 for bar in bars})
    train_days = sum(day * 86_400 < split for day in days)

    cells = {}
    fills_by_stop = {}
    for stop in strategy.grid["stop"]:
        params = strategy.all_params({"stop": stop})
        signals = strategy.signals(bars, context, "all", params)
        fills = execution.resolve(bars, signals, strategy.execution)
        fills_by_stop[stop] = fills
        cells[str(int(stop))] = score(fills, strategy.execution, lo, split)

    # Training only: require positive edge and choose the broadest disaster
    # stop among essentially tied positive cells. A distant stop most closely
    # preserves the source's fixed-horizon payoff rather than curve-fitting an
    # intratrade exit.
    passed = [stop for stop in strategy.grid["stop"]
              if cells[str(int(stop))]["edge"] > 0.0]
    selected_stop = max(passed) if passed else strategy.defaults["stop"]
    holdout = score(
        fills_by_stop[selected_stop], strategy.execution, split, hi
    )
    checks = {
        "enough_trades": holdout["trades"] >= MIN_HOLDOUT_TRADES,
        "profit_factor": holdout["pf_points"] >= MIN_PF,
        "cost_relative_edge": holdout["edge"] >= MIN_EDGE,
        "bootstrap_lower_positive": holdout["bootstrap_mean_95_ci"][0] > 0.0,
    }
    status = "promoted" if all(checks.values()) else "rejected"

    entry_source = {}
    rows = strategy.signals(
        bars, context, "all", strategy.all_params({"stop": selected_stop})
    )
    candidate_source = {
        row["index"]: row["source"]
        for row in whale_rows(
            bars, context["prints"], context["minute_sources"],
            strategy.all_params({"stop": selected_stop}),
        )
    }
    for signal in rows:
        entry_source[int(bars[signal.index][data.TS])] = candidate_source.get(
            signal.index, ""
        )
    selected_fills = fills_by_stop[selected_stop]
    sources = {
        source: sum(
            fill.entry_ts >= split and entry_source.get(fill.entry_ts) == source
            for fill in selected_fills
        )
        for source in ("dbento", "bm")
    }

    result = {
        "strategy": strategy.name,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "status": status,
        "split": {
            "method": "chronological sessions",
            "first": datetime.fromtimestamp(lo, tz=timezone.utc).date().isoformat(),
            "split": SPLIT_DATE,
            "last": datetime.fromtimestamp(hi - 60, tz=timezone.utc).date().isoformat(),
            "train_sessions": train_days,
            "holdout_sessions": len(days) - train_days,
            "train_fraction": round(train_days / len(days), 4),
        },
        "fixed": {
            "signal": "true minute maximum, 100-190, either wick, balanced prior second",
            "time_stop": 15,
            "target": 0.0,
            "execution": asdict(strategy.execution),
        },
        "search": {
            "axis": "emergency stop only",
            "cells": len(strategy.grid["stop"]),
            "values": strategy.grid["stop"],
        },
        "selected_stop": selected_stop,
        "train_cells": cells,
        "holdout": holdout,
        "holdout_sources": sources,
        "promotion_checks": checks,
        "notes": [
            "No weekday, time-of-day, regime, size, book, target, or sizing search.",
            "The current historical feed is MBP-10/L2, not parent-order MBO/L3.",
            "The final 20% had been viewed in earlier prototype work and is therefore not scientifically pristine.",
        ],
    }
    print(json.dumps({
        "status": status,
        "selected_stop": selected_stop,
        "train": cells[str(int(selected_stop))],
        "holdout": holdout,
        "checks": checks,
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
