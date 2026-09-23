"""Do the tuned constants earn anything the untouched ones do not?

Every L2 strategy in `live_trade/src/strategies/idk/` failed its Stage 6 gate and was
compiled anyway, and all three carry a *negative* deflated Sharpe -- the edge each
one found is smaller than what searching that many cells returns on a strategy
with no edge at all.  That verdict cannot be appealed by searching harder:
`trials.py` is cumulative and append-only, so every new cell makes the haircut
worse.  The 18 months are spent as selection evidence.

What is left is the one experiment that *adds* information without spending any:
run each strategy once on constants nobody chose, and compare.  This is the
defaults test.

    defaults ~= compiled   the tuning was decoration.  Ship the defaults; they
                           owe nothing to the search and cost 1 trial.
    defaults <<  compiled   the profit lives in the tuned constants, which is
                           what "overfit" means.  Retire.

The rule that makes it a test rather than another sweep: **every a-priori value
below is declared with its reason in this file, and the script runs each strategy
exactly once.**  Nothing here selects.  There is no grid, no ranking, no "best
of".  A configuration that disappoints is a result, not a starting point -- if
the next reader edits a number in `APRIORI` because the table came back ugly,
this stops being evidence and becomes a 4-cell search with extra steps.

Three rules generated the numbers, applied before any of them was run:

  1. **Round over precise.**  A parameter that needs 5-unit resolution does not
     work (OPTIMIZATION_PLAN.md Stage 3).  `rr` 1.25 -> 2.0, `tilt` 0.16 -> 0.15.
  2. **Symmetric unless there is a stated economic reason to differ.**  The
     50/300 long/short delta split has no rationale in the Rust file or here, and
     an asymmetry that came out of a sweep is a fitted parameter wearing
     structure's clothes.
  3. **Structural filters stay; tuned cuts go.**  "Don't short into an uptrend"
     survives -- it has an a-priori rationale and worked for any N in 30-60.
     "Skip Thursdays" and "no entries before 11:00" do not: both are calendar
     cuts found by reading a table of outcomes.

Phase 0 runs first and is not a judgment call.  The OFI meta-labeler's compiled
coefficients were fit through the last bar in the sample, so every month of its
"out-of-sample" record is in its training set; this script reports that from
`ofi_ml_model.json` rather than asserting it, and the a-priori OFI configuration
simply turns the gate off.

Usage:

    py -B -m sandbox.research.defaults_test
    py -B -m sandbox.research.defaults_test --record-trials
    py -B -m sandbox.research.defaults_test --out sandbox/results/defaults_test.json
"""
import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

from sandbox import data, execution, metrics, strategies, trials, walkforward

#: First test month of the anchored walk-forward.  Everything from here on was
#: never a training window in any fold, so it is the closest thing to an honest
#: read this sample still has -- for the *incumbents*.  For the a-priori
#: configurations the whole sample is honest, because nothing selected them.
OOS_FROM = "2025-08-01"

#: Hard end of the evaluation window.
#:
#: `nq_l2_features_1s` is being written continuously by the live Bookmap capture
#: -- it gains rows every few seconds -- so "the full sample" is a moving target
#: and two runs of this script a day apart do not compare the same thing.  A
#: defaults test whose answer drifts is not a test, so the window is pinned and
#: the pin is a constant rather than "whatever `bar_range` said today".
#:
#: 2026-08-01 is chosen for a reason unrelated to any result: it is the first of
#: the month after the last *complete* month of history, so no partial month
#: enters the monthly consistency measures.  Move it forward only in whole
#: months, and never to make a number look better.
PINNED_TO = "2026-08-01"

#: The account the Rust strategies actually run on.
INITIAL = walkforward.INITIAL

MODEL_PATH = Path(__file__).resolve().parent.parent / "ofi_ml_model.json"


# --------------------------------------------------------------------------
# The pre-declared configurations.  Read the rationale before the results.
# --------------------------------------------------------------------------

APRIORI = {
    "Hourly Delta Reversal": {
        # Symmetric, round.  The compiled 50 long / 300 short split is the only
        # axis the tied search left free, and 300 is its top grid value -- an
        # edge pick on the one axis that was allowed to move.
        "buy_delta": 100,
        "sell_delta": 100,
        # Kept.  The file reports OOS monthly Sharpe between 0.67 and 0.91 for
        # every k in 0.1375..0.25, so 0.2 is a region rather than a point, and
        # it is the round number inside it.
        "k": 0.2,
        # 2:1 is the a-priori bracket.  1.25 is a walk-forward fold median --
        # i.e. a searched value -- and the plateau covers 1.0..2.0 anyway.
        "rr": 2.0,
        # Structural filter, kept: a counter-trend short into an uptrend is a
        # stated economic claim, and any N in 30..60 works.  45 is that range's
        # centre; 35 is where the search happened to land.
        "short_trend_days": 45,
        # Dropped.  A weekday exclusion is a calendar cut with no mechanism.
        "skip_thursday": False,
        # Kept.  Absolute cut with a book-state rationale, plateau 1.0..2.0.
        "max_spread": 1.5,
        "spread_pct": 0,
    },
    "Deep OFI Momentum": {
        # Off.  `ofi_ml_model.json` is a full-sample fit through the last bar in
        # the sample (Phase 0 prints the dates).  Leaving it on would measure the
        # model's memory, not the strategy.
        "ml_gate": False,
        # Reverted to the session open.  The Rust file states the 11:00 cut was
        # chosen after reading the per-hour table, which Stage 5 forbids.
        "entry_from": 570,
        "entry_to": 900,
        # Round and interior.
        "ofi_z": 2.0,
        # 2:1, and unchanged from compiled -- 60/120 already is the round form.
        "stop": 60,
        "target": 120,
        # Kept: 10 and 30 both work, so this is a plateau rather than a pick.
        "time_stop": 20,
        # Kept.  The Rust file records this as stated *before* the number was
        # looked at, which is exactly what Stage 5 asks for.  Quoted pressure
        # nobody lifts is an intention; pressure that trades is a commitment.
        "require_delta_agreement": True,
        "max_spread": 1.0,
    },
}

#: Incumbents are re-run for comparison only.  They are not new draws from the
#: noise -- they are the configuration already compiled -- so they cost 0 trials.
COMPILED = {name: {} for name in APRIORI}

#: The historical fixed-point form, kept registered as the do-nothing control
#: every later Hourly Delta Reversal result is measured against.
CONTROL = "Hourly Delta Reversal (fixed)"


# --------------------------------------------------------------------------
# Phase 0 -- the leak audit
# --------------------------------------------------------------------------

def ml_leak_audit():
    """Does the compiled OFI meta-labeler's training set cover its own OOS?"""
    if not MODEL_PATH.exists():
        return {"error": f"{MODEL_PATH} not found"}
    model = json.loads(MODEL_PATH.read_text())
    bars_from, bars_to = data.bar_range("level_two", "nq")
    trained_through = model.get("trained_through")
    return {
        "model_version": model.get("model_version"),
        "status": model.get("status"),
        "trained_through": trained_through,
        "model_data_range": model.get("data_range"),
        "bars_range": [bars_from, bars_to],
        "oos_from": OOS_FROM,
        # The whole point: a model trained through the end of the sample has
        # seen every month anyone would later call out of sample.
        "oos_inside_training_set": bool(
            trained_through and trained_through[:10] >= OOS_FROM),
    }


# --------------------------------------------------------------------------
# Evaluation.  One configuration, two windows, no selection.
# --------------------------------------------------------------------------

def _fills(strategy, overrides, ex):
    """Resolve one configuration to per-unit fills."""
    configured = strategy.configured(overrides)
    bars = data.load_bars(configured.bars, configured.symbol)
    context = configured.context()
    params = configured.all_params()
    signals = []
    for group in configured.groups():
        signals.extend(configured.signals(bars, context, group, params))
    return execution.resolve(bars, signals, ex), bars


def _window(fills, bars, ex, lo, n_trials):
    """Stats plus the significance panel for the trades entered in `[lo, hi)`."""
    hi = metrics.split_ts(PINNED_TO)
    span_lo = lo if lo is not None else bars[0][data.TS]
    span = (span_lo, min(hi, bars[-1][data.TS] + 60))
    stat, _sized = walkforward.window_stats(fills, ex, lo, hi, ex.initial,
                                            span=span)
    points = walkforward.window_points(fills, lo, hi)
    t = walkforward.edge_t(points)
    ci_lo, ci_hi = walkforward.bootstrap_edge(points) if len(points) > 1 else (0.0, 0.0)
    stat.pop("months", None)
    return {
        **stat,
        "points_per_trade": round(sum(points) / len(points), 3) if points else 0.0,
        "edge_t": round(t, 3),
        # The number the whole exercise turns on.  Positive means the result is
        # bigger than what this much searching hands you for free.
        "deflated_t": round(walkforward.deflated_t(t, n_trials), 3),
        "trials_charged_against": n_trials,
        "boot_ci_lo": round(ci_lo, 3),
        "boot_ci_hi": round(ci_hi, 3),
        "ci_excludes_zero": bool(ci_lo > 0 or ci_hi < 0),
    }


def evaluate(name, label, overrides, n_trials):
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=INITIAL)
    fills, bars = _fills(strategy, overrides, ex)
    oos_lo = metrics.split_ts(OOS_FROM)
    return {
        "strategy": name,
        "label": label,
        "overrides": overrides,
        "full": _window(fills, bars, ex, None, n_trials),
        "oos": _window(fills, bars, ex, oos_lo, n_trials),
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

COLUMNS = (
    ("trades", "trades", "{:>6d}"),
    ("pnl", "pnl", "{:>9.2f}"),
    ("pf", "pf", "{:>5.2f}"),
    ("msharpe", "mSharpe", "{:>7.2f}"),
    ("pos_rate", "pos", "{:>5.2f}"),
    ("max_loss_streak", "strk", "{:>4d}"),
    ("worst_quarter", "worstQ", "{:>8.2f}"),
    ("points_per_trade", "pts/tr", "{:>7.2f}"),
    ("edge_t", "t", "{:>6.2f}"),
    ("deflated_t", "defl_t", "{:>7.2f}"),
)


def _row(label, stat):
    cells = []
    for key, _header, fmt in COLUMNS:
        value = stat.get(key, 0)
        try:
            cells.append(fmt.format(value))
        except (ValueError, TypeError):
            cells.append(f"{value!s:>7}")
    return f"  {label:<34}" + " ".join(cells)


def _header():
    """Column headers padded to each format's own width (`{:>9.2f}` -> 9)."""
    heads = []
    for _key, header, fmt in COLUMNS:
        digits = ""
        for char in fmt.split(">")[1]:
            if not char.isdigit():
                break
            digits += char
        heads.append(f"{header:>{int(digits)}}")
    return f"  {'':<34}" + " ".join(heads)


def report(result):
    audit = result["phase0"]
    print("\nPhase 0 -- ML meta-labeler leak audit")
    print("  " + "-" * 74)
    if "error" in audit:
        print(f"  {audit['error']}")
    else:
        print(f"  model {audit['model_version']}  status={audit['status']}")
        print(f"  trained through   {audit['trained_through']}")
        print(f"  bars available    {audit['bars_range'][0]} .. {audit['bars_range'][1]}")
        print(f"  OOS window starts {audit['oos_from']}")
        verdict = ("LEAK: the compiled coefficients were fit on the whole OOS window"
                   if audit["oos_inside_training_set"] else "clean")
        print(f"  -> {verdict}")

    for window in ("full", "oos"):
        title = ("Full sample" if window == "full"
                 else f"From {OOS_FROM} (walk-forward OOS span)")
        print(f"\nPhase 1 -- defaults test, {title} .. {PINNED_TO}, "
              f"${INITIAL:,.0f}")
        print(_header())
        print("  " + "-" * 108)
        for row in result["rows"]:
            print(_row(row["label"], row[window]))

    print("\nVerdicts")
    print("  " + "-" * 74)
    for line in result["verdicts"]:
        print(f"  {line}")
    print()


def _share(apriori_pnl, compiled_pnl):
    return apriori_pnl / compiled_pnl if compiled_pnl else float("nan")


def verdicts(rows):
    """State the comparison per strategy.  No threshold here was tuned.

    Both windows are read, and the *pair* is what carries the finding.  The
    incumbents were selected on data starting 2025-02, so the full sample is
    partly their own training set while `OOS_FROM` onward is not.  An incumbent
    that beats the a-priori form on the full sample and ties it after `OOS_FROM`
    has an advantage confined to the window it was fit on, which is what
    overfitting looks like when you catch it in the act.

    The a-priori rows carry two haircuts and both are printed, because which one
    is right is a genuine question rather than a settled one.  `deflated_t`
    charges the strategy's whole cumulative trial count -- the conservative
    reading, on the grounds that the strategy *family* was found by searching
    even if these particular constants were not.  `edge_t` is the same number
    with no haircut at all, which is the most generous defensible reading of a
    single pre-declared configuration.  A configuration that fails even the
    generous one is not a close call.
    """
    out = []
    by_strategy = {}
    for row in rows:
        by_strategy.setdefault(row["strategy"], {})[row["label"].split(" -- ")[-1]] = row
    for name, pair in by_strategy.items():
        compiled, apriori = pair.get("compiled"), pair.get("a-priori")
        if not compiled or not apriori:
            continue
        c_full, a_full = compiled["full"], apriori["full"]
        c_oos, a_oos = compiled["oos"], apriori["oos"]
        full_share, oos_share = (_share(a_full["pnl"], c_full["pnl"]),
                                 _share(a_oos["pnl"], c_oos["pnl"]))

        if a_full["pnl"] <= 0 < c_full["pnl"] and a_oos["points_per_trade"] < 0.5:
            call = "OVERFIT: the a-priori form has no per-trade edge at all"
        elif oos_share >= 0.9 and full_share < 0.75:
            call = ("compiled wins ONLY inside its own fit window -- "
                    "the tuning is fitted, the rule is not")
        elif full_share >= 0.75:
            call = "tuning was decoration -- the rule is what earns"
        elif full_share >= 0.4:
            call = "partly structural, partly fitted"
        else:
            call = "OVERFIT: most of the profit is in the tuned constants"

        out.append(f"{name}")
        out.append(f"  full sample   a-priori keeps {full_share:>6.0%} "
                   f"({a_full['pnl']:+8.2f} vs {c_full['pnl']:+8.2f})")
        out.append(f"  from {OOS_FROM}  a-priori keeps {oos_share:>6.0%} "
                   f"({a_oos['pnl']:+8.2f} vs {c_oos['pnl']:+8.2f})")
        out.append(f"  -> {call}")
        # The number that decides whether anything here is tradeable.  Both
        # haircuts, because the generous one is the one worth failing.
        out.append(f"  a-priori significance, {OOS_FROM} on: "
                   f"t {a_oos['edge_t']:+.2f} (no haircut), "
                   f"{a_oos['deflated_t']:+.2f} (vs {a_oos['trials_charged_against']} "
                   f"cumulative trials)")
        out.append("")
    return out


#: The a-priori OFI configuration changes two things at once -- it turns the
#: leaked meta-labeler off *and* reverts the post-hoc 11:00 entry cut -- so its
#: +16% out of sample cannot be attributed to either one.  This 2x2 separates
#: them.  Both intermediate cells are pre-declared here; the two corners are the
#: compiled and a-priori configurations already evaluated above.
OFI_ABLATION = {
    "ml on,  11:00 (compiled)": {"ml_gate": True, "entry_from": 660},
    "ml off, 11:00": {"ml_gate": False, "entry_from": 660},
    "ml on,  09:30": {"ml_gate": True, "entry_from": 570},
    "ml off, 09:30 (a-priori)": {"ml_gate": False, "entry_from": 570},
}


def ofi_ablation(record_trials=False):
    """Which of the two removals earned the improvement?"""
    name = "Deep OFI Momentum"
    base = APRIORI[name]
    n_trials = trials.total(name)
    rows = []
    for label, overrides in OFI_ABLATION.items():
        rows.append(evaluate(name, label, {**base, **overrides}, n_trials))

    print(f"\nOFI ablation -- {OOS_FROM} .. {PINNED_TO}, ${INITIAL:,.0f}")
    print(_header())
    print("  " + "-" * 108)
    for row in rows:
        print(_row(row["label"], row["oos"]))
    print("\n  max drawdown, same window")
    for row in rows:
        stat = row["oos"]
        print(f"    {row['label']:<28} dd {stat['max_dd']:>7.2f}   "
              f"pnl/dd {(stat['pnl'] / stat['max_dd'] if stat['max_dd'] else 0):>5.2f}")
    if record_trials:
        # Only the two interior cells are new draws; the corners were charged by
        # the defaults test itself.
        total = trials.record(name, 2, "OFI 2x2 ablation: ml_gate x entry_from")
        print(f"\n  charged 2 trials to {name}: {total} cumulative")
    return rows


def run(out_path=None, record_trials=False):
    rows = []
    for name in APRIORI:
        n_trials = trials.total(name)
        rows.append(evaluate(name, f"{name} -- compiled", COMPILED[name], n_trials))
        # The a-priori configuration is one pre-declared draw, so its own haircut
        # is the trial it costs -- but it is charged against the strategy's
        # cumulative count anyway, because the honest question is "how many times
        # has anyone looked at this strategy", not "how many times did this
        # script look".
        rows.append(evaluate(name, f"{name} -- a-priori", APRIORI[name],
                             n_trials + 1))
    if CONTROL in strategies.REGISTRY:
        rows.append(evaluate(CONTROL, f"{CONTROL} -- control", {},
                             trials.total("Hourly Delta Reversal")))

    result = {
        "phase0": ml_leak_audit(),
        "oos_from": OOS_FROM,
        "pinned_to": PINNED_TO,
        "initial": INITIAL,
        "rows": rows,
    }
    result["verdicts"] = verdicts(rows)
    report(result)

    if record_trials:
        for name in APRIORI:
            total = trials.record(
                name, 1, "pre-declared a-priori defaults test (research/defaults_test.py)")
            print(f"  charged 1 trial to {name}: {total} cumulative")
    else:
        print("  (no trials charged; pass --record-trials to persist the count)")

    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="sandbox/results/defaults_test.json")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--ofi-ablation", action="store_true",
                        help="run only the ml_gate x entry_from 2x2 for OFI")
    args = parser.parse_args()
    if args.ofi_ablation:
        ofi_ablation(record_trials=args.record_trials)
    else:
        run(out_path=args.out, record_trials=args.record_trials)


if __name__ == "__main__":
    main()
