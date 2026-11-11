"""`results/filtered/the5ers_replay/REPLAY.json` -> `results/filtered/the5ers/`,
in the FundedNext pool's format (one JSON per cell + SURVIVORS.json).

The user (2026-11-08): "export it like fundednext". The same filter as
`filtered/fundednext`: the 2026 replay (here The5ers, $5,000, its hours and
costs) with >= 30 trades and net per-trade t >= 1. The symbols being searched
again from scratch on The5ers (RESEARCHED) are left out: their new search
cells replace them.

    py -m sandbox.research._the5ers_replay_export [--dry-run]
"""
from __future__ import annotations

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
SRC = os.path.join(RESULTS, "filtered", "fundednext")
REPLAY = os.path.join(RESULTS, "filtered", "the5ers_replay", "REPLAY.json")
DST = os.path.join(RESULTS, "filtered", "the5ers")
RESEARCHED = {"chfjpy", "eurusd", "gbpaud", "gbpjpy", "usdcad"}
MIN_TRADES, MIN_T = 30, 1.0
WAVE = "the5ers_replay_2026-11-08"
#: The5ers' own search (`results/the5ers`, IS 2024-2025 / OOS 2026, the
#: symbols FundedNext lacks + the 5 FX pairs searched again), screened by
#: `_replay_the5ers` with T5_SRC -- the same filter, added to the same pool
SEARCH_SRC = os.path.join(RESULTS, "the5ers")
SEARCH_REPLAY = os.path.join(RESULTS, "filtered", "the5ers_search_replay", "REPLAY.json")


def _window(stats, label):
    out = dict(stats)
    out["annual"] = {label: {k: stats.get(k) for k in ("max_dd_pct", "return_pct", "trades")}} \
        if label == "2026" else {}
    return out


def main(dry_run=False):
    replay = json.load(open(REPLAY, encoding="utf-8"))
    cells = replay["cells"]
    provisional = not all("net_t_stat" in c["the5ers"]["2026"] for c in cells)
    if provisional:
        # no t yet (the first replay): trades >= 30 and profitable in both
        # windows; the exact t >= 1 export replaces it after the rerun
        print("PROVISIONAL: no net_t_stat in REPLAY.json -- filter = trades>=30, both windows > 0")
    kept, why = [], {"researched": 0, "trades": 0, "t": 0}
    for c in cells:
        t26 = c["the5ers"]["2026"]
        if c["symbol"] in RESEARCHED:
            why["researched"] += 1
        elif (t26["trades"] or 0) < MIN_TRADES:
            why["trades"] += 1
        elif (t26.get("net_t_stat") or 0) < MIN_T if not provisional else                 (t26["return_pct"] <= 0 or c["the5ers"]["2024-2025"]["return_pct"] <= 0):
            why["t"] += 1
        else:
            kept.append(c)
    print(f"{len(kept)} of {len(cells)} kept; dropped {why}")
    if dry_run:
        return
    os.makedirs(DST, exist_ok=True)
    for name in os.listdir(DST):
        if name.endswith(".json"):
            os.remove(os.path.join(DST, name))
    survivors = []
    for c in kept:
        cell = json.load(open(os.path.join(SRC, c["file"]), encoding="utf-8"))
        t = c["the5ers"]
        out = copy.deepcopy(cell)
        out.update(broker="the5ers", initial_balance=replay["balance"],
                   holdout=f"2026-01-01 through {replay['end']}",
                   in_sample=_window(t["2024-2025"], "2024-2025"),
                   out_of_sample=_window(t["2026"], "2026"),
                   study_wave=WAVE)
        out["provenance"] = dict(cell.get("provenance") or {}, replay_of=f"filtered/fundednext/{c['file']}",
                                 fundednext_wave=cell.get("study_wave"))
        out["fundednext_out_of_sample"] = {k: c.get(k) for k in
                                           ("oos_return_pct", "oos_max_dd_pct", "oos_trades",
                                            "oos_pf", "oos_net_bp_per_trade", "oos_net_t_stat")}
        with open(os.path.join(DST, c["file"]), "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=1)
        survivors.append({"family": c["family"], "file": c["file"], "null_verdict": c.get("null_verdict"),
                          "oos_max_dd_pct": t["2026"]["max_dd_pct"], "oos_return_pct": t["2026"]["return_pct"],
                          "study_wave": WAVE, "symbol": c["symbol"], "timeframe": c["timeframe"],
                          "window": c.get("window", cell.get("window")), "oos_trades": t["2026"]["trades"], "oos_pf": t["2026"]["pf"],
                          "oos_net_bp_per_trade": t["2026"].get("net_bp_per_trade"),
                          "oos_net_t_stat": t["2026"].get("net_t_stat")})
    n_replay = len(survivors)
    if os.path.exists(SEARCH_REPLAY):
        search = json.load(open(SEARCH_REPLAY, encoding="utf-8"))["cells"]
        drop = {"trades": 0, "t": 0}
        for c in search:
            t26 = c["the5ers"]["2026"]
            if (t26["trades"] or 0) < MIN_TRADES:
                drop["trades"] += 1
                continue
            if (t26.get("net_t_stat") or 0) < MIN_T:
                drop["t"] += 1
                continue
            cell = json.load(open(os.path.join(SEARCH_SRC, c["file"]), encoding="utf-8"))
            cell["provenance"] = dict(cell.get("provenance") or {}, screened_by="_replay_the5ers T5_SRC=results/the5ers")
            with open(os.path.join(DST, c["file"]), "w", encoding="utf-8") as handle:
                json.dump(cell, handle, indent=1)
            survivors.append({"family": c["family"], "file": c["file"], "null_verdict": c.get("null_verdict"),
                              "oos_max_dd_pct": t26["max_dd_pct"], "oos_return_pct": t26["return_pct"],
                              "study_wave": cell.get("study_wave"), "symbol": c["symbol"],
                              "timeframe": c["timeframe"], "window": c.get("window", cell.get("window")),
                              "oos_trades": t26["trades"], "oos_pf": t26["pf"],
                              "oos_net_bp_per_trade": t26.get("net_bp_per_trade"),
                              "oos_net_t_stat": t26.get("net_t_stat")})
        print(f"search: {len(survivors) - n_replay} of {len(search)} kept; dropped {drop}")
    index = {"count": len(survivors), "from_replay": n_replay, "from_search": len(survivors) - n_replay,
             "provisional": provisional,
             "filter": ("PROVISIONAL (no t yet): 2026 trades>=30, 2024-25 and 2026 return > 0; " if provisional else "") + f"2026 replay (The5ers 5k, broker hours, The5ers costs): trades>={MIN_TRADES}, "
                       f"net per-trade t>={MIN_T:g}; FundedNext filtered pool replayed with fixed params; "
                       f"{', '.join(sorted(RESEARCHED))} left out (searched again from scratch); "
                       f"plus The5ers' own search survivors (results/the5ers) under the same filter",
             "survivors": survivors}
    with open(os.path.join(DST, "SURVIVORS.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=1)
    print(f"wrote {len(survivors)} cells + SURVIVORS.json -> {os.path.abspath(DST)}")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
