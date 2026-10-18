"""The FundedNext book's speed / drawdown frontier on 2024-2025 only, for a few
ladder-leverage caps (`fundednext_combined_strategies.LEV_CAP`).

For each cap the gates admit their list (best first by return at the capped k);
each (size, risk) book is replayed once at fixed $6,000 sizing with intrabar
marks, and a challenge is walked from every server day on the real day order:
pass rate, fails, median / p90 days to pass both phases, the worst day and the
deepest drawdown (p99 and max), and the p99 share of a loss limit used
(max(worst day / $300, deepest / $600)).

    py -m sandbox.research._fn_frontier prep 5 8 12 0     # PC: states (0 = no cap)
    TWOHOST_PUSH="sandbox/.cache/fn_frontier" \\
    bash sandbox/research/twohost_run.sh <run> 12 3 sandbox.research._fn_frontier run 5 8 12 0
"""
import argparse
import multiprocessing
import os
import pickle

from sandbox.research import fundednext_combined_strategies as fn
from sandbox.research import fundednext_challenge as ch
from sandbox.research import twohost

DIR = os.path.join(fn.base.CACHE_DIR, "fn_frontier")
RISKS = (0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05)
SIZES = (40, 60, 80, 100, 120, 150, 200, 250, 300)
NO_CAP = 99.0


def _cap(text):
    return float(text) or NO_CAP


def _path(cap):
    return os.path.join(DIR, f"is_cap{cap:g}.pkl")


def prep(caps):
    summary = fn._summary()
    lo, hi = summary["start"], fn.ef.IS_END
    os.makedirs(DIR, exist_ok=True)
    for cap in caps:
        fn.LEV_CAP = cap
        rows, chosen, _ = fn.choose(summary["cells"], hi)
        state = fn.load_state(chosen, lo, hi)
        with open(_path(cap), "wb") as handle:
            pickle.dump(state, handle, protocol=5)
        print(f"cap {cap:g}: pool {len(rows)} -> {len(chosen)} admitted", flush=True)


def chain(days):
    """Every start day through both phases on the real day order."""
    A = ch.ACCOUNT
    out = []
    for start in range(len(days)):
        i, used, phases = start, 0, []
        for target in ch.PHASE_TARGETS:
            equity, traded, wd, wt, done = A, 0, 0.0, 0.0, None
            while i < len(days):
                ret, dip, did, gap = days[i]
                i += 1
                used += 1
                low = equity + dip * A
                ref = equity + gap * A
                wd, wt = max(wd, ref - low), max(wt, A - low)
                if low <= A - ch.MAX_LOSS:
                    done = "max"
                    break
                if low <= ref - ch.DAILY_LOSS:
                    done = "daily"
                    break
                equity += ret * A
                traded += did
                if equity >= A + target and traded >= ch.MIN_DAYS:
                    done = "pass"
                    break
            phases.append((done or "open", wd, wt))
            if done != "pass":
                break
        out.append((phases, used))
    return out


def _pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


_STATE = {}


def _job(job):
    from sandbox.research import exness_combined_montecarlo as mc
    cap, n, risk = job
    if cap not in _STATE:
        _STATE.clear()
        with open(_path(cap), "rb") as handle:
            _STATE[cap] = pickle.load(handle)
    mc._MARKING.clear()
    state = fn._prefix(_STATE[cap], n, risk)
    runs = chain(ch.daily_series(state, risk, fixed=True))
    resolved = [(p, u) for p, u in runs if p[-1][0] != "open"]
    passed = [u for p, u in resolved if len(p) == 2 and p[1][0] == "pass"]
    wd = [x[1] for p, _ in runs for x in p]
    wt = [x[2] for p, _ in runs for x in p]
    use = [max(x[1] / ch.DAILY_LOSS, x[2] / ch.MAX_LOSS) for p, _ in runs for x in p]
    return {"cap": cap, "members": len(state["members"]), "risk": risk,
            "symbols": len({m["symbol"] for m in state["members"]}),
            "pass": round(100 * len(passed) / len(resolved), 1) if resolved else None,
            "resolved": len(resolved),
            **{k: sum(1 for p, _ in resolved if p[-1][0] == k) for k in ("daily", "max")},
            "days_median": _pct(passed, 0.5), "days_p90": _pct(passed, 0.9),
            "worst_day_p99": round(_pct(wd, 0.99)), "worst_day_max": round(max(wd)),
            "deepest_p99": round(_pct(wt, 0.99)), "deepest_max": round(max(wt)),
            "limit_use_p99": round(100 * _pct(use, 0.99), 1)}


def _unit_job(job):
    unit = twohost.unit_id("frontier", *job)
    if not twohost.claim(unit):
        return None
    got = _job(job)
    twohost.finish(unit, got)
    return got


def _cells(caps):
    out = []
    for cap in caps:
        with open(_path(cap), "rb") as handle:
            members = pickle.load(handle)["members"]
        cells = set()
        for risk in RISKS:
            fit = sum(fn._fits(m, risk) for m in members)
            cells |= {(cap, min(n, fit), risk) for n in SIZES if fit}
        out += sorted(cells, key=lambda c: -c[1])
    return out


def run(caps, workers):
    jobs = _cells(caps)
    fn_job = _job
    if twohost.enabled():
        fn_job = _unit_job
        if twohost.IS_MAC:
            jobs = jobs[::-1]
    print(f"{len(jobs)} (cap, size, risk) books on {workers} workers", flush=True)
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
        rows = list(twohost.collect(twohost.unit_id("frontier", *j) for j in jobs).values())
    with open(os.path.join(DIR, "rows.pkl"), "wb") as handle:
        pickle.dump(rows, handle, protocol=5)
    show(rows)


def show(rows):
    print(f"\n{'cap':>4}{'sleeves':>8}{'syms':>5}{'risk':>7}{'pass':>7}{'daily':>6}{'max':>4}"
          f"{'med':>5}{'p90':>5}{'worst day p99/max':>19}{'deepest p99/max':>17}{'limit use':>10}")
    for r in sorted(rows, key=lambda r: (r["cap"], r["days_median"] or 9e9)):
        print(f"{r['cap']:>4g}{r['members']:>8}{r['symbols']:>5}{r['risk']:>7}{r['pass']:>7}"
              f"{r['daily']:>6}{r['max']:>4}{r['days_median']:>5}{r['days_p90']:>5}"
              f"{r['worst_day_p99']:>11}/{r['worst_day_max']:<7}{r['deepest_p99']:>9}/"
              f"{r['deepest_max']:<7}{r['limit_use_p99']:>9}%")
    caps = sorted({r["cap"] for r in rows})
    print("\nlowest p99 limit use with no fails at a median of at most D days:")
    print(f"{'D':>4}" + "".join(f"{'cap ' + format(c, 'g'):>22}" for c in caps))
    for D in range(14, 31, 2):
        line = f"{D:>4}"
        for c in caps:
            ok = [r for r in rows if r["cap"] == c and r["days_median"]
                  and r["days_median"] <= D and not r["daily"] and not r["max"]]
            b = min(ok, key=lambda r: r["limit_use_p99"]) if ok else None
            line += f"{(str(b['limit_use_p99']) + '% ' + str(b['members']) + '@' + str(b['risk'])) if b else '-':>22}"
        print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prep", "run", "show"))
    parser.add_argument("caps", nargs="*", default=["5", "8", "12", "0"])
    parser.add_argument("--workers", type=int, default=fn.default_workers())
    args = parser.parse_args()
    caps = [_cap(c) for c in args.caps]
    if args.command == "prep":
        prep(caps)
    elif args.command == "run":
        run(caps, args.workers)
    else:
        with open(os.path.join(DIR, "rows.pkl"), "rb") as handle:
            show(pickle.load(handle))


if __name__ == "__main__":
    main()
