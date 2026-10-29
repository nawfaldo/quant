"""The FundedNext book's (size, risk) grid judged by challenge Monte Carlo only
(`fundednext_combined_strategies.montecarlo`: stressed and edge-kept rows, the module's
dice, so stressed pass rates equal the build's grid), in its default table.

    py -m sandbox.research._fn_mcgrid grid            # 2024-2025, every cell
    py -m sandbox.research._fn_mcgrid test 50 0.03    # one book on 2026, once
    py -m sandbox.research._fn_mcgrid grid --harsh    # + `HARSH` MCs, cells >= 95% stressed
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
    n, risk, harsh = job
    state = fn._prefix(_STATE["s"], n, risk)
    return {"members": len(state["members"]), "risk": risk,
            **fn.montecarlo(state, risk, PATHS, SEED, harsh)}


def _unit_cell(job):
    unit = twohost.unit_id("mcgrid2", *job)
    if not twohost.claim(unit):
        return None
    got = _cell(job)
    twohost.finish(unit, got)
    return got


def _grid_path(harsh):
    return os.path.join(DIR, "grid_harsh.pkl" if harsh else "grid.pkl")


def _jobs(harsh):
    """Every (size, risk) cell; with `harsh`, only those the plain grid passed
    >= 95% stressed (the harsh pick requires it)."""
    if harsh:
        with open(_grid_path(False), "rb") as handle:
            return sorted(((r["members"], r["risk"], True) for r in pickle.load(handle)
                           if r["stressed"]["pass"] >= 95), key=lambda j: -j[0])
    with open(IS_STATE, "rb") as handle:
        members = pickle.load(handle)["members"]
    jobs = set()
    for risk in RISKS:
        fit = sum(fn._fits(m, risk) for m in members)
        jobs |= {(min(n, fit), risk, False) for n in SIZES if fit}
    return sorted(jobs, key=lambda j: -j[0])


def grid(workers, harsh):
    os.makedirs(DIR, exist_ok=True)
    jobs = _jobs(harsh)
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
    with open(_grid_path(harsh), "wb") as handle:
        pickle.dump(rows, handle)
    show(rows)


LABELS = fn.MC_LABELS


def _full(r):
    """The real-days row: "edge 100%" (or "plain" in grids run before the edge rows)."""
    return r.get(fn._edge_label(1.0)) or r["plain"]


def _worst(r):
    """The lowest pass rate over the stressed and harsh Monte Carlos."""
    return min(r[k]["pass"] for k in ("stressed", *fn.HARSH) if k in r)


def show(rows):
    harsh = all(k in rows[0] for k in fn.HARSH)
    print("\n2024-2025 challenge Monte Carlo, fastest plain median first"
          + (" (cells >= 95% stressed)" if harsh else " (stressed pass >= 90% only)"))
    print(f"{'size':>5}{'risk':>7} " + fn.MC_HEAD)
    for r in sorted(rows, key=lambda r: (_full(r)["median_trading_days"] or 9e9, -_worst(r))):
        if harsh or r["stressed"]["pass"] >= 90:
            for label in LABELS:
                if label in r:
                    print(f"{r['members']:>5}{r['risk']:>7} " + fn._mc_line(r[label], label))
    print(fn.MC_ROWS_NOTE)
    print(fn.MC_NOTE)
    if harsh:
        ok = [r for r in rows if _worst(r) >= 95]
        pick = (min(ok, key=lambda r: (_full(r)["median_trading_days"], -_worst(r)))
                if ok else None)
        print("\nE4 pick (stressed, dud75 and costly all >= 95%, then fastest plain median): "
              + (f"{pick['members']} @ {pick['risk']}" if pick else "none qualifies"))


def test(n, risk, harsh=False):
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
        out[label] = fn.montecarlo(fn.load_state(members, x, y), risk, PATHS, SEED, harsh)
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
    p.add_argument("--harsh", action="store_true", help="add the `HARSH` Monte Carlos")
    a = p.parse_args()
    fn.LEV_CAP = 5.0
    if a.command == "grid":
        grid(a.workers, a.harsh)
    elif a.command == "show":
        with open(_grid_path(a.harsh), "rb") as handle:
            show(pickle.load(handle))
    else:
        test(a.n, a.risk, a.harsh)


if __name__ == "__main__":
    main()
