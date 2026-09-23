"""What the volatility-target cap is worth, swept.

NEVER TESTED BEFORE THIS. `VOL_TARGET` 0.20 and `VOL_MAX_MULTIPLIER` 3.0 were
inherited and every search in this repository has varied membership and
`risk_scale` underneath them while holding them fixed. Measured over 2022-2026
they are not a regime throttle on most symbols but a persistent cross-symbol
reweighting: the FX crosses sit at ~2.1x on essentially every day and eurjpy is
clipped by the cap on 19% of them, while ethusd sits at 0.38x on 100%
([[vol-throttle-is-per-market-not-per-sleeve]]).

Lowering the cap therefore does something specific: it de-levers the FX bloc,
which is the same usdjpy+eurjpy+gbpjpy cluster that took 97% of an eight-point
drawdown event when the balance grew. It leaves ethusd and ukoil untouched --
they are below 1.0 already and no cap binds them.

THE MARKING CACHE HOLDS THE MULTIPLIERS, so it is cleared between settings.
Without that, every run after the first silently reuses the first cap's sizing.

    MC_LIVE_START=2022-01-01 py -m sandbox.research._seventh_volcap
"""
from __future__ import annotations

import argparse
import pickle
from datetime import datetime, timezone

from sandbox.research import _mc_live as live
from sandbox.research import _mc_live_frontier as fr
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

CAPS = (3.0, 2.5, 2.0, 1.5, 1.0)


def stamp(text):
    return int(datetime.fromisoformat(text)
               .replace(tzinfo=timezone.utc).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk", type=float, default=ecs.CANON_RISK_SCALE)
    parser.add_argument("--target", type=float, default=None,
                        help="also override VOL_TARGET")
    args = parser.parse_args()
    live.arm()
    with open(fr.cache_path(), "rb") as handle:
        state = pickle.load(handle)
    fr._STATE = state
    mc.pin_external_window(live.window())
    members = [state["members"][k] for k in ecs.BOOK]
    if args.target is not None:
        ecs.VOL_TARGET = args.target

    windows = (("IS 22-25", "2022-01-01", "2025-01-01"),
               ("OOS 25-26", "2025-01-01", "2026-08-21"),
               ("2026", "2026-01-01", "2026-08-21"),
               ("full", "2022-01-01", "2026-08-21"))

    print(f"canon {len(ecs.BOOK)} sleeves, risk {args.risk}, "
          f"VOL_TARGET {ecs.VOL_TARGET:.0%}")
    print(f"{'cap':>6}{'window':11}{'return':>11}{'MTMdd':>8}{'pos mo':>9}"
          f"{'mSh':>7}{'uw days':>9}{'worst mo':>10}{'skip':>6}")
    for cap in CAPS:
        ecs.VOL_MAX_MULTIPLIER = cap
        # The multipliers live in the shared marking cache; without clearing it
        # every later cap silently reuses the first one's sizing.
        mc._MARKING.clear()
        ecs._SLEEVE_MEMO.clear()
        for name, lo_text, hi_text in windows:
            lo, hi = stamp(lo_text), stamp(hi_text)
            book = fr._replay(members, lo=lo, hi=hi, risk_scale=args.risk)
            series, sharpe, positive, total = ecs.monthly(book["settled"],
                                                          ecs.CANON_INITIAL)
            curve = [(ts - lo, v) for ts, v in book["marked"]]
            worst = min((r["return_pct"] for r in series.values()), default=0.0)
            print(f"{cap:>6.1f}{name:11}{book['return_pct']:>+10,.0f}%"
                  f"{book['mtm_dd_pct']:>7.1f}%{positive:>5}/{total:<3}"
                  f"{sharpe:>7.2f}{mc.underwater(curve):>9.1f}{worst:>+9.1f}%"
                  f"{sum(book['below_broker_minimum'].values()):>6}")
        print()


if __name__ == "__main__":
    main()
