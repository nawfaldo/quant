"""Monte Carlo for the canon `exness_combined_strategies` book.

WHY THIS IS NOT A RETURNS BOOTSTRAP. The obvious Monte Carlo -- take the book's
daily return stream, resample it, compound -- has already been measured wrong on
this book. Summing per-sleeve daily streams hid half the drawdown
([[blend-model-understates-portfolio-drawdown]]), because the sleeves share one
compounding balance, size off it at entry, and can be refused outright by the
broker's minimum lot when it is small. None of that survives a return stream.
Every path here is replayed through `exness_combined_strategies.replay` itself:
the same sizing, the same shared balance, the same mark-to-market grid, the same
lot floors, the same broker minimums. A path costs seconds rather than nothing,
and that is the right trade.

THE UNIT IS A BLOCK OF CALENDAR TIME, REPLAYED IN SEQUENCE. The window is tiled
into fixed-length blocks (a fortnight by default). A path is an ordered list of
those blocks; each is replayed AT ITS OWN DATES against its OWN real prices,
starting from whatever equity the previous block left behind. So inside a block
every fill, stop, mark and simultaneity is the one that really happened, and the
only thing the simulation changes is which blocks occur and in what order.

WHY NOT A DAY-LEVEL BOOTSTRAP -- THIS WAS TRIED AND IT IS BIASED. The first
version of this module resampled calendar DAYS with replacement and left each
drawn day at its real timestamp, so that a day drawn twice contributed its
positions TWICE AT THE SAME INSTANT. That doubles the exposure carried on those
days against an unchanged balance, and it does not widen the drawdown
distribution so much as move it: median simulated MTM drawdown came out at
24.70% against a realised 12.73%, with the realised path at the 0th percentile
of its own resampling distribution on both drawdown measures. The level of
return was unaffected -- multiplicity averages to one -- which is exactly what
makes the artefact easy to miss if only returns are read.

Chaining blocks in SEQUENCE is the fix, and it is a fix rather than a patch: a
block drawn twice now lands at two different points of the equity path instead
of on top of itself, so exposure is never stacked and the path length is
preserved exactly. `verify` exists to prove the machinery is sound -- it chains
the blocks in their REAL order and checks the result reproduces the single
full-window replay.

TWO QUESTIONS, TWO METHODS, DELIBERATELY SEPARATE. They are different risks and
reporting one number for both would hide each.

  `boot`   BLOCK BOOTSTRAP -- estimation risk. Blocks drawn WITH replacement.
           "How much of the result is this particular sample of fortnights, and
           how much is the process?" Some stretches appear twice, some not at
           all, and the order is random too, so this is the wider of the two.

  `order`  PERMUTATION -- sequence risk alone. The same blocks, every one
           exactly once, in a random order. The book is not scale-free in
           equity: the broker's minimum lot rounds small requests UP and lot
           granularity floors them down, so a bad stretch arriving first at $400
           is a materially different book from the same stretch arriving at
           $3,000 ([[small-balance-hides-drawdown-by-dropping-trades]],
           [[cold-start-at-400-is-the-binding-test]]). Holding the multiset
           fixed isolates arrival order as the only moving part.

WHAT NEITHER METHOD CAN TELL YOU. Both resample the realised history, so both
inherit its selection. The canon members were chosen on this window, several of
them explicitly for their behaviour in the book's own losing months. A Monte
Carlo over selected history measures the dispersion of a fitted object, not
whether the edge is real. What follows is a RISK statement -- how bad a path
this process can produce -- and not evidence of edge. Read
[[selection-gate-manufactures-drawdown-and-consistency]] before quoting the
upper tail at anybody.

Usage:

    py -m sandbox.research.exness_combined_montecarlo cache
    py -m sandbox.research.exness_combined_montecarlo verify
    py -m sandbox.research.exness_combined_montecarlo run
    py -m sandbox.research.exness_combined_montecarlo run --method boot \
       --paths 2000 --block-days 7 --workers 12
"""

import argparse
import json
import math
import multiprocessing
import os
import pickle
import random
import statistics
import time
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_families as ef

RESULTS = ecs.RESULTS
CACHE = os.path.join(ecs.CACHE_DIR, "montecarlo_canon_inputs.pkl")
OUT_PATH = os.path.join(RESULTS, "exness_combined_montecarlo.json")

#: Block length in days. A block bootstrap and not an IID one because daily book
#: P&L is not independent -- volatility clusters and several sleeves trade the
#: same regime -- and a block shorter than that dependence would break the runs
#: apart and understate the drawdown tail, which is the one number this exercise
#: exists to produce.
#:
#: A fortnight is the compromise. Longer blocks preserve more dependence but
#: leave fewer resampling units (43 at 14 days, 20 at a month, and a bootstrap
#: over 20 units cannot say much about a 5% tail). Shorter blocks give more
#: units but cut more multi-day positions at boundaries, which costs the swing
#: sleeve its marking ([[swing-donchian-is-load-bearing-for-drawdown]]).
#: `verify` measures that boundary cost directly, so the choice is checkable
#: rather than asserted.
BLOCK_DAYS = 14

#: Drawdown levels reported as exceedance probabilities. 15% is the limit the
#: canon risk setting was sized against ([[canon-risk-setting-is-off-the-frontier]]);
#: the rest map the tail beyond it.
DD_LEVELS = (10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0)

#: Equity levels, as a fraction of the starting balance, whose first touch is
#: counted. A $400 account that halves has not blown up, but it has fallen under
#: the minimum lot on several sleeves and is a different book from then on
#: ([[four-hundred-dollars-selects-the-sleeves-for-you]]).
RUIN_LEVELS = (0.75, 0.50, 0.25)

PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

def prepare():
    """The canon book's members, trade logs, bars and specs, ready for replay.

    Read from the sealed `exness_combined_strategies.json` rather than from
    `BOOK`, for the reason `yearly` does: the sealed file is what the reported
    numbers were produced from, and regenerating membership here would let this
    module and its subject drift apart silently.

    THE CONTEXTS ARE THROWN AWAY AND ONLY `cfg` IS KEPT. A full family context
    carries every indicator series it precomputed -- 1,062 MB across the
    twenty-five sleeves -- and `replay` reads exactly one thing out of it,
    `ctx["cfg"]`, which is 4 KB. Spawned workers hold a private copy of whatever
    they are handed ([[multi-symbol-select-leaks-into-one-worker]]), so keeping
    the rest would put 13 GB on a 16 GB machine and take it down.
    """
    with open(ecs.OUT_PATH, encoding="utf-8") as handle:
        members = json.load(handle)["members"]
    logs, bars_by, ctx_by = {}, {}, {}
    for member in members:
        key = f"{member['symbol']}:{member['family']}"
        if "params" in member:
            member["params"] = ecs._retuple(member["params"])
        if key in ecs.EXTERNAL:
            logs[key] = ecs.external_trades(
                key, window=("2025-01-01", ecs.CANON_DATA_END))
            continue
        ef.resolve(member["symbol"], allow_stale=True)
        _result, log, bars, ctx = ecs.sleeve_trades(member)
        logs[key] = log
        bars_by[member["symbol"]] = bars
        # `size` reads `ctx["symbol"]` too, on the MIN_LOT_ALWAYS branch that
        # only fires once the balance is small enough for a broker-margin
        # check; it is one string and dropping it was a latent KeyError.
        ctx_by[member["symbol"]] = {"cfg": ctx["cfg"],
                                    "symbol": ctx.get("symbol", member["symbol"])}
    return {"members": members, "logs": logs,
            "bars_by": bars_by, "ctx_by": ctx_by}


def cache(force=False):
    """Build the worker payload once so paths do not each pay a minute for it."""
    if os.path.exists(CACHE) and not force:
        print(f"cache present: {CACHE} "
              f"({os.path.getsize(CACHE) / 1e6:.0f} MB)")
        return
    started = time.time()
    payload = prepare()
    os.makedirs(ecs.CACHE_DIR, exist_ok=True)
    tmp = f"{CACHE}.tmp"
    with open(tmp, "wb") as handle:
        pickle.dump(payload, handle, protocol=5)
    os.replace(tmp, CACHE)                 # atomic; a killed run leaves no half
    print(f"wrote {CACHE} ({os.path.getsize(CACHE) / 1e6:.0f} MB) "
          f"in {time.time() - started:.0f}s")


def load():
    if not os.path.exists(CACHE):
        cache()
    with open(CACHE, "rb") as handle:
        return pickle.load(handle)


def pin_external_window(span=None):
    """Serve external price grids for `span` whatever window is asked for.

    `replay` derives the window it requests from its own `lo`/`hi`, which is
    right for a full run and WRONG for the block replays chained here, in two
    separate ways.

    Correctness first: `daily_multipliers` needs thirty days of warm-up before
    the volatility throttle moves off 1.0, so a fortnight-long price grid would
    leave every external sleeve -- four of the twenty-five, and most of the
    trades -- permanently unthrottled, and a block replayed alone would size
    differently from the same block inside the whole book.

    Speed second, and it is the difference between a feasible study and an
    infeasible one: `_external_cache` memoises on `(market, window)`, so a fresh
    window per block misses the memo, re-reads QuestDB and writes a new cache
    file. Pinned, a block replay costs 0.4s; unpinned it cost 4.2s, ten times
    over, for exactly the same answer.

    Safe because `replay` filters its revaluation grid to `lo <= ts < hi`
    afterwards, so the extra stamps are carried but never stepped on; and
    because for a full-window replay the pinned span IS the requested one, which
    makes the patch a no-op there by construction.
    """
    span = ("2025-01-01", ecs.CANON_DATA_END) if span is None else span
    original = ecs.external_prices

    def patched(markets, window=None):
        return original(markets, window=span)

    ecs.external_prices = patched


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #

def blocks_of(block_days=BLOCK_DAYS, lo=None, hi=None):
    """`[(lo, hi)]` tiling the window in fixed-length blocks.

    The last block is short and is left that way rather than merged into its
    neighbour. Both methods keep the block LIST fixed and vary only which
    entries of it are used, so a short block is equally present in every path
    and cannot bias a comparison; merging it would instead invent a longer
    stretch the book never traded.
    """
    lo = ef.IS_END if lo is None else lo
    hi = ef.OOS_END if hi is None else hi
    span = block_days * 86_400
    return [(start, min(start + span, hi)) for start in range(lo, hi, span)]


#: Marking inputs shared across every block replay in this process.
#:
#: The price grid, the EWMA vol multipliers and the revaluation stamp list do
#: not depend on the window, but `replay` rebuilds all three per call, and a
#: path is 43 calls on the canon window and 121 on 2022-2026. Measured on the
#: latter: a 14-day block cost 0.81s against 15.7s for the whole 4.7 years, and
#: a profile put 60% of that block in `daily_multipliers`, `external_prices` and
#: the `price_at` comprehension -- all three of them whole-window work.
#:
#: Safe only because `pin_external_window` fixes the external span first; see
#: the precondition on `replay`'s own `marking_cache` note. Keyed per process
#: rather than passed around because every worker replays the same bars for its
#: whole life.
_MARKING = {}


def run_book(state, lo=None, hi=None, initial=None):
    """One replay at canon settings. The only place those settings are named."""
    members = state["members"]
    return ecs.replay(
        members, state["logs"], state["bars_by"], state["ctx_by"],
        scale=ecs.SLEEVE_SCALE, sizing_cap=ecs.sizing_caps(members),
        risk_scale=ecs.CANON_RISK_SCALE, gross_cap=ecs.CANON_GROSS_CAP,
        fair_cap=ecs.FAIR_CAP,
        initial=ecs.CANON_INITIAL if initial is None else initial,
        lo=lo, hi=hi, marking_cache=_MARKING)


def chain(state, order, initial):
    """Replay `order`'s blocks back to back against one compounding balance.

    Each block is replayed AT ITS OWN DATES against its own real prices, so
    every fill, every stop and every mark inside it is the one that really
    happened. The only thing a path changes is the equity a block opens with --
    which is the whole question, because lot floors and lot granularity make
    this book's behaviour depend on the LEVEL of the balance and not only on its
    returns.

    Positions still open when a block ends are settled at their true P&L by
    `replay` itself, so no trade is truncated or double counted at a boundary.
    What a boundary does cost is the MARKING of a position that spanned it: its
    excursion on the far side is not seen, which understates mark-to-market
    drawdown very slightly. `verify` measures that cost rather than assuming it
    away.

    The closed-equity drawdown has to be chained by hand, because a block's own
    `max_dd_pct` is measured from a peak inside that block and knows nothing
    about the high-water mark the path reached earlier.
    """
    equity, elapsed = initial, 0.0
    curve, returns, sleeves = [], [], {}
    trades = skipped = refused = 0
    closed_peak, closed_dd = initial, 0.0
    for lo, hi in order:
        if equity <= 0:                    # ruined; the remaining blocks cannot
            returns.append(0.0)            # trade, and must not be skipped over
            elapsed += hi - lo
            curve.append((elapsed, equity))
            continue
        book = run_book(state, lo=lo, hi=hi, initial=equity)
        for stamp, value in book["marked"]:
            curve.append((elapsed + (stamp - lo), value))
        elapsed += hi - lo
        curve.append((elapsed, book["final"]))
        returns.append(100.0 * (book["final"] - equity) / equity)
        for name, row in book["by_sleeve"].items():
            sleeves[name] = sleeves.get(name, 0.0) + row["pnl"]
        trades += book["trades"]
        skipped += sum(book["below_broker_minimum"].values())
        refused += book["refused_by_gross_cap"]
        for _stamp, value in book["curve"]:
            closed_peak = max(closed_peak, value)
            if closed_peak > 0:
                closed_dd = max(closed_dd, (closed_peak - value) / closed_peak)
        equity = book["final"]
    return ({"final": equity, "max_dd_pct": 100.0 * closed_dd,
             "trades": trades, "below_broker_minimum": skipped,
             "refused_by_gross_cap": refused,
             "by_sleeve": sleeves}, returns, curve)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #

def underwater(curve):
    """Longest run below the running high-water mark, in days.

    Takes `(elapsed_seconds, value)` rather than timestamps because a path's
    blocks are out of calendar order: what is measured is time spent under
    water, not the dates it happened on.
    """
    peak, start, worst = None, None, 0.0
    for elapsed, value in curve:
        if peak is None or value >= peak:
            peak, start = value, elapsed
        elif start is not None:
            worst = max(worst, elapsed - start)
    return worst / 86_400.0


def drawdown_of(values):
    """Max fractional fall from a running peak, and the lowest level reached."""
    peak, worst, low = None, 0.0, None
    for value in values:
        peak = value if peak is None or value > peak else peak
        low = value if low is None or value < low else low
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return 100.0 * worst, low


MONTH_SECONDS = 30 * 86_400


def month_bins(curve, span=MONTH_SECONDS):
    """Per-month returns along the path's OWN elapsed clock, and the longest
    run of losing ones.

    WHY ELAPSED TIME AND NOT CALENDAR MONTHS. A bootstrap path chains fortnights
    out of order, so "2024-10" does not exist on it -- the same calendar month
    can appear twice or not at all, and grouping by date would count a month the
    path never traded. Binning the path's own elapsed seconds into 30-day
    buckets is the analogue that survives reordering: it asks the question
    monthly consistency actually asks -- how often does a month of trading end
    up -- without pretending the dates mean anything.

    The bins are therefore 30 days, not calendar months, so the count differs
    slightly from `exness_combined_strategies.monthly`, which groups realised
    trades by real month. The two are read together, never interchanged.

    Returns `([pct_return_per_bin], longest_negative_run)`.
    """
    if not curve:
        return [], 0
    start = curve[0][0]
    marks = {}
    for elapsed, value in curve:
        marks[int((elapsed - start) // span)] = value
    if not marks:
        return [], 0
    opening = curve[0][1]
    out = []
    previous = opening
    for index in range(max(marks) + 1):
        close = marks.get(index, previous)
        out.append(100.0 * (close - previous) / previous if previous > 0
                   else 0.0)
        previous = close
    longest = run = 0
    for value in out:
        run = run + 1 if value < 0 else 0
        longest = max(longest, run)
    return out, longest


def metrics(book, returns, curve, initial, block_days):
    """One path reduced to the numbers the report quantiles and ranks.

    `mar` divides return by the MARKED drawdown, not the closed one. The closed
    figure is the optimistic one on an intraday book and the gap between them is
    the open risk ([[engine-drawdown-is-mark-to-market]]); a ratio built on it
    would flatter every path by the same amount and rank none of them better.

    The Sharpe is annualised off BLOCK returns, not monthly ones, because a
    permuted path has no calendar months left to group by. It is in the same
    units as the monthly Sharpe the book reports elsewhere -- both are a mean
    over a standard deviation, scaled to a year -- but it is not the same
    number, and the reference line is recomputed the same way so the comparison
    is like for like rather than approximately so.
    """
    values = [value for _elapsed, value in curve]
    mtm_dd, low = drawdown_of(values)
    per_year = 365.0 / block_days
    sharpe = 0.0
    if len(returns) > 1 and statistics.stdev(returns) > 0:
        sharpe = (statistics.fmean(returns) / statistics.stdev(returns)
                  * math.sqrt(per_year))
    ret = 100.0 * (book["final"] - initial) / initial
    months, streak = month_bins(curve)
    positive_months = sum(1 for v in months if v > 0)
    return {
        "return_pct": ret,
        "final": book["final"],
        "mtm_dd_pct": mtm_dd,
        "closed_dd_pct": book["max_dd_pct"],
        "mar": ret / mtm_dd if mtm_dd > 0 else float("nan"),
        "ann_sharpe": sharpe,
        "blocks_positive": sum(1 for v in returns if v > 0),
        "blocks": len(returns),
        "worst_block_pct": min(returns) if returns else 0.0,
        "best_block_pct": max(returns) if returns else 0.0,
        # MONTHLY CONSISTENCY ON A REORDERED PATH; see `month_bins`.
        "positive_months": positive_months,
        "months": len(months),
        "positive_month_share": (100.0 * positive_months / len(months)
                                 if months else 0.0),
        "worst_month_pct": min(months) if months else 0.0,
        "longest_losing_months": streak,
        "trades": book["trades"],
        "under_water_days": underwater(curve),
        "min_equity_ratio": (low / initial) if low is not None else 1.0,
        "below_broker_minimum": book["below_broker_minimum"],
        "refused_by_gross_cap": book["refused_by_gross_cap"],
        "by_sleeve": dict(book["by_sleeve"]),
    }


def reference(state, blocks, block_days):
    """The realised path, chained in its REAL order and reduced identically.

    This and not the single full-window replay is what the percentile ranks are
    measured against. The two differ only by boundary marking, and comparing a
    path built one way against a reference built the other would charge that
    difference to the simulation.
    """
    book, returns, curve = chain(state, blocks, ecs.CANON_INITIAL)
    return metrics(book, returns, curve, ecs.CANON_INITIAL, block_days)


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #

_STATE = None
_BLOCKS = None
_BLOCK_DAYS = BLOCK_DAYS


def _init_worker(cache_path, block_days):
    global _STATE, _BLOCKS, _BLOCK_DAYS
    with open(cache_path, "rb") as handle:
        _STATE = pickle.load(handle)
    _BLOCK_DAYS = block_days
    pin_external_window()
    _BLOCKS = blocks_of(block_days)
    # One throwaway replay so the external price grids are memoised before the
    # timed work starts; otherwise the first path on every worker pays for it
    # and the progress line lies about the rate.
    run_book(_STATE, lo=_BLOCKS[0][0], hi=_BLOCKS[0][1])


def _path(seed, with_replacement):
    rng = random.Random(seed)
    if with_replacement:
        order = [rng.choice(_BLOCKS) for _ in _BLOCKS]
    else:
        order = list(_BLOCKS)
        rng.shuffle(order)
    book, returns, curve = chain(_STATE, order, ecs.CANON_INITIAL)
    return metrics(book, returns, curve, ecs.CANON_INITIAL, _BLOCK_DAYS)


def _path_boot(seed):
    return _path(seed, True)


def _path_order(seed):
    return _path(seed, False)


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #

def quantile(values, pct):
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def rank_of(value, values):
    """Where the realised outcome sits inside the simulated distribution.

    Printed for every headline metric because it is the one thing a Monte Carlo
    can say about luck: a realised return at the 95th percentile of its own
    resampling distribution is a good draw from this process, not a typical one,
    and is not the number to carry forward as an expectation.
    """
    if not values:
        return float("nan")
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return 100.0 * (below + 0.5 * equal) / len(values)


ROWS = (
    ("return %", "return_pct", "{:>10.1f}"),
    ("final equity $", "final", "{:>10.0f}"),
    ("MTM dd %", "mtm_dd_pct", "{:>10.2f}"),
    ("closed dd %", "closed_dd_pct", "{:>10.2f}"),
    ("MAR (ret/MTM dd)", "mar", "{:>10.2f}"),
    ("ann. Sharpe", "ann_sharpe", "{:>10.2f}"),
    ("blocks positive", "blocks_positive", "{:>10.1f}"),
    ("worst block %", "worst_block_pct", "{:>10.2f}"),
    ("best block %", "best_block_pct", "{:>10.2f}"),
    # Monthly consistency as a DISTRIBUTION rather than a realised-path fact.
    # The bins are 30 days of the path's own elapsed clock, not calendar
    # months, because a permuted path has no calendar left -- see `month_bins`.
    ("months positive", "positive_months", "{:>10.1f}"),
    ("months positive %", "positive_month_share", "{:>10.1f}"),
    ("worst month %", "worst_month_pct", "{:>10.2f}"),
    ("longest losing mo", "longest_losing_months", "{:>10.1f}"),
    ("under water days", "under_water_days", "{:>10.1f}"),
    ("min equity / start", "min_equity_ratio", "{:>10.2f}"),
    ("trades", "trades", "{:>10.0f}"),
    ("below broker min", "below_broker_minimum", "{:>10.0f}"),
)


def summarise(label, paths, actual, elapsed):
    print(f"\n{'=' * 97}")
    print(label)
    print(f"{len(paths)} paths in {elapsed / 60:.1f} min")
    print("=" * 97)
    print(f"{'metric':<20}"
          + "".join(f"{'p' + str(p):>10}" for p in PERCENTILES)
          + f"{'ACTUAL':>10}{'rank':>7}")
    payload = {}
    for name, key, spec in ROWS:
        values = [p[key] for p in paths if p[key] == p[key]]     # drop NaN
        rank = rank_of(actual[key], values)
        print(f"{name:<20}"
              + "".join(spec.format(quantile(values, p)) for p in PERCENTILES)
              + spec.format(actual[key]) + f"{rank:>6.0f}%")
        payload[key] = {"percentiles": {str(p): round(quantile(values, p), 4)
                                        for p in PERCENTILES},
                        "mean": round(statistics.fmean(values), 4),
                        "actual": round(actual[key], 4),
                        "actual_rank_pct": round(rank, 1)}

    print("\ntail risk")
    returns = [p["return_pct"] for p in paths]
    dds = [p["mtm_dd_pct"] for p in paths]
    tail = {"p_negative": 100.0 * sum(1 for v in returns if v <= 0) / len(paths)}
    print(f"  P(book ends below its start)          "
          f"{tail['p_negative']:>7.1f}%")
    for level in DD_LEVELS:
        share = 100.0 * sum(1 for v in dds if v > level) / len(paths)
        tail[f"p_mtm_dd_over_{level:g}"] = share
        print(f"  P(MTM drawdown > {level:>4.0f}%)               {share:>7.1f}%")
    # Expected shortfall on the drawdown, which is what gets asked for once the
    # exceedance probability is non-zero: not "how often past 25%" but "how bad
    # when it does go past".
    cut = quantile(dds, 95)
    tail["cvar95_mtm_dd"] = statistics.fmean([v for v in dds if v >= cut])
    print(f"  CVaR(95) of MTM drawdown              "
          f"{tail['cvar95_mtm_dd']:>7.2f}%")
    for level in RUIN_LEVELS:
        share = 100.0 * sum(1 for p in paths
                            if p["min_equity_ratio"] <= level) / len(paths)
        tail[f"p_touch_{level:g}"] = share
        print(f"  P(equity ever <= {level:>4.0%} of start)       {share:>7.1f}%")
    # A path that ever drops below the minimum lot stops being the book that was
    # tested: sleeves start refusing entries and the mix concentrates into the
    # cheap ones, pro-cyclically ([[minimum-lot-de-diversifies-pro-cyclically]]).
    unaffordable = 100.0 * sum(
        1 for p in paths if p["below_broker_minimum"] > 0) / len(paths)
    tail["p_any_below_broker_min"] = unaffordable
    print(f"  P(any entry refused: below broker min){unaffordable:>7.1f}%")
    tail["worst_return_pct"] = min(returns)
    tail["worst_dd_pct"] = max(dds)
    print(f"  worst single path                     {min(returns):>+7.1f}%  "
          f"MTM dd {max(dds):.1f}%")

    print("\nper sleeve, P&L $ across paths")
    print(f"{'sleeve':<28}{'p5':>9}{'p25':>9}{'median':>9}{'p75':>9}{'p95':>9}"
          f"{'ACTUAL':>9}{'P(<0)':>8}")
    sleeves = {}
    for name in sorted(actual["by_sleeve"],
                       key=lambda n: -actual["by_sleeve"][n]):
        values = [p["by_sleeve"].get(name, 0.0) for p in paths]
        negative = 100.0 * sum(1 for v in values if v < 0) / len(values)
        print(f"{name:<28}"
              + "".join(f"{quantile(values, p):>9.0f}"
                        for p in (5, 25, 50, 75, 95))
              + f"{actual['by_sleeve'][name]:>9.0f}{negative:>7.0f}%")
        sleeves[name] = {"p5": round(quantile(values, 5), 2),
                         "p25": round(quantile(values, 25), 2),
                         "median": round(quantile(values, 50), 2),
                         "p75": round(quantile(values, 75), 2),
                         "p95": round(quantile(values, 95), 2),
                         "actual": round(actual["by_sleeve"][name], 2),
                         "p_negative": round(negative, 1)}
    return {"paths": len(paths), "minutes": round(elapsed / 60, 2),
            "metrics": payload,
            "tail": {k: round(v, 3) for k, v in tail.items()},
            "by_sleeve": sleeves}


LABELS = {
    "boot": "BLOCK BOOTSTRAP -- estimation risk: blocks drawn with replacement, "
            "in random order",
    "order": "PERMUTATION -- sequence risk alone: the same blocks, each exactly "
             "once, reordered",
}


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

def verify(block_days):
    """Chain the blocks in their REAL order and compare to one full replay.

    THE ONE CHECK THAT MAKES THE REST READABLE. If chaining reproduces the
    single-window book, then every difference a path shows is caused by the
    resampling and not by the machinery that implements it. If it does not, the
    gap is the boundary cost -- positions spanning a block edge lose the marking
    of their far side -- and it belongs in the report as a known bias with a
    measured size rather than as a footnote saying it is probably small.
    """
    state = load()
    pin_external_window()
    print(f"block length {block_days} days")
    whole = run_book(state)
    marked = [(ts - ef.IS_END, value) for ts, value in whole["marked"]]
    whole_dd, low = drawdown_of([v for _e, v in marked])
    blocks = blocks_of(block_days)
    chained, returns, curve = chain(state, blocks, ecs.CANON_INITIAL)
    chained_dd, chained_low = drawdown_of([v for _e, v in curve])
    print(f"{'':22}{'one replay':>14}{'chained':>14}{'delta':>12}")
    for name, left, right, spec in (
            ("final equity $", whole["final"], chained["final"], "{:>14.2f}"),
            ("return %", 100 * (whole["final"] / ecs.CANON_INITIAL - 1),
             100 * (chained["final"] / ecs.CANON_INITIAL - 1), "{:>14.2f}"),
            ("MTM dd %", whole_dd, chained_dd, "{:>14.2f}"),
            ("closed dd %", whole["max_dd_pct"], chained["max_dd_pct"],
             "{:>14.2f}"),
            ("trades", whole["trades"], chained["trades"], "{:>14.0f}"),
            ("below broker min", sum(whole["below_broker_minimum"].values()),
             chained["below_broker_minimum"], "{:>14.0f}"),
            ("min equity $", low, chained_low, "{:>14.2f}")):
        print(f"{name:<22}" + spec.format(left) + spec.format(right)
              + f"{right - left:>+12.2f}")
    print(f"\n{len(blocks)} blocks, "
          f"{sum(1 for lo, hi in blocks if hi - lo < block_days * 86400)} short")
    print("per-sleeve P&L, one replay vs chained")
    print(f"{'sleeve':<28}{'one replay':>12}{'chained':>12}{'delta':>10}")
    for name, row in sorted(whole["by_sleeve"].items(),
                            key=lambda kv: -kv[1]["pnl"]):
        got = chained["by_sleeve"].get(name, 0.0)
        print(f"{name:<28}{row['pnl']:>12.2f}{got:>12.2f}"
              f"{got - row['pnl']:>+10.2f}")


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def run(method, paths, workers, block_days, seed, out_path):
    state = load()
    pin_external_window()
    print(f"canon book: {len(state['members'])} sleeves, "
          f"${ecs.CANON_INITIAL:.0f}, risk {ecs.CANON_RISK_SCALE}, gross cap "
          + ("off" if ecs.CANON_GROSS_CAP is None
             else f"{ecs.CANON_GROSS_CAP:g}x"))
    blocks = blocks_of(block_days)
    print(f"{len(blocks)} blocks of {block_days} days, {workers} workers")
    print("chaining the realised order for the reference line...", flush=True)
    actual = reference(state, blocks, block_days)
    print(f"  {actual['return_pct']:+.1f}%   MTM dd {actual['mtm_dd_pct']:.2f}%"
          f"   {actual['trades']} trades   "
          f"ann. Sharpe {actual['ann_sharpe']:.2f}")

    payload = {"generated": datetime.now(timezone.utc).isoformat(),
               "members": [f"{m['symbol']}:{m['family']}"
                           for m in state["members"]],
               "initial": ecs.CANON_INITIAL,
               "risk_scale": ecs.CANON_RISK_SCALE,
               "gross_cap": ecs.CANON_GROSS_CAP,
               "block_days": block_days, "blocks": len(blocks),
               "actual": {key: (round(value, 4)
                                if isinstance(value, float) else value)
                          for key, value in actual.items()
                          if key != "by_sleeve"},
               "runs": {}}

    jobs = {"boot": _path_boot, "order": _path_order}
    for name in (("boot", "order") if method == "both" else (method,)):
        started = time.time()
        print(f"\n{name}: {paths} paths", flush=True)
        results, step = [], max(1, paths // 10)
        with multiprocessing.Pool(workers, _init_worker,
                                  (CACHE, block_days)) as pool:
            for index, row in enumerate(
                    pool.imap_unordered(jobs[name],
                                        range(seed, seed + paths),
                                        chunksize=1), start=1):
                results.append(row)
                if index % step == 0 or index == paths:
                    rate = (time.time() - started) / index
                    print(f"    {100 * index // paths:>3}% ({index}/{paths})  "
                          f"{(time.time() - started) / 60:.1f} min elapsed, "
                          f"{rate * (paths - index) / 60:.1f} left", flush=True)
        payload["runs"][name] = summarise(LABELS[name], results, actual,
                                          time.time() - started)

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print(f"\nwrote {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Monte Carlo the canon combined book through its own "
                    "replay engine.")
    parser.add_argument("command", choices=("cache", "verify", "run"))
    parser.add_argument("--method", default="both",
                        choices=("boot", "order", "both"))
    parser.add_argument("--paths", type=int, default=1000)
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4) - 4))
    parser.add_argument("--block-days", type=int, default=BLOCK_DAYS,
                        help="length of one resampling block, in days")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", default=OUT_PATH)
    parser.add_argument("--force", action="store_true",
                        help="rebuild the input cache even if it exists")
    args = parser.parse_args()
    if args.command == "cache":
        cache(force=args.force)
        return
    if args.command == "verify":
        verify(args.block_days)
        return
    run(args.method, args.paths, args.workers, args.block_days, args.seed,
        args.output)


if __name__ == "__main__":
    main()
