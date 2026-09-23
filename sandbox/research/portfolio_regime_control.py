"""Can a causal regime rule cut this book's drawdown better than turning the size down?

THE QUESTION, STATED SO IT CAN FAIL. The three-sleeve book draws 37.5% and
returns 130%. Multiplying every sleeve by a constant `k` is linear in both, so
the control already offers 20% drawdown for 50% return, and 16% for 34%. A
regime overlay earns its place only if, *at the same drawdown*, it returns more
than that constant does -- on the window it was not fitted on. Everything here
is scored that way and the control is printed beside every candidate.

WHAT THE DIAGNOSIS ALREADY ESTABLISHED, because it shapes what is worth trying:

  * The sleeves are not correlated. Daily-return rho is -0.14, +0.01 and -0.14.
    There is no "they all lose together" to filter out.
  * The drawdown is three risk budgets stacked on one balance: the sleeves'
    own drawdowns inside the book sum to 51.5%, diversification already returns
    14 of that, and 37.5% is what is left. That is a *sizing* number, and sizing
    is exactly what a constant fixes.
  * The whole drawdown is a 2025 event -- at every `k` the full-window figure
    equals the 2025 figure -- while 2026 carries all the return at half the risk.

So the prior going in is that the constant is hard to beat, and the only thing
that beats it is a rule genuinely out of the market through 2025 and back in for
2026 *without being told which is which*.

THE SHADOW BOOK, and why the throttle reads it rather than the live balance.
An equity-curve throttle that reads the balance it is throttling is a feedback
loop: the schedule changes the curve, the curve changes the schedule. Iterating
that to a fixed point does not converge here -- a threshold rule on its own
output oscillates, and twelve rounds of it wander between 15% and 41% drawdown
without settling, so there is no "the rule" to report.

The fix is not damping, it is a better specification. The throttle reads a
*reference-size* book -- the same three sleeves at k = 1, tracked in parallel and
never resized. That is feedback-free by construction, single-pass, and is what a
live system would actually run: keep a paper book at reference size, read its
curve each night, and set tomorrow's exposure from it. Because sizing is linear
in equity, the reference curve's *shape* is the signal; only its scale differs
from the traded book.

WHAT IS AND IS NOT IN SAMPLE. Rule parameters are chosen on 2025 and read once
on 2026. But both NQ sleeves were themselves selected on roughly the 2025 span,
so 2025 is not a clean training window for anything -- it is an already fitted
book, and its drawdown is what these strategies did on their own training data.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

from sandbox.research import portfolio_exposure as px


OUTPUT = os.path.join(os.path.dirname(__file__), "portfolio_regime_control.json")
BUDGET = 20.0


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def calendar(first, last):
    start = datetime.strptime(first, "%Y-%m-%d")
    end = datetime.strptime(last, "%Y-%m-%d")
    out = []
    while start <= end:
        out.append(start.strftime("%Y-%m-%d"))
        start += timedelta(days=1)
    return out


def reference_curve(window=px.FULL, initial=px.INITIAL):
    """The shadow book's balance at each calendar day's end.

    Closed-trade rather than marked-to-market: a live throttle resizing on
    unrealised swings would fire and unfire inside a session, and the engine
    only takes exposure at entry. This is what a nightly job would read.
    """
    result = px.run(None, window)
    closes, equity = {}, initial
    for trade in sorted(result["trades_log"], key=lambda t: t["xt"]):
        equity += trade["pnl"]
        closes[day_of(trade["xt"])] = equity
    days = calendar(*window)
    values, index, equity = {}, 0, initial
    ordered = sorted(closes.items())
    for day in days:
        while index < len(ordered) and ordered[index][0] <= day:
            equity = ordered[index][1]
            index += 1
        values[day] = equity
    return days, values


# --------------------------------------------------------------------------
# rules: yesterday's reference history -> today's multiplier


def rule_moving_average(history, window, low):
    """Stand down while the reference balance is below its own moving average."""
    if len(history) < window:
        return 1.0
    return low if history[-1] < statistics.fmean(history[-window:]) else 1.0


def rule_drawdown(history, limit, low):
    """Stand down once the reference balance is `limit` percent off its peak."""
    if not history:
        return 1.0
    peak = max(history)
    if peak <= 0:
        return 1.0
    return low if 100.0 * (peak - history[-1]) / peak > limit else 1.0


def rule_volatility(history, window, target, cap):
    """Scale inversely with the reference book's own realised volatility.

    The standard answer to "cut my drawdown", included because it is the one
    rule here that sizes *up* through 2025 -- 2025 was the quiet window in
    dollar terms, on a balance that had not compounded yet. Reporting that it
    points the wrong way is the point of running it.
    """
    if len(history) < window + 1:
        return 1.0
    returns = [current / previous - 1.0 if previous > 0 else 0.0
               for previous, current in zip(history[-window - 1:-1], history[-window:])]
    deviation = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    if deviation <= 0:
        return cap
    return max(0.0, min(cap, (target / (252 ** 0.5) / 100.0) / deviation))


RULES = {"ma": rule_moving_average, "dd": rule_drawdown, "vol": rule_volatility}


def schedule_from(days, values, rule, params, sleeves=px.SLEEVES):
    """A day -> multiplier step function using only strictly earlier balances."""
    history, points, previous = [], [], None
    for day in days:
        factor = round(float(rule(history, **params)), 4)
        if factor != previous:
            points.append((day, factor))
            previous = factor
        history.append(values[day])
    return {name: dict(points) for name in sleeves}


def build(rule, params, window):
    days, values = reference_curve(window)
    return schedule_from(days, values, RULES[rule], params)


def evaluate_rule(rule, params):
    """One rule across the three windows, each built from that window's own
    reference curve -- so the 2026 run starts cold, exactly as it would live."""
    out = {}
    for tag, window in (("full", px.FULL), ("is", px.IS), ("oos", px.OOS)):
        out[tag] = px.run(build(rule, params, window), window)
    return out


def control_curve(grid=None):
    """The constant frontier on each window, so any candidate can be matched to
    the constant that reaches its drawdown."""
    grid = grid or [round(0.05 * i, 2) for i in range(1, 21)]
    return {factor: px.evaluate(px.flat(factor)) for factor in grid}


def matched_control(frontier, tag, drawdown):
    """Best-returning constant whose drawdown is no worse than `drawdown`."""
    best = None
    for factor, results in frontier.items():
        result = results[tag]
        if result["max_dd_pct"] <= drawdown + 1e-9:
            if best is None or result["return_pct"] > best[1]["return_pct"]:
                best = (factor, result)
    return best


# --------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    report = {}

    print("=" * 86)
    print("CONTROL -- every sleeve times one constant")
    print("=" * 86)
    print(px.HEADER)
    frontier = control_curve()
    for factor in sorted(frontier, reverse=True):
        if factor in (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1):
            print(px.show(f"flat k={factor}", frontier[factor]))
    report["control"] = {
        str(k): {tag: {f: v[tag][f] for f in px.FIELDS}
                 for tag in ("full", "is", "oos")}
        for k, v in frontier.items()
    }

    print()
    print("=" * 86)
    print("REGIME RULES -- throttled off a reference-size shadow book")
    print("=" * 86)
    print(px.HEADER)

    grids = {
        "ma": [{"window": w, "low": low}
               for w in (20, 40, 60, 90) for low in (0.0, 0.25, 0.5)],
        "dd": [{"limit": limit, "low": low}
               for limit in (5, 8, 12, 18) for low in (0.0, 0.25, 0.5)],
        "vol": [{"window": w, "target": target, "cap": 1.0}
                for w in (20, 40, 60) for target in (10, 15, 20, 30)],
    }

    rows = []
    for rule, grid in grids.items():
        for params in grid:
            label = rule + " " + ",".join(f"{k}={v}" for k, v in params.items())
            results = evaluate_rule(rule, params)
            print(px.show(label, results))
            rows.append({"rule": rule, "params": params, "label": label,
                         **{tag: {f: results[tag][f] for f in px.FIELDS}
                            for tag in ("full", "is", "oos")}})
    report["candidates"] = rows

    print()
    print("=" * 86)
    print(f"SELECTION -- best 2025 return inside a {BUDGET:.0f}% drawdown budget")
    print("=" * 86)
    eligible = [row for row in rows if row["is"]["max_dd_pct"] <= BUDGET]
    if not eligible:
        print(f"  no regime rule got 2025 under {BUDGET:.0f}%; nothing to carry forward")
        report["winner"] = None
    else:
        winner = max(eligible, key=lambda row: row["is"]["return_pct"])
        report["winner"] = winner
        print(f"  chosen on 2025 alone:  {winner['label']}")
        print(f"    2025 IS   return {winner['is']['return_pct']:8.2f}%  "
              f"maxDD {winner['is']['max_dd_pct']:6.2f}%")
        print(f"    2026 OOS  return {winner['oos']['return_pct']:8.2f}%  "
              f"maxDD {winner['oos']['max_dd_pct']:6.2f}%")
        for tag, label in (("is", "2025 IS"), ("oos", "2026 OOS")):
            match = matched_control(frontier, tag, winner[tag]["max_dd_pct"])
            if not match:
                continue
            factor, result = match
            verdict = ("rule wins" if winner[tag]["return_pct"] > result["return_pct"]
                       else "CONTROL WINS")
            print(f"    {label} control at the same drawdown: flat k={factor} -> "
                  f"return {result['return_pct']:8.2f}%  "
                  f"maxDD {result['max_dd_pct']:6.2f}%   [{verdict}]")
            report[f"matched_control_{tag}"] = {
                "k": factor, **{f: result[f] for f in px.FIELDS}, "verdict": verdict}

    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
