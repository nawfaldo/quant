"""Which regime DEFINITION, if any, separates these strategies? Searched properly.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Paste the ranking table into the
reply ([[paste-results-into-the-reply]]).

WHY THIS EXISTS.

`exness_regime_switch` fitted a dead switch on ONE regime definition -- an EWMA
of NQ daily vol, split at its own fit-window median -- because that was the axis
with prior support in this repo, not because it had won anything. It lost badly:
on the 166-cell pool the switched book returned 1052.4% against the always-on
control's 1879.1%, and worse, it fell BELOW the entire 10-seed random-assignment
null band of 1314.6%-3268.3%. Refusing trades at random beat refusing them by
that rule, ten times out of ten.

That is one axis refuted, not the idea. This module searches the axis itself.

THE SELECTION PROBLEM, AND THE ONLY HONEST WAY OUT.

Trying 26 regime definitions and reporting the best one on the test window
hands back the maximum of 26 draws, which is a large positive number whether or
not any axis works. So the test window is not used here AT ALL. Every axis is
ranked inside the fit window by blocked cross-validation, one calendar year per
fold:

    for each fold year Y in 2018..2024:
        fit each sleeve's per-regime ON/OFF on the OTHER six years
        apply it to Y's trades
        delta(Y) = -(sum of size-free returns of the trades it refused)

`delta(Y) > 0` means the trades the rule turned away were, collectively, losers
in a year the rule never saw. That is the property that has to hold for a switch
to work at all, and it is measurable seven times inside the fit window without
touching 2025-2026.

RANKED ON SIGN CONSISTENCY FIRST, SIZE SECOND. An axis that helps in 6 of 7
folds is a better bet than one with a bigger mean driven by a single year --
cross-window sign consistency is what found the one L2 candidate that survived
when no t-stat could ([[absorption-is-the-best-l2-candidate]]). The mean delta
breaks ties.

THE FOLDS ARE NOT INDEPENDENT and the table says so. Volatility is persistent,
so 2020 and 2022 are each one macro event rather than 250 draws; seven folds is
seven observations, and an axis winning 5/7 is not a 5-of-7 coin flip. The
ranking is a way to spend the test window ONCE on the best available guess, not
evidence that the winner works.

PERSISTENCE IS REPORTED BESIDE EVERY AXIS, because the first attempt's real
defect was structural rather than statistical: a median-split EWMA flips every
4.4 days, so it labels "is today vol-ish" rather than any regime a strategy
could live inside. `mean_ep` is the mean episode length in days. An axis with a
high fold score and a 4-day mean episode is a day filter that happened to work.

THE AXES.

  ewma_q      EWMA of NQ daily returns, quantile cuts. The refuted baseline.
  ewma_hyst   the same series with TWO thresholds -- enter the fast state at
              the upper quantile, leave it at the lower. Directly targets the
              persistence defect: episodes lengthen because the label stops
              flipping every time the series brushes its own median.
  ewma_abs    absolute cuts, so "fast" means the same annualised vol in 2019 as
              in 2026 rather than the same percentile of whatever happened.
  vix_q       VIX daily close, quantile cuts. Forward-looking rather than a
              backward EWMA, and the standard regime proxy. `vix_1d` covers
              2017-01-03..2026-08-11.
  vix_hyst    VIX with hysteresis, same reasoning as ewma_hyst.
  trend_er    Kaufman efficiency ratio on NQ daily closes: |net move| over the
              summed absolute moves. A DIFFERENT axis rather than another
              estimator of volatility -- trending against choppy.
  ma200       NQ above or below its own 200-day mean. A directional state, the
              only axis here that is not a magnitude.

Every series is causal: the value offered for a day is built from days strictly
before it, the same discipline `daily_multipliers` follows.

    py -m sandbox.research.exness_regime_axes search
    py -m sandbox.research.exness_regime_axes search --buckets-only 2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_families as ef
from sandbox.research import exness_regime_switch as rs

OUT_PATH = os.path.join(ecs.RESULTS, "exness_regime_axes.json")

DAY = 86_400
FOLD_YEARS = (2018, 2019, 2020, 2021, 2022, 2023, 2024)

#: Same admission rules as `exness_regime_switch.assign`, restated here so the
#: search scores exactly the switch it is selecting for.
MIN_REGIME_TRADES = rs.MIN_REGIME_TRADES
MIN_FIT_TRADES = rs.MIN_FIT_TRADES


# --------------------------------------------------------------------------- #
# daily source series, all causal
# --------------------------------------------------------------------------- #

def nq_daily():
    """`{day: (close, high, low)}` for NQ, oldest first."""
    ef.resolve("nq", allow_stale=True)
    bars, _ctx = ecs._context("nq")
    out = {}
    for bar in bars:
        day = bar[ef.TS] // DAY
        if day not in out:
            out[day] = [bar[ef.C], bar[ef.H], bar[ef.L], bar[ef.TS]]
        else:
            row = out[day]
            row[1] = max(row[1], bar[ef.H])
            row[2] = min(row[2], bar[ef.L])
            if bar[ef.TS] >= row[3]:
                row[0], row[3] = bar[ef.C], bar[ef.TS]
    return {day: (row[0], row[1], row[2]) for day, row in out.items()}


def vix_daily():
    """`{day: close}` from `vix_1d`, shifted one day forward.

    The shift is the causality: a day's VIX close is not known until that day
    has ended, so it may only label the NEXT day. Without it the switch would
    read a print stamped after the entries it is gating.
    """
    rows = data.query("select timestamp, close from vix_1d order by timestamp")
    out = {}
    for stamp, close in rows:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        out[int(moment.timestamp()) // DAY + 1] = float(close)
    return out


def ewma_vol(daily, halflife):
    """`{day: annualised EWMA vol}`, the day's own return excluded."""
    lam = 0.5 ** (1.0 / halflife)
    out, variance, seen, previous = {}, 0.0, 0, None
    for day in sorted(daily):
        if seen >= ecs.VOL_MIN_DAYS and variance > 0.0:
            out[day] = math.sqrt(variance * 252.0)
        close = daily[day][0]
        if previous is not None and previous > 0 and close > 0:
            change = close / previous - 1.0
            variance = (change * change if seen == 0
                        else lam * variance + (1.0 - lam) * change * change)
            seen += 1
        previous = close
    return out


def efficiency_ratio(daily, lookback):
    """Kaufman ER over the `lookback` closes ending the day BEFORE each day.

    |last - first| / sum|step|. Near 1.0 is a clean directional move, near 0.0
    is chop that covered no ground.
    """
    days = sorted(daily)
    closes = [daily[day][0] for day in days]
    out = {}
    for index in range(lookback + 1, len(days)):
        window = closes[index - lookback - 1:index]
        moves = [abs(window[i + 1] - window[i]) for i in range(len(window) - 1)]
        total = sum(moves)
        if total > 0:
            out[days[index]] = abs(window[-1] - window[0]) / total
    return out


def above_ma(daily, lookback=200):
    """`{day: 1.0 if the prior close is above the prior `lookback` mean}`."""
    days = sorted(daily)
    closes = [daily[day][0] for day in days]
    out = {}
    running = 0.0
    for index in range(len(days)):
        if index >= lookback:
            running = sum(closes[index - lookback:index]) / lookback
            out[days[index]] = 1.0 if closes[index - 1] > running else 0.0
    return out


# --------------------------------------------------------------------------- #
# turning a series into a labeller
# --------------------------------------------------------------------------- #

def quantile_cuts(series, lo_day, hi_day, buckets):
    sample = sorted(v for day, v in series.items() if lo_day <= day < hi_day)
    if len(sample) < buckets * 30:
        return None
    return [sample[len(sample) * i // buckets] for i in range(1, buckets)]


def quantile_at(series, lo_day, hi_day, fraction):
    sample = sorted(v for day, v in series.items() if lo_day <= day < hi_day)
    if len(sample) < 60:
        return None
    return sample[min(len(sample) - 1, int(len(sample) * fraction))]


def plain_labeller(series, cuts):
    def label(day):
        value = series.get(day)
        if value is None:
            return None
        for index, cut in enumerate(cuts):
            if value < cut:
                return index
        return len(cuts)
    return label


def hysteresis_labeller(series, low, high):
    """Two-state label with a dead band, precomputed over the whole series.

    The state only changes when the series crosses OUT of the band, so brushing
    the middle does not restart an episode. This is the fix for the baseline's
    4.4-day mean episode.

    Precomputed rather than evaluated per call because the label at a day
    depends on the whole path before it, so it cannot be a pure function of the
    day's own value.
    """
    table, state = {}, 0
    for day in sorted(series):
        value = series[day]
        if value >= high:
            state = 1
        elif value <= low:
            state = 0
        table[day] = state
    return lambda day: table.get(day)


def build_axes(buckets_only=None):
    """Every (name, labeller) pair the search will try."""
    fit_lo = rs._stamp(rs.FIT_START) // DAY
    fit_hi = rs._stamp(rs.FIT_END) // DAY
    nq = nq_daily()
    axes = []

    def add(name, series, kind, buckets=2, fraction=None, cuts=None):
        if buckets_only and buckets != buckets_only and kind != "hyst":
            return
        if kind == "quantile":
            found = quantile_cuts(series, fit_lo, fit_hi, buckets)
            if found is None:
                return
            axes.append((name, plain_labeller(series, found), buckets, found))
        elif kind == "absolute":
            axes.append((name, plain_labeller(series, cuts),
                         len(cuts) + 1, list(cuts)))
        elif kind == "hyst":
            low = quantile_at(series, fit_lo, fit_hi, fraction[0])
            high = quantile_at(series, fit_lo, fit_hi, fraction[1])
            if low is None or high is None:
                return
            axes.append((name, hysteresis_labeller(series, low, high),
                         2, [low, high]))

    for halflife in (10, 20, 40):
        series = ewma_vol(nq, halflife)
        for buckets in (2, 3):
            add(f"ewma_q h{halflife} b{buckets}", series, "quantile", buckets)
        for fraction in ((0.35, 0.65), (0.25, 0.75)):
            add(f"ewma_hyst h{halflife} {fraction[0]:.2f}/{fraction[1]:.2f}",
                series, "hyst", fraction=fraction)

    series = ewma_vol(nq, 20)
    for cuts in ((0.15,), (0.20,), (0.25,), (0.15, 0.25)):
        label = "/".join(f"{c:.2f}" for c in cuts)
        add(f"ewma_abs h20 {label}", series, "absolute",
            buckets=len(cuts) + 1, cuts=cuts)

    try:
        vix = vix_daily()
    except Exception as exc:                       # noqa: BLE001
        print(f"  vix unavailable, skipping those axes: {exc}")
        vix = None
    if vix:
        for buckets in (2, 3):
            add(f"vix_q b{buckets}", vix, "quantile", buckets)
        for fraction in ((0.35, 0.65), (0.25, 0.75)):
            add(f"vix_hyst {fraction[0]:.2f}/{fraction[1]:.2f}", vix, "hyst",
                fraction=fraction)
        for cuts in ((20.0,), (16.0, 24.0)):
            label = "/".join(f"{c:.0f}" for c in cuts)
            add(f"vix_abs {label}", vix, "absolute",
                buckets=len(cuts) + 1, cuts=cuts)

    for lookback in (20, 60):
        series = efficiency_ratio(nq, lookback)
        for buckets in (2, 3):
            add(f"trend_er n{lookback} b{buckets}", series, "quantile", buckets)

    add("ma200", above_ma(nq, 200), "absolute", buckets=2, cuts=(0.5,))
    add("ma50", above_ma(nq, 50), "absolute", buckets=2, cuts=(0.5,))
    return axes


# --------------------------------------------------------------------------- #
# the cross-validated score
# --------------------------------------------------------------------------- #

def fit_assignment(trades, label, buckets, exclude_year=None):
    """`{sleeve: {regime: bool}}` from `trades`, optionally holding a year out.

    Identical admission rules to `exness_regime_switch.assign`: default ON below
    `MIN_REGIME_TRADES`, always-on below `MIN_FIT_TRADES` in total.
    """
    table = {}
    for sleeve, rows in trades.items():
        cells = {index: [] for index in range(buckets)}
        total = 0
        for stamp, value, year in rows:
            if year == exclude_year:
                continue
            index = label(stamp // DAY)
            if index is None:
                continue
            cells[index].append(value)
            total += 1
        if total < MIN_FIT_TRADES:
            table[sleeve] = {index: True for index in range(buckets)}
            continue
        table[sleeve] = {
            index: (True if len(cells[index]) < MIN_REGIME_TRADES
                    else sum(cells[index]) > 0)
            for index in range(buckets)}
    return table


def fold_delta(trades, label, table, year):
    """`-(sum of returns of the trades the rule refused)` in `year`.

    Positive means the refused trades lost money in a year the assignment never
    saw. Also returns how many trades were refused, because a delta of +0.01
    from two refusals is not the same finding as one from four hundred.
    """
    refused_sum, refused_n, total_n = 0.0, 0, 0
    for sleeve, rows in trades.items():
        cells = table.get(sleeve)
        if cells is None:
            continue
        for stamp, value, row_year in rows:
            if row_year != year:
                continue
            total_n += 1
            index = label(stamp // DAY)
            if index is None or cells.get(index, True):
                continue
            refused_sum += value
            refused_n += 1
    return -refused_sum, refused_n, total_n


def persistence(label, lo_day, hi_day):
    """`(mean episode length in days, episode count)` over the fit window."""
    runs, days, previous = 0, 0, None
    for day in range(lo_day, hi_day):
        current = label(day)
        if current is None:
            # Do NOT reset `previous` here. Weekends and holidays are unlabelled,
            # so resetting would end an episode every Friday and start a new one
            # every Monday -- which reported a sticky hysteresis state as a
            # 4.8-day "day filter" and made every axis look equally twitchy.
            continue
        days += 1
        if current != previous:
            runs += 1
        previous = current
    return (days / runs if runs else 0.0), runs


def load_trades(source):
    path = os.path.join(ecs.CACHE_DIR, f"regime_trades_{source}.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"no trade cache at {path}\n"
            f"run: py -m sandbox.research.exness_regime_switch dump "
            f"--source {source}")
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    fit_lo, fit_hi = rs._stamp(rs.FIT_START), rs._stamp(rs.FIT_END)
    out = {}
    for sleeve, rows in raw.items():
        kept = []
        for stamp, value in rows:
            if not fit_lo <= stamp < fit_hi:
                continue
            year = datetime.fromtimestamp(stamp, tz=timezone.utc).year
            kept.append((stamp, value, year))
        if kept:
            out[sleeve] = kept
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Rank regime DEFINITIONS by blocked cross-validation "
                    "inside 2018-2024. The test window is never touched.")
    parser.add_argument("command", choices=["search"])
    parser.add_argument("--source", default="ungated")
    parser.add_argument("--buckets-only", type=int, default=None)
    parser.add_argument("--out", default=OUT_PATH)
    args = parser.parse_args()

    trades = load_trades(args.source)
    total_trades = sum(len(v) for v in trades.values())
    print(f"\n{len(trades)} sleeves, {total_trades} fit-window trades, "
          f"folds {FOLD_YEARS[0]}..{FOLD_YEARS[-1]}")

    fit_lo = rs._stamp(rs.FIT_START) // DAY
    fit_hi = rs._stamp(rs.FIT_END) // DAY
    axes = build_axes(args.buckets_only)
    print(f"{len(axes)} regime definitions to score\n")

    results = []
    for name, label, buckets, cuts in axes:
        mean_ep, episodes = persistence(label, fit_lo, fit_hi)
        deltas, refused_total, positive = [], 0, 0
        for year in FOLD_YEARS:
            table = fit_assignment(trades, label, buckets, exclude_year=year)
            delta, refused, _total = fold_delta(trades, label, table, year)
            deltas.append(delta)
            refused_total += refused
            positive += 1 if delta > 0 else 0
        results.append({
            "axis": name, "buckets": buckets, "cuts": cuts,
            "folds_positive": positive, "folds": len(FOLD_YEARS),
            "mean_delta": statistics.fmean(deltas),
            "median_delta": statistics.median(deltas),
            "worst_fold": min(deltas), "best_fold": max(deltas),
            "refused_trades": refused_total,
            "refused_share": 100.0 * refused_total / total_trades,
            "mean_episode_days": mean_ep, "episodes": episodes,
            "deltas": deltas})

    results.sort(key=lambda row: (-row["folds_positive"], -row["mean_delta"]))

    print(f"  {'axis':<28} {'folds+':>7} {'mean d':>9} {'worst':>9} "
          f"{'best':>9} {'refused':>8} {'ref %':>7} {'mean ep':>8} {'eps':>5}")
    for row in results:
        flag = "  <- day filter" if row["mean_episode_days"] < 10 else ""
        print(f"  {row['axis']:<28} {row['folds_positive']:>4}/{row['folds']:<2} "
              f"{row['mean_delta'] * 100:>+8.2f}% {row['worst_fold'] * 100:>+8.2f}% "
              f"{row['best_fold'] * 100:>+8.2f}% {row['refused_trades']:>8} "
              f"{row['refused_share']:>6.1f}% {row['mean_episode_days']:>8.1f} "
              f"{row['episodes']:>5}{flag}")

    best = results[0]
    print(f"\nBEST BY SIGN CONSISTENCY  {best['axis']}  "
          f"{best['folds_positive']}/{best['folds']} folds, "
          f"mean delta {best['mean_delta'] * 100:+.2f}%, "
          f"mean episode {best['mean_episode_days']:.1f} days")
    print("  per-fold: " + "  ".join(
        f"{year} {delta * 100:+.2f}%"
        for year, delta in zip(FOLD_YEARS, best["deltas"])))
    print(f"\n  {len(axes)} definitions were tried. Seven folds of persistent "
          f"volatility are not seven independent draws, so this ranking is a "
          f"way to spend the test window ONCE -- not evidence the winner works.")

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"source": args.source, "folds": list(FOLD_YEARS),
                   "sleeves": len(trades), "fit_trades": total_trades,
                   "axes_tried": len(axes), "results": results},
                  handle, indent=1, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
