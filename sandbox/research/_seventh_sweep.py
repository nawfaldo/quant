"""Score many memberships against one loaded frontier cache.

`_seventh_report` reloads a 76 MB pickle per book, which is most of its runtime
and makes a thirteen-way comparison a coffee break. This loads once and replays
each candidate, so the cost is the replays themselves.

WHAT IT RANKS ON, AND WHY BOTH. Return alone picks the lumpiest book on offer --
`usdjpy:half_life` nearly doubles this one and costs four positive months -- and
consistency alone picks whichever book trades least. So every candidate prints
BOTH eras' return AND the book's positive-month count, and the choice between
them is made in the open rather than by a scalar.

Drawdown here is the realised order only. It is a sanity rail, not the band: the
band is a distribution and only `_mc_live_frontier risk`/`eval` measures it.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_sweep \
        --base a:b,c:d --add-each e:f,g:h
    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_sweep \
        --sets "a:b,c:d;e:f"
"""
from __future__ import annotations

import argparse
import pickle

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs


def score(state, keys, risk_scale):
    members = [state["members"][k] for k in keys]
    out = {}
    for era in ("is", "oos", "full"):
        lo, hi = fr._bounds(era)
        book = fr._replay(members, lo=lo, hi=hi, risk_scale=risk_scale)
        series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                      ecs.CANON_INITIAL)
        out[era] = {"return_pct": book["return_pct"],
                    "mtm_dd_pct": book["mtm_dd_pct"],
                    "sharpe": sharpe, "positive": positive, "total": total,
                    "worst": min((r["return_pct"] for r in series.values()),
                                 default=0.0),
                    "trades": book["trades"],
                    "below_min": sum(book["below_broker_minimum"].values())}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="",
                        help="sleeves to add to BOOK for every candidate")
    parser.add_argument("--drop", default="")
    parser.add_argument("--add-each", default="",
                        help="one candidate per name, each added to the base")
    parser.add_argument("--sets", default="",
                        help="';'-separated add-sets, each a whole candidate")
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    args = parser.parse_args()
    live.arm()
    split = lambda s: [x for x in s.replace(" ", "").split(",") if x]

    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())

    drop = set(split(args.drop))
    base = [k for k in ecs.BOOK if k not in drop] + split(args.base)
    trials = [("(base)", [])]
    trials += [(name, [name]) for name in split(args.add_each)]
    trials += [(" + ".join(split(s)), split(s))
               for s in args.sets.split(";") if s.strip()]

    print(f"base {len(base)} sleeves   risk_scale {args.risk}   "
          f"${ecs.CANON_INITIAL:,.0f}   live execution")
    if drop:
        print(f"  dropped: {', '.join(sorted(drop))}")
    print(f"\n{'candidate':34}{'n':>4}{'IS ret':>10}{'IS dd':>8}"
          f"{'OOS ret':>10}{'OOS dd':>8}{'full ret':>11}"
          f"{'mSh':>7}{'pos mo':>9}{'worst':>8}{'skip':>6}")
    for label, extra in trials:
        keys = base + [k for k in extra if k not in base]
        missing = [k for k in keys if k not in state["members"]]
        if missing:
            print(f"{label:34}  not in cache: {', '.join(missing)}")
            continue
        row = score(state, keys, args.risk)
        i, o, f = row["is"], row["oos"], row["full"]
        print(f"{label[:33]:34}{len(keys):>4}{i['return_pct']:>+9.0f}%"
              f"{i['mtm_dd_pct']:>7.1f}%{o['return_pct']:>+9.0f}%"
              f"{o['mtm_dd_pct']:>7.1f}%{f['return_pct']:>+10.0f}%"
              f"{f['sharpe']:>7.2f}{f['positive']:>5}/{f['total']:<3}"
              f"{f['worst']:>+7.1f}%{f['below_min']:>6}")


if __name__ == "__main__":
    main()
