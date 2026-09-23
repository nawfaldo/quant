"""Cross-sectional long/short on the fifteen US stock CFDs, with a 2025-2026 holdout.

The family study (`stock_families_research`) asked each symbol in isolation
whether a price rule predicted its own next move, and answered no on all
fifteen: median gross edge +0.16bp a trade, and in-sample rank *anti*-predicted
out-of-sample rank at -0.605. This module asks the question that protocol
structurally cannot see -- not "will NVDA go up" but "will NVDA beat AMD" --
because ranking fifteen names against each other and trading the spread removes
the market factor those fifteen overwhelmingly share.

That matters here specifically. Twelve of the fifteen are megacap tech
([[stock-cfd-universe-is-quote-quality-limited]]), so a long-only or
single-name intraday rule is mostly betting on one factor wearing fifteen
costumes. A dollar-neutral book cancels it and leaves the dispersion.

DESIGN

Three families, each a ranking rule plus a holding period:

  ``intraday``   rank on the trailing R buckets' return inside the session,
                 hold H buckets, always flat by the session close
  ``overnight``  rank on the session's own return, hold from the last bucket's
                 open to the next session's first bucket open -- the classic
                 intraday-reversal-into-the-close-to-open-gap structure, and
                 the one place equity cross-section is known to live
  ``daily``      rank on the trailing D sessions' return, enter the next
                 session's open, hold M sessions open-to-open

Each with `direction` (momentum: long the winners; reversal: long the losers)
and `k`, the number of names a side. Grid is 410 cells -- three orders of
magnitude smaller than the family study's 71,100 a symbol, which is deliberate:
that budget is what made its in-sample numbers unreadable
([[coin-flip-control-beats-real-signals]]).

ENTRY TIMING. Signals are built from bucket t's close and enter at bucket
t+1's **open**; the leg return is open-to-open. Building a signal from a close
and filling at that same close is the +12-points-a-trade fiction in
[[entry-must-be-next-bar-open]].

REBALANCE. Non-overlapping: a cell holding H buckets rebalances every H
buckets. Overlapping tranches would quietly average away the variance that
makes the t-statistic honest.

SIZING IS NOT SEARCHED. Gross leverage is fixed at `GROSS` = 2.0x equity, split
equally across the 2k legs. It is not an axis. Searching sizing inside a
selection grid is how [[usoil-intraday-fails-twice]] produced a number that
meant nothing; leverage sensitivity is reported separately by `sensitivity`,
after selection, never inside it.

COSTS. Each leg pays its own measured spread once a round trip, the same
convention and the same per-symbol basis points as the family study. A
cross-sectional book turns over 2k legs a rebalance, so it is structurally
cost-heavy: at k=3 a single rebalance pays six spreads. The sweep runs
0/1/3/5/10/20/30bp on top and `breakeven_bp` reports where the holdout return
crosses zero. Exness charges a stock commission this repo cannot observe; if it
is 0.1% a side, read the 20bp column -- [[maroy-momentum-dies-on-real-costs]].

SCORING. The t-statistic is computed on the per-rebalance portfolio return
series, never on equal-weighted session means, which is the mistake recorded in
[[l2-discovery-found-nothing]].

BENCHMARK AND NULL. The benchmark is the equal-weight long-only basket of the
fifteen -- the market factor this book exists to remove
([[zero-is-the-wrong-backtest-baseline]]). The null shuffles *which* symbols go
long and short while holding k, the schedule and the costs fixed, so it prices
exactly the selection this module performs. 50 seeds, affordable because the
grid is small.

WINDOW. All fifteen must be present in a bucket for it to be ranked, and PLTR
starts 2020-09-30, so the aligned series opens then; in-sample runs 2021-01-01
through 2024-12-31 with the intervening months as lookback warm-up, and
2025-01-01 through 2026-08-07 is the untouched holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import statistics
from datetime import datetime, timezone

from sandbox import data
from sandbox.research import stock_families_research as sf

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

TS, O, H, L, C, V = range(6)

SYMBOLS = ("nvda", "aapl", "amzn", "msft", "googl", "mu", "avgo", "tsm",
           "tsla", "pltr", "orcl", "cvx", "amd", "jpm", "gs")

#: Reused verbatim from the family study so the two are directly comparable.
SPREAD_BPS = sf.SPREAD_BPS
COST_SWEEP = (0.0, 1.0, 3.0, 5.0, 10.0, 20.0, 30.0)

WARMUP_START = "2020-09-30"
IS_START = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp())
IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 8, tzinfo=timezone.utc).timestamp())

SESSION_OPEN_MINUTE = sf.SESSION_OPEN_MINUTE      # 09:30
SESSION_CLOSE_MINUTE = sf.SESSION_CLOSE_MINUTE    # 15:30, the flatten bucket
BARS_PER_SESSION = sf.BARS_PER_SESSION            # 13

#: Fixed, never searched. 1:20 leverage allows 20x; 2x is a deliberate long way
#: inside it so the margin leg cannot bind and distort a comparison.
GROSS = 2.0

MIN_REBALANCES = 100
SELECTION_DD_LIMIT = 20.0
MIN_IS_SHARPE = 0.5


# --------------------------------------------------------------------------- #
# aligned panel
# --------------------------------------------------------------------------- #


def panel():
    """`(timestamps, opens, closes)` aligned across all fifteen symbols.

    `opens[i][j]` is symbol j's open in bucket i. Only buckets where every
    symbol quotes are kept: a rank is meaningless if the universe changes size
    underneath it.
    """
    key = "xs_panel:" + data._table_fingerprint([f"{s}_1m" for s in SYMBOLS])

    def build():
        per = {}
        for symbol in SYMBOLS:
            sql = (
                "SELECT cast(timestamp as long) ts,first(open),max(high),"
                f"min(low),last(close),sum(volume) FROM {symbol}_1m "
                f"WHERE timestamp >= '{WARMUP_START}' "
                "SAMPLE BY 30m FILL(NONE) ALIGN TO CALENDAR"
            )
            rows = {}
            for row in data.query(sql):
                ts = int(row[0]) // 1_000_000
                minute = ts % 86_400 // 60
                if SESSION_OPEN_MINUTE <= minute <= SESSION_CLOSE_MINUTE:
                    rows[ts] = (float(row[1]), float(row[4]))
            per[symbol] = rows
        common = set.intersection(*(set(r) for r in per.values()))
        stamps = sorted(common)
        opens = [[per[s][ts][0] for s in SYMBOLS] for ts in stamps]
        closes = [[per[s][ts][1] for s in SYMBOLS] for ts in stamps]
        return {"ts": stamps, "open": opens, "close": closes}

    got = data._cached("xs_panel_15", key, build)
    return got["ts"], got["open"], got["close"]


def session_index(stamps):
    """`(day_of, first_bucket_of_day, last_bucket_of_day)` for each bucket."""
    day_of = [ts // 86_400 for ts in stamps]
    first, last = {}, {}
    for i, day in enumerate(day_of):
        first.setdefault(day, i)
        last[day] = i
    return day_of, first, last


# --------------------------------------------------------------------------- #
# signals -- every one returns a per-bucket score a symbol, or None
# --------------------------------------------------------------------------- #


def trailing_return(closes, lookback, day_of, first_of_day):
    """Return over the prior `lookback` buckets, never crossing a session."""
    n, m = len(closes), len(closes[0])
    out = []
    for i in range(n):
        back = i - lookback
        if back < first_of_day[day_of[i]]:
            out.append(None)
            continue
        out.append([closes[i][j] / closes[back][j] - 1.0 for j in range(m)])
    return out


def session_return(opens, closes, day_of, first_of_day):
    """Return from the session's first open to this bucket's close."""
    return [[closes[i][j] / opens[first_of_day[day_of[i]]][j] - 1.0
             for j in range(len(closes[0]))] for i in range(len(closes))]


def daily_return(closes, sessions, back, last_of_day):
    """Return over the prior `back` complete sessions, indexed by session."""
    out = {}
    for position, day in enumerate(sessions):
        if position < back:
            continue
        now = last_of_day[day]
        then = last_of_day[sessions[position - back]]
        out[day] = [closes[now][j] / closes[then][j] - 1.0
                    for j in range(len(closes[0]))]
    return out


# --------------------------------------------------------------------------- #
# portfolio
# --------------------------------------------------------------------------- #


def legs(score, k, direction, order=None):
    """The k longs and k shorts implied by `score`.

    `order` overrides the ranking with a supplied permutation, which is how the
    null control randomises *which* names are picked while leaving k, the
    schedule and the costs exactly as the real cell has them.
    """
    if score is None:
        return None
    m = len(score)
    if order is None:
        order = sorted(range(m), key=lambda j: score[j])
    if 2 * k > m:
        return None
    low, high = order[:k], order[-k:]
    return (high, low) if direction == "momentum" else (low, high)


LEG_COST = [SPREAD_BPS[s] / 1e4 for s in SYMBOLS]


def run(pairs, extra_bp=0.0, initial=1000.0):
    """Compound a list of `(long_legs, short_legs, entry_prices, exit_prices)`.

    Returns the equity curve's summary plus the per-rebalance return series the
    t-statistic is computed on.
    """
    equity = peak = initial
    drawdown = 0.0
    rets = []
    for longs, shorts, entry, exit_ in pairs:
        k = len(longs)
        weight = GROSS / (2.0 * k)
        gross = 0.0
        cost = 0.0
        for j in longs:
            gross += weight * (exit_[j] / entry[j] - 1.0)
            cost += weight * (LEG_COST[j] + extra_bp / 1e4)
        for j in shorts:
            gross -= weight * (exit_[j] / entry[j] - 1.0)
            cost += weight * (LEG_COST[j] + extra_bp / 1e4)
        step = gross - cost
        rets.append(step)
        equity *= (1.0 + step)
        if equity <= 0.0:
            equity = 0.0
            rets.extend([])
            break
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return equity, drawdown, rets


def summarize(equity, drawdown, rets, stamps, initial=1000.0):
    if not rets:
        return {"rebalances": 0, "return_pct": 0.0, "max_dd_pct": 0.0,
                "t": 0.0, "monthly_sharpe": 0.0, "positive_months": "0/0",
                "mean_bp": 0.0, "final": initial}
    mean = statistics.fmean(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    t = mean / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
    months = {}
    for ts, r in zip(stamps, rets):
        key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + r
    values = list(months.values())
    msd = statistics.pstdev(values) if len(values) > 1 else 0.0
    sharpe = (statistics.fmean(values) / msd * math.sqrt(12)) if msd > 0 else 0.0
    return {
        "rebalances": len(rets),
        "return_pct": round(100.0 * (equity / initial - 1.0), 2),
        "max_dd_pct": round(100.0 * drawdown, 2),
        "t": round(t, 2),
        "monthly_sharpe": round(sharpe, 3),
        "positive_months": f"{sum(1 for v in values if v > 0)}/{len(values)}",
        "mean_bp": round(1e4 * mean, 3),
        "final": round(equity, 2),
    }


# --------------------------------------------------------------------------- #
# families
# --------------------------------------------------------------------------- #


def build_pairs(family, params, panel_data, lo, hi, shuffle=None):
    """Every (longs, shorts, entry, exit) this cell takes inside [lo, hi)."""
    stamps, opens, closes = panel_data
    day_of, first_of, last_of = session_index(stamps)
    k, direction = params["k"], params["direction"]
    out, when = [], []

    if family == "daily":
        sessions = sorted(set(day_of))
        scores = daily_return(closes, sessions, params["lookback"], last_of)
        hold = params["hold"]
        position = 0
        while position + hold < len(sessions):
            day = sessions[position]
            if day not in scores:
                position += 1
                continue
            entry_i = first_of[sessions[position + 1]] if position + 1 < len(sessions) else None
            exit_i = (first_of[sessions[position + 1 + hold]]
                      if position + 1 + hold < len(sessions) else None)
            if entry_i is None or exit_i is None:
                break
            if not lo <= stamps[entry_i] < hi:
                position += hold
                continue
            picked = legs(scores[day], k, direction,
                          None if shuffle is None else shuffle(len(SYMBOLS)))
            if picked:
                out.append((picked[0], picked[1], opens[entry_i], opens[exit_i]))
                when.append(stamps[entry_i])
            position += hold
        return out, when

    if family == "overnight":
        sessions = sorted(set(day_of))
        scores = session_return(opens, closes, day_of, first_of)
        for position in range(len(sessions) - 1):
            day = sessions[position]
            signal_i = last_of[day] - 1
            entry_i = last_of[day]
            exit_i = first_of[sessions[position + 1]]
            if signal_i < first_of[day]:
                continue
            if not lo <= stamps[entry_i] < hi:
                continue
            picked = legs(scores[signal_i], k, direction,
                          None if shuffle is None else shuffle(len(SYMBOLS)))
            if picked:
                out.append((picked[0], picked[1], opens[entry_i], opens[exit_i]))
                when.append(stamps[entry_i])
        return out, when

    # intraday
    scores = trailing_return(closes, params["lookback"], day_of, first_of)
    hold = params["hold"]
    i = 0
    while i < len(stamps) - 1:
        day = day_of[i]
        entry_i, exit_i = i + 1, i + 1 + hold
        if exit_i > last_of[day] or day_of[entry_i] != day:
            i = last_of[day] + 1 if last_of[day] >= i else i + 1
            continue
        if scores[i] is None or not lo <= stamps[entry_i] < hi:
            i += 1
            continue
        picked = legs(scores[i], k, direction,
                      None if shuffle is None else shuffle(len(SYMBOLS)))
        if picked:
            out.append((picked[0], picked[1], opens[entry_i], opens[exit_i]))
            when.append(stamps[entry_i])
        i += hold
    return out, when


AXES = {
    "intraday": {"direction": ("momentum", "reversal"),
                 "lookback": (1, 2, 4, 6, 13),
                 "hold": (1, 2, 4, 13),
                 "k": (1, 2, 3, 4, 5)},
    "overnight": {"direction": ("momentum", "reversal"),
                  "k": (1, 2, 3, 4, 5)},
    "daily": {"direction": ("momentum", "reversal"),
              "lookback": (1, 2, 3, 5, 10, 20),
              "hold": (1, 2, 5),
              "k": (1, 2, 3, 4, 5)},
}


def cells(family):
    axes = AXES[family]
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


def evaluate(family, params, panel_data, lo, hi, extra_bp=0.0, shuffle=None,
             initial=1000.0):
    pairs, when = build_pairs(family, params, panel_data, lo, hi, shuffle)
    equity, drawdown, rets = run(pairs, extra_bp, initial)
    return summarize(equity, drawdown, rets, when, initial)


# --------------------------------------------------------------------------- #
# benchmark
# --------------------------------------------------------------------------- #


def benchmark(panel_data, lo, hi, initial=1000.0):
    """Equal-weight long-only basket of the fifteen, held open-to-open daily.

    This is the factor the long/short book exists to remove. Beating it is not
    required -- a market-neutral book with a lower return but no beta is still
    worth having -- but a long/short that merely tracks it has removed nothing.
    """
    stamps, opens, closes = panel_data
    day_of, first_of, last_of = session_index(stamps)
    sessions = sorted(set(day_of))
    equity = peak = initial
    drawdown = 0.0
    rets, when = [], []
    for position in range(len(sessions) - 1):
        a, b = first_of[sessions[position]], first_of[sessions[position + 1]]
        if not lo <= stamps[a] < hi:
            continue
        step = statistics.fmean([opens[b][j] / opens[a][j] - 1.0
                                 for j in range(len(SYMBOLS))])
        rets.append(step)
        when.append(stamps[a])
        equity *= (1.0 + step)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    return summarize(equity, drawdown, rets, when, initial)


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #


def output_path():
    return os.path.join(RESULTS, "stock_cross_sectional.json")


def seal(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(panel_data):
    picked = {}
    for family in AXES:
        ranked = []
        for params in cells(family):
            stat = evaluate(family, params, panel_data, IS_START, IS_END)
            if stat["rebalances"] < MIN_REBALANCES:
                continue
            if stat["max_dd_pct"] > SELECTION_DD_LIMIT:
                continue
            if stat["monthly_sharpe"] < MIN_IS_SHARPE:
                continue
            ranked.append((stat["monthly_sharpe"], params, stat))
        ranked.sort(key=lambda item: item[0], reverse=True)
        picked[family] = ({"params": ranked[0][1], "in_sample": ranked[0][2]}
                          if ranked else None)
        mark = "-" if not ranked else (
            f"{ranked[0][2]['return_pct']:+.1f}% mSh {ranked[0][0]:.2f} "
            f"dd {ranked[0][2]['max_dd_pct']:.1f}% "
            f"(of {len(ranked)} passing)")
        print(f"  {family:<10} {mark}", flush=True)
    return picked


def validate(panel_data, picked, seeds=50):
    out = {}
    for family, winner in picked.items():
        if winner is None:
            continue
        params = winner["params"]
        oos = evaluate(family, params, panel_data, IS_END, OOS_END)
        sweep = {}
        for bp in COST_SWEEP:
            sweep[f"{bp:.1f}"] = evaluate(family, params, panel_data,
                                          IS_END, OOS_END, extra_bp=bp)
        lo_bp, hi_bp = -30.0, 200.0
        for _ in range(40):
            mid = (lo_bp + hi_bp) / 2
            got = evaluate(family, params, panel_data, IS_END, OOS_END,
                           extra_bp=mid)
            if got["return_pct"] > 0:
                lo_bp = mid
            else:
                hi_bp = mid
        nulls = []
        for seed in range(seeds):
            rng = random.Random(10_000 + seed)

            def shuffle(m, rng=rng):
                order = list(range(m))
                rng.shuffle(order)
                return order

            nulls.append(evaluate(family, params, panel_data, IS_END, OOS_END,
                                  shuffle=shuffle)["return_pct"])
        nulls.sort()
        beat = sum(1 for value in nulls if oos["return_pct"] > value)
        out[family] = {
            "params": params,
            "in_sample": winner["in_sample"],
            "out_of_sample": oos,
            "cost_sweep_bp": {k: v["return_pct"] for k, v in sweep.items()},
            "breakeven_extra_bp": round(lo_bp, 2),
            "null_median_pct": round(statistics.median(nulls), 2),
            "null_p95_pct": round(nulls[int(0.95 * len(nulls))], 2),
            "null_percentile": round(100.0 * beat / len(nulls), 1),
        }
        print(f"  {family:<10} OOS {oos['return_pct']:+7.2f}%  "
              f"t {oos['t']:+.2f}  mSh {oos['monthly_sharpe']:+.2f}  "
              f"dd {oos['max_dd_pct']:.1f}%  n={oos['rebalances']}  "
              f"null p{out[family]['null_percentile']:.0f} "
              f"(med {out[family]['null_median_pct']:+.1f}%)", flush=True)
    return out


def sensitivity(panel_data, picked):
    """Leverage sensitivity, run *after* selection and never inside it."""
    global GROSS
    base = GROSS
    out = {}
    for family, winner in picked.items():
        if winner is None:
            continue
        row = {}
        for lever in (1.0, 2.0, 4.0, 8.0):
            GROSS = lever
            got = evaluate(family, winner["params"], panel_data, IS_END, OOS_END)
            row[f"{lever:.0f}x"] = {"return_pct": got["return_pct"],
                                    "max_dd_pct": got["max_dd_pct"]}
        out[family] = row
    GROSS = base
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("probe", "run"))
    parser.add_argument("--seeds", type=int, default=50)
    args = parser.parse_args()

    panel_data = panel()
    stamps = panel_data[0]
    span = (datetime.fromtimestamp(stamps[0], tz=timezone.utc),
            datetime.fromtimestamp(stamps[-1], tz=timezone.utc))
    print(f"panel: {len(stamps):,} aligned buckets across {len(SYMBOLS)} symbols "
          f"{span[0]:%Y-%m-%d}..{span[1]:%Y-%m-%d}")
    print(f"grid: {sum(len(cells(f)) for f in AXES)} cells across {len(AXES)} families")
    if args.phase == "probe":
        print("benchmark IS :", json.dumps(benchmark(panel_data, IS_START, IS_END)))
        print("benchmark OOS:", json.dumps(benchmark(panel_data, IS_END, OOS_END)))
        return

    print("\nselect (in sample 2021-01-01..2024-12-31)")
    picked = select(panel_data)
    print(f"\nvalidate (holdout 2025-01-01..2026-08-07, {args.seeds} null seeds)")
    validated = validate(panel_data, picked, args.seeds)
    payload = {
        "sealed": True,
        "protocol": {
            "universe": list(SYMBOLS),
            "gross_leverage": GROSS,
            "in_sample": "2021-01-01..2024-12-31",
            "out_of_sample": "2025-01-01..2026-08-07",
            "grid_cells": {f: len(cells(f)) for f in AXES},
            "entry": "signal from bucket t close, fill at bucket t+1 open",
            "costs": "each leg pays its own measured spread once a round trip",
            "null": "which symbols go long/short is shuffled; k, schedule and "
                    "costs held fixed",
            "selection_gate": f"mSharpe >= {MIN_IS_SHARPE}, dd <= "
                              f"{SELECTION_DD_LIMIT}%, >= {MIN_REBALANCES} rebalances",
        },
        "benchmark": {
            "in_sample": benchmark(panel_data, IS_START, IS_END),
            "out_of_sample": benchmark(panel_data, IS_END, OOS_END),
        },
        "families": validated,
        "leverage_sensitivity": sensitivity(panel_data, picked),
    }
    seal(payload, output_path())
    print(f"\nsealed -> {output_path()}")


if __name__ == "__main__":
    main()
