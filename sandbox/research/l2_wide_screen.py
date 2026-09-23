"""A wide, pre-registered gross-edge screen over every NQ level-two column.

This is the third L2 discovery pass in this repo and it is deliberately built to
be able to say no. The previous two failed in two different ways and both are
designed around here:

  * `l2_discovery_v2.py` reported `add_ratio@5m` at +15.8 points, t 3.60, stable
    across 11 months, clean against a shuffled placebo -- and all of it was an
    estimator artifact. Averaging *per-session* long-short differences with equal
    weight let a session with one qualifying minute carry a full 60-minute NQ
    move of variance. Observation-weighted, the same quantity was -0.28.
    **So every number here is a pooled, observation-weighted mean with a session
    block bootstrap, and nothing is ever averaged per session.**
  * `microprice_optimization.py` searched 39,230 cells and produced 25 gated
    winners of which 1 was profitable out of sample. **So this screen measures
    gross forward edge only. It builds no strategy, fits no bracket and tunes no
    threshold.** A family that cannot show edge before costs cannot be rescued by
    a bracket, and finding out costs 192 tests instead of 39,000.

WHAT IS BEING ASKED. For each of twelve mechanisms, at four aggregation windows
and four forward horizons: conditional on this signal being extreme, does NQ move
in the direction the mechanism predicts, and by how many points?

THE TWELVE FAMILIES ARE TWELVE MECHANISMS, not twelve parameterisations of one.
Four of them have never been measured in this repo (`standing`, `depth_dist`,
`cancel_ratio`, `divergence`); the rest are included so a new result can be read
against a known one rather than in isolation.

WINDOWS AND HORIZONS ARE LONG ON PURPOSE. The decay study established that the
book is worth ~0.10 points flat from 1s to 300s while the noise grows as sqrt(t),
so no snapshot signal survives at any horizon. The single exception was
`trade_delta`, whose point edge *grows* -- -0.03 at 1s to -0.56 at 300s -- which
is the aggregate-flow reversal the one profitable NQ strategy trades over 60
minutes. This screen starts where that curve was still rising.

THE HOLDOUT SITUATION, stated plainly because it decides what a hit is worth.
2025 is the screen. 2026 Jan-Jul is a second look and is ALREADY WEAKENED -- the
OFI-30m candidate spent it. The Bookmap live capture, 2026-07-17 onward and ~20
sessions, is the only untouched data left and this module does not read it. A
family clearing 2025 and 2026 is a paper-trading candidate, not a promotable one.

DECLARED BEFORE ANY NUMBER IS READ. Twelve families, four windows
(15/30/60/120m), four horizons (30/60/120/240m), one extremity cut (|z| >= 2.0),
one z-score basis (causal, trailing 20 sessions). 192 tests, so the Bonferroni
threshold is t ~ 3.7 and that is the bar, not 2.0. Each family's DIRECTION is
declared in the table below and is not allowed to flip after the fact: a
mechanism that only works with its sign reversed is a different hypothesis and
would need its own correction.

    py -B -m sandbox.research.l2_wide_screen
    py -B -m sandbox.research.l2_wide_screen --window 2026 --record-trials
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import numpy as np

from sandbox import data, trials
from sandbox.data import C, TS
from sandbox.paths import result_path

STRATEGY = "NQ L2 Wide Screen"

RTH_OPEN, RTH_CLOSE = 570, 960
WINDOWS = (15, 30, 60, 120)
HORIZONS = (30, 60, 120, 240)
Z_CUT = 2.0
#: sessions of trailing history behind the causal z-score
Z_SESSIONS = 20
RTH_MINUTES = RTH_CLOSE - RTH_OPEN
BOOTSTRAP_DRAWS = 2_000
SEED = 20260815

WINDOWS_BY_NAME = {
    "2025": ("2025-01-01", "2026-01-01"),
    "2026": ("2026-01-01", "2026-07-17"),   # stops where Bookmap begins
}

#: name -> (what the signal measures, +1 to follow it, -1 to fade it)
#:
#: The sign is the mechanism's own claim and is fixed here. `delta` is faded
#: because aggregate flow reverses (that is the live edge); `imbalance` and
#: `microprice` are followed because book pressure is a continuation thesis;
#: `absorption` is faded because volume that fails to move price is exhaustion.
FAMILIES = {
    "delta":        ("cumulative aggressor delta", -1),
    "delta_cont":   ("cumulative aggressor delta, followed", +1),
    "absorption":   ("delta with no price response", -1),
    "ofi_deep":     ("deep order flow imbalance, adds minus cancels", +1),
    "imbalance5":   ("mean top-5 book imbalance", +1),
    "imbalance10":  ("mean top-10 book imbalance", +1),
    "replenish":    ("mean replenishment asymmetry", +1),
    "standing":     ("standing depth asymmetry", +1),
    "depth_dist":   ("depth-distance asymmetry", -1),
    "cancel_ratio": ("cancel-to-add asymmetry", -1),
    "divergence":   ("price extreme unconfirmed by delta", -1),
    "microprice":   ("mean microprice tilt", +1),
}


def _epoch(stamp):
    return int(dt.datetime.strptime(stamp, "%Y-%m-%d")
               .replace(tzinfo=dt.UTC).timestamp())


def load_frame():
    """RTH minute arrays: price, session id, and every L2 column, aligned.

    Minutes whose feature row is missing or whose book is invalid are dropped
    outright rather than filled. A forward-filled book is a book that was never
    observed, and a screen that reads its own imputation will find structure in
    it.
    """
    bars = data.load_bars("level_two", "nq")
    rows = data.load_l2_features("nq")

    keep, price, session = [], [], []
    # Every column any screen reads, loaded once. The second block is what v2's
    # families need; carrying them here keeps both screens on one frame rather
    # than one loader each, which is how the two would drift apart.
    columns = {name: [] for name in (
        "trade_delta", "price_change", "bid_add_volume", "bid_cancel_volume",
        "ask_add_volume", "ask_cancel_volume", "top5_imbalance",
        "top10_imbalance", "replenishment_score", "bid_standing_depth",
        "ask_standing_depth", "bid_depth_distance", "ask_depth_distance",
        "microprice", "midprice", "trade_count",
        "aggressive_buy_volume", "aggressive_sell_volume", "executed_at_bid",
        "executed_at_ask", "depth_event_count", "depth_weighted_distance",
        "spread")}

    for i, bar in enumerate(bars):
        ts = bar[TS]
        minute = (ts % 86_400) // 60
        if not (RTH_OPEN <= minute < RTH_CLOSE):
            continue
        row = rows.get(ts)
        if row is None or not row["book_valid"]:
            continue
        keep.append(ts)
        price.append(bar[C])
        session.append(ts // 86_400)
        for name, series in columns.items():
            series.append(row[name])

    frame = {name: np.asarray(series, dtype=float)
             for name, series in columns.items()}
    frame["ts"] = np.asarray(keep, dtype=np.int64)
    frame["price"] = np.asarray(price, dtype=float)
    session = np.asarray(session, dtype=np.int64)
    _, frame["session"] = np.unique(session, return_inverse=True)
    frame["session_day"] = session
    return frame


def rolling_sum(values, window):
    """Causal rolling sum ending at and including each element."""
    cumulative = np.concatenate(([0.0], np.cumsum(values)))
    out = np.full(len(values), np.nan)
    if window <= len(values):
        out[window - 1:] = cumulative[window:] - cumulative[:-window]
    return out


def within_session(values, session, window, reducer=rolling_sum):
    """`reducer` applied per session, so no window spans an overnight gap."""
    out = np.full(len(values), np.nan)
    for sid in np.unique(session):
        mask = session == sid
        out[mask] = reducer(values[mask], window)
    return out


def causal_z(signal, session, sessions=Z_SESSIONS):
    """Z-score against the trailing `sessions` sessions, strictly before today.

    Per-session rather than per-minute-slot: a rolling window of fixed length
    would straddle the overnight gap and mix yesterday's close with today's open.
    Sessions before the warm-up are left NaN and never scored.
    """
    out = np.full(len(signal), np.nan)
    ids = np.unique(session)
    for position, sid in enumerate(ids):
        if position < sessions:
            continue
        history = signal[(session >= ids[position - sessions]) & (session < sid)]
        history = history[np.isfinite(history)]
        if len(history) < 100:
            continue
        sd = history.std(ddof=1)
        if sd <= 0:
            continue
        mask = session == sid
        out[mask] = (signal[mask] - history.mean()) / sd
    return out


def forward_return(price, session, horizon):
    """Points from this minute's close to `horizon` later, truncated at the close.

    A position opened intraday is flattened at the session close, so a horizon
    that runs past it is worth exactly what the flatten pays -- not what the
    instrument did overnight. Truncating rather than dropping keeps late-session
    minutes in the sample at their true value.
    """
    out = np.full(len(price), np.nan)
    for sid in np.unique(session):
        index = np.nonzero(session == sid)[0]
        target = np.minimum(np.arange(len(index)) + horizon, len(index) - 1)
        out[index] = price[index[target]] - price[index]
    return out


def build_signals(frame, window):
    """Raw (un-z-scored) signal per family at one aggregation window."""
    session = frame["session"]
    roll = lambda name: within_session(frame[name], session, window)

    delta = roll("trade_delta")
    move = roll("price_change")
    trades = roll("trade_count")
    bid_add, bid_cancel = roll("bid_add_volume"), roll("bid_cancel_volume")
    ask_add, ask_cancel = roll("ask_add_volume"), roll("ask_cancel_volume")

    def mean_of(name):
        return within_session(frame[name], session, window) / window

    with np.errstate(divide="ignore", invalid="ignore"):
        # Delta that failed to move price. Scaled by trades so a quiet stretch
        # cannot look like absorption merely by having no volume either.
        absorption = np.where(np.abs(move) > 0,
                              delta / (np.abs(move) + 1.0), delta) / np.maximum(trades, 1.0)
        cancel_ratio = (bid_cancel / np.maximum(bid_add, 1.0)
                        - ask_cancel / np.maximum(ask_add, 1.0))
        standing = mean_of("bid_standing_depth") - mean_of("ask_standing_depth")
        standing = standing / (mean_of("bid_standing_depth")
                               + mean_of("ask_standing_depth") + 1.0)
        depth_dist = mean_of("bid_depth_distance") - mean_of("ask_depth_distance")
        microprice = mean_of("microprice") - mean_of("midprice")

    # Divergence: price at a window extreme that cumulative delta does not
    # confirm. Positive means price made a HIGH on weak delta, which the sign
    # convention (-1) then fades.
    price_rank = within_session(
        frame["price"], session, window,
        lambda v, w: _rolling_rank(v, w))
    delta_rank = within_session(
        delta, session, window, lambda v, w: _rolling_rank(v, w))
    divergence = price_rank - delta_rank

    return {
        "delta": delta,
        "delta_cont": delta,
        "absorption": absorption,
        "ofi_deep": (bid_add - bid_cancel) - (ask_add - ask_cancel),
        "imbalance5": mean_of("top5_imbalance"),
        "imbalance10": mean_of("top10_imbalance"),
        "replenish": mean_of("replenishment_score"),
        "standing": standing,
        "depth_dist": depth_dist,
        "cancel_ratio": cancel_ratio,
        "divergence": divergence,
        "microprice": microprice,
    }


def _rolling_rank(values, window):
    """Where the current value sits inside its trailing window, in [-1, +1]."""
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1:i + 1]
        finite = chunk[np.isfinite(chunk)]
        if len(finite) < 2 or not np.isfinite(values[i]):
            continue
        out[i] = 2.0 * (finite < values[i]).mean() - 1.0
    return out


def block_bootstrap(payoff, session, draws=BOOTSTRAP_DRAWS, rng=None):
    """Session-level block bootstrap of the pooled, observation-weighted mean.

    Resampling whole sessions keeps the within-day correlation that makes an
    i.i.d. t-statistic optimistic; pooling rather than averaging per session is
    the estimator fix from `l2_discovery_v2`.
    """
    rng = rng or np.random.default_rng(SEED)
    ids = np.unique(session)
    if len(ids) < 5:
        return None
    buckets = {sid: payoff[session == sid] for sid in ids}
    means = np.empty(draws)
    for draw in range(draws):
        picked = rng.choice(ids, size=len(ids), replace=True)
        pooled = np.concatenate([buckets[sid] for sid in picked])
        means[draw] = pooled.mean()
    return means


def score(payoff, session, rng):
    """Pooled edge with a bootstrap t, never an equal-weighted session mean."""
    n = len(payoff)
    if n < 200:
        return None
    mean = float(payoff.mean())
    means = block_bootstrap(payoff, session, rng=rng)
    if means is None:
        return None
    sd = float(means.std(ddof=1))
    return {
        "n": n,
        "edge": round(mean, 4),
        "boot_sd": round(sd, 4),
        "t": round(mean / sd, 3) if sd > 0 else None,
        "p05": round(float(np.percentile(means, 5)), 4),
        "p95": round(float(np.percentile(means, 95)), 4),
        "sessions": int(len(np.unique(session))),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", default="2025", choices=sorted(WINDOWS_BY_NAME))
    parser.add_argument("--out", default=None)
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()

    start, end = WINDOWS_BY_NAME[args.window]
    lo, hi = _epoch(start), _epoch(end)
    rng = np.random.default_rng(SEED)

    print(f"loading nq level-two frame ...")
    frame = load_frame()
    inside = (frame["ts"] >= lo) & (frame["ts"] < hi)
    print(f"  {len(frame['ts'])} valid RTH minutes total, "
          f"{int(inside.sum())} in {args.window} ({start} .. {end})")

    forwards = {h: forward_return(frame["price"], frame["session"], h)
                for h in HORIZONS}

    report = {"strategy": STRATEGY, "window": args.window, "range": [start, end],
              "z_cut": Z_CUT, "bonferroni_t": 3.7,
              "families": {k: {"describes": v[0], "direction": v[1]}
                           for k, v in FAMILIES.items()},
              "results": []}

    header = (f"{'family':14}{'win':>5}{'hor':>5}{'n':>8}{'edge':>9}"
              f"{'t':>7}{'p05':>9}{'p95':>9}")
    print(f"\n{header}")
    print("-" * len(header))

    cells = 0
    for window in WINDOWS:
        signals = build_signals(frame, window)
        for name, raw in signals.items():
            direction = FAMILIES[name][1]
            z = causal_z(raw, frame["session"])
            fires = inside & np.isfinite(z) & (np.abs(z) >= Z_CUT)
            if fires.sum() < 200:
                continue
            side = np.sign(z[fires]) * direction
            for horizon in HORIZONS:
                forward = forwards[horizon][fires]
                usable = np.isfinite(forward)
                if usable.sum() < 200:
                    continue
                stats = score(side[usable] * forward[usable],
                              frame["session"][fires][usable], rng)
                cells += 1
                if stats is None:
                    continue
                stats.update({"family": name, "window": window,
                              "horizon": horizon})
                report["results"].append(stats)
                flag = ""
                if stats["t"] is not None and abs(stats["t"]) >= 3.7:
                    flag = "  <- clears Bonferroni"
                print(f"{name:14}{window:>5}{horizon:>5}{stats['n']:>8}"
                      f"{stats['edge']:>9.3f}{stats['t'] or 0:>7.2f}"
                      f"{stats['p05']:>9.3f}{stats['p95']:>9.3f}{flag}")

    ranked = sorted((r for r in report["results"] if r["t"] is not None),
                    key=lambda r: -abs(r["t"]))
    print(f"\ntop 10 by |t| (Bonferroni threshold across {cells} tests is ~3.7):")
    for row in ranked[:10]:
        print(f"  {row['family']:14} win {row['window']:>4}m hor {row['horizon']:>4}m"
              f"  edge {row['edge']:+8.3f} pts  t {row['t']:+6.2f}  n {row['n']}")
    survivors = [r for r in ranked if abs(r["t"]) >= 3.7]
    print(f"\nfamilies clearing Bonferroni: {len(survivors)}")
    for row in survivors:
        print(f"  {row['family']} @{row['window']}m h{row['horizon']}m "
              f"edge {row['edge']:+.3f} t {row['t']:+.2f}")

    charged = trials.total(STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(STRATEGY, cells,
                                f"wide L2 gross-edge screen, {args.window}: "
                                f"12 families x 4 windows x 4 horizons")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {STRATEGY!r}: {charged}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out or f"l2_wide_screen_{args.window}.json")
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
