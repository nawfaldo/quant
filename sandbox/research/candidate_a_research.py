"""Candidate A preflight and event-study kill gate.

This module deliberately cannot expose 2026 or launch an optimization before
the default event passes the protocol in LIQUIDITY_STRATEGY_CANDIDATES.md.  A
failed definition does not earn more axes; this is what keeps exit, calendar,
VIX, and sizing searches from manufacturing a result from the holdout.
"""
import argparse
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timezone

from sandbox import data, execution, metrics, trials, walkforward
from sandbox.data import O, TS
from sandbox.strategies.l2_opening_range_breakout import L2OpeningRangeBreakout

IS_FROM = "2025-02-12"
IS_TO = "2026-01-01"
HORIZONS = (1, 5, 15, 30, 60)


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def point_summary(fills):
    points = [fill.points for fill in fills]
    lo, hi = walkforward.bootstrap_edge(points, seed=7)
    return {
        "trades": len(points),
        "net_points": round(sum(points), 3),
        "mean_net_points": round(sum(points) / len(points), 4) if points else 0.0,
        "edge_t": round(walkforward.edge_t(points), 4),
        "bootstrap_mean_95_ci": [round(lo, 4), round(hi, 4)],
    }


def event_study(bars, signals, spread):
    """Signed close move from entry open, at fixed elapsed horizons."""
    samples = {h: [] for h in HORIZONS}
    for signal in signals:
        entry = bars[signal.index]
        entry_ts = entry[TS]
        entry_day = entry_ts // 86_400
        sign = 1.0 if signal.side == execution.LONG else -1.0
        cursor = signal.index
        for horizon in HORIZONS:
            target = entry_ts + horizon * 60
            while (cursor < len(bars) and bars[cursor][TS] < target
                   and bars[cursor][TS] // 86_400 == entry_day):
                cursor += 1
            if (cursor >= len(bars)
                    or bars[cursor][TS] // 86_400 != entry_day
                    or bars[cursor][TS] < target):
                continue
            samples[horizon].append(sign * (bars[cursor][4] - entry[O]) - spread)

    out = {}
    for horizon, values in samples.items():
        lo, hi = walkforward.bootstrap_edge(values, seed=7 + horizon)
        out[str(horizon)] = {
            "events": len(values),
            "mean_net_points": round(sum(values) / len(values), 4) if values else 0.0,
            "net_points": round(sum(values), 3),
            "edge_t": round(walkforward.edge_t(values), 4),
            "bootstrap_mean_95_ci": [round(lo, 4), round(hi, 4)],
        }
    return out


def complete_sessions(bars):
    sessions = {}
    for bar in bars:
        day = bar[TS] // 86_400
        minute = (bar[TS] % 86_400) // 60
        sessions.setdefault(day, [minute, minute])
        sessions[day][0] = min(sessions[day][0], minute)
        sessions[day][1] = max(sessions[day][1], minute)
    return sum(lo <= 570 and hi >= 959 for lo, hi in sessions.values())


def run(use_cached=True, record_trials=False):
    strategy = L2OpeningRangeBreakout()
    bars = (data.load_cached_level_two_bars()
            if use_cached else data.load_level_two_bars())
    features = (data.load_cached_l2_features()
                if use_cached else data.load_l2_features())
    vix = data.vix_series(bars, data.load_hourly_vix())
    context = {"features": features, "vix": vix}
    ex = replace(strategy.execution, initial=1_000.0, slippage=0.2)
    lo, hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)

    def evaluate(require_l2):
        params = strategy.all_params({
            "require_l2": require_l2,
            "from_date": IS_FROM,
            "to_date": IS_TO,
        })
        signals = strategy.signals(bars, context, "all", params)
        fills = [fill for fill in execution.resolve(bars, signals, ex)
                 if lo <= fill.entry_ts < hi]
        sized = execution.size(fills, ex)
        return params, signals, fills, {
            **point_summary(fills),
            "sized": metrics.stats(sized, initial=ex.initial, span=(lo, hi)),
            "event_study": event_study(bars, signals, ex.entry_cost),
        }

    params, candidate_signals, candidate_fills, candidate = evaluate(True)
    _, control_signals, control_fills, control = evaluate(False)
    positive_horizons = sum(
        row["mean_net_points"] > 0 for row in candidate["event_study"].values()
    )
    beats_control_horizons = sum(
        candidate["event_study"][h]["mean_net_points"]
        > control["event_study"][h]["mean_net_points"]
        for h in candidate["event_study"]
    )

    reasons = []
    if candidate["mean_net_points"] <= 0:
        reasons.append("default bracket has non-positive net edge")
    if candidate["mean_net_points"] <= control["mean_net_points"]:
        reasons.append("default bracket does not beat the matched no-L2 control")
    if positive_horizons == 0:
        reasons.append("directional effect is absent at every fixed horizon")
    if beats_control_horizons < 3:
        reasons.append("L2 confirmation does not beat control at most horizons")

    charged = trials.total(strategy.name)
    if record_trials:
        charged = trials.record(
            strategy.name, 2,
            "Candidate A 2025 kill test: default L2 event and matched control",
        )

    report = {
        "candidate": strategy.name,
        "status": "killed_before_optimization" if reasons else "eligible_for_optimization",
        "reason": reasons,
        "data": {
            "bars": len(bars),
            "features": len(features),
            "coverage": [iso(bars[0][TS]), iso(bars[-1][TS])],
            "complete_sessions": complete_sessions(bars),
            "timestamp_convention": "New York wall clock encoded as UTC",
        },
        "experiment": {
            "in_sample": [IS_FROM, IS_TO],
            "out_of_sample_evaluated": False,
            "instrument": "NQ",
            "account_model": "Forex",
            "execution": asdict(ex),
            "default_params": params,
            "cumulative_trials": charged,
        },
        "candidate_2025": candidate,
        "matched_control_2025": control,
        "candidate_minus_control": {
            "mean_bracket_points": round(
                candidate["mean_net_points"] - control["mean_net_points"], 4
            ),
            "fixed_horizon_mean_points": {
                h: round(candidate["event_study"][h]["mean_net_points"]
                         - control["event_study"][h]["mean_net_points"], 4)
                for h in candidate["event_study"]
            },
        },
        "optimization": {
            "run": not reasons,
            "evaluated_axes": [] if reasons else [
                "opening_range", "buffer", "min_imbalance", "stop", "target",
                "entry_time", "weekday", "vix", "sizing",
            ],
            "note": ("SL/TP, day/time, VIX, and sizing were not searched because "
                     "the predeclared kill gate failed; 2026 remains untouched.")
                    if reasons else "Run the separately predeclared optimizer.",
        },
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true",
                        help="fingerprint QuestDB and refresh caches first")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--out", default="sandbox/results/candidate_a_result.json")
    args = parser.parse_args()
    report = run(use_cached=not args.fresh, record_trials=args.record_trials)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)

    print(json.dumps({
        "status": report["status"],
        "reason": report["reason"],
        "candidate": report["candidate_2025"],
        "control": report["matched_control_2025"],
        "candidate_minus_control": report["candidate_minus_control"],
        "out_of_sample_evaluated": False,
        "output": args.out,
    }, indent=2))


if __name__ == "__main__":
    main()
