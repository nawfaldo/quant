"""A meta-labeler for Deep OFI Momentum.

The base rule proposes every trade; a fixed logistic model decides which to take.
It cannot invent edge -- a filter can only reallocate what the entry rule already
produces -- so this is worth running for one reason: an entry-hour cut chosen by a
human reading a full-sample table is not admissible, while a model that
rediscovers time-of-day from training folds alone, never seeing its own test
window, is. `session_progress` is in the feature set so it can.

The whole design is declared here and none of it is chosen by result. Everything
below was fixed before the first number was read, because the previous attempt at
this (`ofi_ml.py`) shows what happens otherwise: sound per-fold validation, and
then a *full-sample refit* exported and compiled, so the live gate had trained on
every month of its own out-of-sample record.

  * **One model family.** L2 logistic regression, `C=0.1`, balanced classes,
    threshold 0.5 -- the same settings `ofi_ml.py` already used. No comparison
    against a gradient booster or a ridge: the earlier run charged three trials
    doing exactly that, and picking the winner of three families is a search.
  * **One fixed feature set.** Eleven columns every level-two bar carries,
    chosen before any fitting, so there is no feature selection -- the cheapest
    way to overfit a model and the hardest to see afterwards.
  * **One threshold.** 0.5, the natural cut of a calibrated probability. A tuned
    threshold is a tuned parameter wearing a model's clothes.
  * **Training is strictly causal.** Fold `i` trains on trades that *entered*
    before its test window opens. Purged CV is reported beside it as the regime
    veto and is explicitly not a deployment simulation -- it trains on later
    blocks by design.
  * **Nothing is exported.** This run measures; it does not produce coefficients
    for anyone to compile. That is the specific failure being avoided.

Labels are `points > 0` on the realised fill. Evaluation is on the strategy's
actual fills, so a rejected trade does *not* free the strategy to take an
overlapping one -- `ofi_ml.py`'s `candidate_pool` models that occupancy effect
and this does not. The simplification keeps both strategies on identical
machinery simple; the direction of its bias is ambiguous rather than flattering,
since the trade it suppresses may have been a winner or a loser.

    py -B -m sandbox.research.meta_label
    py -B -m sandbox.research.meta_label --record-trials
"""
import argparse
import json
import math
import os
from dataclasses import replace

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data, execution, metrics, purged_cv, strategies, trials, walkforward
from sandbox.data import TS

MODEL_C = 0.1
MODEL_THRESHOLD = 0.5
MODEL_SEED = 20260805

STRATEGIES = ("Deep OFI Momentum",)

FEATURE_NAMES = (
    "signed_trade_delta",
    "signed_top1_imbalance",
    "signed_top5_imbalance",
    "signed_top10_imbalance",
    "signed_microprice_ticks",
    "signed_price_change",
    "signed_replenishment",
    "log_trade_count",
    "log_depth_events",
    "spread",
    "session_progress",
)

RTH_OPEN, RTH_CLOSE = 570, 960


def features(row, is_long, ts):
    """Eleven numbers known on the closed signal bar, before the fill.

    Sign-folded onto the side taken, so the model learns "is this book agreeing
    with me" rather than having to learn long and short separately from half the
    data each.
    """
    sign = 1.0 if is_long else -1.0
    minute = (ts % 86_400) // 60
    progress = (minute - RTH_OPEN) / (RTH_CLOSE - RTH_OPEN)
    return (
        math.asinh(sign * row["trade_delta"] / 100.0),
        sign * row["top1_imbalance"],
        sign * row["top5_imbalance"],
        sign * row["top10_imbalance"],
        sign * (row["microprice"] - row["midprice"]) / 0.25,
        math.asinh(sign * row["price_change"] / 2.0),
        sign * row["replenishment_score"],
        math.log1p(row["trade_count"]),
        math.log1p(row["depth_event_count"]),
        row["spread"],
        min(1.0, max(0.0, progress)),
    )


def labelled_fills(name):
    """Every fill the compiled strategy takes, with its signal-bar features.

    The signal bar is the minute *before* entry: entries fill at a bar's open, so
    reading the entry bar's own end-of-minute snapshot would be lookahead.
    """
    strategy = strategies.get(name)
    ex = replace(strategy.execution, initial=walkforward.INITIAL)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    rows = data.load_l2_features(strategy.symbol)
    params = strategy.all_params()

    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    fills = execution.resolve(bars, signals, ex)

    out = []
    for fill in fills:
        row = rows.get(fill.entry_ts - 60)
        if row is None or not row["book_valid"]:
            continue
        out.append((fill, features(row, fill.side == execution.LONG,
                                   fill.entry_ts - 60)))
    return strategy, ex, out


def make_model():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=MODEL_C, class_weight="balanced", max_iter=1_000,
                           random_state=MODEL_SEED),
    )


def _fit(rows):
    model = make_model()
    x = np.asarray([f for _fill, f in rows], dtype=float)
    y = np.asarray([fill.points > 0 for fill, _f in rows])
    if len(set(y.tolist())) < 2:
        return None
    model.fit(x, y)
    return model


def _accepts(model, rows):
    if model is None or not rows:
        return []
    x = np.asarray([f for _fill, f in rows], dtype=float)
    return model.predict_proba(x)[:, 1] >= MODEL_THRESHOLD


def walk_forward(rows):
    """Anchored, causal: every fold trains only on trades entered earlier."""
    kept, base, folds = [], [], []
    for _train_lo, train_hi, test_lo, test_hi in walkforward.fold_windows():
        train = [r for r in rows if r[0].entry_ts < train_hi]
        test = [r for r in rows if test_lo <= r[0].entry_ts < test_hi]
        if not train or not test:
            continue
        model = _fit(train)
        mask = _accepts(model, test)
        taken = [r for r, ok in zip(test, mask) if ok]
        kept.extend(taken)
        base.extend(test)
        folds.append({
            "test_from": metrics.month_key(test_lo),
            "train": len(train),
            "candidates": len(test),
            "taken": len(taken),
            "base_points": round(sum(r[0].points for r in test), 2),
            "model_points": round(sum(r[0].points for r in taken), 2),
        })
    return kept, base, folds


def purged(rows):
    """Regime veto. Trains on later blocks by design; never selects anything."""
    out = []
    for lo_iso, hi_iso in purged_cv.BLOCKS:
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        purge = purged_cv.EMBARGO_DAYS * 86_400
        train = [r for r in rows
                 if r[0].entry_ts < lo - purge or r[0].entry_ts >= hi + purge]
        test = [r for r in rows if lo <= r[0].entry_ts < hi]
        if not train or not test:
            continue
        model = _fit(train)
        taken = [r for r, ok in zip(test, _accepts(model, test)) if ok]
        out.append({
            "block": lo_iso,
            "candidates": len(test),
            "taken": len(taken),
            "base_points": round(sum(r[0].points for r in test), 2),
            "model_points": round(sum(r[0].points for r in taken), 2),
        })
    return out


def _panel(rows, ex, n_trials, span):
    fills = [f for f, _ in rows]
    points = [f.points for f in fills]
    sized = execution.size(fills, ex)
    stat = metrics.stats(sized, initial=ex.initial, span=span)
    t = walkforward.edge_t(points)
    lo, hi = walkforward.bootstrap_edge(points) if len(points) > 1 else (0.0, 0.0)
    return {
        "trades": len(points),
        "pnl": stat["pnl"],
        "pf": stat["pf"],
        "msharpe": stat["msharpe"],
        "max_dd": stat["max_dd"],
        "points_per_trade": round(sum(points) / len(points), 3) if points else 0.0,
        "edge_t": round(t, 3),
        "deflated_t": round(walkforward.deflated_t(t, n_trials), 3),
        "boot_lo": round(lo, 3),
        "boot_hi": round(hi, 3),
    }


def evaluate(name):
    strategy, ex, rows = labelled_fills(name)
    n_trials = trials.total(name) + 1
    kept, base, folds = walk_forward(rows)
    span = None
    if base:
        span = (min(f.entry_ts for f, _ in base), max(f.entry_ts for f, _ in base) + 60)
    return {
        "strategy": name,
        "labelled_fills": len(rows),
        "base_oos": _panel(base, ex, n_trials, span),
        "model_oos": _panel(kept, ex, n_trials, span),
        "folds": folds,
        "purged_cv": purged(rows),
    }


def report(result):
    for row in result["rows"]:
        print(f"\n{row['strategy']} -- stitched walk-forward OOS")
        print(f"  {row['labelled_fills']} labelled fills, "
              f"{len(row['folds'])} folds, threshold {MODEL_THRESHOLD}")
        print(f"  {'':<10}{'trades':>8}{'pnl':>10}{'pf':>7}{'mSharpe':>9}"
              f"{'maxDD':>9}{'pts/tr':>9}{'t':>7}{'defl_t':>8}")
        for label, key in (("base", "base_oos"), ("model", "model_oos")):
            s = row[key]
            print(f"  {label:<10}{s['trades']:>8}{s['pnl']:>10.2f}{s['pf']:>7.2f}"
                  f"{s['msharpe']:>9.2f}{s['max_dd']:>9.2f}"
                  f"{s['points_per_trade']:>9.2f}{s['edge_t']:>7.2f}"
                  f"{s['deflated_t']:>8.2f}")
        kept = sum(f["taken"] for f in row["folds"])
        cand = sum(f["candidates"] for f in row["folds"])
        better = sum(1 for f in row["folds"] if f["model_points"] > f["base_points"])
        print(f"  keeps {kept}/{cand} candidates; beats base in "
              f"{better}/{len(row['folds'])} folds")
        blocks = row["purged_cv"]
        if blocks:
            won = sum(1 for b in blocks if b["model_points"] > b["base_points"])
            print(f"  purged CV: model ahead in {won}/{len(blocks)} regime blocks")


def run(out_path=None, record_trials=False):
    rows = [evaluate(name) for name in STRATEGIES]
    result = {"model": {"family": "logistic", "C": MODEL_C,
                        "threshold": MODEL_THRESHOLD,
                        "features": list(FEATURE_NAMES)},
              "exported": False,
              "rows": rows}
    report(result)
    if record_trials:
        for name in STRATEGIES:
            total = trials.record(name, 1,
                                  "pre-declared meta-labeler: one family, one "
                                  "threshold, one feature set (research/meta_label.py)")
            print(f"\n  charged 1 trial to {name}: {total} cumulative")
    else:
        print("\n  (no trials charged; pass --record-trials to persist)")
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"  wrote {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="sandbox/results/meta_label.json")
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()
    run(out_path=args.out, record_trials=args.record_trials)


if __name__ == "__main__":
    main()
