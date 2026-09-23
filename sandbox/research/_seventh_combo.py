"""Whole candidate books, not one addition at a time.

SINGLE-CHANGE DELTAS DO NOT ADD UP. Two sleeves that each look good alone can be
the same bet, and because they share one compounding balance the second is sized
against equity the first has already spent -- so a pair's drawdown is not the sum
of its parts ([[combined-book-stacks-risk-budgets]]). Every combination has to be
replayed as the book it actually produces
([[drop-test-not-standalone-return-values-a-sleeve]]).

Realised only, single process. Whatever survives here goes to
`_seventh_mc` for the distribution before anything is seated
([[refill-guarded-on-one-path-hides-its-tail]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_combo
"""
from __future__ import annotations

import argparse
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

HK = "hk50:level_confluence"
EP = "ethusd:pullback"
CC = "usdjpy:cci"
ES = "es:regime_breakout"
FD = "usdjpy:fracdiff"

SETS = (
    ("canon 20", ()),
    ("+EP", (EP,)),
    ("+EP+HK", (EP, HK)),
    ("+EP+FD", (EP, FD)),
    ("+EP+ES", (EP, ES)),
    ("+FD+ES", (FD, ES)),
    ("+EP+HK+FD", (EP, HK, FD)),
    ("+EP+HK+ES", (EP, HK, ES)),
    ("+EP+FD+ES", (EP, FD, ES)),
    ("+EP+HK+FD+ES", (EP, HK, FD, ES)),
    ("+all five", (EP, HK, FD, ES, CC)),
    ("+CC alone", (CC,)),
)


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())

    windows = (("IS", "2022-01-01", "2025-01-01"),
               ("OOS", "2025-01-01", "2026-08-21"),
               ("2026", "2026-01-01", "2026-08-21"),
               ("full", "2022-01-01", "2026-08-21"))

    print(f"EP ethusd:pullback  HK hk50:level_confluence  "
          f"FD usdjpy:fracdiff  ES es:regime_breakout  CC usdjpy:cci")
    print(f"risk {args.risk}, ${ecs.CANON_INITIAL:,.0f}, live execution\n")
    header = f"{'book':16}{'n':>4}"
    for name, _lo, _hi in windows:
        header += f"{name + ' ret':>11}{'dd':>7}{'mo':>7}{'uw':>7}"
    print(header)
    for label, add in SETS:
        keys = list(ecs.BOOK) + list(add)
        members = [state["members"][k] for k in keys]
        line = f"{label:16}{len(keys):>4}"
        for _name, lo_text, hi_text in windows:
            lo, hi = stamp(lo_text), stamp(hi_text)
            book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk)
            _series, _sh, positive, total = ecs.monthly(book["settled"],
                                                        ecs.CANON_INITIAL)
            curve = [(ts - lo, v) for ts, v in book["marked"]]
            line += (f"{book['return_pct']:>+10,.0f}%"
                     f"{book['mtm_dd_pct']:>6.1f}%"
                     f"{positive:>4}/{total:<2}"
                     f"{mc.underwater(curve):>7.0f}")
        print(line, flush=True)


if __name__ == "__main__":
    main()
