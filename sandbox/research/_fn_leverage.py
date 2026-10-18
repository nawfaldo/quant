"""Per-sleeve leverage rules for the FundedNext book, judged out of fit.

Each RULE sets every admitted sleeve's k (its risk multiple) from trades in a
FIT window only. Two windows:

    wf   members chosen and k fit on 2024, the book walked on 2025 -- the fit
         never saw the year it is scored on (the ladder rule leaks: its k is
         the cell's 2024-2025 ladder, which only flatters it)
    wb   the same backwards: chosen and fit on 2025, walked on 2024
    is   members chosen and k fit on 2024-2025, walked on 2024-2025

Each (rule, window, size, risk) book is walked from every server day exactly
as `_fn_frontier` does (fixed $6,000 sizing, intrabar marks). A rule wins if
it puts a lower p99 limit use at the same median days on `wf`.

Rules (fixed before any run):
    ladder   the cell's ladder k, cap 5 (the canon)
    flat     k = 1 for every sleeve
    vol      equal daily volatility: k = median sd / sd, the sd shrunk toward
             the list's median with N0 = 30 trades of prior weight (a 20-trade
             sleeve cannot earn 10x on a quiet sample); k in [0.25, 10]
    vol_edge vol times a Sharpe tilt clip(S / median S, 0.5, 2), the mean also
             shrunk by n / (n + N0); k in [0.25, 10]
    ladder10 the cell's ladder k, cap 10 (leaks on `wf` like `ladder`)
    dd5      the ladder rebuilt on the FIT window: the highest whole k <= 5 whose
             closed-trade drawdown stays under DD_TARGET. DD_TARGET is the one
             value that best reproduces the cells' own ladder k on 2024-2025
             (calibrated to mimic the ladder, not tuned on any book)
    dd10     the same, k <= 10

Run a subset with `--rules` / `--pools`; rows of other rules are kept.

    py -m sandbox.research._fn_leverage prep
    TWOHOST_PUSH="sandbox/.cache/fn_leverage" \\
    bash sandbox/research/twohost_run.sh <run> 12 6 sandbox.research._fn_leverage run
"""
import argparse
import multiprocessing
import os
import pickle

import numpy as np

from sandbox.research import fundednext_combined_strategies as fn
from sandbox.research import fundednext_challenge as ch
from sandbox.research import twohost
from sandbox.research._fn_frontier import chain, _pct

DIR = os.path.join(fn.base.CACHE_DIR, "fn_leverage")
RULES = ("ladder", "flat", "vol", "vol_edge", "ladder10", "dd5", "dd10")
#: `all` is the whole pool; `old` drops the 124 cells added 2026-10-18 (their
#: groups are listed in `new_groups.txt`) -- does the bigger pool help?
POOLS = ("all", "old")
WINDOWS = {"wf": ("2024-01-01", "2025-01-01", "2025-01-01", "2026-01-01"),
           "wb": ("2025-01-01", "2026-01-01", "2024-01-01", "2025-01-01"),
           "is": ("2024-01-01", "2026-01-01", "2024-01-01", "2026-01-01")}
RISKS = (0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05)
SIZES = (40, 60, 80, 100, 120, 150, 200)
N0 = 30
K_MIN, K_MAX = 0.25, 10.0


def _weekdays(lo, hi):
    days = np.arange(lo // fn.base.DAY, hi // fn.base.DAY)
    return int(((days + 3) % 7 < 5).sum())   # 1970-01-01 was a Thursday


def _fit_days(cell, lo, hi):
    d = cell["days"]
    return d[(d[:, 0] * fn.base.DAY >= lo) & (d[:, 0] * fn.base.DAY < hi)]


def _dd_k(days, target, cap):
    """The highest whole k <= cap whose closed drawdown is at most `target` (1 if none)."""
    ok = [k for k in range(1, int(cap) + 1) if fn._stats(days, k)["dd"] <= target]
    return float(max(ok)) if ok else 1.0


def calibrate(cells):
    """The closed-drawdown target whose `_dd_k` on 2024-2025 matches the most
    cells' own ladder k (uncapped, the ladder's own top of 19)."""
    lo, hi = fn._stamp("2024-01-01"), fn._stamp("2026-01-01")
    days = {k: _fit_days(c, lo, hi) for k, c in cells.items()}
    best = None
    for target in np.arange(2.0, 15.01, 0.25):
        hit = sum(_dd_k(days[k], target, 19) == c["k"] for k, c in cells.items())
        if best is None or hit > best[1]:
            best = (float(target), hit)
    print(f"DD_TARGET {best[0]}: matches {best[1]} of {len(cells)} ladder k", flush=True)
    return best[0]


def leverage(rule, cells, members, lo, hi, dd_target=None):
    """`{key: k}` for `members` under `rule`, from their trades in [lo, hi)."""
    keys = [fn._key(m) for m in members]
    if rule == "ladder":
        return {fn._key(m): m["k"] for m in members}
    if rule == "ladder10":
        return {k: min(cells[k]["k"], 10.0) for k in keys}
    if rule in ("dd5", "dd10"):
        cap = 5 if rule == "dd5" else 10
        return {k: _dd_k(_fit_days(cells[k], lo, hi), dd_target, cap) for k in keys}
    if rule == "flat":
        return dict.fromkeys(keys, 1.0)
    n_days = _weekdays(lo, hi)
    sd, mean, trades = {}, {}, {}
    for key in keys:
        d = cells[key]["days"]
        d = d[(d[:, 0] * fn.base.DAY >= lo) & (d[:, 0] * fn.base.DAY < hi)]
        pnl = d[:, 2]
        mean[key] = pnl.sum() / n_days
        sd[key] = max(0.0, (pnl ** 2).sum() / n_days - mean[key] ** 2) ** 0.5
        trades[key] = d[:, 5].sum()
    prior = float(np.median([sd[k] for k in keys]))
    shrunk = {k: ((trades[k] * sd[k] ** 2 + N0 * prior ** 2) / (trades[k] + N0)) ** 0.5
              for k in keys}
    target = float(np.median(list(shrunk.values())))
    k_vol = {k: target / shrunk[k] for k in keys}
    if rule == "vol":
        return {k: float(np.clip(v, K_MIN, K_MAX)) for k, v in k_vol.items()}
    sharpe = {k: trades[k] / (trades[k] + N0) * mean[k] / shrunk[k] for k in keys}
    mid = float(np.median(list(sharpe.values())))
    return {k: float(np.clip(k_vol[k] * np.clip(sharpe[k] / mid, 0.5, 2.0), K_MIN, K_MAX))
            for k in keys}


def _path(variant):
    return os.path.join(DIR, "_".join(variant) + ".pkl")


def _variants(rules, pools, windows=WINDOWS):
    return [(pool, window, rule) for pool in pools for window in windows for rule in rules]


def prep(rules, pools, windows=WINDOWS):
    summary = fn._summary()
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, "new_groups.txt"), encoding="utf-8") as handle:
        new = {line.strip() for line in handle if line.strip()}
    dd_target = None
    if {"dd5", "dd10"} & set(rules):
        dd_target = calibrate({k: c for k, c in summary["cells"].items()
                               if c["symbol"] not in fn.EXCLUDE_SYMBOLS})
    for pool in pools:
        cells = {k: c for k, c in summary["cells"].items()
                 if pool == "all" or c["group"] not in new}
        for window in windows:
            fit_lo, fit_hi, lo, hi = map(fn._stamp, WINDOWS[window])
            # members from the fit window's trades only
            fit_cells = {k: dict(c, days=_fit_days(c, fit_lo, fit_hi)) for k, c in cells.items()}
            _, chosen, _ = fn.choose(fit_cells, fit_hi)
            for rule in rules:
                ks = leverage(rule, cells, chosen, fit_lo, fit_hi, dd_target)
                members = [dict(m, k=ks[fn._key(m)]) for m in chosen]
                state = fn.load_state(members, lo, hi)
                with open(_path((pool, window, rule)), "wb") as handle:
                    pickle.dump(state, handle, protocol=5)
                k = np.array(list(ks.values()))
                print(f"{pool} {window} {rule:9}: {len(cells)} cells, {len(chosen)} admitted, "
                      f"k median {np.median(k):.2f} mean {k.mean():.2f} max {k.max():.2f}, "
                      f"at {K_MAX:g}: {(k >= K_MAX).sum()}", flush=True)


_STATE = {}


def _job(job):
    from sandbox.research import exness_combined_montecarlo as mc
    variant, n, risk = job[:3], job[3], job[4]
    if variant not in _STATE:
        _STATE.clear()
        with open(_path(variant), "rb") as handle:
            _STATE[variant] = pickle.load(handle)
    mc._MARKING.clear()
    state = fn._prefix(_STATE[variant], n, risk)
    runs = chain(ch.daily_series(state, risk, fixed=True))
    resolved = [(p, u) for p, u in runs if p[-1][0] != "open"]
    passed = [u for p, u in resolved if len(p) == 2 and p[1][0] == "pass"]
    wd = [x[1] for p, _ in runs for x in p]
    wt = [x[2] for p, _ in runs for x in p]
    use = [max(x[1] / ch.DAILY_LOSS, x[2] / ch.MAX_LOSS) for p, _ in runs for x in p]
    return {"pool": variant[0], "window": variant[1], "rule": variant[2],
            "members": len(state["members"]), "risk": risk,
            "symbols": len({m["symbol"] for m in state["members"]}),
            "pass": round(100 * len(passed) / len(resolved), 1) if resolved else None,
            "resolved": len(resolved),
            **{k: sum(1 for p, _ in resolved if p[-1][0] == k) for k in ("daily", "max")},
            "days_median": _pct(passed, 0.5), "days_p90": _pct(passed, 0.9),
            "worst_day_p99": round(_pct(wd, 0.99)), "worst_day_max": round(max(wd)),
            "deepest_p99": round(_pct(wt, 0.99)), "deepest_max": round(max(wt)),
            "limit_use_p99": round(100 * _pct(use, 0.99), 1)}


def _unit_job(job):
    unit = twohost.unit_id("leverage", *job)
    if not twohost.claim(unit):
        return None
    got = _job(job)
    twohost.finish(unit, got)
    return got


def _jobs(rules, pools, windows=WINDOWS):
    out = []
    for variant in _variants(rules, pools, windows):
        with open(_path(variant), "rb") as handle:
            members = pickle.load(handle)["members"]
        cells = set()
        for risk in RISKS:
            fit = sum(fn._fits(m, risk) for m in members)
            cells |= {(*variant, min(n, fit), risk) for n in SIZES if fit}
        out += sorted(cells, key=lambda c: -c[3])
    return out


def run(workers, rules, pools, windows=WINDOWS):
    jobs = _jobs(rules, pools, windows)
    fn_job = _unit_job if twohost.enabled() else _job
    if twohost.IS_MAC and twohost.enabled():
        jobs = jobs[::-1]
    print(f"{len(jobs)} (pool, window, rule, size, risk) books on {workers} workers", flush=True)
    rows = []
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        for i, got in enumerate(pool.imap_unordered(fn_job, jobs), 1):
            if got:
                rows.append(got)
            if i % max(1, len(jobs) // 10) == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    if twohost.enabled():
        if twohost.IS_MAC:
            print("twohost: Mac side finished")
            return
        rows = list(twohost.collect(twohost.unit_id("leverage", *j) for j in jobs).values())
    path = os.path.join(DIR, "rows.pkl")
    if os.path.exists(path):
        with open(path, "rb") as handle:
            done = {(r["pool"], r["window"], r["rule"]) for r in rows}
            rows += [r for r in pickle.load(handle)
                     if (r["pool"], r["window"], r["rule"]) not in done]
    with open(path, "wb") as handle:
        pickle.dump(rows, handle, protocol=5)
    show(rows)


def show(rows):
    rules = [r for r in RULES if any(x["rule"] == r for x in rows)]
    pools = [p for p in POOLS if any(x["pool"] == p for x in rows)]
    for pool, window in ((p, w) for w in WINDOWS for p in pools):
        print(f"\n{window} / pool {pool}: lowest p99 limit use, no fails, at a median of "
              f"at most D days (sleeves@risk)")
        print(f"{'D':>4}" + "".join(f"{r:>22}" for r in rules))
        for D in range(16, 41, 2):
            line = f"{D:>4}"
            for rule in rules:
                ok = [r for r in rows if r["rule"] == rule and r["window"] == window
                      and r["pool"] == pool and r["days_median"] and r["days_median"] <= D
                      and not r["daily"] and not r["max"]]
                b = min(ok, key=lambda r: r["limit_use_p99"]) if ok else None
                line += (f"{str(b['limit_use_p99']) + '% ' + str(b['members']) + '@' + str(b['risk']):>22}"
                         if b else f"{'-':>22}")
            print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prep", "run", "show"))
    parser.add_argument("--workers", type=int, default=fn.default_workers())
    parser.add_argument("--rules", default=",".join(RULES))
    parser.add_argument("--pools", default=",".join(POOLS))
    parser.add_argument("--windows", default=",".join(WINDOWS))
    args = parser.parse_args()
    rules, pools = args.rules.split(","), args.pools.split(",")
    windows = args.windows.split(",")
    if args.command == "prep":
        prep(rules, pools, windows)
    elif args.command == "run":
        run(args.workers, rules, pools, windows)
    else:
        with open(os.path.join(DIR, "rows.pkl"), "rb") as handle:
            show(pickle.load(handle))


if __name__ == "__main__":
    main()
