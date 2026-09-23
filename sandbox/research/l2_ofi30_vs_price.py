"""Does 30-minute cumulative OFI predict anything the price move does not?

`ofi_deep@30m` held 30 minutes earned +3.02 points per trade against a
volatility-matched control of -0.54 on 2025.  Before that can mean anything,
one alternative has to be ruled out: order flow and price move together, so a
30-minute sum of book pressure may simply be a restatement of the 30-minute
price change -- an OHLCV effect that needs no level-two feed and that
`nq_noise_momentum` already trades for free.

Three strategies, identical machinery, identical brackets, identical matched
control:

1. the candidate, `ofi_deep` summed over 30 minutes;
2. the trailing 30-minute price move, with no level-two data at all;
3. the candidate *residualised* on the price move -- the part of the book
   signal that price cannot explain.

If (2) matches (1), the candidate is price in disguise.  If (3) collapses, the
same conclusion holds by a different route.  Only if (3) survives does the book
carry something of its own.

2025 only.
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np

from sandbox.data import C
from sandbox.execution import LONG, SHORT, Signal
from sandbox.research.l2_discovery import (
    cumulative, minute_of, raw_signals, session_of, slot_normalize,
)
from sandbox.research.l2_ofi30_strategy import ENTRY_LAST_MINUTE, HOLD, WINDOW
from sandbox.research.l2_quote_side_strategy import (
    panel, report, trailing_range, ts_of,
)

CUTS = (2.0, 3.0)
STOP, TARGET = 200.0, 0.0


def past_move(rows, close, window):
    n = len(rows)
    out = np.full(n, np.nan)
    for i in range(n):
        j = i - window
        if j >= 0 and session_of(rows[j][0]) == session_of(rows[i][0]):
            out[i] = close[i] - close[j]
    return out


def residualise(target, control):
    """target minus its least-squares projection on control, where both exist."""
    both = ~np.isnan(target) & ~np.isnan(control)
    out = np.full(len(target), np.nan)
    x, y = control[both], target[both]
    beta = float(np.dot(x - x.mean(), y - y.mean()) / np.dot(x - x.mean(), x - x.mean()))
    out[both] = y - beta * x
    return out, beta


def build(rows, z, cut):
    """Signals complete in minute `i` and enter at the open of minute `i + 1`.

    `z[i]` is built from minute `i`'s close and its completed order flow, so
    entering at minute `i`'s own open would trade on the bar the signal is made
    of.  `LIQUIDITY_STRATEGY_CANDIDATES.md` requires `t + 1`; this is that rule.
    """
    out, busy_until = [], -1
    for i, (ts, _bar, _f) in enumerate(rows):
        entry = i + 1
        if i <= busy_until or minute_of(ts) > ENTRY_LAST_MINUTE:
            continue
        if entry >= len(rows) or session_of(rows[entry][0]) != session_of(ts):
            continue
        if math.isnan(z[i]):
            continue
        if z[i] >= cut:
            side = LONG
        elif z[i] <= -cut:
            side = SHORT
        else:
            continue
        out.append(Signal(entry, side, STOP, TARGET, HOLD))
        busy_until = entry + HOLD
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="sandbox/results/l2_ofi30_vs_price.json")
    args = parser.parse_args()

    rows = panel(ts_of("2025-02-12"), ts_of("2026-01-01"))
    close = np.array([r[1][C] for r in rows], dtype=float)
    vol = trailing_range(rows)

    z_ofi = slot_normalize(cumulative(raw_signals(rows)["ofi_deep"], rows, WINDOW),
                           rows)
    z_mv = slot_normalize(past_move(rows, close, WINDOW), rows)

    both = ~np.isnan(z_ofi) & ~np.isnan(z_mv)
    r = float(np.corrcoef(z_ofi[both], z_mv[both])[0, 1])
    print(f"\ncorr(ofi_deep@30m, past 30m price move) = {r:+.3f} "
          f"({both.sum()} shared minutes)")

    z_res, beta = residualise(z_ofi, z_mv)
    print(f"residualising ofi on price move, beta = {beta:+.3f}")

    runs = []
    for label, z in (("ofi_deep@30m (candidate)", z_ofi),
                     ("past 30m price move (no L2)", z_mv),
                     ("ofi_deep residualised on price", z_res)):
        for cut in CUTS:
            # entries land one minute after the signal, so the control pool
            # has to reach one minute further than the signal cutoff
            got = report(f"{label}, |z|>={cut}", rows, build(rows, z, cut), vol,
                         cutoff=ENTRY_LAST_MINUTE + 1)
            if got:
                runs.append({"signal": label, "cut": cut,
                             **{k: v for k, v in got.items() if k != "stats"},
                             "pf": got["stats"]["pf"],
                             "pos_months": got["stats"]["pos_months"]})

    print(f"\n{'signal':<34} {'cut':>4} {'trades':>7} {'net/tr':>9} "
          f"{'control':>9} {'edge':>8} {'z':>7} {'pf':>7}")
    for x in runs:
        print(f"{x['signal']:<34} {x['cut']:>4.1f} {x['trades']:>7} "
              f"{x['edge']:>+9.3f} {x['control_mean']:>+9.3f} "
              f"{x['edge'] - x['control_mean']:>+8.3f} {x['z']:>+7.2f} "
              f"{x['pf']:>7}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"corr_ofi_price": r, "beta": beta, "runs": runs}, fh,
                  indent=2, default=str)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
