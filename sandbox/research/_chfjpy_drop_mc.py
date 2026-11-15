"""Leave-out / long-only challenge Monte Carlo for the live FN canon's CHFJPY sleeves.

Seal f61f9b946b90 (50 @ 0.03), states cached by `fundednext_combined_strategies test`
(`.cache/fundednext_challenge_test_{2024-2025,2026}.pkl`). Each variant edits the
CHFJPY members only; every cell is the `_grid_job` logic (10k paths, seed 2026).

  py -m sandbox.research._chfjpy_drop_mc [workers]

Resumable: finished cells are kept in results/chfjpy_drop_mc.json.
"""
import json
import os
import pickle
import platform
import sys

platform._wmi = None
from sandbox.research import fundednext_combined_strategies as fc  # noqa: E402

RISK, PATHS, SEED = 0.03, 10_000, 2026
OUT = os.path.join(fc.RESULTS, "chfjpy_drop_mc.json")
ST = "chfjpy:stochastic@15m-evening"
ON = "chfjpy:overnight@5m-night"
AP = "chfjpy:adaptive_pullback@5m-offhour"
VARIANTS = {                     # name -> (dropped sleeves, long-only sleeves)
    "full": ((), ()),
    "-stochastic": ((ST,), ()),
    "-overnight": ((ON,), ()),
    "-adaptive_pullback": ((AP,), ()),
    "-all chfjpy": ((ST, ON, AP), ()),
    "long-only stoch+overnight": ((), (ST, ON)),
    "long-only all chfjpy": ((), (ST, ON, AP)),
}
CELLS = ((1.0, 1.0), (0.5, 1.0), (1.0, 0.5), (0.5, 0.5), (1.0, 0.25), (1.0, 0.0))
WINDOWS = ("2024-2025", "2026")

_STATES = {}


def variant(state, name):
    drop, longs = VARIANTS[name]
    keep = [m for m in state["members"] if fc._key(m) not in drop]
    keys = {fc._key(m) for m in keep}
    logs = {k: ([t for t in v if t["side"] > 0] if k in longs else v)
            for k, v in state["logs"].items() if k in keys}
    return {**state, "members": keep, "logs": logs,
            "scale": {k: v for k, v in state["scale"].items() if k in keys}}


def job(item):
    from sandbox.research import exness_combined_montecarlo as mc
    from sandbox.research import fundednext_challenge as ch
    window, name, alive, keep = item
    if window not in _STATES:
        _STATES.clear()
        with open(os.path.join(fc._CACHE, f"fundednext_challenge_test_{window}.pkl"), "rb") as h:
            _STATES[window] = pickle.load(h)
    state = variant(_STATES[window], name)
    if alive == 1:
        mc._MARKING.clear()
        logs = state["logs"] if keep == 1 else {k: fc._dud(v, keep) for k, v in state["logs"].items()}
        rows = ch.mc_paths(ch.daily_series({**state, "logs": logs}, RISK, fixed=True), PATHS, SEED)
    else:
        rows, _, _ = fc.stressed_paths(state, RISK, PATHS, 1 - alive, 0.0, fc.GRID_DRAWS, keep)
    return item, ch.mc_stats(rows)


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    todo = [(w, n, a, k) for w in WINDOWS for n in VARIANTS for a, k in CELLS
            if f"{w}|{n}|{a:g}/{k:g}" not in done]
    print(f"{len(todo)} cells to run, {workers} workers", flush=True)
    import multiprocessing
    with multiprocessing.Pool(workers) as pool:
        for (w, n, a, k), stats in pool.imap_unordered(job, todo):
            done[f"{w}|{n}|{a:g}/{k:g}"] = stats
            with open(OUT + ".tmp", "w") as h:
                json.dump(done, h, indent=1, default=str)
            os.replace(OUT + ".tmp", OUT)
            print(f"{w} {n:28} alive {a:.0%} kept {k:.0%}: pass {stats['pass']}% "
                  f"median {stats['median_trading_days']}d max-fail {stats.get('max')}", flush=True)
    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
