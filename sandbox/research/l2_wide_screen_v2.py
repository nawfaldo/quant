"""Second L2 screen: ten new mechanisms, and a scale-free way to normalise them.

Two changes from `l2_wide_screen`, and the second matters more than the first.

**Ten new families.** The first screen read the netted and top-of-book columns.
This one reads what was left: gross aggression rather than net delta, execution
location rather than inferred side, quote-to-trade intensity, book thinness,
book *instability* rather than book level, liquidity building versus withdrawing,
flow acceleration rather than flow level, and realised impact per unit of flow.
Twelve of the thirty-one columns had never been read by any screen in this repo.

**Causal rank instead of causal z, and this is the point.** The Bookmap collector
nets 25% less delta and splits trades 68% finer than Databento, so a signal
normalised by a trailing standard deviation silently changes its threshold at the
2026-07-17 boundary: `absorption`'s fire rate falls 3.76% -> 1.35% there for
purely instrumental reasons. That is why the live window cannot currently score
anything. A percentile rank depends only on ORDERING, so a collector that scales
every reading by a constant leaves it untouched. If a candidate survives in rank
form, the sealed Bookmap window becomes usable on it -- which is worth more than
any single family below.

So `absorption` is re-run here in rank form as well. That is not a second look
for its own sake: it is the specific test of whether the repo's best candidate
can be validated at all.

DECLARED BEFORE ANY NUMBER IS READ. Ten families plus the absorption bridge,
four windows (15/30/60/120m), four horizons (30/60/120/240m), one extremity cut
(top/bottom 5% of the trailing rank distribution, the rank analogue of |z|>=2),
one basis (causal, trailing 20 sessions). Every family's DIRECTION is fixed in
the table below and may not flip afterwards.

Three directions are inherited from prior measurement rather than from intuition,
and are marked `(prior)` in the table. That is legitimate -- it is evidence from
earlier runs, not from this sample -- but it is recorded so nobody later reads
them as this screen's own discovery. In particular `impact` is declared AGAINST
the textbook thesis, because the cheap-impact continuation story was measured
reliably backwards here (t -5.8 to -8.8).

Scored exactly as the first screen: pooled, observation-weighted conditional
means with a session block bootstrap. Never an equal-weighted session mean.

    py -B -m sandbox.research.l2_wide_screen_v2 --window 2025
    py -B -m sandbox.research.l2_wide_screen_v2 --window 2026 --record-trials
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from sandbox import trials
from sandbox.paths import result_path
from sandbox.research import l2_wide_screen as v1

STRATEGY = "NQ L2 Wide Screen v2"

#: aggregation lengths in minutes, and the named date ranges. v1 calls the first
#: `WINDOWS` and the second `WINDOWS_BY_NAME`; both are spelled out here because
#: one name for two things is how the first draft of this module broke.
AGG_WINDOWS = v1.WINDOWS
DATE_WINDOWS = v1.WINDOWS_BY_NAME
HORIZONS = v1.HORIZONS
Z_SESSIONS = v1.Z_SESSIONS
#: Rank analogue of |z| >= 2.0: the extreme 5% of each tail.
RANK_TAIL = 0.05

#: name -> (what it measures, direction, whether the sign is inherited)
FAMILIES = {
    "aggression":    ("total aggressive volume, signed by delta", -1, False),
    "exec_side":     ("executions at ask vs bid", -1, False),
    "churn":         ("gross add+cancel activity, signed by delta", +1, False),
    "provision":     ("gross adds minus gross cancels", +1, False),
    "quote_ratio":   ("depth events per trade, signed by delta", -1, False),
    "thinness":      ("depth-weighted distance, signed by delta", +1, False),
    "imbalance_vol": ("dispersion of top-5 imbalance, signed by delta", -1, False),
    "spread_regime": ("mean spread, signed by delta", +1, False),
    "delta_accel":   ("second-half minus first-half delta", +1, False),
    "impact":        ("price move per unit delta", +1, True),
    # The bridge: the standing candidate, rebuilt scale-free.
    "absorption":    ("delta that fails to move price", -1, True),
}


def causal_rank(signal, session, sessions=Z_SESSIONS):
    """Percentile rank in [-1, +1] against the trailing `sessions`, causally.

    The scale-free counterpart of `l2_wide_screen.causal_z`. Because it reads
    only the ordering of the trailing distribution, any collector difference that
    scales readings by a constant -- which is what separates Bookmap from
    Databento -- leaves this untouched. A z-score does not have that property,
    which is why the live window cannot score a z-normalised signal.
    """
    out = np.full(len(signal), np.nan)
    ids = np.unique(session)
    for position, sid in enumerate(ids):
        if position < sessions:
            continue
        history = signal[(session >= ids[position - sessions]) & (session < sid)]
        history = np.sort(history[np.isfinite(history)])
        if len(history) < 100:
            continue
        mask = session == sid
        values = signal[mask]
        ranks = np.searchsorted(history, values) / len(history)
        out[mask] = np.where(np.isfinite(values), 2.0 * ranks - 1.0, np.nan)
    return out


def build_signals(frame, window):
    """Raw signal per new family at one aggregation window.

    Families that measure a MAGNITUDE rather than a direction (aggression, churn,
    quote_ratio, thinness, imbalance_vol, spread_regime) are signed by the
    window's own delta, because "the book is unstable" is not a trade until it
    says which way. Signing by delta rather than by the price move keeps them
    from collapsing into a momentum proxy.
    """
    session = frame["session"]
    roll = lambda name: v1.within_session(frame[name], session, window)
    mean_of = lambda name: roll(name) / window

    delta = roll("trade_delta")
    move = roll("price_change")
    trades = roll("trade_count")
    buys, sells = roll("aggressive_buy_volume"), roll("aggressive_sell_volume")
    at_bid, at_ask = roll("executed_at_bid"), roll("executed_at_ask")
    adds = roll("bid_add_volume") + roll("ask_add_volume")
    cancels = roll("bid_cancel_volume") + roll("ask_cancel_volume")
    events = roll("depth_event_count")
    way = np.sign(delta)

    with np.errstate(divide="ignore", invalid="ignore"):
        aggression = way * (buys + sells)
        exec_side = (at_ask - at_bid) / np.maximum(at_ask + at_bid, 1.0)
        churn = way * (adds + cancels)
        provision = (adds - cancels) / np.maximum(adds + cancels, 1.0)
        quote_ratio = way * events / np.maximum(trades, 1.0)
        thinness = way * mean_of("depth_weighted_distance")
        spread_regime = way * mean_of("spread")
        # Impact per unit of flow. Declared +1 against the textbook thesis:
        # the cheap-impact continuation story measured reliably backwards here.
        impact = move / np.maximum(np.abs(delta), 1.0)
        absorption = (delta / (np.abs(move) + 1.0)) / np.maximum(trades, 1.0)

    # Dispersion of book imbalance, not its level: an unstable book rather than
    # a lopsided one. Rolling sd via the sum-of-squares identity.
    imbalance = frame["top5_imbalance"]
    mean_i = v1.within_session(imbalance, session, window) / window
    mean_sq = v1.within_session(imbalance ** 2, session, window) / window
    imbalance_vol = way * np.sqrt(np.maximum(mean_sq - mean_i ** 2, 0.0))

    # Acceleration: the window's second half against its first.
    half = max(1, window // 2)
    recent = v1.within_session(frame["trade_delta"], session, half)
    delta_accel = recent - (delta - recent)

    return {
        "aggression": aggression,
        "exec_side": exec_side,
        "churn": churn,
        "provision": provision,
        "quote_ratio": quote_ratio,
        "thinness": thinness,
        "imbalance_vol": imbalance_vol,
        "spread_regime": spread_regime,
        "delta_accel": delta_accel,
        "impact": impact,
        "absorption": absorption,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", default="2025", choices=sorted(DATE_WINDOWS))
    parser.add_argument("--out", default=None)
    parser.add_argument("--record-trials", action="store_true")
    args = parser.parse_args()

    start, end = DATE_WINDOWS[args.window]
    lo, hi = v1._epoch(start), v1._epoch(end)
    rng = np.random.default_rng(v1.SEED)

    print("loading nq level-two frame ...")
    frame = v1.load_frame()
    inside = (frame["ts"] >= lo) & (frame["ts"] < hi)
    print(f"  {int(inside.sum())} valid RTH minutes in {args.window} "
          f"({start} .. {end})")
    print(f"  normalisation: causal PERCENTILE RANK, trailing {Z_SESSIONS} "
          f"sessions, extreme {RANK_TAIL:.0%} tails")

    forwards = {h: v1.forward_return(frame["price"], frame["session"], h)
                for h in HORIZONS}

    report = {"strategy": STRATEGY, "window": args.window, "range": [start, end],
              "normalisation": "causal_rank", "rank_tail": RANK_TAIL,
              "families": {k: {"describes": v[0], "direction": v[1],
                               "sign_inherited": v[2]}
                           for k, v in FAMILIES.items()},
              "results": []}

    header = (f"{'family':16}{'win':>5}{'hor':>5}{'n':>8}{'edge':>9}"
              f"{'t':>7}{'p05':>9}{'p95':>9}")
    print(f"\n{header}")
    print("-" * len(header))

    cells = 0
    cut = 1.0 - 2.0 * RANK_TAIL   # rank is in [-1, 1]; keep the extreme tails
    for window in AGG_WINDOWS:
        signals = build_signals(frame, window)
        for name, raw in signals.items():
            direction = FAMILIES[name][1]
            rank = causal_rank(raw, frame["session"])
            fires = inside & np.isfinite(rank) & (np.abs(rank) >= cut)
            if fires.sum() < 200:
                continue
            side = np.sign(rank[fires]) * direction
            for horizon in HORIZONS:
                forward = forwards[horizon][fires]
                usable = np.isfinite(forward)
                if usable.sum() < 200:
                    continue
                stats = v1.score(side[usable] * forward[usable],
                                 frame["session"][fires][usable], rng)
                cells += 1
                if stats is None:
                    continue
                stats.update({"family": name, "window": window,
                              "horizon": horizon})
                report["results"].append(stats)
                flag = "  <- clears Bonferroni" if abs(stats["t"] or 0) >= 3.7 else ""
                print(f"{name:16}{window:>5}{horizon:>5}{stats['n']:>8}"
                      f"{stats['edge']:>9.3f}{stats['t'] or 0:>7.2f}"
                      f"{stats['p05']:>9.3f}{stats['p95']:>9.3f}{flag}")

    ranked = sorted((r for r in report["results"] if r["t"] is not None),
                    key=lambda r: -abs(r["t"]))
    print(f"\ntop 10 by |t| (Bonferroni across {cells} tests is ~3.7):")
    for row in ranked[:10]:
        print(f"  {row['family']:16} win {row['window']:>4}m hor {row['horizon']:>4}m"
              f"  edge {row['edge']:+8.3f} pts  t {row['t']:+6.2f}  n {row['n']}")
    print(f"\nclearing Bonferroni: "
          f"{sum(1 for r in ranked if abs(r['t']) >= 3.7)}")

    charged = trials.total(STRATEGY) + cells
    if args.record_trials:
        charged = trials.record(STRATEGY, cells,
                                f"L2 screen v2 (rank-normalised), {args.window}: "
                                f"11 families x 4 windows x 4 horizons")
    print(f"\ncells evaluated: {cells}")
    print(f"cumulative trials charged to {STRATEGY!r}: {charged}")
    report["cells_evaluated"] = cells
    report["cumulative_trials"] = charged

    path = result_path(args.out or f"l2_wide_screen_v2_{args.window}.json")
    with open(path, "w") as handle:
        json.dump(report, handle, indent=1)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
