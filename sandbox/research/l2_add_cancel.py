"""Adds and cancels carry opposite-signed information; OFI nets them away.

`nq_ofi_momentum.rs` trades `(bid_add - bid_cancel) - (ask_add - ask_cancel)`.
The discovery screen suggests that subtraction is destroying the signal: which
side is *adding* and which side is *cancelling* predict opposite directions, so
netting them leaves the residue that strategy has been trading.

This maps the two components separately over a window/horizon grid, checks they
are not the same series, and forms the combined signal.  2025 only.
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

WINDOWS = (1, 3, 5, 10, 15, 30)
HORIZONS = (15, 30, 60, 90)


def components(rows):
    """Add-side and cancel-side book pressure, each as a bounded ratio."""
    n = len(rows)
    add = np.zeros(n)
    cancel = np.zeros(n)
    ofi = np.zeros(n)
    for i, (_ts, _bar, f) in enumerate(rows):
        adds = f["bid_add_volume"] + f["ask_add_volume"]
        cancels = f["bid_cancel_volume"] + f["ask_cancel_volume"]
        add[i] = (f["bid_add_volume"] - f["ask_add_volume"]) / (adds + EPS)
        # positive = the ask side is the one being pulled
        cancel[i] = (f["ask_cancel_volume"] - f["bid_cancel_volume"]) / (cancels + EPS)
        ofi[i] = ((f["bid_add_volume"] - f["bid_cancel_volume"])
                  - (f["ask_add_volume"] - f["ask_cancel_volume"]))
    return {"add": add, "cancel": cancel, "ofi_deep": ofi}


def paired_spread(rows, z, fwd):
    hi, lo = defaultdict(list), defaultdict(list)
    for i in np.nonzero(z >= Z_CUT)[0]:
        if not math.isnan(fwd[i]):
            hi[session_of(rows[i][0])].append(fwd[i])
    for i in np.nonzero(z <= -Z_CUT)[0]:
        if not math.isnan(fwd[i]):
            lo[session_of(rows[i][0])].append(fwd[i])
    common = sorted(set(hi) & set(lo))
    if len(common) < 30:
        return None, None, 0
    diffs = np.array([np.mean(hi[d]) - np.mean(lo[d]) for d in common])
    se = diffs.std(ddof=1) / math.sqrt(len(diffs))
    return float(diffs.mean()), float(diffs.mean() / se) if se > EPS else 0.0, len(common)


def grid(label, series_by_window, rows, fwd_by_h):
    print(f"\n{label}: long-short points (session-clustered t)")
    header = "".join(f"{'h=' + str(h):>18}" for h in HORIZONS)
    print(f"{'window':>8}{header}")
    for window, z in series_by_window.items():
        cells = ""
        for h in HORIZONS:
            mean, t, n = paired_spread(rows, z, fwd_by_h[h])
            cells += "  n/a".rjust(18) if mean is None else f"{mean:>+11.2f} ({t:>4.2f})"
        print(f"{window:>8}{cells}")


def main():
    rows = build_panel()
    close = np.array([r[1][C] for r in rows], dtype=float)
    fwd_by_h = {h: forward_returns(rows, close, h) for h in HORIZONS}
    comp = components(rows)

    zs = {}
    for name in ("add", "cancel", "ofi_deep"):
        zs[name] = {w: slot_normalize(cumulative(comp[name], rows, w) if w > 1
                                      else comp[name], rows)
                    for w in WINDOWS}
        grid(name, zs[name], rows, fwd_by_h)

    # combined: bid-side building AND ask-side pulling should agree
    combined = {}
    for w in WINDOWS:
        a, c = zs["add"][w], zs["cancel"][w]
        both = ~np.isnan(a) & ~np.isnan(c)
        z = np.full(len(a), np.nan)
        z[both] = (a[both] - c[both]) / math.sqrt(2.0)
        combined[w] = z
    grid("combined (add - cancel)", combined, rows, fwd_by_h)

    print("\ncorrelations between the two components")
    for w in WINDOWS:
        a, c = zs["add"][w], zs["cancel"][w]
        both = ~np.isnan(a) & ~np.isnan(c)
        r = float(np.corrcoef(a[both], c[both])[0, 1])
        ro = float(np.corrcoef(a[both], zs["ofi_deep"][w][both])[0, 1])
        rc = float(np.corrcoef(c[both], zs["ofi_deep"][w][both])[0, 1])
        print(f"  window {w:>3}m: corr(add, cancel) = {r:+.3f}   "
              f"corr(add, ofi) = {ro:+.3f}   corr(cancel, ofi) = {rc:+.3f}")


if __name__ == "__main__":
    main()
