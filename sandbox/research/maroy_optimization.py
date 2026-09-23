"""Cut the Maroy strategies' drawdown to 15% without giving up the return.

The transfer test in `maroy_intraday_momentum` left every exit family profitable
on BTC but unusable: drawdowns ran 67-96%. The diagnosis is not the exits. The
paper sizes every entry at the full 4x available margin whenever realised
volatility sits under the target, so the account carries maximum leverage into
every regime, and the drawdown is a property of the sizing rule rather than of
the signal. This searches for a sizing and filtering policy that holds max
drawdown at or under 15% while keeping as much CAGR as possible.

PROTOCOL.
  in-sample      2020-01-01 .. 2024-12-31   (selection sees only this)
  out-of-sample  2025-01-01 .. 2026-08-03   (scored once, per family)

Equity restarts at $1,000 on the first session of each window, so the two
numbers are independent; sessions before a window still warm the noise profile
and the volatility lookback, so neither window is handicapped by a cold start.

SELECTION. Maximise in-sample CAGR subject to in-sample MDD <= 15%. If no cell
clears the constraint, the least-bad MDD is taken and reported as a failure to
meet the target rather than quietly relabelled a success. The chosen cell's
neighbours along the leverage axis are printed alongside it: a cell that only
works at one setting is a fit to noise, and the reader should be able to see
that without rerunning anything.

WHAT IS SEARCHED, in stages, each stage keeping the previous stage's winner:
  1. sizing        leverage cap / volatility target / fixed-fractional risk
  2. equity curve  cut size to a fraction while underwater past a threshold
  3. hours         contiguous entry window inside the session
  4. weekday       drop at most one day
  5. regime        BTC realised-volatility, trend and VIX gates

WHAT IS NOT SEARCHED. Every parameter of the paper itself -- lookbacks,
multipliers, entry cadence, ladder steps -- is frozen at its published value.
This search only touches how much is bet and when betting is allowed, which is
the part the paper never optimised for drawdown.

HONEST LIMITS, and they are not small:
  * Staged greedy search is not a joint optimisation. Each stage is conditioned
    on the previous winner, so the result is a good policy, not the best one.
  * Five stages over four families is a real multiple-comparisons burden. The
    trial count is reported; treat a marginal OOS pass as noise.
  * BTC 2025-2026 has been read repeatedly by other studies in this directory.
    It is untouched *by this strategy*, but it is not a pristine holdout for BTC
    in general.
  * The OOS window is 19 months. A 15% MDD target verified over 19 months is a
    weaker claim than the same target over the five-year fit.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
from datetime import datetime, timezone

from sandbox.research import maroy_intraday_momentum as base


OUTPUT = os.path.join(os.path.dirname(__file__), "maroy_optimization_result.json")

IS_LO = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
IS_HI = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_HI = int(datetime(2026, 8, 4, tzinfo=timezone.utc).timestamp())

IN_SAMPLE = (IS_LO, IS_HI)
OUT_OF_SAMPLE = (IS_HI, OOS_HI)

#: The drawdown the user asked for.
MDD_TARGET = 15.0

#: The exit families worth the compute: the best per-trade edge (profit factor
#: 1.18-1.24) and the best Sharpe, rather than the biggest headline return.
FAMILIES = [
    ("Boundary with different exit #1", "rth"),
    ("Boundary & Ladder #2", "rth"),
    ("VWAP #1", "full"),
    ("Time only", "full"),
]


def config_for(name):
    return next(c for c in base.CONFIGS if c["name"] == name)


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #


def evaluate(sessions, config, session_name, policy, window):
    result = base.run_config(sessions, config, session_name, policy, window)
    summary = base.summarise(result, config["name"], warmup=0)
    summary["exit_reasons"] = result["exit_reasons"]
    return summary


def score(summary):
    """Rank key: CAGR, but only among cells that meet the drawdown constraint.

    Returning a tuple makes the constraint lexicographic -- every feasible cell
    outranks every infeasible one, and infeasible cells are ordered by how close
    they get -- so a search that finds nothing feasible still returns something
    interpretable instead of the highest-leverage cell that happened to survive.
    """
    if not summary.get("trades"):
        return (-1, -math.inf, -math.inf)
    feasible = summary["max_drawdown_pct"] <= MDD_TARGET
    if feasible:
        return (1, summary["cagr_pct"], -summary["max_drawdown_pct"])
    return (0, -summary["max_drawdown_pct"], summary["cagr_pct"])


# --------------------------------------------------------------------------- #
# candidate policies per stage
# --------------------------------------------------------------------------- #


def sizing_candidates(has_price_stop):
    out = [{"sizing": "paper"}]
    for leverage in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0):
        out.append({"sizing": "leverage_cap", "leverage": leverage})
    for target in (0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.50):
        for cap in (1.0, 2.0, 4.0):
            out.append({"sizing": "vol_target", "vol_target_annual": target,
                        "leverage": cap})
    if has_price_stop:
        for fraction in (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02):
            for cap in (1.0, 2.0, 4.0):
                out.append({"sizing": "risk_per_trade", "risk_fraction": fraction,
                            "leverage": cap})
    return out


def throttle_candidates():
    out = [{}]
    for threshold in (0.05, 0.075, 0.10, 0.15):
        for throttle in (0.25, 0.5, 0.75):
            out.append({"dd_threshold": threshold, "dd_throttle": throttle})
    return out


def hour_candidates(session_name):
    low, high = base.SESSIONS[session_name]
    hours = list(range(low // 60, (high + 59) // 60))
    out = [{}]
    # Contiguous windows only. An arbitrary subset of hours would fit the sample
    # far better and mean far less.
    for width in range(max(3, len(hours) // 3), len(hours)):
        for start in range(0, len(hours) - width + 1):
            out.append({"hours": set(hours[start:start + width])})
    return out


def weekday_candidates():
    out = [{}]
    for dropped in range(7):
        out.append({"weekdays": {d for d in range(7) if d != dropped}})
    return out


def regime_candidates():
    out = [{}]
    for ceiling in (40.0, 50.0, 60.0, 70.0, 80.0, 100.0):
        out.append({"regime": {"rv20": (0.0, ceiling)}})
    for ceiling in (20.0, 25.0, 30.0):
        out.append({"regime": {"vix": (0.0, ceiling)}})
    for percentile in (0.6, 0.8):
        out.append({"regime": {"rv_pct": (0.0, percentile)}})
    return out


STAGES = [
    ("sizing", None),
    ("equity curve", throttle_candidates),
    ("hours", hour_candidates),
    ("weekday", weekday_candidates),
    ("regime", regime_candidates),
]


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def search_family(sessions, name, session_name, regime_features, verbose=True):
    config = config_for(name)
    has_price_stop = ("boundary" in config["exit_kind"] and config["k_exit"] is not None) \
        or "vwap" in config["exit_kind"]

    policy = {"sizing": "paper", "regime_features": regime_features}
    baseline = evaluate(sessions, config, session_name, policy, IN_SAMPLE)
    if verbose:
        print(f"\n  stage 0  paper sizing (control)"
              f"   CAGR {baseline['cagr_pct']:>7.1f}%  MDD {baseline['max_drawdown_pct']:>5.1f}%"
              f"  Sharpe {baseline['sharpe']:>5.2f}  trades {baseline['trades']}")

    best = baseline
    trials = 1
    history = []
    for stage, builder in STAGES:
        if stage == "sizing":
            candidates = sizing_candidates(has_price_stop)
            merge = lambda base_policy, extra: {**base_policy, **extra}  # noqa: E731
        else:
            candidates = builder(session_name) if stage == "hours" else builder()
            merge = lambda base_policy, extra: {**base_policy, **extra}  # noqa: E731

        stage_best, stage_best_policy = best, policy
        for extra in candidates:
            trial_policy = merge(policy, extra)
            summary = evaluate(sessions, config, session_name, trial_policy, IN_SAMPLE)
            trials += 1
            if score(summary) > score(stage_best):
                stage_best, stage_best_policy = summary, trial_policy
        policy, best = stage_best_policy, stage_best
        chosen = {k: v for k, v in policy.items() if k != "regime_features"}
        if verbose:
            print(f"  stage {stage:12} CAGR {best['cagr_pct']:>7.1f}%  "
                  f"MDD {best['max_drawdown_pct']:>5.1f}%  Sharpe {best['sharpe']:>5.2f}  "
                  f"trades {best['trades']:>5}   {_describe(chosen)}")
        history.append({"stage": stage, "is": _slim(best), "policy": _jsonable(chosen)})

    return {"policy": policy, "is": best, "baseline_is": baseline,
            "trials": trials, "stages": history}


#: Multiplicative steps applied to the chosen size when spending the drawdown
#: budget. The sizing grid searched in stage 1 moves in factors of two, so most
#: winners land well under the 15% target; this scans the space between.
CALIBRATION_FACTORS = [round(1.0 + 0.1 * step, 2) for step in range(0, 71)]


def calibrate(sessions, config, session_name, policy, target=MDD_TARGET):
    """Scale the position size up until in-sample MDD is just under `target`.

    The sizing axis and the leverage cap move together -- "risk more" has to
    raise the ceiling too, or the cap silently absorbs the increase and the
    drawdown stops responding. Scanning ascending and stopping at the *first*
    violation, rather than taking the largest feasible factor anywhere on the
    grid, keeps a non-monotone patch in the drawdown curve from selecting a cell
    on the far side of a breach.
    """
    axis = {"leverage_cap": "leverage", "vol_target": "vol_target_annual",
            "risk_per_trade": "risk_fraction"}.get(policy.get("sizing"))
    if axis is None:
        return policy, None

    best_policy, best_summary = policy, None
    previous_mdd = None
    stalled = 0
    for factor in CALIBRATION_FACTORS:
        trial = dict(policy)
        trial[axis] = policy[axis] * factor
        if "leverage" in policy and axis != "leverage":
            trial["leverage"] = min(1.0 / base.MARGIN, policy["leverage"] * factor)
        summary = evaluate(sessions, config, session_name, trial, IN_SAMPLE)
        if summary["max_drawdown_pct"] > target:
            break
        # A size increase that moves nothing means some other limit binds -- the
        # margin ceiling, or the 0.01 lot floor. Report rather than keep scaling.
        if previous_mdd is not None and abs(summary["max_drawdown_pct"] - previous_mdd) < 0.05:
            stalled += 1
            if stalled >= 8:
                break
        else:
            stalled = 0
        previous_mdd = summary["max_drawdown_pct"]
        best_policy, best_summary = trial, summary
    return best_policy, best_summary


def neighbours(sessions, config, session_name, policy):
    """In-sample metrics either side of the chosen size, as a plateau check."""
    axis = {"leverage_cap": "leverage", "vol_target": "vol_target_annual",
            "risk_per_trade": "risk_fraction"}.get(policy.get("sizing"))
    if axis is None or axis not in policy:
        return []
    out = []
    for factor in (0.67, 1.0, 1.5):
        trial = dict(policy)
        trial[axis] = round(policy[axis] * factor, 6)
        summary = evaluate(sessions, config, session_name, trial, IN_SAMPLE)
        out.append({axis: trial[axis], "cagr_pct": summary["cagr_pct"],
                    "max_drawdown_pct": summary["max_drawdown_pct"],
                    "sharpe": summary["sharpe"]})
    return out


def _describe(policy):
    parts = []
    for key, value in sorted(policy.items()):
        if isinstance(value, set):
            value = f"{min(value)}-{max(value)}" if key == "hours" else sorted(value)
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _jsonable(policy):
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in policy.items()}


def _slim(summary):
    return {k: summary[k] for k in
            ("cagr_pct", "total_return_pct", "max_drawdown_pct", "sharpe",
             "trades", "win_rate_pct", "profit_factor")}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=OUTPUT)
    parser.add_argument("--symbol", default="btc")
    parser.add_argument("--all-rth", action="store_true",
                        help="optimise every exit type on the RTH session")
    args = parser.parse_args()

    families = ([(config["name"], "rth") for config in base.CONFIGS]
                if args.all_rth else FAMILIES)

    try:
        from sandbox.research import btc_donchian_regime
        regime_features = btc_donchian_regime.daily_features()
        print(f"regime features for {len(regime_features)} days")
    except Exception as error:  # noqa: BLE001 - VIX table may be absent
        print(f"regime features unavailable ({error}); regime stage will no-op")
        regime_features = {}

    loaded = {}
    report = {"in_sample": ["2020-01-01", "2024-12-31"],
              "out_of_sample": ["2025-01-01", "2026-08-03"],
              "mdd_target": MDD_TARGET, "families": []}
    total_trials = 0

    for name, session_name in families:
        if session_name not in loaded:
            loaded[session_name] = base.load_sessions(session_name, args.symbol)
        sessions = loaded[session_name]
        config = config_for(name)

        print(f"\n{'=' * 100}")
        print(f"{name}   session={session_name}")
        print(f"{'=' * 100}")

        found = search_family(sessions, name, session_name, regime_features)
        total_trials += found["trials"]

        # The holdout is touched exactly twice per family: once for the paper's
        # own sizing as a control, once for the selected policy.
        control_oos = evaluate(sessions, config, session_name,
                               {"sizing": "paper", "regime_features": regime_features},
                               OUT_OF_SAMPLE)
        chosen_oos = evaluate(sessions, config, session_name, found["policy"],
                              OUT_OF_SAMPLE)
        plateau = neighbours(sessions, config, session_name, found["policy"])

        print(f"\n  {'':22}{'CAGR%':>9}{'MDD%':>8}{'Sharpe':>8}{'ret%':>10}{'trades':>8}")
        for label, summary in (("IS  paper sizing", found["baseline_is"]),
                               ("IS  optimised", found["is"]),
                               ("OOS paper sizing", control_oos),
                               ("OOS optimised", chosen_oos)):
            print(f"  {label:22}{summary['cagr_pct']:>9.1f}"
                  f"{summary['max_drawdown_pct']:>8.1f}{summary['sharpe']:>8.2f}"
                  f"{summary['total_return_pct']:>10.1f}{summary['trades']:>8}")
        if plateau:
            axis = next(iter(plateau[0]))
            print(f"  plateau on {axis}: " + "  ".join(
                f"{row[axis]:g}->{row['cagr_pct']:.0f}%/{row['max_drawdown_pct']:.0f}%"
                for row in plateau))

        report["families"].append({
            "strategy": name, "session": session_name,
            "policy": _jsonable({k: v for k, v in found["policy"].items()
                                 if k != "regime_features"}),
            "trials": found["trials"], "stages": found["stages"],
            "is_baseline": _slim(found["baseline_is"]), "is_optimised": _slim(found["is"]),
            "oos_baseline": _slim(control_oos), "oos_optimised": _slim(chosen_oos),
            "plateau": plateau,
            "met_target_is": found["is"]["max_drawdown_pct"] <= MDD_TARGET,
            "met_target_oos": chosen_oos["max_drawdown_pct"] <= MDD_TARGET,
        })

    report["total_trials"] = total_trials
    print(f"\ntotal in-sample trials: {total_trials}")
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
