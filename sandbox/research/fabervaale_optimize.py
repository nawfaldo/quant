"""Staged optimizer for the Fabervaale orderflow models.

Protocol, fixed before the first cell is scored:

  * **In sample** is 2025 (2025-02-12 to 2026-01-01). **Out of sample** is
    2026-01-01 onward. Every selection decision reads the in-sample window
    only; the out-of-sample window is scored exactly once, at the end, for the
    single configuration the in-sample search returned.
  * Selection ranks on **monthly consistency**, not total PnL. A cell must
    clear floors on trades, positive-month share and losing streak, and is
    then scored on the weaker of its own monthly Sharpe and its grid
    neighbours' — which prefers a broad plateau to a lucky corner.
  * The search is **staged and greedy**: exits, then structure, then
    time-of-day, then regime. Each stage fixes its winner before the next
    begins. A joint grid over all of these axes would be tens of thousands of
    cells against eleven in-sample months, which is not a search but a
    guarantee of finding something.
  * **Sizing is not searched.** `execution.size` already compounds off live
    equity, and the risk fraction is a pure scale knob: it moves PnL and
    drawdown together without changing which trades are taken. Searching it
    would buy an impressive-looking equity curve and no edge. The risk table
    at the end is *reported*, never selected on.

Even so: eleven in-sample months is thin, the staged search still consumes
real trials, and the deflated monthly Sharpe in the report is the number to
read before believing any of it.
"""
import argparse
import itertools
import json
import os
from dataclasses import asdict, replace

from sandbox import data, execution, metrics, trials, walkforward
from sandbox.strategies.fabervaale_orderflow import FabervaaleOrderflow

IS_FROM, IS_TO = "2025-02-12", "2026-01-01"
OOS_FROM = "2026-01-01"

#: A cell must clear all of these on the in-sample window to be selectable.
MIN_TRADES = 60
MIN_POS_RATE = 0.5
MAX_LOSS_STREAK = 3
MAX_TOP_MONTH_SHARE = 0.5

#: The staged axis groups, searched in this order.
STAGES = (
    ("exits", {
        "stop": [10.0, 15.0, 20.0, 25.0],
        "rr": [0.0, 1.5, 2.0, 3.0],
        "trail_frac": [0.0, 0.5, 1.0, 1.5],
    }),
    ("structure", {
        "opening_range": [15, 30, 60],
        "buffer": [0.0, 1.0, 2.0],
        "confirm_window": [3, 5, 10],
        "max_trades": [1, 2],
    }),
    ("session", {
        "entry_from": [0, 630, 690],
        "entry_to": [720, 810, 900],
        "skip_days": ["", "mon", "fri", "mon,fri"],
    }),
    ("regime", {
        "vix_min": [0.0, 15.0, 18.0],
        "vix_max": [0.0, 25.0, 30.0],
        "atr_min": [0.0, 100.0, 150.0],
        "trend_days": [0, 5, 20],
    }),
)

RISK_TABLE = (0.0025, 0.005, 0.01, 0.02)


def load(use_cached=True):
    """Bars and the full gate context, loaded once for the whole campaign."""
    bars = (data.load_cached_level_two_bars() if use_cached
            else data.load_level_two_bars())
    features = (data.load_cached_l2_features() if use_cached
                else data.load_l2_features())
    context = {
        "features": features,
        "vix": data.vix_series(bars, data.load_hourly_vix()),
        "atr": data.atr_by_day(bars, 20),
        "closes": data.load_session_closes(),
    }
    return bars, context


def window_stats(fills, ex, lo, hi):
    """Consistency panel for the trades entered in `[lo, hi)`, sized from scratch.

    Each window compounds from the same starting equity so the two are
    comparable: an out-of-sample window that inherited an in-sample balance
    would report position sizes the strategy only earned by being fitted.
    """
    inside = [fill for fill in fills if lo <= fill.entry_ts < hi]
    sized = execution.size(inside, ex)
    stat = metrics.stats(sized, initial=ex.initial, span=(lo, hi))
    stat["final_equity"] = round(ex.initial + stat["pnl"], 2)
    stat["return_pct"] = round(100.0 * stat["pnl"] / ex.initial, 2)
    return stat


def selectable(stat):
    return (stat["trades"] >= MIN_TRADES
            and stat["pos_rate"] >= MIN_POS_RATE
            and stat["max_loss_streak"] <= MAX_LOSS_STREAK
            and stat["top_month_share"] <= MAX_TOP_MONTH_SHARE)


def neighbours(axes, combo):
    """Cells one grid step away from `combo` on exactly one of `axes`."""
    out = []
    for name, values in axes.items():
        index = values.index(combo[name])
        for step in (index - 1, index + 1):
            if 0 <= step < len(values):
                variant = dict(combo)
                variant[name] = values[step]
                out.append(tuple(sorted(variant.items())))
    return out


def run_stage(strategy, bars, context, ex, base, axes, lo, hi):
    """Score every cell of one stage in sample. -> (best combo, ranked rows)."""
    scores = {}
    stats = {}
    names = sorted(axes)
    for values in itertools.product(*(axes[name] for name in names)):
        combo = dict(zip(names, values))
        params = strategy.all_params({**base, **combo})
        if not strategy.valid(params):
            continue
        signals = strategy.signals(bars, context, "all", params)
        fills = execution.resolve(bars, signals, ex)
        key = tuple(sorted(combo.items()))
        stats[key] = window_stats(fills, ex, lo, hi)
        scores[key] = stats[key]["msharpe"]

    ranked = []
    for key, stat in stats.items():
        if not selectable(stat):
            continue
        near = [scores[n] for n in neighbours(axes, dict(key)) if n in scores]
        plateau = sum(near) / len(near) if near else 0.0
        ranked.append((round(min(stat["msharpe"], plateau), 4), round(plateau, 4),
                       dict(key), stat))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return (ranked[0][2] if ranked else None), ranked, len(stats)


def risk_exposure(strategy, bars, context, ex, params, lo, hi, oos_lo, oos_hi):
    """Reported-only: what the risk fraction does to the selected config."""
    signals = strategy.signals(bars, context, "all", strategy.all_params(params))
    fills = execution.resolve(bars, signals, ex)
    rows = []
    for risk in RISK_TABLE:
        scaled = ex.with_risk(risk)
        in_sample = window_stats(fills, scaled, lo, hi)
        out_sample = window_stats(fills, scaled, oos_lo, oos_hi)
        rows.append({
            "risk": risk,
            "is_pnl": in_sample["pnl"], "is_max_dd": in_sample["max_dd"],
            "is_final_equity": in_sample["final_equity"],
            "oos_pnl": out_sample["pnl"], "oos_max_dd": out_sample["max_dd"],
            "oos_final_equity": out_sample["final_equity"],
        })
    return rows


def run(model="ivb2", use_cached=True, record_trials=False, top=5):
    strategy = FabervaaleOrderflow()
    bars, context = load(use_cached)
    ex = strategy.execution
    lo, hi = metrics.split_ts(IS_FROM), metrics.split_ts(IS_TO)
    oos_lo, oos_hi = metrics.split_ts(OOS_FROM), bars[-1][0] + 86_400

    base = {"model": model}
    cells = 0
    stage_reports = []
    for name, axes in STAGES:
        best, ranked, scored = run_stage(
            strategy, bars, context, ex, base, axes, lo, hi)
        cells += scored
        stage_reports.append({
            "stage": name,
            "cells_scored": scored,
            "cells_passing_floors": len(ranked),
            "chosen": best,
            "top": [{"score": score, "plateau": plateau, "params": combo,
                     "pnl": stat["pnl"], "trades": stat["trades"],
                     "pf": stat["pf"], "msharpe": stat["msharpe"],
                     "pos_rate": stat["pos_rate"],
                     "max_loss_streak": stat["max_loss_streak"]}
                    for score, plateau, combo, stat in ranked[:top]],
        })
        if best is not None:
            base.update(best)

    selected = strategy.all_params(base)
    signals = strategy.signals(bars, context, "all", selected)
    fills = execution.resolve(bars, signals, ex)
    in_sample = window_stats(fills, ex, lo, hi)
    out_sample = window_stats(fills, ex, oos_lo, oos_hi)

    baseline_fills = execution.resolve(
        bars, strategy.signals(bars, context, "all",
                               strategy.all_params({"model": model})), ex)

    charged = trials.total(strategy.name)
    if record_trials:
        charged = trials.record(
            strategy.name, cells,
            f"Fabervaale staged {model} optimization: exits, structure, "
            f"session, regime; selection on 2025 only")

    return {
        "strategy": strategy.name,
        "model": model,
        "protocol": {
            "in_sample": [IS_FROM, IS_TO],
            "out_of_sample": [OOS_FROM, "end of data"],
            "selection_metric": "monthly Sharpe, floored on consistency, "
                                "scored against grid neighbours",
            "floors": {"min_trades": MIN_TRADES, "min_pos_rate": MIN_POS_RATE,
                       "max_loss_streak": MAX_LOSS_STREAK,
                       "max_top_month_share": MAX_TOP_MONTH_SHARE},
            "sizing": "equity-compounding; risk fraction reported, not searched",
            "execution": asdict(ex),
            "cells_scored": cells,
            "cumulative_trials": charged,
        },
        "stages": stage_reports,
        "selected_params": selected,
        "in_sample": in_sample,
        "out_of_sample": out_sample,
        # Deflated against the *cumulative* trial count, not this run's grid:
        # the haircut asks how good the best cell would look if none of them
        # worked, and that depends on every draw taken from the same noise.
        "deflated_msharpe": {
            "in_sample": round(walkforward.deflated(
                in_sample["msharpe"], in_sample["n_months"],
                max(charged, cells, 1)), 4),
            "charged_against": max(charged, cells),
            "out_of_sample_raw": out_sample["msharpe"],
        },
        "baseline_defaults": {
            "in_sample": window_stats(baseline_fills, ex, lo, hi),
            "out_of_sample": window_stats(baseline_fills, ex, oos_lo, oos_hi),
        },
        "risk_exposure": risk_exposure(
            strategy, bars, context, ex, base, lo, hi, oos_lo, oos_hi),
    }


def print_report(report):
    def panel(label, stat):
        print(f"\n{label}: pnl {stat['pnl']:+.2f} on "
              f"{report['protocol']['execution']['initial']:.0f} "
              f"({stat['return_pct']:+.2f}%)  trades {stat['trades']}  "
              f"pf {stat['pf']}  dd {stat['max_dd']}")
        print(f"  months {stat['pos_months']}/{stat['n_months']} positive  "
              f"mSharpe {stat['msharpe']}  streak {stat['max_loss_streak']}  "
              f"worst {stat['worst_month']}  top-month share "
              f"{stat['top_month_share']}")
        for month, value in stat["months"].items():
            bar = "+" if value > 0 else ("-" if value < 0 else ".")
            print(f"    {month}  {value:+9.2f}  {bar * min(30, int(abs(value)))}")

    print(f"=== {report['strategy']} / {report['model']} ===")
    for stage in report["stages"]:
        print(f"\nstage {stage['stage']}: {stage['cells_passing_floors']}"
              f"/{stage['cells_scored']} cells passed the floors")
        if stage["chosen"] is None:
            print("  no cell cleared the floors; stage left at its defaults")
        for row in stage["top"]:
            print(f"  score {row['score']:>7.3f}  plateau {row['plateau']:>7.3f}  "
                  f"pnl {row['pnl']:>8.2f}  tr {row['trades']:>4}  "
                  f"pf {row['pf']:>5.2f}  {row['params']}")

    print(f"\nselected: "
          f"{ {k: v for k, v in report['selected_params'].items()} }")
    panel("IN SAMPLE (2025, selection window)", report["in_sample"])
    panel("OUT OF SAMPLE (2026, scored once)", report["out_of_sample"])
    panel("baseline defaults, out of sample",
          report["baseline_defaults"]["out_of_sample"])

    print(f"\ncells scored {report['protocol']['cells_scored']}, "
          f"cumulative trials {report['protocol']['cumulative_trials']}")
    print(f"deflated in-sample mSharpe "
          f"{report['deflated_msharpe']['in_sample']}")

    print("\nrisk exposure (reported, not selected on; sizing compounds):")
    print(f"  {'risk':>7} {'IS pnl':>9} {'IS dd':>8} {'IS end':>9} "
          f"{'OOS pnl':>9} {'OOS dd':>8} {'OOS end':>9}")
    for row in report["risk_exposure"]:
        print(f"  {row['risk']:>7.4f} {row['is_pnl']:>9.2f} {row['is_max_dd']:>8.2f} "
              f"{row['is_final_equity']:>9.2f} {row['oos_pnl']:>9.2f} "
              f"{row['oos_max_dd']:>8.2f} {row['oos_final_equity']:>9.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="ivb2",
                        choices=["ivb1", "ivb2", "exhaustion"])
    parser.add_argument("--fresh", action="store_true",
                        help="fingerprint QuestDB and refresh caches first")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    report = run(model=args.model, use_cached=not args.fresh,
                 record_trials=args.record_trials)
    print_report(report)

    out = args.out or f"sandbox/results/fabervaale_{args.model}_optimize.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
