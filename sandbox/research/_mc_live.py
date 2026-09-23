"""Monte Carlo the canon book with LIVE execution, over the full 1m window.

Same machinery as `exness_combined_montecarlo` -- blocks of calendar time
chained through the real `replay` -- with the execution path of
`exness_live_execution.book`'s `live` run wired in before the trade logs are
built: the broker's own per-minute spread at the entry bar, the entry filled one
publish lag plus bridge queue after the open, the exit filled one whole bar plus
the same lag and queue after the bar it fired on, and no broker-side stop.

TWO THINGS DIFFER FROM THE SEALED STUDY AND BOTH ARE DELIBERATE.

Membership is checked against `exness_combined_strategies.BOOK` instead of being
taken from the sealed `exness_combined_strategies.json` on faith -- that file is
written by whatever `build` ran last and has already been found holding a
different set ([[sealed-json-drifts-from-book]]).

The window is the full canon window, 2025-01-01 .. 2026-08-21, rather than the
seven-month tick window: the BARS maps reach 2020, or 2022 on jp225 and ukoil,
on every mapped symbol. `MC_LIVE_START=2022-01-01` moves it back; bars before a
symbol's map keep the constant spread and the vendor open, so a longer window is
a LOWER BOUND on execution cost, not a fuller measurement of it.

Separate cache and output paths per window start, so neither the sealed study
nor another window's is ever overwritten.
"""

import json
import os
import sys
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_live_execution as le
from sandbox.research import exness_combined_montecarlo as mc

SOURCE = "bars"
BUILD_PATH = os.path.join(mc.RESULTS, "exness_combined_strategies_live.json")

#: Window start, as `YYYY-MM-DD`, from the environment. Empty means canon's own
#: 2025-01-01.
#:
#: SET IN ONE PLACE BECAUSE IT HAS TO MOVE FOUR THINGS TOGETHER: the block
#: tiling, the window every sleeve's trades are generated over, the external
#: sleeves' imported trades, and the pinned external price grid. Moving any
#: three of the four produces a book that still runs and is silently measuring
#: something else -- a shorter grid marks fewer excursions, and an external
#: sleeve whose trades were imported over a different span simply has none in
#: the extra years.
START = os.environ.get("MC_LIVE_START", "")

_PIN = mc.pin_external_window


def window():
    return ((START or "2025-01-01"), ecs.CANON_DATA_END)


def arm():
    """Every global `_book_runs` sets for its `live` run, set the same way."""
    if START:
        ecs.ef.IS_END = int(datetime.fromisoformat(START)
                            .replace(tzinfo=timezone.utc).timestamp())

    def pin(span=None):
        _PIN(span or window())

    mc.pin_external_window = pin
    ecs.MAPS_OVERRIDE = le.maps_path(SOURCE)
    ecs.TICK_COSTS = True
    ecs.BROKER_STOPS = False
    ecs.FORCE_MINIMUM_LOT = True
    ecs.UNCAPPED = True
    ecs.GLOBAL_SIZING_CAP = 1500.0
    ecs.MAX_DD_CONCENTRATION = None
    ecs.FAIR_CAP = False
    ecs._TICK_COST_CACHE.clear()
    ecs._SLEEVE_MEMO.clear()
    tag = f"_{START[:4]}" if START else ""
    mc.CACHE = os.path.join(ecs.CACHE_DIR,
                            f"montecarlo_canon_inputs_live{tag}.pkl")
    mc.OUT_PATH = os.path.join(
        mc.RESULTS, f"exness_combined_montecarlo_live{tag}.json")


def members():
    """`BOOK`'s member rows, resolved once by `build` and then reused.

    THE SEALED FILE IS USED ONLY IF ITS MEMBERSHIP IS `BOOK`. It is written by
    whatever `build` ran last and has already been found holding 18 members
    while `BOOK` held 23, which ran a Monte Carlo over 2,707 trades instead of
    6,852 and printed a plausible number for it
    ([[sealed-json-drifts-from-book]]). Checked rather than trusted, and
    rebuilt from `BOOK` when it does not match.

    `build` writes to `OUT_PATH`, so that is redirected first: regenerating the
    sealed canon file as a side effect of a Monte Carlo would be a silent edit
    to the record every other module reads.
    """
    if os.path.exists(ecs.OUT_PATH):
        with open(ecs.OUT_PATH, encoding="utf-8") as handle:
            sealed = json.load(handle)["members"]
        if {f"{m['symbol']}:{m['family']}" for m in sealed} == set(ecs.BOOK):
            return sealed
    if not os.path.exists(BUILD_PATH):
        sealed_out = ecs.OUT_PATH
        try:
            ecs.OUT_PATH = BUILD_PATH
            ecs.build(len(ecs.BOOK), ecs.MAX_DOWN_RHO, ecs.MAX_LOSS_LIFT,
                      risk_scale=ecs.CANON_RISK_SCALE,
                      gross_cap=ecs.CANON_GROSS_CAP,
                      initial=ecs.CANON_INITIAL, members_exact=ecs.BOOK)
        finally:
            ecs.OUT_PATH = sealed_out
    with open(BUILD_PATH, encoding="utf-8") as handle:
        return json.load(handle)["members"]


def prepare():
    """`mc.prepare`, off `BOOK` rather than off the sealed file."""
    logs, bars_by, ctx_by = {}, {}, {}
    rows = members()
    for member in rows:
        key = f"{member['symbol']}:{member['family']}"
        if "params" in member:
            member["params"] = ecs._retuple(member["params"])
        if key in ecs.EXTERNAL:
            logs[key] = ecs.external_trades(key, window=window())
            continue
        ecs.ef.resolve(member["symbol"], allow_stale=True)
        _result, log, bars, ctx = ecs.sleeve_trades(member)
        logs[key] = log
        bars_by[member["symbol"]] = bars
        ctx_by[member["symbol"]] = {"cfg": ctx["cfg"],
                                    "symbol": ctx.get("symbol",
                                                      member["symbol"])}
    return {"members": rows, "logs": logs,
            "bars_by": bars_by, "ctx_by": ctx_by}


_MC_INIT = mc._init_worker


def _init_worker(cache_path, block_days):
    arm()
    _MC_INIT(cache_path, block_days)


if __name__ == "__main__":
    arm()
    mc.prepare = prepare
    mc._init_worker = _init_worker
    print(f"LIVE EXECUTION  maps={os.path.basename(ecs.MAPS_OVERRIDE)}  "
          f"window {le._stamp(ecs.ef.IS_END)} .. {le._stamp(ecs.ef.OOS_END)}",
          flush=True)
    sys.argv = ["mc"] + sys.argv[1:]
    mc.main()
