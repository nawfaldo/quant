"""Purged k-fold cross-validation. OPTIMIZATION_PLAN.md Stage 4b.

The anchored walk-forward in `walkforward.py` answers one question honestly --
*would I have found this in real time?* -- and is structurally blind to the
first third of the sample. With a ~5.5-month minimum train, 2025-02 .. 2025-07
is train-only in every fold and test data in none, which is exactly where the
worst month in the sample sits. No fold layout fixes that; it is what an
anchored scheme costs on eighteen months of history.

So this module answers the other question: *does the edge exist in every regime
present in the data?* Six blocks of ~three months, train on five, test on the
held-out one, with a one-session purge and embargo on both sides of the test
block. Every block gets tested, 2025-02 included.

It trains on data later than its test block **on purpose**. That makes it
useless as a deployment simulation and it is never used to choose what ships --
the walk-forward median still does that. It is a veto. A configuration that
looks fine walking forward but loses in the 2025 blocks has an edge that
depends on a regime, and the walk-forward cannot tell you so because it never
scored one.

Read the per-block table, not the total. The total can be carried by two good
blocks, which is the failure mode the whole plan is about.
"""
import math
from dataclasses import replace

from sandbox import data
from sandbox import metrics
from sandbox import search
from sandbox import trials
from sandbox import walkforward
from sandbox.data import TS

EMBARGO_DAYS = 1

#: Block edges, six ~3-month spans covering the whole sample. The first begins
#: before the first bar and the last ends after the last, so no trade is
#: silently outside every block.
BLOCKS = [
    ("2025-02-01", "2025-05-01"),
    ("2025-05-01", "2025-08-01"),
    ("2025-08-01", "2025-11-01"),
    ("2025-11-01", "2026-02-01"),
    ("2026-02-01", "2026-05-01"),
    ("2026-05-01", "2026-08-01"),
]


def train_mask(fills, test_lo, test_hi):
    """Fills usable for training when `[test_lo, test_hi)` is held out.

    Purge and embargo in one step: a trade is dropped if it *overlaps* the test
    block at all, not merely if it starts inside it. A position opened days
    before the block and closed inside it saw the held-out data, and leaving it
    in training is the leak purging exists to remove.
    """
    lo = test_lo - EMBARGO_DAYS * 86_400
    hi = test_hi + EMBARGO_DAYS * 86_400
    return [f for f in fills if f.exit_ts < lo or f.entry_ts >= hi]


def select_on(strategy, cells, test_lo, test_hi, min_trades, ex):
    """Best cell by plateau-guarded trade t-stat, over everything but the block.

    The same objective and the same plateau-width rule as the walk-forward, so
    a difference between the two schemes is a difference in *coverage* and not
    in how a winner is chosen.
    """
    scores, counts, edges = {}, {}, {}
    for key, fills in cells.items():
        kept = train_mask(fills, test_lo, test_hi)
        points = [f.points for f in kept]
        counts[key] = len(kept)
        scores[key] = walkforward.edge_t(points)
        edges[key] = sum(points) / len(points) if points else 0.0

    best, best_score = None, None
    for key, score in scores.items():
        if counts[key] < min_trades:
            continue
        # Same per-trade edge floor the walk-forward applies, so the two
        # schemes differ only in coverage.
        if edges[key] < walkforward.MIN_EDGE_MULTIPLE * ex.entry_cost:
            continue
        near = [n for n in search.neighbours(strategy, key) if n in scores]
        if not near or any(scores[n] <= 0 for n in near):
            continue
        plateau = sum(scores[n] for n in near) / len(near)
        combined = min(score, plateau)
        if best_score is None or combined > best_score:
            best, best_score = key, combined
    return best


def run(
    strategy,
    initial=walkforward.INITIAL,
    cells=None,
    ex=None,
    progress=True,
    record_trials=True,
):
    """Per-block out-of-sample results over the whole sample."""
    ex = ex or replace(strategy.execution, initial=initial)
    if cells is None:
        bars = data.load_bars(strategy.bars, strategy.symbol)
        cells = walkforward.cell_fills(strategy, bars, strategy.context(), ex,
                                       progress=progress)
    # Charged even when the fills are handed over from a walk-forward: this is a
    # second selection across the same grid, and the haircut counts selections,
    # not bar walks. Over-counting is the safe direction.
    if record_trials:
        trials.record(strategy.name, len(cells), f"purged CV, {len(BLOCKS)} blocks")

    axes = sorted(strategy.grid)
    width = max(9, max(len(a) for a in axes))
    print(f"\n--- purged {len(BLOCKS)}-block CV (all {len(BLOCKS) * 3} months) " + "-" * 20)
    print("  block              " + "  ".join(f"{a:>{width}}" for a in axes)
          + f"  {'pts':>9} {'trades':>7} {'t':>6}")

    out = []
    for lo_iso, hi_iso in BLOCKS:
        lo, hi = metrics.split_ts(lo_iso), metrics.split_ts(hi_iso)
        # The trade floor scales with the training sample, which is five blocks
        # wide here rather than growing fold by fold as it does walking forward.
        key = select_on(strategy, cells, lo, hi,
                        walkforward.MIN_TRADES_PER_MONTH * 12, ex)
        if key is None:
            print(f"  {lo_iso}..{hi_iso[:7]}  (nothing cleared)")
            out.append({"block": lo_iso, "params": None, "points": 0.0,
                        "trades": 0, "t": 0.0})
            continue
        points = walkforward.window_points(cells[key], lo, hi)
        t = walkforward.edge_t(points)
        params = dict(key)
        print(f"  {lo_iso}..{hi_iso[:7]}  "
              + "  ".join(f"{params[a]!s:>{width}}" for a in axes)
              + f"  {sum(points):>9.1f} {len(points):>7} {t:>6.2f}")
        out.append({"block": lo_iso, "params": params, "points": round(sum(points), 1),
                    "trades": len(points), "t": round(t, 3)})

    values = [b["points"] for b in out]
    mean = sum(values) / len(values)
    print(f"  total {sum(values):+.1f} pts   mean block {mean:+.1f}   "
          f"worst {min(values):+.1f}   positive blocks {sum(1 for v in values if v > 0)}"
          f"/{len(values)}")
    return {"blocks": out, "mean": mean, "worst": min(values),
            "positive": sum(1 for v in values if v > 0)}
