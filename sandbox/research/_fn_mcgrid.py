"""The FundedNext book's (size, risk) grid judged by challenge Monte Carlo only
(`fundednext_combined_strategies.montecarlo`: stressed and plain, the module's
dice, so stressed pass rates equal the build's grid), in its default table.

    py -m sandbox.research._fn_mcgrid grid            # 2024-2025, every cell
    py -m sandbox.research._fn_mcgrid test 50 0.03    # one book on 2026, once
"""
import argparse
import multiprocessing
import os
import pickle

from sandbox.research import fundednext_combined_strategies as fn
from sandbox.research import twohost

DIR = os.path.join(fn.base.CACHE_DIR, "fn_mcgrid")
IS_STATE = os.path.join(fn.base.CACHE_DIR, "fn_frontier", "is_cap5.pkl")
RISKS = (0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05)
SIZES = (20, 30, 40, 50, 60, 80, 100, 120, 150, 200)
PATHS = 5000
SEED = 7000

_STATE = {}


def _init(path):
    with open(path, "rb") as handle:
        _STATE["s"] = pickle.load(handle)


def _cell(job):
    n, risk = job
    state = fn._prefix(_STATE["s"], n, risk)
    return {"members": len(state["members"]), "risk": risk,
            **fn.montecarlo(state, risk, PATHS, SEED)}


def _unit_cell(job):
    unit = twohost.unit_id("mcgrid2", *job)
    if not twohost.claim(unit):
        return None
    got = _cell(job)
    twohost.finish(unit, got)
    return got


def _jobs():
    with open(IS_STATE, "rb") as handle:
        members = pickle.load(handle)["members"]
    jobs = set()
    for risk in RISKS:
        fit = sum(fn._fits(m, risk) for m in members)
        jobs |= {(min(n, fit), risk) for n in SIZES if fit}
    return sorted(jobs, key=lambda j: -j[0])


def grid(workers):
    os.makedirs(DIR, exist_ok=True)
    jobs = _jobs()
    work = _cell
    if twohost.enabled():
        work = _unit_cell
        if twohost.IS_MAC:
            jobs = jobs[::-1]
    print(f"{len(jobs)} (size, risk) cells, {PATHS} paths each, stressed + plain", flush=True)
    rows = []
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers, initializer=_init, initargs=(IS_STATE,)) as pool:
        for i, r in enumerate(pool.imap_unordered(work, jobs), 1):
            if r:
                rows.append(r)
            print(f"  {i}/{len(jobs)}", flush=True)
    if twohost.enabled():
        if twohost.IS_MAC:
            print("twohost: Mac side finished")
            return
        rows = list(twohost.collect(twohost.unit_id("mcgrid2", *j) for j in jobs).values())
    with open(os.path.join(DIR, "grid.pkl"), "wb") as handle:
        pickle.dump(rows, handle)
    show(rows)


def show(rows):
    print("\n2024-2025 challenge Monte Carlo, fastest stressed median first "
          "(stressed pass >= 90% only)")
    print(f"{'size':>5}{'risk':>7} " + fn.MC_HEAD)
    for r in sorted(rows, key=lambda r: r["stressed"]["median_trading_days"] or 9e9):
        if r["stressed"]["pass"] >= 90:
            for label in ("stressed", "plain"):
                print(f"{r['members']:>5}{r['risk']:>7} " + fn._mc_line(r[label], label))
    print(fn.MC_NOTE)


def test(n, risk):
    """One book: its 2024-2025 and 2026 Monte Carlos."""
    summary = fn._summary()
    lo, hi = summary["start"], fn.ef.IS_END
    _, chosen, _ = fn.choose(summary["cells"], hi)
    with open(IS_STATE, "rb") as handle:
        is_state = pickle.load(handle)
    members = fn._book(is_state, chosen, {"members": n, "risk": risk})
    print(f"{len(members)} sleeves on {len({m['symbol'] for m in members})} symbols at risk {risk}")
    out = {}
    for label, x, y in (("2024-2025", lo, hi), ("2026", hi, fn.ef.OOS_END)):
        out[label] = fn.montecarlo(fn.load_state(members, x, y), risk, PATHS, SEED)
        print(f"\n{label}")
        fn._mc_table(out[label])
    with open(os.path.join(DIR, f"test_{n}_{risk}.pkl"), "wb") as handle:
        pickle.dump({"members": members, "out": out}, handle)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("grid", "show", "test"))
    p.add_argument("n", nargs="?", type=int)
    p.add_argument("risk", nargs="?", type=float)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    fn.LEV_CAP = 5.0
    if a.command == "grid":
        grid(a.workers)
    elif a.command == "show":
        with open(os.path.join(DIR, "grid.pkl"), "rb") as handle:
            show(pickle.load(handle))
    else:
        test(a.n, a.risk)


if __name__ == "__main__":
    main()
