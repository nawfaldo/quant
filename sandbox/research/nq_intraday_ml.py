"""Does an ML model find directional edge at NQ intraday boundaries?

WHY THIS SHAPE. The obvious experiment -- meta-label the Hourly Delta Reversal
trades and let a model choose which to take -- is not runnable. HDR fires 292
times in 18 months (184 in 2025, 108 in 2026) and per-trade sd on its ATR-scaled
bracket is 94 points, so the minimum detectable edge is 11.0 points/trade
against a rule that earns 1.4. Loosening every gate raises the pool only to 546,
because there are just ~2,280 RTH hour boundaries in the sample and the
direction condition fires on a quarter of them. 328 training rows is not a
training set.

So this asks the same question with the sample turned up two ways: regress the
**signed forward move over the next period** on *every* RTH boundary rather than
classifying 546 win/loss labels, and run that at four period lengths, because a
15-minute grid has four times the boundaries of an hourly one. The model, not a
threshold, picks the direction.

THE RESOLUTION TEST IS NOT A CONFOUND HERE, and that took care. Finer bars
normally shrink an ATR-scaled stop, so a timeframe comparison silently compares
two different strategies. The stop below is `0.2 x DAILY ATR` at every period, so
it does not move; only the prediction horizon and the time exit do.

THE SPLIT IS 2025 IN-SAMPLE, 2026 OUT-OF-SAMPLE, and 2026 is predicted exactly
once per configuration by a model that has never seen it. Inside 2025 an anchored
walk-forward gives the in-sample read, each fold trained only on boundaries that
closed earlier.

TWO FEATURE SETS, because they are two different claims and the difference
between them IS the answer to "is there edge in level two data":

  * `flow`  -- 11 columns derivable from the traded bars alone: the completed
    period's aggressor delta, body, range, trade and depth-event counts, plus
    ATR, trend distance and session position. This is what the profitable
    strategy already reads.
  * `book`  -- the same 11 plus 6 level-two snapshot columns read from the minute
    *before* the entry bar: spread, the three imbalance depths, microprice tilt
    and replenishment. If level two carries edge the flow columns do not already
    have, `book` beats `flow`. If it does not, they tie.

COSTS ARE THE LIVE EXNESS PRO ACCOUNT, not the 0.2 constant: `combined_book`
prices NQ at 0.300 bp of 29,731 = 0.892 points of spread, plus 0.2 slippage and
no commission, so every entry pays 1.092 points. Charged once at entry, as the
broker bills it. This is also why the shortest period is not automatically the
best one: the cost is fixed per entry while the move being predicted shrinks with
the horizon.

WHAT IS FIXED BEFORE ANY NUMBER IS READ.

  * Periods, feature sets, model families and hyperparameters. Nothing tuned.
  * The bracket -- stop 0.2 x daily ATR, target 2 x stop, time exit at the period
    length, session flatten -- so this changes the entry rule and nothing else.
  * The headline cut is `|prediction| > 0`, i.e. take a position at every
    boundary. It needs no choice and it maximises power. The quantile curve
    underneath it is reported because the shape matters, but every point on it is
    another draw from the same noise and all of them are charged to `trials`.
    With 16 declared headline cells the Bonferroni t threshold is about 2.9.
  * Two controls, both mandatory. A random-direction null over 200 draws on the
    identical entry bars and brackets, because a coin flip has scored +622% on
    this repo's data before; and buy-and-hold, because three survivors have beaten
    their null and still lost to simply holding the instrument.

    py -B -m sandbox.research.nq_intraday_ml
    py -B -m sandbox.research.nq_intraday_ml --periods 60 --record-trials
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sandbox import data, trials
from sandbox import execution as ex_mod
from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.execution import LONG, SHORT, Fill, Signal
from sandbox.paths import result_path
from sandbox.research import combined_book as cb
from sandbox.strategies import base as sbase
import sandbox.strategies.hourly_delta_reversal  # noqa: F401  (registers HDR)

STRATEGY = "NQ Intraday ML"
BASE_STRATEGY = "Hourly Delta Reversal"

OPEN_MIN, CLOSE_MIN = 570, 960
IS_START, IS_END = "2025-01-01", "2026-01-01"
OOS_START, OOS_END = "2026-01-01", "2026-08-05"

SEED = 20260815
NULL_DRAWS = 200
WALK_FORWARD_FOLDS = 5

#: Boundary spacing in minutes. 390 RTH minutes a session, so 15 gives ~26
#: boundaries a day and 120 gives ~3.
PERIODS = (15, 30, 60, 120)

#: Bracket, taken from the compiled strategy rather than searched here. The stop
#: is a fraction of the DAILY ATR at every period, which is what keeps the
#: timeframe comparison honest.
K_STOP, TARGET_RR, FALLBACK_STOP, ATR_DAYS, TREND_DAYS = 0.2, 2.0, 50.0, 20, 45

#: Reported as a curve, never selected from. Each entry keeps the top fraction
#: of boundaries by |prediction|; 1.0 is the declared headline.
KEEP_FRACTIONS = (1.0, 0.75, 0.50, 0.25, 0.10)

FLOW_FEATURES = (
    "period_delta",            # aggressor delta of the completed period
    "period_delta_per_bar",    # ... normalised by how much traded
    "period_body_atr",         # (close - open) / ATR
    "period_range_atr",        # (high - low) / ATR
    "delta_body_disagree",     # the HDR condition itself, made continuous
    "log_trade_count",
    "log_depth_events",
    "atr_bps",
    "trend_distance_atr",      # (price - 45-session SMA) / ATR
    "session_progress",
    "weekday",
)
BOOK_FEATURES = (
    "spread",
    "top1_imbalance",
    "top5_imbalance",
    "top10_imbalance",
    "microprice_ticks",
    "replenishment_score",
)
FEATURE_SETS = {"flow": FLOW_FEATURES, "book": FLOW_FEATURES + BOOK_FEATURES}

NQ_TICK = 0.25


def _epoch(stamp):
    return int(dt.datetime.strptime(stamp, "%Y-%m-%d")
               .replace(tzinfo=dt.UTC).timestamp())


def trailing_sma(session_closes, days):
    """`{day: SMA}` over the `days` sessions that closed strictly before `day`.

    Same point-in-time discipline as `data.sma_gate`: a session's own close is
    withheld until the next day begins, so a boundary never reads a level that
    was not yet printed.
    """
    out, closes = {}, []
    for day, close in sorted(session_closes or []):
        if len(closes) >= days:
            out[day] = sum(closes[-days:]) / days
        closes.append(close)
    return out


def build_rows(bars, features, atr, sma, period):
    """One row per RTH boundary at `period` spacing: features known before it,
    and the move that follows it.

    A boundary at bar `i` is the first bar of a new period bucket whose
    predecessor bucket completed immediately before it. Everything in the feature
    row comes from that completed bucket or from bar `i - 1`'s end-of-minute book
    snapshot -- never from bar `i`, which is the bar a position would fill on.
    Reading bar `i`'s own close here is the mistake that once produced a fake
    +12 points a trade.

    The target is the signed points move from bar `i`'s open to the close of the
    bucket it begins, truncated at the session flatten.
    """
    span = period * 60
    # Forward pass first: bucket -> last bar index inside RTH.
    bucket_last = {}
    for i, bar in enumerate(bars):
        if (bar[TS] % 86_400) // 60 < CLOSE_MIN:
            bucket_last[bar[TS] // span] = i

    rows = []
    bucket = None
    p_open = p_close = p_delta = 0.0
    p_high, p_low = -math.inf, math.inf
    trade_count = depth_events = 0.0

    for i, bar in enumerate(bars):
        ts = bar[TS]
        minute = (ts % 86_400) // 60
        current = ts // span
        day = ts // 86_400

        if bucket == current:
            p_close = bar[C]
            p_delta += bar[D]
            depth_events += bar[DE]
            p_high = max(p_high, bar[H])
            p_low = min(p_low, bar[L])
            trade_count += 1
            continue

        boundary = (bucket is not None and bucket + 1 == current
                    and OPEN_MIN <= minute < CLOSE_MIN and depth_events != 0)
        if boundary:
            unit = atr.get(day)
            end = bucket_last.get(current)
            if unit and end is not None and end > i:
                body = p_close - p_open
                level = sma.get(day)
                row = {
                    "ts": ts, "index": i, "atr": unit, "price": bar[O],
                    "target": bars[end][C] - bar[O],
                    "period_delta": math.asinh(p_delta / 100.0),
                    "period_delta_per_bar": math.asinh(
                        p_delta / max(1.0, trade_count)),
                    "period_body_atr": body / unit,
                    "period_range_atr": (p_high - p_low) / unit,
                    # Positive when delta and body point opposite ways, which is
                    # the disagreement HDR fades, scaled by how big each is.
                    "delta_body_disagree": -math.copysign(1.0, body) * math.asinh(
                        p_delta / 100.0),
                    "log_trade_count": math.log1p(trade_count),
                    "log_depth_events": math.log1p(depth_events),
                    "atr_bps": 10_000.0 * unit / bar[O] if bar[O] else math.nan,
                    "trend_distance_atr": ((bar[O] - level) / unit
                                           if level else math.nan),
                    "session_progress": (minute - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN),
                    "weekday": float((day + 3) % 7),
                }
                snapshot = features.get(bars[i - 1][TS]) if i else None
                if snapshot is not None and snapshot["book_valid"] and snapshot["spread"]:
                    row.update({
                        "spread": snapshot["spread"],
                        "top1_imbalance": snapshot["top1_imbalance"],
                        "top5_imbalance": snapshot["top5_imbalance"],
                        "top10_imbalance": snapshot["top10_imbalance"],
                        "microprice_ticks": (snapshot["microprice"]
                                             - snapshot["midprice"]) / NQ_TICK,
                        "replenishment_score": snapshot["replenishment_score"],
                    })
                rows.append(row)

        bucket = current
        p_open, p_close = bar[O], bar[C]
        p_delta = bar[D]
        depth_events = bar[DE]
        p_high, p_low = bar[H], bar[L]
        trade_count = 1.0

    return rows


def matrix(rows, names):
    """`(X, keep)` for one feature set; `keep` masks rows with any column absent.

    A boundary whose book snapshot is missing or invalid is dropped from the
    `book` set and kept in the `flow` set, so the two are scored on different row
    counts. That is deliberate and reported -- imputing a book that was never
    observed would let the model read the imputation. It also matters for the
    window: the feature table ends 2026-07-16 while the bars run to 08-13.
    """
    x = np.array([[row.get(name, math.nan) for name in names] for row in rows],
                 dtype=float)
    return x, np.isfinite(x).all(axis=1)


def resolve_both_sides(bars, rows, period, ex):
    """`{side: [Fill or None]}`, aligned to `rows`, for every boundary.

    Positions never interact in `execution.resolve` -- each is bracketed and
    exits on its own terms -- so one boundary's outcome does not depend on which
    other boundaries were taken. That makes the whole experiment a gather over
    two precomputed passes instead of a fresh 145k-bar walk per decision set,
    which is what makes 200 null draws per cell affordable at all.
    """
    out = {}
    for side in (LONG, SHORT):
        signals = [Signal(row["index"], side, K_STOP * row["atr"] or FALLBACK_STOP,
                          K_STOP * row["atr"] * TARGET_RR, max_minutes=period)
                   for row in rows]
        by_ts = {}
        for fill in ex_mod.resolve(bars, signals, ex):
            by_ts[fill.entry_ts] = fill
        out[side] = [by_ts.get(row["ts"]) for row in rows]
    return out


def score(resolved, rows, positions, direction, ex, sized=True):
    """Per-unit points, and optionally the sized return, for one decision set.

    `positions` indexes into `rows`; `direction` is the matching -1/0/+1 vector.
    `sized=False` skips `execution.size`, whose queue re-sorts once per fill and
    so costs O(n^2 log n) -- affordable once per reported cell, not 200 times per
    cell inside the null. The null only ever reads `points_per_trade`.
    """
    fills = []
    for position, way in zip(positions, direction):
        if not way:
            continue
        fill = resolved[LONG if way > 0 else SHORT][position]
        if fill is not None:
            fills.append(fill)
    if len(fills) < 2:
        return None
    points = np.array([f.points for f in fills])
    n = len(points)
    sd = float(points.std(ddof=1))
    if not sized:
        return {"trades": n, "points_per_trade": round(float(points.mean()), 4)}
    total = sum(pnl for _ts, pnl in ex_mod.size(fills, ex))
    return {
        "trades": n,
        "points_per_trade": round(float(points.mean()), 4),
        "total_points": round(float(points.sum()), 1),
        "sd": round(sd, 2),
        "t": round(float(points.mean() / (sd / math.sqrt(n))), 3) if sd else None,
        "win_rate": round(float((points > 0).mean()), 4),
        "return_pct": round(100.0 * total / ex.initial, 2),
    }


def null_distribution(resolved, rows, positions, taken, ex, rng):
    """Points/trade from `NULL_DRAWS` random-direction runs on the same entries.

    Identical boundaries, identical brackets, identical trade count -- only the
    sign is randomised. This is the number a result has to beat; a coin flip has
    cleared +622% on this repo's data before, so an absolute return means nothing
    without it.
    """
    live = np.nonzero(taken)[0]
    if not len(live):
        return None
    draws = []
    for _ in range(NULL_DRAWS):
        direction = np.zeros(len(positions))
        direction[live] = rng.choice((-1.0, 1.0), size=len(live))
        stats = score(resolved, rows, positions, direction, ex, sized=False)
        if stats:
            draws.append(stats["points_per_trade"])
    if not draws:
        return None
    draws = np.array(draws)
    return {"mean": round(float(draws.mean()), 4),
            "sd": round(float(draws.std(ddof=1)), 4),
            "draws": len(draws)}


def evaluate(resolved, rows, positions, prediction, ex, rng, fraction):
    """Score the model's directions over the top `fraction` of |prediction|."""
    magnitude = np.abs(prediction)
    if fraction >= 1.0:
        taken = magnitude > 0
    else:
        taken = magnitude >= np.quantile(magnitude, 1.0 - fraction)
    direction = np.where(taken, np.sign(prediction), 0.0)
    stats = score(resolved, rows, positions, direction, ex)
    if stats is None:
        return None
    null = null_distribution(resolved, rows, positions, taken, ex, rng)
    if null and null["sd"]:
        stats["null_mean"] = null["mean"]
        stats["null_sd"] = null["sd"]
        stats["edge_vs_null"] = round(stats["points_per_trade"] - null["mean"], 4)
        stats["z_vs_null"] = round(
            (stats["points_per_trade"] - null["mean"]) / null["sd"], 3)
    return stats


def information_coefficient(prediction, actual):
    """Spearman correlation of prediction with the realised move.

    Threshold-free and bracket-free, so it answers "does the model predict?"
    with no trading decision in the way. Spearman rather than Pearson because a
    handful of violent periods would otherwise carry the whole statistic.
    """
    if len(prediction) < 3 or not np.ptp(prediction):
        return None
    def rank(v):
        return np.argsort(np.argsort(v)).astype(float)
    return round(float(np.corrcoef(rank(prediction), rank(actual))[0, 1]), 4)


def make_model(family):
    if family == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    return HistGradientBoostingRegressor(
        max_depth=3, max_iter=200, learning_rate=0.05,
        l2_regularization=1.0, random_state=SEED)


def walk_forward(x, y, ts, family, lo, hi, folds=WALK_FORWARD_FOLDS):
    """Anchored causal predictions across the in-sample window."""
    inside = np.nonzero((ts >= lo) & (ts < hi))[0]
    prediction = np.zeros(len(ts))
    tested = np.zeros(len(ts), dtype=bool)
    if len(inside) < 200:
        return prediction, tested
    edges = np.linspace(lo, hi, folds + 1).astype(np.int64)
    for fold in range(1, folds):
        train = inside[ts[inside] < edges[fold]]
        test = inside[(ts[inside] >= edges[fold]) & (ts[inside] < edges[fold + 1])]
        if len(train) < 100 or not len(test):
            continue
        model = make_model(family)
        model.fit(x[train], y[train])
        prediction[test] = model.predict(x[test])
        tested[test] = True
    return prediction, tested


def buy_and_hold(bars, lo, hi):
    """Points from holding one unit across the window, for scale."""
    inside = [bar for bar in bars if lo <= bar[TS] < hi]
    if len(inside) < 2:
        return None
    return round(inside[-1][C] - inside[0][O], 1)


def _line(label, stats):
    if not stats:
        return f"{label:34}{'   -- no trades --':>58}"
    return (f"{label:34}{stats['trades']:>7}{stats['points_per_trade']:>10.3f}"
            f"{stats.get('t') or 0:>8.2f}{stats.get('edge_vs_null', 0):>10.3f}"
            f"{stats.get('z_vs_null', 0):>8.2f}{stats['win_rate']:>7.3f}"
            f"{stats['return_pct']:>10.2f}")


HEADER = (f"{'variant':34}{'n':>7}{'pts/trd':>10}{'t':>8}{'vsNull':>10}"
          f"{'z':>8}{'win':>7}{'ret%':>10}")


def run_period(bars, features, atr, sma, period, ex, report):
    """Everything for one boundary spacing. Returns the number of cells scored."""
    rows = build_rows(bars, features, atr, sma, period)
    if len(rows) < 300:
        print(f"\nperiod {period}m: only {len(rows)} boundaries, skipped")
        return 0
    ts = np.array([row["ts"] for row in rows])
    y = np.array([row["target"] for row in rows])
    resolved = resolve_both_sides(bars, rows, period, ex)

    is_lo, is_hi = _epoch(IS_START), _epoch(IS_END)
    oos_lo, oos_hi = _epoch(OOS_START), _epoch(OOS_END)
    rng = np.random.default_rng(SEED)
    cells = 0

    entry = {"boundaries": len(rows),
             "forward_move_sd": round(float(y.std(ddof=1)), 2),
             "cost_as_fraction_of_move": round(
                 ex.entry_cost / float(np.abs(y).mean()), 4),
             "sets": {}}
    print(f"\n{'#' * len(HEADER)}")
    print(f"PERIOD {period}m: {len(rows)} boundaries, forward move sd "
          f"{y.std(ddof=1):.1f} pts, mean |move| {np.abs(y).mean():.1f} pts, "
          f"entry cost is {100 * ex.entry_cost / np.abs(y).mean():.1f}% of it")
    print(f"{'#' * len(HEADER)}")

    for set_name, names in FEATURE_SETS.items():
        x, keep = matrix(rows, names)
        usable = np.nonzero(keep)[0]
        sub_ts, sub_y = ts[usable], y[usable]
        n_is = int(((sub_ts >= is_lo) & (sub_ts < is_hi)).sum())
        n_oos = int(((sub_ts >= oos_lo) & (sub_ts < oos_hi)).sum())
        print(f"\nfeature set {set_name!r}: {len(names)} columns, "
              f"{len(usable)}/{len(rows)} usable ({n_is} IS, {n_oos} OOS)")
        print(HEADER)
        print("-" * len(HEADER))
        block = {"columns": len(names), "usable": len(usable),
                 "in_sample_rows": n_is, "out_of_sample_rows": n_oos,
                 "families": {}}

        for family in ("ridge", "gradient_boost"):
            record = {}
            wf_prediction, wf_tested = walk_forward(
                x[usable], sub_y, sub_ts, family, is_lo, is_hi)
            tested = np.nonzero(wf_tested)[0]
            if len(tested):
                record["in_sample_ic"] = information_coefficient(
                    wf_prediction[tested], sub_y[tested])
                stats = evaluate(resolved, rows, usable[tested],
                                 wf_prediction[tested], ex, rng, 1.0)
                record["in_sample"] = stats
                print(_line(f"{family}/{set_name} 2025 wf", stats))
                cells += 1

            train = np.nonzero((sub_ts >= is_lo) & (sub_ts < is_hi))[0]
            test = np.nonzero((sub_ts >= oos_lo) & (sub_ts < oos_hi))[0]
            if len(train) < 100 or not len(test):
                block["families"][family] = record
                continue
            model = make_model(family)
            model.fit(x[usable][train], sub_y[train])
            prediction = model.predict(x[usable][test])
            record["out_of_sample_ic"] = information_coefficient(
                prediction, sub_y[test])
            record["out_of_sample"] = []
            for fraction in KEEP_FRACTIONS:
                stats = evaluate(resolved, rows, usable[test], prediction, ex,
                                 rng, fraction)
                tag = "  <- declared" if fraction == 1.0 else ""
                print(_line(f"{family}/{set_name} 2026 top{fraction:.0%}",
                            stats) + tag)
                record["out_of_sample"].append(
                    {"keep_fraction": fraction, "stats": stats})
                cells += 1
            print(f"  IC  in-sample {record.get('in_sample_ic')}  "
                  f"out-of-sample {record.get('out_of_sample_ic')}")
            block["families"][family] = record
        entry["sets"][set_name] = block

    report["periods"][str(period)] = entry
    return cells


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--periods", type=int, nargs="+", default=list(PERIODS),
                        help="boundary spacings in minutes")
    parser.add_argument("--out", default="nq_intraday_ml_result.json")
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()

    account = cb.apply_cost_model("pro")
    base = sbase.get(BASE_STRATEGY)
    ex = cb.nq_execution(base.execution)
    print(f"cost model {account!r}: spread {ex.spread:.4f} + slippage "
          f"{ex.slippage} + commission {ex.commission_per_lot} "
          f"= {ex.entry_cost:.4f} points/entry")

    bars = data.load_bars(base.bars, base.symbol)
    features = data.load_l2_features(base.symbol)
    atr = data.atr_by_day(bars, ATR_DAYS)
    sma = trailing_sma(data.load_session_closes(base.symbol), TREND_DAYS)
    print(f"{len(bars)} bars, {len(features)} feature minutes")

    is_lo, is_hi = _epoch(IS_START), _epoch(IS_END)
    oos_lo, oos_hi = _epoch(OOS_START), _epoch(OOS_END)

    report = {"strategy": STRATEGY, "cost_model": account,
              "entry_cost_points": round(ex.entry_cost, 4),
              "in_sample": [IS_START, IS_END],
              "out_of_sample": [OOS_START, OOS_END],
              "periods_tested": args.periods,
              "bracket": {"k": K_STOP, "rr": TARGET_RR, "atr_days": ATR_DAYS,
                          "time_exit": "period length"},
              "feature_sets": {k: list(v) for k, v in FEATURE_SETS.items()},
              "controls": {}, "periods": {}}

    # ---------------------------------------------------------------- controls
    base_signals = base.signals(bars, base.context(), "all", base.all_params())
    for window, (lo, hi) in (("in_sample", (is_lo, is_hi)),
                             ("out_of_sample", (oos_lo, oos_hi))):
        kept = [s for s in base_signals if lo <= bars[s.index][TS] < hi]
        fills = ex_mod.resolve(bars, kept, ex)
        points = np.array([f.points for f in fills]) if fills else np.array([0.0])
        report["controls"][f"hdr_{window}"] = {
            "trades": len(fills),
            "points_per_trade": round(float(points.mean()), 4),
            "total_points": round(float(points.sum()), 1)}
        report["controls"][f"buy_and_hold_{window}_points"] = buy_and_hold(
            bars, lo, hi)
    print("\ncontrols (Pro cost)")
    for key, value in report["controls"].items():
        print(f"  {key:28} {value}")

    cells = sum(run_period(bars, features, atr, sma, period, ex, report)
                for period in args.periods)

    charged = trials.total(STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(
            STRATEGY, cells,
            f"intraday forward-return regression: periods {args.periods} x 2 "
            f"feature sets x 2 families x keep-fraction curve")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {STRATEGY!r}: {charged}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out)
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
