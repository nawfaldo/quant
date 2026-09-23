"""Leakage-controlled S1 prior-session value-area reaction research.

The pre-declared 30-minute non-overlapping effect is a kill test. Only if its
signed fade return has t >= 2 may a strategy grid be created. Day/time/side,
cost, and sizing tables are fixed diagnostics and cannot reopen a failed gate.
ML requires at least 2,000 primary observations; otherwise it is refused.

Run from the repository root:

    py -3.12 -B -m sandbox.research.s1_volume_profile_research \
        --out sandbox/results/s1_volume_profile_result.json --record-trials
"""

import argparse
from datetime import datetime, timezone
import glob
import json
import os
from pathlib import Path
import random
import subprocess

from sandbox import data
from sandbox import metrics
from sandbox import trials
from sandbox import walkforward
from sandbox.paths import PROJECT_ROOT
from sandbox.strategies.volume_profile_reaction import (
    PRIMARY_HORIZON,
    REFERENCE_SPREAD,
    Observation,
    first_touches,
    fixed_horizon_observations,
    prior_profiles_for_bars,
    profiles_from_rows,
)

NAME = "S1 Volume Profile Reaction"
INITIAL = 1_000.0
ML_MINIMUM = 2_000
HORIZONS = (15, PRIMARY_HORIZON, 60)
COSTS = (0.0, 0.2, 0.25, 0.4, 0.5)


def git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def latest_cache(pattern):
    matches = glob.glob(str(Path(data.CACHE_DIR) / pattern))
    if not matches:
        raise SystemExit(
            f"missing {pattern!r} under {data.CACHE_DIR}; populate the "
            "fingerprinted optimizer caches first"
        )
    return Path(max(matches, key=os.path.getmtime))


def load_inputs():
    """Read existing fingerprinted caches without rescanning raw depth."""
    bars_path = latest_cache("nq_l2_bars.*.json")
    profile_path = latest_cache("nq_profile.*.json")
    with bars_path.open() as handle:
        bars = json.load(handle)
    with profile_path.open() as handle:
        profiles = profiles_from_rows(json.load(handle))
    return bars, profiles, bars_path, profile_path


def bootstrap(values, draws=10_000, seed=20260731):
    if len(values) < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        means.append(
            sum(values[rng.randrange(len(values))] for _ in values)
            / len(values)
        )
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def unit_stats(rows, lo, hi, multiplier=None):
    trades = []
    points = []
    for row in rows:
        weight = multiplier(row) if multiplier else 1.0
        value = row.fill.points * weight
        trades.append((row.fill.entry_ts, value))
        points.append(value)
    stat = metrics.stats(trades, initial=INITIAL, span=(lo, hi))
    wins = sum(value for value in points if value > 0.0)
    losses = -sum(value for value in points if value < 0.0)
    ci_lo, ci_hi = bootstrap(points)
    stat.update(
        {
            "points": round(sum(points), 4),
            "edge": round(sum(points) / len(points), 4) if points else 0.0,
            "edge_t": round(walkforward.edge_t(points), 4),
            "edge_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
            "pf_points": (
                round(wins / losses, 4)
                if losses
                else (999.0 if wins else 0.0)
            ),
        }
    )
    return stat


def filter_rows(rows, name):
    weekdays = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}
    if name == "baseline":
        return rows
    if name == "long_only":
        return [row for row in rows if row.fill.side == "long"]
    if name == "short_only":
        return [row for row in rows if row.fill.side == "short"]
    if name.startswith("skip_") and name[5:] in weekdays:
        weekday = weekdays[name[5:]]
        return [
            row
            for row in rows
            if (row.fill.entry_ts // 86_400 + 3) % 7 != weekday
        ]

    windows = {
        "open_only": lambda minute: minute < 660,
        "mid_only": lambda minute: 660 <= minute < 840,
        "late_only": lambda minute: minute >= 840,
        "skip_open": lambda minute: minute >= 660,
        "skip_mid": lambda minute: minute < 660 or minute >= 840,
        "skip_late": lambda minute: minute < 840,
    }
    return [
        row
        for row in rows
        if windows[name]((row.fill.entry_ts % 86_400) // 60)
    ]


FILTERS = (
    "baseline",
    "long_only",
    "short_only",
    "skip_mon",
    "skip_tue",
    "skip_wed",
    "skip_thu",
    "skip_fri",
    "open_only",
    "mid_only",
    "late_only",
    "skip_open",
    "skip_mid",
    "skip_late",
)


def fold_deltas(candidate, baseline):
    rows = []
    improved = 0
    for _train_lo, _train_hi, lo, hi in walkforward.fold_windows():
        base = sum(
            row.fill.points
            for row in baseline
            if lo <= row.fill.entry_ts < hi
        )
        filtered = sum(
            row.fill.points
            for row in candidate
            if lo <= row.fill.entry_ts < hi
        )
        improved += filtered > base
        rows.append(
            {
                "month": metrics.month_key(lo),
                "baseline_points": round(base, 4),
                "filter_points": round(filtered, 4),
                "delta_points": round(filtered - base, 4),
            }
        )
    return improved, rows


def cost_rows(rows, spread):
    adjustment = REFERENCE_SPREAD - spread
    out = []
    for row in rows:
        fill = row.fill
        out.append(
            Observation(
                row.touch,
                type(fill)(
                    fill.entry_ts,
                    fill.exit_ts,
                    fill.side,
                    fill.points + adjustment,
                    fill.price,
                    fill.stop,
                ),
            )
        )
    return out


def inverse_atr_multiplier(atr, target):
    def weight(row):
        value = atr.get(row.fill.entry_ts // 86_400)
        if value is None or value <= 0.0:
            return 1.0
        return max(0.5, min(1.5, target / value))

    return weight


def iso_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def run(out_path=None, record_trials=False):
    bars, profiles, bars_path, profile_path = load_inputs()
    prior = prior_profiles_for_bars(bars, profiles)
    touches = first_touches(bars, prior)
    study_days = sorted(prior)
    lo = study_days[0] * 86_400
    hi = (study_days[-1] + 1) * 86_400

    effects = {}
    observations = {}
    for horizon in HORIZONS:
        rows = fixed_horizon_observations(
            bars, touches, horizon, REFERENCE_SPREAD
        )
        observations[horizon] = rows
        effects[str(horizon)] = unit_stats(rows, lo, hi)

    primary = observations[PRIMARY_HORIZON]
    primary_stat = effects[str(PRIMARY_HORIZON)]
    kill_passed = (
        primary_stat["edge_t"] >= 2.0 and primary_stat["edge"] > 0.0
    )

    filters = {}
    for name in FILTERS:
        selected = filter_rows(primary, name)
        improved, deltas = fold_deltas(selected, primary)
        filters[name] = {
            **unit_stats(selected, lo, hi),
            "improved_oos_months": improved,
            "oos_month_deltas": deltas,
        }

    atr = data.atr_by_day(bars, 20)
    training_cut = metrics.split_ts(walkforward.FOLDS[0][0])
    train_atr = sorted(
        value for day, value in atr.items() if day * 86_400 < training_cut
    )
    target_atr = train_atr[len(train_atr) // 2]
    sizing = {
        "constant_0.50x": unit_stats(
            primary, lo, hi, lambda _row: 0.5
        ),
        "constant_1.00x": unit_stats(primary, lo, hi),
        "constant_1.50x": unit_stats(
            primary, lo, hi, lambda _row: 1.5
        ),
        "causal_inverse_atr_0.50_to_1.50x": {
            **unit_stats(
                primary,
                lo,
                hi,
                inverse_atr_multiplier(atr, target_atr),
            ),
            "target_atr": round(target_atr, 4),
        },
    }

    costs = {
        str(spread): unit_stats(cost_rows(primary, spread), lo, hi)
        for spread in COSTS
    }

    if record_trials:
        trials.record(
            NAME,
            1,
            "pre-declared non-overlapping 30-minute first-touch kill test",
        )
        trials.record(
            NAME,
            len(HORIZONS) - 1,
            "fixed 15/60-minute horizon diagnostics; not selected",
        )
        trials.record(
            NAME,
            len(FILTERS) - 1,
            "fixed day/time/side diagnostics; not selected",
        )
        trials.record(
            NAME,
            len(sizing),
            "fixed exposure diagnostics; not selected",
        )
        trials.record(
            NAME,
            len(COSTS) - 1,
            "fixed cost sensitivity excluding reference cost",
        )

    ml_run = len(primary) >= ML_MINIMUM and kill_passed
    result = {
        "strategy": NAME,
        "status": "eligible_for_optimization" if kill_passed else "rejected",
        "git_sha": git_sha(),
        "data": {
            "bars": len(bars),
            "profiles": len(profiles),
            "touch_candidates": len(touches),
            "study_range": [iso_day(lo), iso_day(hi - 1)],
            "bar_cache": bars_path.name,
            "profile_cache": profile_path.name,
            "timestamp_convention": "New York wall clock encoded as UTC",
        },
        "predeclared_definition": {
            "profile_session": "09:30-15:59 New York",
            "price_bucket_points": 1.0,
            "value_area_fraction": 0.70,
            "vah": "first touch from below, short next minute open",
            "val": "first touch from above, long next minute open",
            "primary_horizon_minutes": PRIMARY_HORIZON,
            "spread_points": REFERENCE_SPREAD,
            "sampling": "globally non-overlapping forward windows",
            "kill_criterion": "primary signed fade edge t >= 2 and mean > 0",
        },
        "kill_test": {
            "passed": kill_passed,
            "reason": (
                "primary effect cleared; a coarse grid may be designed"
                if kill_passed
                else "primary effect is negative and |t| < 2; stop before grid"
            ),
            "horizons": effects,
        },
        "optimization": {
            "grid_run": False,
            "reason": (
                "The pre-declared kill test failed. Searching thresholds, "
                "brackets, or profile variants would fit noise."
            ),
        },
        "diagnostic_filters": filters,
        "sizing": sizing,
        "cost_sensitivity": costs,
        "ml": {
            "run": ml_run,
            "minimum_observations": ML_MINIMUM,
            "observations": len(primary),
            "reason": (
                "eligible"
                if ml_run
                else "refused: the effect failed and the event set is below "
                "the 2,000-observation minimum"
            ),
        },
        "notes": [
            "Filter, sizing, alternate-horizon, and cost tables are descriptive.",
            "No diagnostic is adopted and none can reverse the kill decision.",
            "Constant sizing variants only scale exposure; they cannot create edge.",
            "The profile is never carried across a session missing its own profile.",
            "The final month is partial because the historical profile feed ends.",
        ],
        "cumulative_trials": trials.total(NAME),
    }

    print("\nS1 non-overlapping first-touch effect")
    for horizon in HORIZONS:
        stat = effects[str(horizon)]
        print(
            f"  {horizon:>2}m n={stat['trades']:>4} "
            f"edge={stat['edge']:>8.3f} t={stat['edge_t']:>6.2f} "
            f"points={stat['points']:>9.1f}"
        )
    print(
        "\nS1 kill gate: "
        + ("PASSED" if kill_passed else "FAILED; grid and ML refused")
    )
    print("\nS1 primary monthly net points")
    for month, value in primary_stat["months"].items():
        print(f"  {month}: {value:>9.2f}")
    print("\nS1 fixed filter diagnostics (not selected)")
    for name, stat in filters.items():
        print(
            f"  {name:<13} n={stat['trades']:>4} "
            f"edge={stat['edge']:>8.3f} t={stat['edge_t']:>6.2f} "
            f"improved={stat['improved_oos_months']}/12"
        )
    print("\nS1 sizing diagnostics")
    for name, stat in sizing.items():
        print(
            f"  {name:<36} pnl={stat['pnl']:>9.2f} "
            f"dd={stat['max_dd']:>9.2f} pf={stat['pf']:>6.3f}"
        )
    print(
        f"\nS1 ML: {'run' if ml_run else 'skipped'} "
        f"({result['ml']['reason']})"
    )

    if out_path:
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    run(args.out, args.record_trials)


if __name__ == "__main__":
    main()
