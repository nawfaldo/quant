"""Does quote-side pressure predict anything price does not already say?

The surviving signal is `(bid_add - ask_add) / total_adds` over a trailing 5
minutes: which side of the book the quoting activity sits on.  It predicts +15.8
points long-short at a 60-minute horizon on 2025.

The obvious innocent explanation is that quoting follows price -- if the last
five minutes rallied, quotes stack on the bid -- in which case this is intraday
momentum wearing a level-two costume, and `nq_noise_momentum` already trades it
off OHLCV for free.

Two controls:

1. double sort -- bucket every minute by its trailing price move, then measure
   the quote-side effect *within* each bucket.  Survival there means the book
   carries information the price does not.
2. the mirror test -- run the identical machinery on the trailing price move
   alone and see how much of the effect it reproduces.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from sandbox.data import C
from sandbox.research.l2_discovery import (
    EPS, Z_CUT, build_panel, cumulative, forward_returns, session_of,
    slot_normalize,
)

WINDOW = 5
HORIZON = 60


def quote_side(rows):
    n = len(rows)
    out = np.zeros(n)
    for i, (_ts, _bar, f) in enumerate(rows):
        adds = f["bid_add_volume"] + f["ask_add_volume"]
        out[i] = (f["bid_add_volume"] - f["ask_add_volume"]) / (adds + EPS)
    return out


def past_move(rows, close, window):
    """close[t] - close[t-window], never crossing a session boundary."""
    n = len(rows)
    out = np.full(n, np.nan)
    for i in range(n):
        j = i - window
        if j >= 0 and session_of(rows[j][0]) == session_of(rows[i][0]):
            out[i] = close[i] - close[j]
    return out


def paired_spread(rows, z, fwd, subset=None):
    hi, lo = defaultdict(list), defaultdict(list)
    sel = np.ones(len(z), dtype=bool) if subset is None else subset
    for i in np.nonzero((z >= Z_CUT) & sel)[0]:
        if not math.isnan(fwd[i]):
            hi[session_of(rows[i][0])].append(fwd[i])
    for i in np.nonzero((z <= -Z_CUT) & sel)[0]:
        if not math.isnan(fwd[i]):
            lo[session_of(rows[i][0])].append(fwd[i])
    common = sorted(set(hi) & set(lo))
    if len(common) < 25:
        return None, None, len(common)
    diffs = np.array([np.mean(hi[d]) - np.mean(lo[d]) for d in common])
    se = diffs.std(ddof=1) / math.sqrt(len(diffs))
    return (float(diffs.mean()),
            float(diffs.mean() / se) if se > EPS else 0.0, len(common))


def main():
    rows = build_panel()
    close = np.array([r[1][C] for r in rows], dtype=float)
    fwd = forward_returns(rows, close, HORIZON)

    qs = cumulative(quote_side(rows), rows, WINDOW)
    z_quote = slot_normalize(qs, rows)

    mean, t, n = paired_spread(rows, z_quote, fwd)
    print(f"\nquote-side pressure, {WINDOW}m window, h={HORIZON}: "
          f"{mean:+.2f} pts (t {t:.2f}, {n} sessions)")

    # --- control 2: the trailing price move on its own --------------------
    print("\nmirror test - trailing price move as the signal")
    for w in (5, 15, 30, 60):
        z_mv = slot_normalize(past_move(rows, close, w), rows)
        m, tt, nn = paired_spread(rows, z_mv, fwd)
        if m is not None:
            print(f"  past {w:>2}m move: {m:>+8.2f} pts (t {tt:>5.2f}, {nn} sessions)")

    # --- how related are they at all? -------------------------------------
    z_mv5 = slot_normalize(past_move(rows, close, WINDOW), rows)
    z_mv60 = slot_normalize(past_move(rows, close, 60), rows)
    for label, z_mv in (("5m", z_mv5), ("60m", z_mv60)):
        both = ~np.isnan(z_quote) & ~np.isnan(z_mv)
        r = float(np.corrcoef(z_quote[both], z_mv[both])[0, 1])
        print(f"\ncorr(quote-side, past {label} move) = {r:+.3f}")

    # --- control 1: double sort on the trailing move ----------------------
    for label, z_mv in (("5m", z_mv5), ("60m", z_mv60)):
        finite = ~np.isnan(z_mv)
        edges = np.quantile(z_mv[finite], [0.2, 0.4, 0.6, 0.8])
        print(f"\nquote-side effect within trailing {label} price-move buckets")
        print(f"  {'bucket':<14} {'long-short pts':>15} {'t':>7} {'sessions':>9}")
        buckets = [("1 falling", z_mv < edges[0]),
                   ("2", (z_mv >= edges[0]) & (z_mv < edges[1])),
                   ("3 flat", (z_mv >= edges[1]) & (z_mv < edges[2])),
                   ("4", (z_mv >= edges[2]) & (z_mv < edges[3])),
                   ("5 rising", z_mv >= edges[3])]
        for name, mask in buckets:
            m, tt, nn = paired_spread(rows, z_quote, fwd, mask & finite)
            if m is None:
                print(f"  {name:<14} {'too few sessions':>15} {'':>7} {nn:>9}")
            else:
                print(f"  {name:<14} {m:>+15.2f} {tt:>7.2f} {nn:>9}")


if __name__ == "__main__":
    main()
