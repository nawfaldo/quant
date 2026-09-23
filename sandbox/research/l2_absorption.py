"""Absorption: fade aggressor flow that fails to move price. NQ, Exness Pro cost.

The one candidate out of `l2_wide_screen`'s 192 tests worth converting into a
strategy. Gross conditional edge, pooled and observation-weighted, at the 15m
aggregation window:

    horizon     2025            2026
    120m        +6.56 (t 2.19)  +5.08 (t 1.75)
    240m        +7.95 (t 2.28)  +8.59 (t 1.65)

Positive in both windows, in the direction declared *before* the screen ran, with
a monotone horizon curve -- the same rising-with-horizon signature as
`trade_delta`, which is the only NQ signal that has ever worked. Against a
1.092-point entry cost that is a 6-8x margin, which is why this one gets built
and the other eleven families do not.

THE MECHANISM. `absorption = delta / (|price_change| + 1) / trades`: aggressive
volume that arrived and did not move the tape. The thesis is exhaustion -- buyers
lifting offers into a wall that does not give way are buyers who will be on the
wrong side when they stop. So the signal is FADED, and that sign is not a
post-hoc choice; the screen's twelve directions were fixed in its own header
before any number was read.

WHY THIS IS NOT YET A RESULT. A conditional mean is not a PnL. The last L2
candidate to look like this reported +15.8 points at t 3.60, stable across 11
months and clean against a shuffled placebo, and every bit of it was an
estimator artifact that only died when it was pushed through `execution.resolve`.
That step is this module. It changes three things a conditional mean ignores:

  * **Occupancy.** 3,579 firing minutes are not 3,579 trades. A 120-minute hold
    means one position blocks the next two hours of signals, so the traded
    subset is roughly a tenth of the screened one and is selected by arrival
    time, not by strength.
  * **Cost.** Charged once at entry, at the live Pro spread.
  * **Path.** The screen measured close-to-close. A real position has a stop
    under it and gets flattened at the session close.

DECLARED BEFORE ANY NUMBER IS READ. Signal `absorption` at the 15m window,
`|z| >= 2.0` against a causal trailing 20 sessions, entry at the NEXT bar's open,
hold 120 minutes, stop `0.2 x daily ATR` carried over unchanged from the compiled
strategy, no target, one position at a time, session flatten. Nothing here is
swept. The 240m horizon is reported alongside 120m because the screen declared
both, and that is two cells, not a search.

Three controls, all mandatory: a random-direction null on identical entries, a
random-ENTRY null at matched trade count and hold (which tests whether the
signal's timing matters at all, not just its sign), and buy-and-hold.

    py -B -m sandbox.research.l2_absorption
    py -B -m sandbox.research.l2_absorption --record-trials
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math

import numpy as np

from sandbox import data, trials
from sandbox import execution as ex_mod
from sandbox.data import C, TS
from sandbox.execution import LONG, SHORT, Signal
from sandbox.paths import result_path
from sandbox.research import combined_book as cb
from sandbox.research import l2_wide_screen as screen
from sandbox.strategies import base as sbase
import sandbox.strategies.hourly_delta_reversal  # noqa: F401

STRATEGY = "NQ L2 Absorption"

#: Every one of these is inherited, not chosen here. The window/cut/z-basis come
#: from the screen that produced the candidate; the stop comes from the compiled
#: strategy; the holds are the two horizons the screen declared.
SIGNAL_WINDOW = 15
Z_CUT = 2.0
HOLDS = (120, 240)
K_STOP, ATR_DAYS = 0.2, 20
FALLBACK_STOP = 50.0

NULL_DRAWS = 200
SEED = 20260815

WINDOWS = {
    "2025": ("2025-01-01", "2026-01-01"),
    "2026": ("2026-01-01", "2026-07-17"),
}


def build_entries(frame, bars):
    """`[(bar index, side, ts)]` -- one candidate per firing minute.

    The signal is computed on minute `i`'s completed data and the position opens
    at bar `i + 1`'s open. Reading bar `i`'s own open would be entering on a bar
    whose close built the signal, which is the error that once produced a fake
    +12 points a trade.
    """
    signals = screen.build_signals(frame, SIGNAL_WINDOW)["absorption"]
    z = screen.causal_z(signals, frame["session"])
    direction = screen.FAMILIES["absorption"][1]

    index_by_ts = {bar[TS]: i for i, bar in enumerate(bars)}
    out = []
    for position, stamp in enumerate(frame["ts"]):
        value = z[position]
        if not np.isfinite(value) or abs(value) < Z_CUT:
            continue
        i = index_by_ts.get(int(stamp))
        if i is None or i + 1 >= len(bars):
            continue
        side = LONG if np.sign(value) * direction > 0 else SHORT
        out.append((i + 1, side, int(bars[i + 1][TS])))
    return out


def resolve_candidates(bars, entries, hold, atr, ex):
    """`(candidates, outcome)` -- both sides priced at every firing minute.

    Positions never interact in `execution.resolve`, so all candidates can be
    priced in one pass and the one-position-at-a-time rule applied afterwards as
    a selection over known exits. BOTH sides are resolved, not just the signalled
    one, because the direction null needs the counterfactual: what this same
    entry would have paid had the sign been the other way.
    """
    candidates = []
    for index, side, stamp in entries:
        unit = atr.get(bars[index][TS] // 86_400)
        stop = K_STOP * unit if unit else FALLBACK_STOP
        if stop > 0:
            candidates.append({"index": index, "side": side, "ts": stamp,
                               "stop": stop})

    outcome = {}
    for side in (LONG, SHORT):
        signals = [Signal(c["index"], side, c["stop"], 0.0, max_minutes=hold)
                   for c in candidates]
        for fill in ex_mod.resolve(bars, signals, ex):
            outcome.setdefault(fill.entry_ts, {})[side] = fill
    return candidates, outcome


def occupy(candidates, outcome):
    """Take candidates in time order, skipping any that a live position covers.

    This is where 3,579 firing minutes become a few hundred trades, and it is
    also where the traded subset stops being a random sample of the screened one:
    what survives is selected by arrival time, not by signal strength.
    """
    taken = []
    free_at = -1
    for candidate in sorted(candidates, key=lambda c: c["ts"]):
        if candidate["ts"] < free_at:
            continue
        fill = outcome.get(candidate["ts"], {}).get(candidate["side"])
        if fill is None:
            continue
        taken.append(fill)
        free_at = fill.exit_ts
    return taken


def summarise(fills, ex):
    if len(fills) < 2:
        return None
    points = np.array([f.points for f in fills])
    n = len(points)
    sd = float(points.std(ddof=1))
    total = sum(pnl for _ts, pnl in ex_mod.size(fills, ex))
    wins = points > 0
    gross = points + ex.entry_cost
    # The 2025 result is 80% five trades against a -9.34 median, so the mean is
    # reported next to estimators that a handful of outliers cannot carry. If
    # `drop_best_5` is negative the strategy is a lottery ticket, not an edge.
    ordered = np.sort(points)
    return {
        "trades": n,
        "points_per_trade": round(float(points.mean()), 4),
        "median_points": round(float(np.median(points)), 4),
        "drop_best_5": round(float(ordered[:-5].mean()), 4) if n > 6 else None,
        "top5_share_of_total": round(float(ordered[-5:].sum() / points.sum()), 3)
        if n > 6 and points.sum() > 0 else None,
        "gross_points_per_trade": round(float(gross.mean()), 4),
        "total_points": round(float(points.sum()), 1),
        "sd": round(sd, 2),
        "t": round(float(points.mean() / (sd / math.sqrt(n))), 3) if sd else None,
        "win_rate": round(float(wins.mean()), 4),
        "profit_factor": round(float(points[wins].sum() / -points[~wins].sum()), 3)
        if (~wins).any() and points[~wins].sum() < 0 else None,
        "return_pct": round(100.0 * total / ex.initial, 2),
    }


def direction_null(taken, outcome, rng):
    """Same entries and holds, random side. Tests whether the SIGN carries.

    Each draw re-flips every taken entry independently, reading the real
    counterfactual fill rather than negating the points -- a stop and a session
    flatten make the two sides asymmetric, so `-points` would be a fiction.
    """
    pairs = []
    for fill in taken:
        both = outcome.get(fill.entry_ts, {})
        if LONG in both and SHORT in both:
            pairs.append((both[LONG].points, both[SHORT].points))
    if len(pairs) < 2:
        return None
    values = np.array(pairs)
    draws = [float(values[np.arange(len(values)),
                          rng.integers(0, 2, size=len(values))].mean())
             for _ in range(NULL_DRAWS)]
    return _null_stats(draws)


def entry_null(taken, ambient, rng):
    """Matched-count entries at ARBITRARY RTH minutes, same long/short mix.

    This is the null that decides the study, and getting it wrong is easy: an
    earlier version of this function sampled from the other *firing* minutes,
    which only re-tests the occupancy rule and quietly assumes the thing being
    questioned. The signal fires 55-60% long inside two windows where NQ rose
    ~3,900 and ~3,500 points, so "be in the market, mostly long, with a wide stop"
    is a live explanation for the whole result and has to be priced.

    `ambient` is every RTH minute resolved on both sides at the same hold and
    stop. Each draw takes the traded long/short counts from that pool, so what
    is left over is the signal's timing and nothing else.
    """
    if len(taken) < 2 or not ambient:
        return None
    longs = sum(1 for f in taken if f.side == LONG)
    shorts = len(taken) - longs
    pools = {side: np.array([f.points for f in ambient.get(side, [])])
             for side in (LONG, SHORT)}
    if len(pools[LONG]) <= longs or len(pools[SHORT]) <= shorts:
        return None
    draws = []
    for _ in range(NULL_DRAWS):
        picked = []
        if longs:
            picked.append(rng.choice(pools[LONG], size=longs, replace=False))
        if shorts:
            picked.append(rng.choice(pools[SHORT], size=shorts, replace=False))
        draws.append(float(np.concatenate(picked).mean()))
    return _null_stats(draws)


def ambient_pool(bars, atr, hold, ex, lo, hi, stride=5):
    """Every `stride`-th RTH minute in the window, resolved on both sides.

    Strided rather than exhaustive only for cost: at stride 5 this is still
    ~15,000 reference positions per window, far more than the few hundred the
    null needs to draw from, and it inherits the same stop, hold and session
    flatten as the real trades so the comparison isolates timing.
    """
    indices = [i for i, bar in enumerate(bars)
               if lo <= bar[TS] < hi
               and screen.RTH_OPEN <= (bar[TS] % 86_400) // 60 < screen.RTH_CLOSE
               and i % stride == 0]
    out = {}
    for side in (LONG, SHORT):
        signals = []
        for i in indices:
            unit = atr.get(bars[i][TS] // 86_400)
            stop = K_STOP * unit if unit else FALLBACK_STOP
            if stop > 0:
                signals.append(Signal(i, side, stop, 0.0, max_minutes=hold))
        out[side] = ex_mod.resolve(bars, signals, ex)
    return out


def _null_stats(draws):
    if not draws:
        return None
    draws = np.array(draws)
    return {"mean": round(float(draws.mean()), 4),
            "sd": round(float(draws.std(ddof=1)), 4),
            "p95": round(float(np.percentile(draws, 95)), 4)}


def _versus(stats, null, label):
    if not stats or not null or not null["sd"]:
        return {}
    edge = stats["points_per_trade"] - null["mean"]
    return {f"{label}_null_mean": null["mean"],
            f"edge_vs_{label}": round(edge, 4),
            f"z_vs_{label}": round(edge / null["sd"], 3)}


def holdout_diagnostic(frame):
    """Why the Bookmap window cannot score this candidate, measured not assumed.

    The plan was to validate here: Bookmap has captured live since 2026-07-17 and
    nothing has read it. It does not work, and the reason is a property of the
    data rather than of the strategy.

    The two collectors are not on one scale. Bookmap reports 25% less |delta| and
    68% more `trade_count` per second than Databento, and `absorption` divides the
    first by the second, so its raw dispersion lands at 0.55x. The signal
    normalises against a trailing 20 sessions, which at the Bookmap boundary are
    all Databento -- so the z-score compresses, the fire rate falls from 3.76% to
    1.35%, and `|z| >= 2.0` stops selecting the population it was declared on. It
    is a different rule wearing the same threshold.

    This cannot be calibrated away: the tables do not overlap (Databento ends
    2026-07-16, Bookmap starts 07-17), so any rescaling factor would have to be
    fitted on the holdout it is meant to protect. And with 14 usable sessions and
    ~58 firings there is no power to spend even if the scale matched.

    So the window stays sealed. What unlocks it is source-consistent history --
    either enough Bookmap sessions to normalise a signal against its own
    collector (>= 40, so a 20-session warm-up leaves something to test on), or a
    period with both collectors running to measure the ratio honestly.
    """
    signal = screen.build_signals(frame, SIGNAL_WINDOW)["absorption"]
    z = screen.causal_z(signal, frame["session"])
    boundary = screen._epoch("2026-07-17")
    recent = (frame["ts"] >= screen._epoch("2026-05-01")) & (frame["ts"] < boundary)
    live = frame["ts"] >= boundary

    def clean(values):
        return values[np.isfinite(values)]

    return {
        "raw_sd_databento": round(float(clean(signal[recent]).std()), 6),
        "raw_sd_bookmap": round(float(clean(signal[live]).std()), 6),
        "z_sd_databento": round(float(clean(z[recent]).std()), 3),
        "z_sd_bookmap": round(float(clean(z[live]).std()), 3),
        "fire_rate_databento": round(float((np.abs(clean(z[recent])) >= Z_CUT).mean()), 4),
        "fire_rate_bookmap": round(float((np.abs(clean(z[live])) >= Z_CUT).mean()), 4),
        "bookmap_sessions": int(len(np.unique(frame["session"][live]))),
        "bookmap_firings": int((np.abs(clean(z[live])) >= Z_CUT).sum()),
        "verdict": "sealed -- cross-collector scale shift makes |z|>=2.0 a "
                   "different rule, and 14 sessions carry no power",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="l2_absorption_result.json")
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()

    account = cb.apply_cost_model("pro")
    base = sbase.get("Hourly Delta Reversal")
    ex = cb.nq_execution(base.execution)
    print(f"cost model {account!r}: {ex.entry_cost:.4f} points/entry")

    bars = data.load_bars("level_two", "nq")
    atr = data.atr_by_day(bars, ATR_DAYS)
    frame = screen.load_frame()
    entries = build_entries(frame, bars)
    print(f"{len(bars)} bars, {len(entries)} firing minutes at "
          f"absorption@{SIGNAL_WINDOW}m |z|>={Z_CUT}")

    rng = np.random.default_rng(SEED)
    report = {"strategy": STRATEGY, "cost_model": account,
              "entry_cost_points": round(ex.entry_cost, 4),
              "signal": {"family": "absorption", "window": SIGNAL_WINDOW,
                         "z_cut": Z_CUT, "direction": "fade"},
              "stop": {"k": K_STOP, "atr_days": ATR_DAYS}, "holds": {}}

    header = (f"{'variant':22}{'n':>6}{'net':>9}{'med':>8}{'-best5':>8}"
              f"{'top5%':>7}{'t':>6}{'pf':>7}{'zDir':>7}{'ambient':>9}{'zEnt':>7}"
              f"{'ret%':>8}")

    cells = 0
    for hold in HOLDS:
        candidates, outcome = resolve_candidates(bars, entries, hold, atr, ex)
        taken_all = occupy(candidates, outcome)
        print(f"\n{'=' * len(header)}")
        print(f"HOLD {hold}m: {len(candidates)} candidates -> {len(taken_all)} "
              f"trades after one-position-at-a-time")
        print(f"{'=' * len(header)}")
        print(header)
        print("-" * len(header))
        block = {"candidates": len(candidates), "trades": len(taken_all),
                 "windows": {}}

        for name, (start, end) in WINDOWS.items():
            lo, hi = screen._epoch(start), screen._epoch(end)
            taken = [f for f in taken_all if lo <= f.entry_ts < hi]
            stats = summarise(taken, ex)
            if stats is None:
                continue
            stats.update(_versus(stats, direction_null(taken, outcome, rng),
                                 "dir"))
            ambient = ambient_pool(bars, atr, hold, ex, lo, hi)
            stats.update(_versus(stats, entry_null(taken, ambient, rng), "ent"))
            stats["ambient_points_per_trade"] = round(float(np.mean(
                [f.points for side in ambient.values() for f in side])), 4)
            inside = [bar for bar in bars if lo <= bar[TS] < hi]
            stats["buy_and_hold_points"] = (round(inside[-1][C] - inside[0][C], 1)
                                            if len(inside) > 1 else None)
            block["windows"][name] = stats
            cells += 1
            print(f"{name + f' ({hold}m)':22}{stats['trades']:>6}"
                  f"{stats['points_per_trade']:>9.3f}"
                  f"{stats['median_points']:>8.2f}"
                  f"{stats['drop_best_5'] or 0:>8.2f}"
                  f"{stats['top5_share_of_total'] or 0:>7.2f}"
                  f"{stats['t'] or 0:>6.2f}{stats['profit_factor'] or 0:>7.3f}"
                  f"{stats.get('z_vs_dir', 0):>7.2f}"
                  f"{stats['ambient_points_per_trade']:>9.2f}"
                  f"{stats.get('z_vs_ent', 0):>7.2f}{stats['return_pct']:>8.2f}")
            print(f"{'':22}buy-and-hold same window {stats['buy_and_hold_points']:+.0f} pts"
                  f"   |   long {sum(1 for f in taken if f.side == LONG)}"
                  f" / short {sum(1 for f in taken if f.side == SHORT)}")
        report["holds"][str(hold)] = block

    report["holdout"] = holdout_diagnostic(frame)
    print(f"\nBookmap holdout (2026-07-17 onward): NOT SPENT")
    for key, value in report["holdout"].items():
        print(f"  {key:22} {value}")

    charged = trials.total(STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(STRATEGY, cells,
                                "absorption candidate through execution.resolve: "
                                "2 holds x 2 windows, no sweep")
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
