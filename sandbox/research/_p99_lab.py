"""Score many book variants on the SAME Monte Carlo paths.

Each variant is `(keys, risk, gross_cap)`. Every variant is chained over the
same block orders (seed s -> the same fortnights for every variant), so two
variants differ only by what they are, not by which paths they drew -- a paired
comparison, which is what makes a 1,000-path p99 usable for ranking.

The trade logs come from the canon report's window caches
(`.cache/mc_books_canon_<window>.pkl`) plus any EXTRA pool pickled beside them
(`.cache/p99_extra_<window>.pkl`, same shape), so candidate sleeves can be added.

    from sandbox.research import _p99_lab as lab
    rows = lab.evaluate("2224", [(keys, 0.13, None), ...], paths=1000)
"""
import platform

platform._wmi = None

import math
import multiprocessing
import os
import pickle
import random
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs
from sandbox.research import exness_combined_montecarlo as mc

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")
WINDOWS = {
    "2224": ("mc_books_canon_2224_2022.pkl", "2022-01-01", "2025-01-01"),
    "2526": ("mc_books_canon_2526.pkl", "2025-01-01", "2026-08-21"),
    "2226": ("mc_books_canon_2226_2022.pkl", "2022-01-01", "2026-08-21"),
    "26": ("mc_books_canon_26_2026.pkl", "2026-01-01", "2026-08-21"),
}
WORKERS = int(os.environ.get("LAB_WORKERS", "12"))


def ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def load_state(window):
    name, lo, hi = WINDOWS[window]
    with open(os.path.join(CACHE, name), "rb") as handle:
        state = pickle.load(handle)
    extra = os.path.join(CACHE, f"p99_extra_{window}.pkl")
    if os.path.exists(extra):
        with open(extra, "rb") as handle:
            more = pickle.load(handle)
        state["members"] = list(state["members"]) + list(more["members"])
        state["logs"].update(more["logs"])
        for symbol, bars in more["bars_by"].items():
            state["bars_by"].setdefault(symbol, bars)
        for symbol, ctx in more["ctx_by"].items():
            state["ctx_by"].setdefault(symbol, ctx)
    state["by_key"] = {f"{m['symbol']}:{m['family']}": m for m in state["members"]}
    return state, ts(lo), ts(hi)


_S = {}


def _arm():
    ecs.FORCE_MINIMUM_LOT = True
    ecs.UNCAPPED = True
    ecs.GLOBAL_SIZING_CAP = 1500.0
    ecs.MAX_DD_CONCENTRATION = None
    ecs.FAIR_CAP = False


def _init(window):
    _arm()
    state, lo, hi = load_state(window)
    ecs.ef.IS_END, ecs.ef.OOS_END = lo, hi
    _S.update(state=state, blocks=mc.blocks_of(14, lo, hi), lo=lo, hi=hi)


def _sub(keys):
    state = _S["state"]
    return {"members": [state["by_key"][k] for k in keys], "logs": state["logs"],
            "bars_by": state["bars_by"], "ctx_by": state["ctx_by"]}


def _task(task):
    index, keys, risk, cap, seed = task
    ecs.CANON_RISK_SCALE, ecs.CANON_GROSS_CAP = risk, cap
    sub = _sub(keys)
    if seed is None:          # the realised path
        book = mc.run_book(sub, lo=_S["lo"], hi=_S["hi"], initial=500.0)
        return index, seed, book["return_pct"], book["mtm_dd_pct"], book["by_sleeve"]
    rng = random.Random(seed)
    order = [rng.choice(_S["blocks"]) for _ in _S["blocks"]]
    book, _returns, curve = mc.chain(sub, order, 500.0)
    dd, _low = mc.drawdown_of([v for _e, v in curve])
    return index, seed, 100.0 * (book["final"] / 500.0 - 1.0), dd, None


def q(values, pct):
    return mc.quantile(sorted(values), pct)


def evaluate(window, configs, paths=1000, seed0=1, realised=True):
    """`configs`: list of `(keys, risk, cap)`. Returns one dict per config."""
    tasks = []
    for index, (keys, risk, cap) in enumerate(configs):
        keys = tuple(keys)
        if realised:
            tasks.append((index, keys, risk, cap, None))
        tasks.extend((index, keys, risk, cap, seed) for seed in range(seed0, seed0 + paths))
    out = [{"ret": [], "dd": [], "real_ret": None, "real_dd": None, "by_sleeve": None}
           for _ in configs]
    with multiprocessing.Pool(WORKERS, _init, (window,)) as pool:
        for index, seed, ret, dd, by in pool.imap_unordered(_task, tasks, chunksize=4):
            if seed is None:
                out[index].update(real_ret=ret, real_dd=dd, by_sleeve=by)
            else:
                out[index]["ret"].append(ret)
                out[index]["dd"].append(dd)
    rows = []
    for (keys, risk, cap), o in zip(configs, out):
        # A path can go THROUGH zero (the book is not stopped out at ruin), so
        # the log is clamped rather than undefined; `ruin` counts those paths.
        logret = [math.log1p(max(r / 100.0, -0.9999)) for r in o["ret"]]
        rows.append({
            "keys": tuple(keys), "risk": risk, "cap": cap, "n": len(keys),
            "real_ret": o["real_ret"], "real_dd": o["real_dd"], "by_sleeve": o["by_sleeve"],
            "med_ret": q(o["ret"], 50), "p5_ret": q(o["ret"], 5),
            "dd50": q(o["dd"], 50), "dd95": q(o["dd"], 95), "dd99": q(o["dd"], 99),
            "p_dd20": 100.0 * sum(d > 20 for d in o["dd"]) / max(1, len(o["dd"])),
            "med_logret": q(logret, 50),
            "ruin": sum(r <= -100.0 for r in o["ret"]),
            "p_half": 100.0 * sum(r <= -50.0 for r in o["ret"]) / max(1, len(o["ret"])),
        })
    return rows


def canon_keys():
    return list(ecs.BOOK)
