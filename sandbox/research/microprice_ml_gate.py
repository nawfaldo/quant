"""A meta-labeler for NQ Microprice Divergence: fewer, better-chosen trades.

The base rule proposes every signal; a model decides which to take. A filter
cannot invent edge -- it can only reallocate what the entry already produces --
so the question this answers is narrow and worth stating plainly: *is the
strategy's win/loss outcome conditionally predictable from the book state at the
signal bar, even though its unconditional edge is not distinguishable from
zero?* If it is, a gate that keeps a few hundred trades instead of a few
thousand should beat the base rule out of sample. If it is not, every threshold
will trace the same flat line and the trade count will be the only thing that
changed.

WHAT IS FIXED BEFORE ANY NUMBER IS READ. `research/meta_label.py` documents why
this matters; the same discipline applies here.

  * **One feature set**, thirteen columns every level-two bar already carries,
    sign-folded onto the side the base rule takes so the model learns "is this
    book agreeing with me" rather than learning long and short separately from
    half the data each. No feature selection.
  * **Strictly causal training.** The model trains on 2025 signals only and is
    asked about 2026 once. Inside 2025 an anchored walk-forward gives the
    in-sample read, with every fold trained on signals that entered earlier.
  * **Labels are the realised outcome** of taking that candidate under the base
    rule's own bracket: `points > 0` after the spread.
  * **Occupancy is modelled.** The gate is applied as a filter mask *before* the
    single-position selection, exactly as the parameter search applies its
    regime filters, so a rejected signal leaves the account flat and the next
    signal is free to take the slot. `meta_label.py` deliberately skips this;
    here the machinery was already verified against `execution.resolve`, so
    there is no reason to.
  * **Nothing is exported.** This run measures. It does not produce coefficients
    for anyone to compile.

WHAT IS A SEARCH, AND IS COUNTED AS ONE. Two model families and a threshold
curve are reported because the request asked for both "a better filter" and "an
ML model", and refusing to show the curve would hide the shape of the answer.
Every threshold and every family is a draw from the same noise and all of them
are charged to `trials.json`. The headline is always the pre-declared cut of
0.5, not the best point on the curve.

    py -B -m sandbox.research.microprice_ml_gate
    py -B -m sandbox.research.microprice_ml_gate --record-trials
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data, trials
from sandbox.paths import result_path
from sandbox.research import microprice_optimization as base

MODEL_C = 0.1
MODEL_SEED = 20260806
DECLARED_THRESHOLD = 0.5
#: Reported as a curve, not selected from. Each point is a separate draw.
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)

FEATURE_NAMES = (
    "signed_tilt",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_trade_delta",
    "signed_price_change",
    "signed_replenishment",
    "log_trade_count",
    "log_depth_events",
    "spread",
    "session_progress",
    "atr_bps",
)

RTH_OPEN, RTH_CLOSE = 570, 960

#: The two base rules the gate is tried on: the parameters actually compiled,
#: and the signal/bracket cell with the largest gross per-trade edge in 2025.
#: Both are declared here rather than chosen from the gate's own results.
BASE_RULES = {
    "compiled": dict(base.COMPILED),
    "best_gross": {**base.COMPILED, "halflife": 10, "tilt": 0.05,
                   "max_spread": 0.75, "k": 0.03, "rr": 3.0, "time_stop": 20,
                   "trail_r": 0.75},
}


def build_matrix(state, candidates, cell):
    """Features, labels and the side taken, for every candidate the rule fires on.

    The feature row is the *signal* bar's end-of-minute snapshot. Entries fill at
    the next bar's open, so reading the fill bar's own snapshot would be
    lookahead -- the same boundary the strategy itself respects.
    """
    side = base.signal_mask(candidates, cell["halflife"], cell["tilt"],
                            cell["direction"], cell["max_spread"])
    resolved = base.PathCache(state, candidates)(
        cell["k"], cell["rr"], cell["time_stop"], cell["trail_r"])

    rows = data.load_l2_features("nq")
    eligible = np.nonzero(side != 0)[0]
    signal_ts = state["ts"][candidates["signal"][eligible]]

    matrix = np.full((len(eligible), len(FEATURE_NAMES)), np.nan)
    points = np.empty(len(eligible))
    for direction in (base.LONG, base.SHORT):
        which = side[eligible] == direction
        if which.any():
            points[which] = resolved[direction][1][eligible[which]]

    for position, (index, stamp) in enumerate(zip(eligible, signal_ts)):
        row = rows.get(int(stamp))
        if row is None or not row["book_valid"]:
            continue
        sign = 1.0 if side[index] == base.LONG else -1.0
        minute = (int(stamp) % 86_400) // 60
        price = candidates["entry"][index]
        matrix[position] = (
            sign * (row["microprice"] - row["midprice"]) / row["spread"],
            sign * row["top1_imbalance"],
            sign * row["top5_imbalance"],
            sign * row["top10_imbalance"],
            sign * (row["microprice"] - row["midprice"]) / 0.25,
            math.asinh(sign * row["trade_delta"] / 100.0),
            math.asinh(sign * row["price_change"] / 2.0),
            sign * row["replenishment_score"],
            math.log1p(row["trade_count"]),
            math.log1p(row["depth_event_count"]),
            row["spread"],
            min(1.0, max(0.0, (minute - RTH_OPEN) / (RTH_CLOSE - RTH_OPEN))),
            10_000.0 * candidates["atr"][index] / price if price > 0 else np.nan,
        )

    usable = np.isfinite(matrix).all(axis=1)
    return {
        "index": eligible[usable],
        "x": matrix[usable],
        "y": points[usable] > 0,
        "ts": candidates["ts"][eligible[usable]],
        "side": side,
        "resolved": resolved,
    }


def make_model(family):
    if family == "logistic":
        return make_pipeline(
            StandardScaler(),
            LogisticRegression(C=MODEL_C, class_weight="balanced",
                               max_iter=1_000, random_state=MODEL_SEED),
        )
    return HistGradientBoostingClassifier(
        max_depth=3, max_iter=200, learning_rate=0.05,
        class_weight="balanced", random_state=MODEL_SEED)


def score_gate(state, candidates, cell, sample, accepted, lo, hi, initial):
    """Run the base pipeline with the gate's acceptances as the filter mask."""
    keep = np.zeros(len(candidates["signal"]), dtype=bool)
    keep[sample["index"][accepted]] = True
    trades = base.build_trades(candidates, sample["side"], keep,
                               sample["resolved"], lo, hi)
    if trades is None:
        return None
    stop = cell["k"] * trades["atr"]
    if cell["trail_r"] is not None:
        stop = np.minimum(stop, cell["trail_r"] * stop)
    pnls, closed, marked = base.equity_walk(trades["entry"], stop,
                                            trades["points"], trades["atr"],
                                            cell["sizing"], initial)
    stats = base.score(trades["ts"], pnls, closed, marked, initial)
    if stats is not None:
        stats["gross_points_per_trade"] = round(
            float(np.mean(trades["points"] + base.SPREAD)), 4)
    return stats


def walk_forward(sample, family, is_lo, is_hi, folds=5):
    """Anchored, causal acceptance probabilities across 2025.

    Fold i trains only on signals that entered earlier. Returns the raw
    probability per row (and the mask of rows a fold actually scored) rather
    than a boolean at one cut, so the exposure ladder can re-threshold the same
    causal predictions instead of silently reusing the 0.50 acceptances under
    another label.
    """
    inside = np.nonzero((sample["ts"] >= is_lo) & (sample["ts"] < is_hi))[0]
    probability = np.zeros(len(sample["ts"]))
    tested = np.zeros(len(sample["ts"]), dtype=bool)
    if len(inside) < 200:
        return probability, tested, []

    edges = np.linspace(is_lo, is_hi, folds + 1).astype(np.int64)
    report = []
    for fold in range(1, folds + 1):
        train = inside[sample["ts"][inside] < edges[fold]]
        test = inside[(sample["ts"][inside] >= edges[fold])
                      & (sample["ts"][inside] < edges[fold + 1])] \
            if fold < folds else np.array([], dtype=np.int64)
        if not len(train) or not len(test) or len(set(sample["y"][train])) < 2:
            continue
        model = make_model(family)
        model.fit(sample["x"][train], sample["y"][train])
        probability[test] = model.predict_proba(sample["x"][test])[:, 1]
        tested[test] = True
        report.append({"fold": fold, "train": int(len(train)),
                       "candidates": int(len(test)),
                       "taken": int((probability[test] >= DECLARED_THRESHOLD).sum())})
    return probability, tested, report


def threshold_curve(sample, family, is_lo, is_hi, oos_lo, oos_hi):
    """Probabilities for 2026 from a model trained on 2025 only."""
    train = np.nonzero((sample["ts"] >= is_lo) & (sample["ts"] < is_hi))[0]
    test = np.nonzero((sample["ts"] >= oos_lo) & (sample["ts"] < oos_hi))[0]
    if not len(train) or not len(test) or len(set(sample["y"][train])) < 2:
        return None, None, None
    model = make_model(family)
    model.fit(sample["x"][train], sample["y"][train])
    probability = np.zeros(len(sample["ts"]))
    probability[test] = model.predict_proba(sample["x"][test])[:, 1]
    return model, probability, test


#: Margin requirements to sweep when a configuration's drawdown is below the
#: band asked for. 0.25 is the compiled account (4x notional); the rest are
#: broker leverage settings a retail index CFD account can actually offer.
MARGIN_LADDER = (0.25, 0.10, 0.05, 0.025, 0.01)

TARGET_DRAWDOWN = (0.10, 0.15)


def exposure_sweep(state, candidates, cell, sample, accepted, lo, hi, initial):
    """The same trade list at increasing account leverage.

    Exposure does not change which trades are taken or their per-unit outcome,
    so this is a pure re-sizing of one decision set. It moves return and
    drawdown together: scaling a set of trades up cannot improve the ratio
    between them, and past the point where compounding bites it makes the ratio
    worse. Reported so the leverage needed to reach a drawdown band is visible
    as a broker requirement rather than hidden inside a sizing mode.
    """
    rows = []
    for margin in MARGIN_LADDER:
        sizing = {**cell["sizing"], "margin": margin}
        stats = score_gate(state, candidates, {**cell, "sizing": sizing},
                           sample, accepted, lo, hi, initial)
        rows.append({"margin": margin, "notional_leverage": round(1.0 / margin, 1),
                     "stats": base._trim(stats)})
    return rows


def _line(label, stats):
    if not stats:
        return f"{label:34}{'  -- no trades --':>52}"
    return (f"{label:34}{stats['total_return_pct']:>9.2f}"
            f"{100 * stats['max_drawdown']:>9.2f}{stats['trades']:>8}"
            f"{stats['msharpe']:>8.2f}{stats['pos_rate']:>7.2f}"
            f"{stats['max_loss_streak']:>6}{stats['gross_points_per_trade']:>9.3f}")


HEADER = (f"{'variant':34}{'ret%':>9}{'maxDD%':>9}{'n':>8}{'mSh':>8}"
          f"{'pos':>7}{'str':>6}{'grossPt':>9}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--balance", type=float, default=base.INITIAL)
    parser.add_argument("--out", default="microprice_ml_gate_result.json")
    parser.add_argument("--record-trials", action="store_true")
    parser.add_argument("--exposure-sweep", action="store_true",
                        help="re-size each gated decision set up the margin "
                             "ladder, to show what leverage a drawdown band "
                             "requires")
    args = parser.parse_args()

    is_lo, is_hi = base._epoch(base.IS_START), base._epoch(base.IS_END)
    oos_lo, oos_hi = base._epoch(base.OOS_START), base._epoch(base.OOS_END)

    print("loading nq level-two bars and features ...")
    state = base.load_state("nq")
    candidates = base.build_candidates(state)
    print(f"  {state['n']} bars, {len(candidates['signal'])} candidate signals")

    report = {"strategy": base.STRATEGY, "initial_balance": args.balance,
              "declared_threshold": DECLARED_THRESHOLD,
              "in_sample": [base.IS_START, base.IS_END],
              "out_of_sample": [base.OOS_START, base.OOS_END],
              "features": list(FEATURE_NAMES), "rules": {}}
    cells = 0

    for rule_name, cell in BASE_RULES.items():
        print(f"\n{'=' * len(HEADER)}")
        print(f"BASE RULE: {rule_name}  {base.cell_label(cell)}")
        print(f"{'=' * len(HEADER)}")
        sample = build_matrix(state, candidates, cell)
        win_rate = float(np.mean(sample["y"]))
        print(f"  {len(sample['ts'])} labelled candidates, "
              f"{100 * win_rate:.1f}% would have won")
        print(HEADER)
        print("-" * len(HEADER))

        entry = {"cell": base._serialisable(cell),
                 "candidates": int(len(sample["ts"])),
                 "candidate_win_rate": round(win_rate, 4), "families": {}}

        ungated = np.ones(len(sample["ts"]), dtype=bool)
        is_base = score_gate(state, candidates, cell, sample, ungated,
                             is_lo, is_hi, args.balance)
        oos_base = score_gate(state, candidates, cell, sample, ungated,
                              oos_lo, oos_hi, args.balance)
        print(_line("no gate (2025)", is_base))
        print(_line("no gate (2026)", oos_base))
        entry["no_gate"] = {"in_sample": base._trim(is_base),
                            "out_of_sample": base._trim(oos_base)}

        for family in ("logistic", "gradient_boost"):
            print("-" * len(HEADER))
            wf_probability, wf_tested, folds = walk_forward(sample, family,
                                                            is_lo, is_hi)
            accepted = wf_tested & (wf_probability >= DECLARED_THRESHOLD)
            is_gated = score_gate(state, candidates, cell, sample, accepted,
                                  is_lo, is_hi, args.balance)
            print(_line(f"{family} @0.50 (2025 wf)", is_gated))
            cells += 1

            model, probability, test = threshold_curve(
                sample, family, is_lo, is_hi, oos_lo, oos_hi)
            curve = []
            for threshold in THRESHOLDS:
                if probability is None:
                    continue
                mask = np.zeros(len(sample["ts"]), dtype=bool)
                mask[test] = probability[test] >= threshold
                stats = score_gate(state, candidates, cell, sample, mask,
                                   oos_lo, oos_hi, args.balance)
                tag = " <- declared" if threshold == DECLARED_THRESHOLD else ""
                print(_line(f"{family} @{threshold:.2f} (2026)", stats) + tag)
                curve.append({"threshold": threshold, "stats": base._trim(stats)})
                cells += 1

            exposure = {}
            if args.exposure_sweep and probability is not None:
                for threshold in (DECLARED_THRESHOLD, 0.55):
                    mask = np.zeros(len(sample["ts"]), dtype=bool)
                    mask[test] = probability[test] >= threshold
                    print(f"  exposure ladder, {family} @{threshold:.2f}:")
                    for window, (lo, hi) in (("2025", (is_lo, is_hi)),
                                             ("2026", (oos_lo, oos_hi))):
                        # 2025 is re-sized here, not re-selected: the acceptances
                        # are the walk-forward ones, so this stays a leverage
                        # statement rather than a second look at the ranking.
                        source = (wf_tested & (wf_probability >= threshold)
                                  if window == "2025" else mask)
                        rows = exposure_sweep(state, candidates, cell, sample,
                                              source, lo, hi, args.balance)
                        for row in rows:
                            print("  " + _line(
                                f"  {row['notional_leverage']:g}x notional "
                                f"({window})", row["stats"]))
                        exposure[f"{family}@{threshold:.2f}/{window}"] = rows
                        cells += len(rows)

            entry["families"][family] = {
                "in_sample_walk_forward": base._trim(is_gated),
                "folds": folds,
                "out_of_sample_curve": curve,
                "exposure_ladder": exposure,
            }
        report["rules"][rule_name] = entry

    charged = trials.total(base.STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(base.STRATEGY, cells,
                                "microprice ML meta-label gate: 2 rules x 2 "
                                "families x threshold curve")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {base.STRATEGY!r}: {charged}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out)
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
