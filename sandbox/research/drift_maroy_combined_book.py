"""Run every surviving cell as one book and size the book to a ~15% drawdown.

The five sleeves are the pairs that beat their own coin-flip control at all three
cost settings in `drift_maroy_symbol_study`.  Each one's sizing policy is the
shape its own solo 15% calibration chose in `drift_maroy_risk_budget`, so the
sleeves enter the book carrying equal risk rather than equal leverage.  Maroy
sleeves all run flat leverage plus the drawdown throttle; Drift sizes off its
stop and has no throttle to run.

STRUCTURE.  One sub-account per sleeve, equal capital, each compounding its own
slice.  The book's equity is their sum on a shared calendar.  This is a structure
that can actually be run, and it makes the combined drawdown exact rather than
modelled, because the curves are summed as *equity*, never as return streams
([[blend-model-understates-portfolio-drawdown]]).

WHAT IS CALIBRATED.  One number: a joint scale applied to every sleeve at once,
set on 2020-2024 only.  The signal cells, the policy shapes and the equal capital
split are all fixed in advance.  The out-of-sample drawdown is reported as
whatever it turns out to be.

DO NOT READ THE BOOK AS FIVE INDEPENDENT BETS.  Two sleeves are the same
instrument in the same session, and three are Maroy's time exit on three
different instruments -- one strategy, three symbols.  The diversification that
shows up here is measured over one holdout, not structural
([[low-correlation-sleeves-let-you-size-up]]).  DE40 in particular was beaten by
its own null out of sample, and is carried here only because it was asked for;
its per-sleeve column is worth checking before trusting the total.

CALENDARS.  Sleeves trade different sessions on different symbols, so the book
runs on the union of their trading days and each sub-account carries its last
equity across days it does not trade.  A sleeve with no data in a window (XPTUSD
before 2021-10) simply sits flat with its capital idle, which is what a real book
would do.  Annualisation is derived from the calendar itself rather than assumed,
because a book mixing a 365-day crypto sleeve with 252-day CFD sleeves has
neither convention.

Run from the repository root:

    py -B -m sandbox.research.drift_maroy_combined_book
    py -B -m sandbox.research.drift_maroy_combined_book --target 10
    py -B -m sandbox.research.drift_maroy_combined_book --sleeves ethusd:maroy,de40:maroy
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics

from sandbox.research import drift_maroy_symbol_study as study
from sandbox.research import drift_maroy_risk_budget as budget
from sandbox.research import drift_maroy_validate as val
from sandbox.research import maroy_intraday_momentum as maroy


OUTPUT = os.path.join(os.path.dirname(__file__), "..", "results",
                      "drift_maroy_combined_book_result.json")

TOTAL_BALANCE = 1_000.0
DEFAULT_TARGET = 15.0

#: Each sleeve at the size and shape its own solo 15% calibration selected.
#: Maroy uses the flat+throttle variant throughout; Drift has only a stop risk.
BASE = {
    ("ethusd", "maroy"): {"sizing": "leverage_cap", "leverage": 0.95,
                          "dd_threshold": 0.05, "dd_throttle": 0.25},
    ("ethusd", "drift"): {"risk_fraction": 0.00375},
    ("de40", "maroy"): {"sizing": "leverage_cap", "leverage": 2.55,
                        "dd_threshold": 0.05, "dd_throttle": 0.25},
    ("xptusd", "maroy"): {"sizing": "leverage_cap", "leverage": 4.00,
                          "dd_threshold": 0.05, "dd_throttle": 0.25},
    ("xagusd", "maroy"): {"sizing": "leverage_cap", "leverage": 1.15,
                          "dd_threshold": 0.05, "dd_throttle": 0.25},
}

SLEEVES = list(BASE)

SCALE_STEPS = [round(0.05 * step, 3) for step in range(1, 81)]

SESSIONS_PER_YEAR_DAYS = 365.25


def scaled(sleeve, scale):
    policy = dict(BASE[sleeve])
    if "risk_fraction" in policy:
        policy["risk_fraction"] *= scale
    else:
        policy["leverage"] *= scale
    return policy


# --------------------------------------------------------------------------- #
# sleeves
# --------------------------------------------------------------------------- #


def prepare(sleeves, cost_bp):
    """Warm each sleeve's bars and register its session with the base module."""
    for symbol, strategy in sleeves:
        val._prepare(symbol, strategy, cost_bp)
        if strategy == "maroy":
            study._maroy_sessions(symbol)


def sleeve_trades(sleeve, params, window, policy, initial, cost_bp):
    """One sleeve's trades, compounding from `initial` inside its sub-account."""
    symbol, strategy = sleeve
    saved_study, saved_maroy = study.INITIAL_BALANCE, maroy.INITIAL
    study.INITIAL_BALANCE = maroy.INITIAL = initial
    try:
        if strategy == "maroy":
            maroy.SESSIONS[symbol] = study.SYMBOLS[symbol]["session"]
            maroy.SESSIONS_PER_YEAR = study.SYMBOLS[symbol]["sessions_per_year"]
            maroy.COST_BPS = cost_bp
            result = maroy.run_config(study._maroy_sessions(symbol), params,
                                      symbol, policy, window)
            return [{**t, "entry_ts": t["ts"]} for t in result["trades"]]
        original = study.DRIFT_RISK_FRACTION
        study.DRIFT_RISK_FRACTION = policy["risk_fraction"]
        try:
            minutes = val._minutes(symbol)
            session = study.SYMBOLS[symbol]["session"]
            states = study.drift_states(study.aggregate(minutes, 15), session,
                                        params["momentum"])
            return study.drift_backtest(study.aggregate(minutes, 5, session),
                                        states, session, params, window,
                                        cost_bp)["trades"]
        finally:
            study.DRIFT_RISK_FRACTION = original
    finally:
        study.INITIAL_BALANCE, maroy.INITIAL = saved_study, saved_maroy


def sleeve_days(sleeve, window):
    """The sleeve's trading days, shifted back to the real-time calendar.

    Without the subtraction an hour-shifted symbol would land on a different day
    index from an unshifted one and the book would double its calendar.
    """
    symbol, strategy = sleeve
    shift_days = study.shift_seconds(symbol) // 86_400
    return [day - shift_days for day in val._days(symbol, strategy, window)]


def daily_equity(trades, days, initial, shift_days):
    """Sub-account equity on every book day, carried across days it does not trade."""
    by_day = {}
    for trade in trades:
        day = trade["exit_ts"] // 86_400 - shift_days
        by_day[day] = by_day.get(day, 0.0) + trade["pnl"]
    equity = initial
    out = {}
    for day in days:
        equity += by_day.get(day, 0.0)
        out[day] = equity
    return out


def _drawdown(series):
    peak, worst = series[0], 0.0
    for value in series:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak > 0 else 0.0)
    return worst


def book(sleeves, params, window, days, scale, cost_bp):
    """The book's combined curve and per-sleeve detail at `scale`."""
    if len(days) < 2:
        return None
    weight = 1.0 / len(sleeves)
    curves, counts = {}, {}
    for sleeve in sleeves:
        initial = TOTAL_BALANCE * weight
        trades = sleeve_trades(sleeve, params[sleeve], window,
                               scaled(sleeve, scale), initial, cost_bp)
        counts[sleeve] = len(trades)
        curves[sleeve] = daily_equity(trades, days, initial,
                                      study.shift_seconds(sleeve[0]) // 86_400)

    combined = [sum(curves[s][day] for s in sleeves) for day in days]
    returns = [b / a - 1.0 if a > 0 else 0.0 for a, b in zip(combined, combined[1:])]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    mean = statistics.fmean(returns) if returns else 0.0
    # Annualise on the book's own calendar rather than a borrowed convention.
    span_years = max((days[-1] - days[0]) / SESSIONS_PER_YEAR_DAYS, 1e-9)
    per_year = len(days) / span_years
    sharpe = mean / volatility * math.sqrt(per_year) if volatility else 0.0

    detail = {}
    for sleeve in sleeves:
        series = [curves[sleeve][day] for day in days]
        detail[f"{sleeve[0]}/{sleeve[1]}"] = {
            "return_pct": round(100 * (series[-1] / (TOTAL_BALANCE * weight) - 1.0), 2),
            "max_dd_pct": round(100 * _drawdown(series), 2),
            "trades": counts[sleeve],
            "pnl": round(series[-1] - TOTAL_BALANCE * weight, 2),
        }

    return {
        "scale": scale,
        "return_pct": round(100 * (combined[-1] / TOTAL_BALANCE - 1.0), 2),
        "max_dd_pct": round(100 * _drawdown(combined), 2),
        "sharpe": round(sharpe, 3),
        "ann_vol_pct": round(100 * volatility * math.sqrt(per_year), 1),
        "book_days": len(days),
        "sleeves": detail,
        # How much drawdown the sleeves spend separately versus what the book
        # actually pays: the whole point of holding more than one.
        "dd_sum_of_sleeves": round(sum(d["max_dd_pct"] for d in detail.values()), 2),
    }


def calibrate(sleeves, params, window, days, cost_bp, target):
    """Largest joint scale whose in-sample drawdown stays at or under `target`."""
    best, previous, stalled, reason = None, None, 0, "exhausted"
    for scale in SCALE_STEPS:
        result = book(sleeves, params, window, days, scale, cost_bp)
        if result is None:
            continue
        if result["max_dd_pct"] > target:
            reason = "breach"
            break
        if previous is not None and abs(result["max_dd_pct"] - previous) < 0.05:
            stalled += 1
            if stalled >= 10:
                reason = "stalled"
                break
        else:
            stalled = 0
        previous = result["max_dd_pct"]
        best = result
    return best, reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", default=os.path.join(
        os.path.dirname(__file__), "..", "results",
        "drift_maroy_symbol_study_0.5bp.json"))
    parser.add_argument("--cost-bp", type=float, default=0.5)
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--sleeves", default=None,
                        help="comma-separated symbol:strategy; default is all five")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    sleeves = SLEEVES
    if args.sleeves:
        sleeves = [tuple(part.split(":")) for part in args.sleeves.split(",")]
        unknown = [s for s in sleeves if s not in BASE]
        if unknown:
            raise SystemExit(f"no calibrated base policy for {unknown}")

    winners = val.load_winners(args.sweep)
    params = {sleeve: winners[sleeve] for sleeve in sleeves}
    prepare(sleeves, args.cost_bp)

    windows = budget._windows(sleeves[0][0])
    days = {}
    for name in ("prior", "is", "oos"):
        union = set()
        for sleeve in sleeves:
            union |= set(sleeve_days(sleeve, budget._windows(sleeve[0])[name]))
        days[name] = sorted(union)

    selected, reason = calibrate(sleeves, params, windows["is"], days["is"],
                                 args.cost_bp, args.target)
    if selected is None:
        raise SystemExit("no joint scale met the target in-sample")
    scale = selected["scale"]

    rows = {"is": selected}
    for name in ("oos", "prior"):
        rows[name] = book(sleeves, params, windows[name], days[name], scale,
                          args.cost_bp)

    print(f"\n{'=' * 104}")
    print(f"{len(sleeves)}-SLEEVE BOOK, sized to {args.target:g}% max drawdown on "
          f"2020-2024 only     cost {args.cost_bp} bp     ${TOTAL_BALANCE:,.0f}")
    print(f"{'=' * 104}")
    print(f"  joint scale {scale:g}   equal capital "
          f"(${TOTAL_BALANCE / len(sleeves):,.0f} each)   scan stopped: {reason}")
    for sleeve in sleeves:
        policy = scaled(sleeve, scale)
        size = (f"{policy['risk_fraction']:.4%}/stop" if "risk_fraction" in policy
                else f"{policy['leverage']:.3f}x")
        print(f"    {sleeve[0] + '/' + sleeve[1]:16}{size:>16}")

    print(f"\n  {'window':8}{'ret%':>10}{'DD%':>8}{'Shp':>7}{'vol%':>8}"
          f"{'days':>7}{'sum of sleeve DDs':>20}")
    print("  " + "-" * 100)
    for name in ("prior", "is", "oos"):
        row = rows[name]
        print(f"  {name:8}{row['return_pct']:>10.1f}{row['max_dd_pct']:>8.1f}"
              f"{row['sharpe']:>7.2f}{row['ann_vol_pct']:>8.1f}"
              f"{row['book_days']:>7}{row['dd_sum_of_sleeves']:>20.1f}")

    for name in ("is", "oos"):
        print(f"\n  {name.upper()} per sleeve ($"
              f"{TOTAL_BALANCE / len(sleeves):,.0f} each):")
        print(f"    {'sleeve':18}{'ret%':>9}{'DD%':>8}{'PnL $':>10}{'trades':>9}")
        for label, detail in rows[name]["sleeves"].items():
            print(f"    {label:18}{detail['return_pct']:>9.1f}"
                  f"{detail['max_dd_pct']:>8.1f}{detail['pnl']:>10.0f}"
                  f"{detail['trades']:>9}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump({"target_pct": args.target, "cost_bp": args.cost_bp,
                   "joint_scale": scale, "calibration_stop": reason,
                   "calibrated_on": [study.IS_FROM, study.IS_TO],
                   "sleeves": [f"{s}:{t}" for s, t in sleeves],
                   "base_policies": {f"{s}:{t}": p for (s, t), p in BASE.items()},
                   "windows": rows}, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
