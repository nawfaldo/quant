"""Why 2026 barely moves whatever is added, and at what balance it starts to.

THE OBSERVATION. Every candidate tested moves the 2026 window by a rounding
error -- canon makes +390% and the five best additions make +391% to +419% --
while the same sleeves are worth hundreds of points on the longer windows.

THE HYPOTHESIS. 2026 is eight months from a cold $500. At that balance a
sleeve's requested size is far below the broker minimum, so `force_minimum_lot`
rounds it UP and every sleeve places the same floor regardless of what the risk
fraction asked for. A book of twenty such sleeves is not twenty edges weighted
by conviction, it is twenty minimum lots; adding a twenty-first adds one more
minimum lot, not a proportional share of its edge
([[four-hundred-dollars-selects-the-sleeves-for-you]],
[[minimum-lot-de-diversifies-pro-cyclically]]).

THE TEST. Run 2026 alone from rising starting balances, with and without each
candidate. If the floor is the cause, the additions are indistinguishable at
$500 and separate as the balance clears the floor.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_2026
"""
from __future__ import annotations

import argparse
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

PICKS = ("usdjpy:cci", "usdjpy:fracdiff", "ethusd:pullback",
         "es:regime_breakout", "hk50:level_confluence")
STARTS = (500.0, 2_000.0, 6_000.0, 20_000.0)


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
    lo, hi = stamp("2026-01-01"), stamp("2026-08-21")

    print(f"2026-01-01 .. 2026-08-21, canon {len(ecs.BOOK)} sleeves, "
          f"risk {args.risk}")
    print(f"{'book':26}" + "".join(f"{'$' + format(s, ',.0f'):>13}"
                                   for s in STARTS))
    print(f"{'':26}" + "".join(f"{'ret':>7}{'dd':>6}" for _ in STARTS))
    rows = {}
    for label, add in [("canon 20", [])] + [(f"+{k}", [k]) for k in PICKS]:
        members = [state["members"][k] for k in list(ecs.BOOK) + add]
        line, got = f"{label[:25]:26}", {}
        for start in STARTS:
            book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk,
                              initial=start)
            got[start] = book["return_pct"]
            line += f"{book['return_pct']:>6.0f}%{book['mtm_dd_pct']:>5.1f}%"
        rows[label] = got
        print(line)

    print(f"\nGAIN OVER CANON, by starting balance")
    print(f"{'book':26}" + "".join(f"{'$' + format(s, ',.0f'):>11}"
                                   for s in STARTS))
    for label in rows:
        if label == "canon 20":
            continue
        line = f"{label[:25]:26}"
        for start in STARTS:
            line += f"{rows[label][start] - rows['canon 20'][start]:>+10.1f}%"
        print(line)

    # How much of the book is actually being SIZED rather than floored.
    print(f"\nLOT FLOOR PRESSURE at each balance (canon, 2026)")
    print(f"  {'start $':>10}{'trades':>9}{'skipped':>9}{'final $':>12}")
    members = [state["members"][k] for k in ecs.BOOK]
    for start in STARTS:
        book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk,
                          initial=start)
        print(f"  {start:>10,.0f}{book['trades']:>9,}"
              f"{sum(book['below_broker_minimum'].values()):>9,}"
              f"{book['final']:>12,.0f}")


if __name__ == "__main__":
    main()
