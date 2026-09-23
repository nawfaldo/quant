"""Size the surviving cells to a ~15% max drawdown, calibrated in-sample only.

The signal cells are already fixed: they were selected on 2020-2024 by
`drift_maroy_symbol_study` and are not touched here.  The only free variable is
how much is bet, and it is set so that the *in-sample* drawdown lands just under
the target.  The out-of-sample drawdown is then whatever it turns out to be --
which is the number worth reading, because nothing was fitted to it.

WHY NOT JUST SOLVE FOR 15% ON THE HOLDOUT.  Because that would guarantee the
answer.  A drawdown produced by tuning against the window it is reported on is
evidence about the tuning, not about the strategy
([[selection-gate-manufactures-drawdown-and-consistency]]).  The honest claim is
"sized to 15% on 2020-2024, delivered X% on 2025-2026", and X is allowed to miss.

THREE WAYS TO SPEND THE BUDGET, because they are not equivalent:

  flat        one leverage, always.  Drawdown scales with it and Sharpe does not
              move at all, so this is the pure control: it buys the target by
              giving up return proportionally and nothing else.
  vol_target  leverage set so the position's annualised volatility hits a
              target, using the trailing session-return volatility of the symbol
              itself.  This is exogenous price volatility, which throttles a
              book far better than any equity-curve signal
              ([[price-vol-beats-pnl-vol-for-exposure]]).
  throttle    flat leverage, cut to a fraction while the equity curve is more
              than `dd_threshold` underwater.  Reads only closed PnL, so it is a
              rule an account could actually follow -- but it reacts after the
              damage, which is exactly the weakness the memory above describes.

CALIBRATION scans the size axis upward and stops at the *first* setting that
breaches the target, rather than taking the largest feasible setting anywhere on
the grid, so a non-monotone patch in the drawdown curve cannot select a cell on
the far side of a breach.  A size increase that moves the drawdown less than
0.05 points repeatedly means some other limit binds -- the margin ceiling or the
0.01 lot floor -- and the scan stops and says so rather than scaling into a wall
([[thousand-dollar-account-is-margin-capped]], [[lot-granularity-fakes-low-drawdown]]).

Run from the repository root:

    py -B -m sandbox.research.drift_maroy_risk_budget
    py -B -m sandbox.research.drift_maroy_risk_budget --target 10
"""
from __future__ import annotations

import argparse
import json
import os

from sandbox import metrics
from sandbox.research import drift_maroy_symbol_study as study
from sandbox.research import drift_maroy_validate as val
from sandbox.research import maroy_intraday_momentum as maroy


OUTPUT = os.path.join(os.path.dirname(__file__), "..", "results",
                      "drift_maroy_risk_budget_result.json")

DEFAULT_TARGET = 15.0

#: The pairs that beat their own null at all three cost settings.  ETHUSD/Maroy
#: is the only one that also beat buy-and-hold and earned in both holdout years;
#: the rest are carried so the risk budget can be read across the whole survivor
#: set rather than one cherry.
PAIRS = [("ethusd", "maroy"), ("ethusd", "drift"),
         ("de40", "maroy"), ("xptusd", "maroy"), ("xagusd", "maroy")]

#: Multiplicative steps on the size axis.  Fine enough that the calibrated cell
#: sits close under the target instead of wherever a coarse grid happened to land.
SCALE_STEPS = [round(0.05 * step, 3) for step in range(1, 81)]

VOL_TARGETS = [round(0.02 * step, 3) for step in range(1, 61)]

THROTTLES = [(0.05, 0.25), (0.05, 0.5), (0.10, 0.25), (0.10, 0.5)]


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #


def _windows(symbol):
    shift = study.shift_seconds(symbol)
    return {
        "is": (metrics.split_ts(study.IS_FROM) + shift,
               metrics.split_ts(study.IS_TO) + shift),
        "oos": (metrics.split_ts(study.OOS_FROM) + shift,
                metrics.split_ts(study.OOS_TO) + shift),
        "prior": (metrics.split_ts(val.PRIOR_FROM) + shift,
                  metrics.split_ts(val.PRIOR_TO) + shift),
    }


def evaluate(symbol, strategy, params, window, days, policy, cost_bp):
    """One run at `policy`.  Maroy reads the policy; Drift reads a risk fraction."""
    per_year = study.SYMBOLS[symbol]["sessions_per_year"]
    if strategy == "maroy":
        maroy.COST_BPS = cost_bp
        result = maroy.run_config(study._maroy_sessions(symbol), params, symbol,
                                  {**policy, **({"null_seed": policy["null_seed"]}
                                                if "null_seed" in policy else {})},
                                  window)
        trades = [{**t, "entry_ts": t["ts"], "return_bp": study._maroy_return_bp(t)}
                  for t in result["trades"]]
    else:
        original = study.DRIFT_RISK_FRACTION
        study.DRIFT_RISK_FRACTION = policy["risk_fraction"]
        try:
            minutes = val._minutes(symbol)
            session = study.SYMBOLS[symbol]["session"]
            bars_5m = study.aggregate(minutes, 5, session)
            states = study.drift_states(study.aggregate(minutes, 15), session,
                                        params["momentum"])
            trades = study.drift_backtest(bars_5m, states, session, params, window,
                                          cost_bp, policy.get("null_seed"))["trades"]
        finally:
            study.DRIFT_RISK_FRACTION = original
    return study.summarise(trades, window, per_year, days)


def policies_for(strategy, mode, scale):
    """The sizing policy at size-axis position `scale`, for one mode."""
    if strategy == "drift":
        # Drift sizes off its stop, so its size axis is the risk fraction.
        return {"risk_fraction": 0.005 * scale}
    if mode == "flat":
        return {"sizing": "leverage_cap", "leverage": scale}
    if mode == "vol_target":
        return {"sizing": "vol_target", "vol_target_annual": scale, "leverage": 4.0}
    raise ValueError(mode)


def calibrate(symbol, strategy, params, window, days, mode, cost_bp, target,
              throttle=None):
    """Largest size whose in-sample drawdown stays at or under `target`.

    Returns the policy, its summary, and why the scan stopped -- `breach` when
    the next step exceeded the target, `stalled` when size stopped moving the
    drawdown, `exhausted` when the grid ran out below the target.
    """
    axis = VOL_TARGETS if (mode == "vol_target" and strategy == "maroy") else SCALE_STEPS
    best_policy = best_summary = None
    previous = None
    stalled = 0
    reason = "exhausted"
    for scale in axis:
        policy = policies_for(strategy, mode, scale)
        if throttle is not None:
            policy = {**policy, "dd_threshold": throttle[0], "dd_throttle": throttle[1]}
        summary = evaluate(symbol, strategy, params, window, days, policy, cost_bp)
        if summary["trades"] == 0:
            continue
        if summary["max_dd_pct"] > target:
            reason = "breach"
            break
        if previous is not None and abs(summary["max_dd_pct"] - previous) < 0.05:
            stalled += 1
            if stalled >= 10:
                reason = "stalled"
                break
        else:
            stalled = 0
        previous = summary["max_dd_pct"]
        best_policy, best_summary = policy, summary
    return best_policy, best_summary, reason


def study_pair(symbol, strategy, winners, cost_bp, target):
    params = winners[(symbol, strategy)]
    val._prepare(symbol, strategy, cost_bp)
    if strategy == "maroy":
        study._maroy_sessions(symbol)
    windows = _windows(symbol)
    days = {name: val._days(symbol, strategy, w) for name, w in windows.items()}

    # Only Maroy's `run_config` reads a sizing policy.  Drift sizes off its own
    # stop through `DRIFT_RISK_FRACTION` and has no throttle or volatility mode,
    # so offering it those rows would print four copies of `flat` under labels
    # that suggest otherwise.
    modes = [("flat", None)]
    if strategy == "maroy":
        modes += [("vol_target", None)] + [("flat", t) for t in THROTTLES]

    rows = []
    for mode, throttle in modes:
        policy, is_summary, reason = calibrate(
            symbol, strategy, params, windows["is"], days["is"], mode, cost_bp,
            target, throttle)
        if policy is None:
            rows.append({"mode": mode, "throttle": throttle,
                         "note": f"no size met the target ({reason})"})
            continue
        rows.append({
            "mode": mode, "throttle": throttle, "policy": policy,
            "calibration_stop": reason,
            "is": is_summary,
            "oos": evaluate(symbol, strategy, params, windows["oos"], days["oos"],
                            policy, cost_bp),
            "prior": evaluate(symbol, strategy, params, windows["prior"],
                              days["prior"], policy, cost_bp),
            "oos_null": evaluate(symbol, strategy, params, windows["oos"],
                                 days["oos"],
                                 {**policy, "null_seed": study.NULL_SEED}, cost_bp),
        })
    return {"symbol": symbol, "strategy": strategy, "params": params, "rows": rows}


def _label(row):
    mode = row["mode"]
    if row.get("throttle"):
        mode = f"{mode}+dd{row['throttle'][0]:g}/{row['throttle'][1]:g}"
    policy = row.get("policy") or {}
    if "risk_fraction" in policy:
        size = f"{policy['risk_fraction']:.3%}/stop"
    elif policy.get("sizing") == "vol_target":
        size = f"volT {policy['vol_target_annual']:.0%}"
    else:
        size = f"{policy.get('leverage', 0):.2f}x"
    return f"{mode:22}{size:>13}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", default=os.path.join(
        os.path.dirname(__file__), "..", "results",
        "drift_maroy_symbol_study_0.5bp.json"))
    parser.add_argument("--cost-bp", type=float, default=0.5)
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args()

    winners = val.load_winners(args.sweep)
    results = [study_pair(symbol, strategy, winners, args.cost_bp, args.target)
               for symbol, strategy in PAIRS if (symbol, strategy) in winners]

    print(f"\n{'=' * 126}")
    print(f"SIZED TO {args.target:g}% MAX DRAWDOWN, CALIBRATED ON 2020-2024 ONLY"
          f"     cost {args.cost_bp} bp     $1,000")
    print(f"{'=' * 126}")
    for result in results:
        print(f"\n  {result['symbol'].upper()} / {result['strategy']}")
        print(f"  {'policy':22}{'size':>13}{'IS ret%':>10}{'IS DD%':>8}"
              f"{'OOS ret%':>10}{'OOS DD%':>9}{'OOS Shp':>9}"
              f"{'null ret%':>11}{'prior ret%':>12}{'prior DD%':>11}")
        print("  " + "-" * 122)
        for row in result["rows"]:
            if "policy" not in row:
                print(f"  {row['mode']:22}{row['note']}")
                continue
            print(f"  {_label(row)}"
                  f"{row['is']['return_pct']:>10.1f}{row['is']['max_dd_pct']:>8.1f}"
                  f"{row['oos']['return_pct']:>10.1f}{row['oos']['max_dd_pct']:>9.1f}"
                  f"{row['oos']['sharpe']:>9.2f}"
                  f"{row['oos_null']['return_pct']:>11.1f}"
                  f"{row['prior']['return_pct']:>12.1f}"
                  f"{row['prior']['max_dd_pct']:>11.1f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump({"target_pct": args.target, "cost_bp": args.cost_bp,
                   "calibrated_on": [study.IS_FROM, study.IS_TO],
                   "pairs": results}, handle, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
