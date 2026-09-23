"""Where, if anywhere, does NQ level-two data carry directional information?

Every prior L2 experiment in `sandbox/` picked a setup, wrapped a bracket
around it, and read the PnL.  At ~100-1700 trades against a ~17-point per-trade
standard deviation that design cannot resolve an edge below ~0.85 points, so a
negative result never distinguished "no signal" from "no power".

This inverts the order.  It measures the conditional forward return directly
over every eligible minute, which is ~90k observations instead of hundreds, and
only then asks whether anything found is large enough to trade.

Discipline:

* 2025 only.  2026 is not read by this module at all.
* Signals are normalized against the trailing 20 sessions of the *same*
  minute-of-day, so nothing uses a statistic from its own future.
* Standard errors are clustered by session.  Overlapping forward windows make
  minute-level t-statistics meaningless, so every test aggregates to a session
  mean first and tests across sessions.
* The reported number is the long-short spread in NQ points: mean forward
  return when the signal is high minus when it is low.  That is directly
  comparable with the 0.2 spread and with the volatility-matched bracket drag.

**The estimator in `clustered_test`/`evaluate` below is wrong -- use
`l2_discovery_v2` instead.** Averaging per-session long-short differences with
equal weight lets a session whose high leg holds one qualifying minute count as
much as a session with fifty, and a single 60-minute NQ move carries roughly the
variance of the whole effect.  It reported +15.8 points for `add_ratio@5m`,
whose observation-weighted value is -0.28; the strategy built on that reading
lost 1.5 points per trade against a matched control, and re-estimating flipped
the sign of the largest hit.  `build_panel`, `raw_signals`, `cumulative` and
`slot_normalize` are sound and v2 imports them; only the scoring was at fault.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

import numpy as np

from sandbox import data
from sandbox.data import TS, O, H, L, C

RTH_START, RTH_END = 570, 955
IN_SAMPLE_END = "2026-01-01"
HORIZONS = (5, 15, 30, 60)
NORM_SESSIONS = 20
Z_CUT = 1.5
EPS = 1e-9


def session_of(ts):
    return ts // 86_400


def minute_of(ts):
    return (ts % 86_400) // 60


def build_panel(symbol="nq"):
    """Aligned per-minute arrays of bars and level-two features, RTH, 2025."""
    bars = data.load_bars("level_two", symbol)
    feats = data.load_l2_features(symbol)
    cutoff = data.day_seconds(IN_SAMPLE_END) if hasattr(data, "day_seconds") else None
    if cutoff is None:
        from datetime import datetime, timezone
        cutoff = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())

    rows = []
    for bar in bars:
        ts = bar[TS]
        if ts >= cutoff:
            continue
        if not (RTH_START <= minute_of(ts) <= RTH_END):
            continue
        f = feats.get(ts)
        if f is None or not f["book_valid"] or f.get("source") != "dbento":
            continue
        rows.append((ts, bar, f))
    rows.sort(key=lambda r: r[0])
    print(f"panel: {len(rows)} RTH minutes with a valid dbento book, "
          f"{len({session_of(r[0]) for r in rows})} sessions")
    return rows


def raw_signals(rows):
    """Causal per-minute candidate signals, before normalization.

    Each value uses only data from its own completed minute or earlier.  Names
    marked NEW are combinations no strategy in `sandbox/` or `live_trade/` trades.
    """
    n = len(rows)
    out = defaultdict(lambda: np.zeros(n))
    close = np.array([r[1][C] for r in rows], dtype=float)

    for i, (ts, bar, f) in enumerate(rows):
        bid_net = f["bid_add_volume"] - f["bid_cancel_volume"]
        ask_net = f["ask_add_volume"] - f["ask_cancel_volume"]
        agg_b, agg_s = f["aggressive_buy_volume"], f["aggressive_sell_volume"]
        agg_tot = agg_b + agg_s
        adds = f["bid_add_volume"] + f["ask_add_volume"]
        cancels = f["bid_cancel_volume"] + f["ask_cancel_volume"]
        move = bar[C] - bar[O]
        rng = max(bar[H] - bar[L], 0.25)

        # --- baselines already traded or already tested somewhere -------------
        out["ofi_deep"][i] = bid_net - ask_net
        out["imb_top1"][i] = f["top1_imbalance"]
        out["imb_top5"][i] = f["top5_imbalance"]
        out["imb_top10"][i] = f["top10_imbalance"]
        out["trade_delta"][i] = f["trade_delta"]
        out["replenish"][i] = f["replenishment_score"]
        out["depth_dist"][i] = f["depth_weighted_distance"]

        # --- normalized flow ratios (scale-free versions of the above) --------
        out["agg_ratio"][i] = (agg_b - agg_s) / (agg_tot + EPS)
        out["exec_side"][i] = ((f["executed_at_ask"] - f["executed_at_bid"])
                               / (f["executed_at_ask"] + f["executed_at_bid"] + EPS))
        out["add_ratio"][i] = ((f["bid_add_volume"] - f["ask_add_volume"])
                               / (adds + EPS))
        out["cancel_ratio"][i] = ((f["ask_cancel_volume"] - f["bid_cancel_volume"])
                                  / (cancels + EPS))

        # --- NEW: flow that did not get paid ---------------------------------
        # Aggressive delta divided by the price move it bought.  Large positive
        # means heavy buying that went nowhere, i.e. someone absorbed it.
        out["delta_unpaid"][i] = f["trade_delta"] / (abs(move) + 1.0)
        # Same idea scaled by the bar's own range rather than its net move.
        out["delta_per_range"][i] = f["trade_delta"] / rng

        # --- NEW: quoted pressure that the tape does not confirm --------------
        # Book leaning one way while aggressors lean the other.  Deep OFI and
        # trade delta are traded separately in this repo but never differenced.
        out["book_vs_tape"][i] = f["top10_imbalance"] - (agg_b - agg_s) / (agg_tot + EPS)

        # --- NEW: cancellation asymmetry against book distance ---------------
        # One-sided pulling in an already-thin book is a different state from
        # one-sided pulling in a deep one.
        out["pull_thin"][i] = out["cancel_ratio"][i] * f["depth_weighted_distance"]

        # --- NEW: effort vs result on the quote side -------------------------
        # Adds minus cancels per unit of realised range: book churn that the
        # price ignored.
        out["churn_unpaid"][i] = (bid_net - ask_net) / rng

    out["close"] = close
    return dict(out)


def cumulative(sig, rows, window):
    """Trailing sum of `sig` over `window` minutes, reset at each session."""
    n = len(sig)
    out = np.zeros(n)
    start = 0
    for i in range(n):
        if i and session_of(rows[i][0]) != session_of(rows[i - 1][0]):
            start = i
        lo = max(start, i - window + 1)
        out[i] = sig[lo:i + 1].sum()
    return out


def slot_normalize(sig, rows, sessions=NORM_SESSIONS):
    """Z-score against the same minute-of-day over the prior `sessions` days.

    Strictly causal: a value is scored against sessions that closed before its
    own.  Slots without enough history score NaN and drop out of every test.
    """
    by_slot = defaultdict(list)          # minute -> [(session, value)]
    order = []
    for i, (ts, _bar, _f) in enumerate(rows):
        order.append((minute_of(ts), session_of(ts), i))
    z = np.full(len(sig), np.nan)
    history = defaultdict(list)          # minute -> [(session, value)]
    for slot, day, i in order:
        prior = [v for d, v in history[slot] if d < day]
        if len(prior) >= sessions // 2:
            window = prior[-sessions:]
            mu = float(np.mean(window))
            sd = float(np.std(window))
            if sd > EPS:
                z[i] = (sig[i] - mu) / sd
        history[slot].append((day, sig[i]))
        _ = by_slot
    return z


def forward_returns(rows, close, horizon):
    """close[t+h] - close[t], in points, never crossing a session boundary."""
    n = len(rows)
    fwd = np.full(n, np.nan)
    for i in range(n):
        j = i + horizon
        if j < n and session_of(rows[j][0]) == session_of(rows[i][0]):
            fwd[i] = close[j] - close[i]
    return fwd


def clustered_test(rows, mask, fwd):
    """Session-clustered mean and t-statistic of `fwd` over `mask`."""
    per_day = defaultdict(list)
    for i in np.nonzero(mask)[0]:
        if not math.isnan(fwd[i]):
            per_day[session_of(rows[i][0])].append(fwd[i])
    if len(per_day) < 30:
        return None
    day_means = np.array([np.mean(v) for v in per_day.values()])
    n = len(day_means)
    mean = float(day_means.mean())
    se = float(day_means.std(ddof=1) / math.sqrt(n))
    return {
        "mean": mean,
        "t": mean / se if se > EPS else 0.0,
        "sessions": n,
        "observations": int(sum(len(v) for v in per_day.values())),
    }


def evaluate(name, z, rows, fwd_by_h):
    results = []
    for horizon, fwd in fwd_by_h.items():
        hi = clustered_test(rows, z >= Z_CUT, fwd)
        lo = clustered_test(rows, z <= -Z_CUT, fwd)
        if not hi or not lo:
            continue
        spread = hi["mean"] - lo["mean"]
        # session-clustered t on the difference, paired by session
        per_day_hi, per_day_lo = defaultdict(list), defaultdict(list)
        for i in np.nonzero(z >= Z_CUT)[0]:
            if not math.isnan(fwd[i]):
                per_day_hi[session_of(rows[i][0])].append(fwd[i])
        for i in np.nonzero(z <= -Z_CUT)[0]:
            if not math.isnan(fwd[i]):
                per_day_lo[session_of(rows[i][0])].append(fwd[i])
        common = sorted(set(per_day_hi) & set(per_day_lo))
        if len(common) < 30:
            continue
        diffs = np.array([np.mean(per_day_hi[d]) - np.mean(per_day_lo[d])
                          for d in common])
        se = float(diffs.std(ddof=1) / math.sqrt(len(diffs)))
        results.append({
            "signal": name,
            "horizon": horizon,
            "long_short_points": spread,
            "t": float(diffs.mean() / se) if se > EPS else 0.0,
            "paired_sessions": len(common),
            "hi_mean": hi["mean"], "hi_obs": hi["observations"],
            "lo_mean": lo["mean"], "lo_obs": lo["observations"],
        })
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_discovery.json")
    parser.add_argument("--windows", default="1,5,15,60")
    args = parser.parse_args()

    rows = build_panel()
    sigs = raw_signals(rows)
    close = sigs.pop("close")
    fwd_by_h = {h: forward_returns(rows, close, h) for h in HORIZONS}

    windows = [int(w) for w in args.windows.split(",")]
    results = []
    for name, sig in sorted(sigs.items()):
        for window in windows:
            series = sig if window == 1 else cumulative(sig, rows, window)
            z = slot_normalize(series, rows)
            label = name if window == 1 else f"{name}@{window}m"
            results.extend(evaluate(label, z, rows, fwd_by_h))
        print(f"  scored {name}")

    results.sort(key=lambda r: -abs(r["t"]))
    print(f"\n{'signal':<24} {'h':>4} {'long-short pts':>15} {'t':>7} "
          f"{'sessions':>9} {'hi obs':>8} {'lo obs':>8}")
    for r in results[:35]:
        print(f"{r['signal']:<24} {r['horizon']:>4} "
              f"{r['long_short_points']:>+15.4f} {r['t']:>7.2f} "
              f"{r['paired_sessions']:>9} {r['hi_obs']:>8} {r['lo_obs']:>8}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"in_sample_end": IN_SAMPLE_END, "z_cut": Z_CUT,
                   "norm_sessions": NORM_SESSIONS, "results": results}, fh, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
