"""Test consistency candidates in the book: positive months, underwater, drawdown.

Scores each change on the three things being traded off, and on the decay rule
that produced the current book, so a candidate cannot buy consistency with a
sleeve whose edge has already gone.

`underwater` is `exness_combined_montecarlo.underwater` -- the LONGEST single run
below the high-water mark, in days -- so the number is the same one the Monte
Carlo reports and the two can be read together.

DRAWDOWN HERE IS ONE PATH AND IS ONLY A FILTER. A change that survives this must
still be measured on the distribution before it is seated: a refill guarded on
realised drawdown ran 15.6% realised against a 35.1% Monte Carlo p95
([[refill-guarded-on-one-path-hides-its-tail]]).

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_consistency \
        --add a:b,c:d --drop e:f
"""
from __future__ import annotations

import argparse
import math
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

ADDS = ("uk100:gated_fade", "de40:value_area", "usdjpy:cci",
        "usdjpy:half_life", "ethusd:kalman", "ethusd:kendall",
        "xaueur:autocorr")
DROPS = ("audusd:zscore", "usdjpy:volume_thrust")


def decay(state, key):
    """2026 mean R against the 2022-2025 mean, and the R banked in 2026."""
    by = {}
    for t in state["logs"][key]:
        if t.get("distance", 0) <= 0:
            continue
        year = datetime.fromtimestamp(t["exit_ts"], tz=timezone.utc).year
        by.setdefault(year, []).append(t["gross"] / t["distance"])
    r26 = by.get(2026, [])
    early = [r for y in (2022, 2023, 2024, 2025) for r in by.get(y, [])]
    if not r26 or not early:
        return 0.0, 0.0, float("nan")
    m26 = sum(r26) / len(r26)
    me = sum(early) / len(early)
    return sum(r26), sum(by.get(2025, [])), (m26 / me if me > 0 else float("nan"))


def score(state, keys, risk, lo, hi):
    members = [state["members"][k] for k in keys]
    book = fr._replay(members, lo=lo, hi=hi, risk_scale=risk)
    series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                  ecs.CANON_INITIAL)
    curve = [(ts - lo, v) for ts, v in book["marked"]]
    return {"return_pct": book["return_pct"], "dd": book["mtm_dd_pct"],
            "sharpe": sharpe, "positive": positive, "total": total,
            "uw": mc.underwater(curve),
            "worst": min((r["return_pct"] for r in series.values()),
                         default=0.0),
            "skip": sum(book["below_broker_minimum"].values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--add", default="")
    parser.add_argument("--drop", default="")
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    lo, hi = fr._bounds("full")
    split = lambda s: [x for x in s.replace(" ", "").split(",") if x]

    trials = [("(canon)", [], [])]
    if args.add or args.drop:
        trials.append(("custom", split(args.add), split(args.drop)))
    else:
        trials += [(f"+{a}", [a], []) for a in ADDS]
        trials += [(f"-{d}", [], [d]) for d in DROPS]

    print(f"canon {len(ecs.BOOK)} sleeves @ risk {args.risk}, "
          f"full window, live execution")
    print(f"{'change':28}{'n':>4}{'return':>11}{'MTMdd':>7}{'pos mo':>9}"
          f"{'mSh':>6}{'uw days':>9}{'worst mo':>10}{'skip':>6}"
          f"{'2026 R':>9}{'decay':>8}")
    for label, add, drop in trials:
        keys = [k for k in ecs.BOOK if k not in set(drop)] + add
        row = score(state, keys, args.risk, lo, hi)
        if add:
            r26, _r25, ratio = decay(state, add[0])
            tail = f"{r26:>9.1f}{ratio:>7.2f}x"
        else:
            tail = f"{'-':>9}{'-':>8}"
        print(f"{label[:27]:28}{len(keys):>4}{row['return_pct']:>+10,.0f}%"
              f"{row['dd']:>6.1f}%{row['positive']:>5}/{row['total']:<3}"
              f"{row['sharpe']:>6.2f}{row['uw']:>9.1f}{row['worst']:>+9.1f}%"
              f"{row['skip']:>6}{tail}")


if __name__ == "__main__":
    main()
