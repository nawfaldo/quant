"""The forward record, and the kill rule that reads it.

Every trial in `trials.json` -- 43 704, 774 and 1 133 for the three level-two
strategies -- was spent against Databento data, which ends 2026-07-16. The
Bookmap capture that starts 2026-07-17 has never been searched by anybody. It is
the only unspent evidence in the project, and it is the only thing that can move
a verdict the 18 months of dbento history cannot.

So this module does two jobs and deliberately refuses a third.

**It measures the forward window.** Each strategy runs once, on the constants
compiled in `live_trade/src/strategies/idk/` today, over the bm-only span. No grid,
no selection, nothing charged to `trials.json` -- a fixed configuration evaluated
on new data is a measurement, not a search.

**It pre-registers the kill rule**, from the out-of-sample distribution measured
on the dbento span *before* the forward data is read. That ordering is the whole
point. A threshold picked after seeing live results is the same error as the
11:00 entry cut deleted from `ofi_momentum.rs` this morning, and the fact that it
would be wearing a risk-management hat rather than an alpha hat changes nothing.

**It refuses to say whether anything works.** At ~12 trades a month a
three-week window holds single-digit trades per strategy. Reported honestly,
that is a status line, not evidence, and `power_note` in the output says so in
the file rather than leaving it to a reader's optimism. The rule below is a
tripwire for detecting *breakage* quickly, which is possible; it is not a
validation plan, which is not.

Usage:

    py -B -m sandbox.research.forward_test
    py -B -m sandbox.research.forward_test --out sandbox/results/forward_test.json
"""
import argparse
import json
import os
from dataclasses import replace

from sandbox import data, execution, metrics, strategies, walkforward

#: First Bookmap session. Everything from here is unsearched.
FORWARD_FROM = "2026-07-17"

#: Start of the anchored walk-forward's test span, and therefore the start of the
#: window the null distribution is measured over.
NULL_FROM = "2025-08-01"

INITIAL = walkforward.INITIAL

STRATEGIES = ("Hourly Delta Reversal", "Deep OFI Momentum")

#: Consecutive months below `kill_month_p10` that stop a strategy.
#:
#: 3, chosen before the forward data was read and defensible from the dbento
#: record rather than from taste: the worst historical losing streak across the
#: three is 2 months (`max_loss_streak` in `defaults_test.json`), so 3 is one
#: month past anything the strategy has done while still working. Raising it
#: after a bad run is the failure mode this constant exists to prevent.
KILL_STREAK_MONTHS = 3


def _fills(strategy, ex):
    """Resolve the compiled configuration to per-unit fills."""
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    params = strategy.all_params()
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    return execution.resolve(bars, signals, ex), bars


def null_distribution(fills, ex, lo, hi):
    """Monthly PnL distribution over the searched span -- the null to beat.

    Percentiles are nearest-rank over the monthly series rather than a fitted
    normal: eighteen months is too few to trust a parametric tail, and the
    quantity being thresholded is a monthly total whose distribution nobody has
    any reason to think is Gaussian.
    """
    stat, _sized = walkforward.window_stats(fills, ex, lo, hi, ex.initial,
                                            span=(lo, hi))
    months = sorted(stat["months"].values())
    if not months:
        return None
    n = len(months)

    def percentile(fraction):
        return months[min(n - 1, int(fraction * n))]

    mean = sum(months) / n
    sd = (sum((m - mean) ** 2 for m in months) / n) ** 0.5 if n > 1 else 0.0
    return {
        "months_observed": n,
        "mean_month": round(mean, 2),
        "sd_month": round(sd, 2),
        "p05": round(percentile(0.05), 2),
        "p10": round(percentile(0.10), 2),
        "worst_month": round(months[0], 2),
        "median_month": round(percentile(0.50), 2),
    }


def forward_window(fills, ex, lo):
    """What the unsearched span has done so far."""
    stat, _sized = walkforward.window_stats(fills, ex, lo, None, ex.initial)
    points = walkforward.window_points(fills, lo, None)
    stat.pop("months", None)
    return {
        "trades": stat["trades"],
        "pnl": stat["pnl"],
        "pf": stat["pf"],
        "win_rate": stat["win_rate"],
        "points_per_trade": round(sum(points) / len(points), 3) if points else 0.0,
        "max_dd": stat["max_dd"],
    }


def evaluate(name):
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    fills, bars = _fills(strategy, ex)
    null_lo, forward_lo = metrics.split_ts(NULL_FROM), metrics.split_ts(FORWARD_FROM)
    null = null_distribution(fills, ex, null_lo, forward_lo)
    forward = forward_window(fills, ex, forward_lo)

    # Pre-registered, written from the null alone. Nothing below reads `forward`.
    rule = None
    if null:
        rule = {
            "kill_month_p10": null["p10"],
            "kill_streak_months": KILL_STREAK_MONTHS,
            "kill_cumulative_below": null["p05"],
            "stated": (
                f"stop after {KILL_STREAK_MONTHS} consecutive months below "
                f"{null['p10']:.2f}, or if cumulative forward PnL falls below "
                f"{null['p05']:.2f}"),
        }
    return {
        "strategy": name,
        "null_span": [NULL_FROM, FORWARD_FROM],
        "null": null,
        "forward_span": [FORWARD_FROM, data.bar_range("level_two", "nq")[1]],
        "forward": forward,
        "kill_rule": rule,
    }


def report(result):
    print(f"\nForward record -- unsearched Bookmap span from {FORWARD_FROM}")
    print(f"  every trial in trials.json was spent on dbento data ending 2026-07-16")
    print("  " + "-" * 88)
    print(f"  {'strategy':<24}{'trades':>7}{'pnl':>9}{'pf':>6}{'pts/tr':>8}"
          f"{'maxDD':>8}   kill rule")
    for row in result["rows"]:
        f, rule = row["forward"], row["kill_rule"]
        print(f"  {row['strategy']:<24}{f['trades']:>7}{f['pnl']:>9.2f}"
              f"{f['pf']:>6.2f}{f['points_per_trade']:>8.2f}{f['max_dd']:>8.2f}"
              f"   {rule['stated'] if rule else 'n/a'}")

    print(f"\n  null distribution, monthly PnL over {NULL_FROM} .. {FORWARD_FROM}")
    for row in result["rows"]:
        n = row["null"]
        if n:
            print(f"    {row['strategy']:<24} n={n['months_observed']:>2}  "
                  f"mean {n['mean_month']:>7.2f}  sd {n['sd_month']:>6.2f}  "
                  f"p10 {n['p10']:>7.2f}  p05 {n['p05']:>7.2f}  "
                  f"worst {n['worst_month']:>7.2f}")

    print(f"\n  {result['power_note']}\n")


def run(out_path=None):
    rows = [evaluate(name) for name in STRATEGIES]
    total_trades = sum(r["forward"]["trades"] for r in rows)
    result = {
        "forward_from": FORWARD_FROM,
        "null_from": NULL_FROM,
        "initial": INITIAL,
        "rows": rows,
        "power_note": (
            f"{total_trades} forward trades across three strategies. This is a "
            f"status line, not evidence: Hourly Delta Reversal needs roughly 400 "
            f"trades for t=2 at its measured dispersion, so the forward window "
            f"can disconfirm quickly and cannot confirm for years."),
        "trials_charged": 0,
    }
    report(result)
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="sandbox/results/forward_test.json")
    run(out_path=parser.parse_args().out)


if __name__ == "__main__":
    main()
