"""Search membership for a drawdown BAND, on the live-execution Monte Carlo.

THE TARGET IS A BAND AND NOT A MINIMUM. `p95(MTM dd) <= 25%` with
`p5(MTM dd) >= 10%`: the upper edge is the risk limit, the lower edge says a
book that never draws down is one that stopped betting, and a uniform cut of the
risk dial moves BOTH edges together and therefore cannot hit the band
([[membership-beats-sizing-for-drawdown]]). Only membership changes the SHAPE.

HOW A CANDIDATE IS SCORED, AND WHY NOT ON THE WHOLE DISTRIBUTION. A 1,000-path
run costs 42 minutes, so a leave-one-out over 23 sleeves is a day. Instead every
candidate is replayed on the SAME block orders -- common random numbers, so the
comparison is paired -- and on the subset of them that produced the WORST
drawdowns for the full book. p95 is a statement about the tail, and the tail
orders are where a damper has to earn its place. The screen ranks; it does not
decide. `eval` re-measures the survivors on a fresh full distribution.

WHAT IS HELD FIXED. `bars_by` and `ctx_by` are passed whole even when members
are dropped, so the revaluation grid is identical for every candidate: a
membership must not be able to look calmer merely by marking itself less often.

    py -m sandbox.research._mc_live_search base --paths 250
    py -m sandbox.research._mc_live_search loo --tail 40
    py -m sandbox.research._mc_live_search nested --tail 40 --sets "a:b;a:b,c:d"
    py -m sandbox.research._mc_live_search eval --drop a:b,c:d --paths 400
"""

import argparse
import json
import multiprocessing
import os
import pickle
import random
import time

from sandbox.research import _mc_live as live
from sandbox.research import exness_combined_montecarlo as mc
from sandbox.research import exness_combined_strategies as ecs

STATE_PATH = os.path.join(mc.RESULTS, "_mc_live_search.json")

_STATE = None
_BLOCKS = None
_BLOCK_DAYS = mc.BLOCK_DAYS


def _init(cache_path, block_days):
    global _STATE, _BLOCKS, _BLOCK_DAYS
    live.arm()
    with open(cache_path, "rb") as handle:
        _STATE = pickle.load(handle)
    _BLOCK_DAYS = block_days
    mc.pin_external_window()
    _BLOCKS = mc.blocks_of(block_days)
    mc.run_book(_STATE, lo=_BLOCKS[0][0], hi=_BLOCKS[0][1])   # warm the memo


def _subset(drop):
    drop = set(drop)
    members = [m for m in _STATE["members"]
               if f"{m['symbol']}:{m['family']}" not in drop]
    logs = {k: v for k, v in _STATE["logs"].items() if k not in drop}
    return {"members": members, "logs": logs,
            "bars_by": _STATE["bars_by"], "ctx_by": _STATE["ctx_by"]}


def _job(args):
    """One (membership, seed) path. Seeds are shared across memberships."""
    drop, seed, with_replacement = args
    rng = random.Random(seed)
    if with_replacement:
        order = [rng.choice(_BLOCKS) for _ in _BLOCKS]
    else:
        order = list(_BLOCKS)
        rng.shuffle(order)
    state = _subset(drop)
    book, returns, curve = mc.chain(state, order, ecs.CANON_INITIAL)
    row = mc.metrics(book, returns, curve, ecs.CANON_INITIAL, _BLOCK_DAYS)
    return {"drop": list(drop), "seed": seed,
            "mtm_dd_pct": row["mtm_dd_pct"], "return_pct": row["return_pct"],
            "final": row["final"], "trades": row["trades"],
            "below_broker_minimum": row["below_broker_minimum"]}


def _run(jobs, workers, block_days, label):
    started = time.time()
    out, step = [], max(1, len(jobs) // 10)
    with multiprocessing.Pool(workers, _init, (mc.CACHE, block_days)) as pool:
        for index, row in enumerate(pool.imap_unordered(_job, jobs,
                                                        chunksize=1), start=1):
            out.append(row)
            if index % step == 0 or index == len(jobs):
                rate = (time.time() - started) / index
                print(f"    {label} {100 * index // len(jobs):>3}% "
                      f"({index}/{len(jobs)})  "
                      f"{(time.time() - started) / 60:.1f} min, "
                      f"{rate * (len(jobs) - index) / 60:.1f} left", flush=True)
    return out


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _save_state(payload):
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _band(rows):
    dds = sorted(r["mtm_dd_pct"] for r in rows)
    rets = sorted(r["return_pct"] for r in rows)
    q = mc.quantile
    return {"p5_dd": q(dds, 5), "p50_dd": q(dds, 50), "p95_dd": q(dds, 95),
            "mean_dd": sum(dds) / len(dds),
            "p5_ret": q(rets, 5), "p50_ret": q(rets, 50),
            "p95_ret": q(rets, 95), "paths": len(rows)}


def _members():
    with open(live.BUILD_PATH, encoding="utf-8") as handle:
        return [f"{m['symbol']}:{m['family']}"
                for m in json.load(handle)["members"]]


# --------------------------------------------------------------------------- #


def base(paths, workers, block_days, method):
    """The full book on `paths` seeds, recorded seed by seed.

    The tail of THIS is what every later screen is measured on, so it is stored
    rather than recomputed: a screen run against different orders is not a
    paired comparison and its ranking means nothing.
    """
    jobs = [((), seed, method == "boot") for seed in range(1, paths + 1)]
    rows = _run(jobs, workers, block_days, "base")
    rows.sort(key=lambda r: -r["mtm_dd_pct"])
    state = _load_state()
    state["base"] = {"method": method, "paths": paths,
                     "block_days": block_days,
                     "band": _band(rows),
                     "by_seed": [{"seed": r["seed"],
                                  "mtm_dd_pct": round(r["mtm_dd_pct"], 3),
                                  "return_pct": round(r["return_pct"], 2)}
                                 for r in rows]}
    _save_state(state)
    band = state["base"]["band"]
    print(f"\nfull book, {paths} paths: dd p5 {band['p5_dd']:.2f}  "
          f"p50 {band['p50_dd']:.2f}  p95 {band['p95_dd']:.2f}   "
          f"return p50 {band['p50_ret']:.0f}%")
    print("worst 10 seeds: "
          + ", ".join(f"{r['seed']}({r['mtm_dd_pct']:.1f})" for r in rows[:10]))


def _tail_seeds(count):
    state = _load_state()
    if "base" not in state:
        raise SystemExit("run `base` first")
    return [r["seed"] for r in state["base"]["by_seed"][:count]]


def _tail_reference(seeds):
    """`{seed: row}` for the full book, PER SEED and not averaged.

    Paired: the same block order run twice differs only by the membership, and
    one order in this tail draws down 91% while another draws 39%, so an average
    of averages is dominated by which orders happened to be in the set. The mean
    of the per-seed DIFFERENCES is not.
    """
    state = _load_state()
    by_seed = {r["seed"]: r for r in state["base"]["by_seed"]}
    return {s: by_seed[s] for s in seeds}


def loo(tail, workers, block_days, method, held=()):
    """Every remaining sleeve dropped on its own, on the tail orders.

    `held` is the set already dropped, so a second round measures the MARGINAL
    value of the next drop rather than re-ranking against the full book.
    """
    seeds = _tail_seeds(tail)
    held = tuple(held)
    names = [n for n in _members() if n not in held]
    jobs = [(held + (name,), seed, method == "boot")
            for name in names for seed in seeds]
    rows = _run(jobs, workers, block_days, "loo")
    grouped = {}
    for row in rows:
        grouped.setdefault(row["drop"][-1], []).append(row)
    if held:
        ref = {r["seed"]: r for r in
               _run([(held, seed, method == "boot") for seed in seeds],
                    workers, block_days, "ref")}
    else:
        ref = _tail_reference(seeds)
    ref_dd = sum(r["mtm_dd_pct"] for r in ref.values()) / len(ref)
    ref_ret = sum(r["return_pct"] for r in ref.values()) / len(ref)
    print(f"\ntail-order reference ({len(seeds)} worst orders"
          + (f", already dropped {', '.join(held)}" if held else "")
          + f"): mean dd {ref_dd:.2f}%   mean return {ref_ret:.0f}%")
    print(f"{'drop this sleeve':30}{'mean dd':>10}{'d dd':>9}{'med d dd':>10}"
          f"{'mean ret':>10}{'d ret':>9}{'dd per ret':>12}")
    ranked = []
    for name, group in grouped.items():
        deltas = sorted(row["mtm_dd_pct"] - ref[row["seed"]]["mtm_dd_pct"]
                        for row in group)
        d_dd = sum(deltas) / len(deltas)
        med = deltas[len(deltas) // 2]
        dd = sum(r["mtm_dd_pct"] for r in group) / len(group)
        ret = sum(r["return_pct"] for r in group) / len(group)
        ranked.append((d_dd, name, dd, ret, ret - ref_ret, med))
    for d_dd, name, dd, ret, d_ret, med in sorted(ranked):
        cost = (-d_dd / -d_ret) if d_ret < 0 else float("inf")
        print(f"{name:30}{dd:>10.2f}{d_dd:>+9.2f}{med:>+10.2f}{ret:>10.0f}"
              f"{d_ret:>+9.0f}{cost:>12.3f}")
    state = _load_state()
    state.setdefault("loo", {})[",".join(held) or "-"] = {
        "tail": len(seeds), "ref_dd": ref_dd, "ref_ret": ref_ret,
        "rows": [{"sleeve": n, "mean_dd": dd, "d_dd": d, "median_d_dd": med,
                  "mean_ret": r, "d_ret": dr}
                 for d, n, dd, r, dr, med in sorted(ranked)]}
    _save_state(state)


def nested(sets, tail, workers, block_days, method):
    """Cumulative drop sets, on the same tail orders."""
    seeds = _tail_seeds(tail)
    jobs = [(tuple(s), seed, method == "boot") for s in sets for seed in seeds]
    rows = _run(jobs, workers, block_days, "nested")
    grouped = {}
    for row in rows:
        grouped.setdefault(tuple(row["drop"]), []).append(row)
    ref = _tail_reference(seeds)
    ref_dd = sum(r["mtm_dd_pct"] for r in ref.values()) / len(ref)
    ref_ret = sum(r["return_pct"] for r in ref.values()) / len(ref)
    print(f"\ntail-order reference: mean dd {ref_dd:.2f}%  "
          f"mean return {ref_ret:.0f}%")
    print(f"{'dropped':60}{'mean dd':>10}{'mean ret':>11}")
    out = []
    for key in sorted(grouped, key=len):
        group = grouped[key]
        dd = sum(r["mtm_dd_pct"] for r in group) / len(group)
        ret = sum(r["return_pct"] for r in group) / len(group)
        out.append({"drop": list(key), "mean_dd": dd, "mean_ret": ret})
        label = ", ".join(key) if key else "-"
        print(f"{label[:59]:60}{dd:>10.2f}{ret:>11.0f}")
    state = _load_state()
    state.setdefault("nested", []).append({"tail": len(seeds), "rows": out})
    _save_state(state)


def evaluate(drop, paths, workers, block_days, method, tag):
    """A candidate membership on a full fresh distribution."""
    jobs = [(tuple(drop), seed, method == "boot")
            for seed in range(1, paths + 1)]
    rows = _run(jobs, workers, block_days, "eval")
    band = _band(rows)
    dds = [r["mtm_dd_pct"] for r in rows]
    band["p_dd_over_25"] = 100.0 * sum(1 for v in dds if v > 25) / len(dds)
    band["p_dd_over_30"] = 100.0 * sum(1 for v in dds if v > 30) / len(dds)
    band["below_broker_minimum_paths"] = sum(
        1 for r in rows if r["below_broker_minimum"] > 0)
    print(f"\ndropped: {', '.join(drop) or 'nothing'}   ({paths} paths)")
    print(f"  MTM dd    p5 {band['p5_dd']:.2f}   p50 {band['p50_dd']:.2f}   "
          f"p95 {band['p95_dd']:.2f}")
    print(f"  return    p5 {band['p5_ret']:.0f}%  p50 {band['p50_ret']:.0f}%  "
          f"p95 {band['p95_ret']:.0f}%")
    print(f"  P(dd>25%) {band['p_dd_over_25']:.1f}%   "
          f"P(dd>30%) {band['p_dd_over_30']:.1f}%")
    inside = band["p5_dd"] >= 10.0 and band["p95_dd"] <= 25.0
    print(f"  band 10-25%: {'MET' if inside else 'not met'}")
    state = _load_state()
    state.setdefault("eval", {})[tag or ",".join(drop) or "-"] = {
        "drop": list(drop), "paths": paths, "band": band}
    _save_state(state)


def realised(drop):
    """The candidate book on the REAL calendar, not a resampled one.

    The Monte Carlo says what the process can do; this says what the account
    would have got. Both are needed and neither substitutes for the other, and
    the per-sleeve column is not optional
    ([[show-per-sleeve-detail-every-time]]).
    """
    _init(mc.CACHE, mc.BLOCK_DAYS)
    for label, members in (("full book", ()), ("candidate", tuple(drop))):
        book = mc.run_book(_subset(members))
        mtm, low = mc.drawdown_of([v for _ts, v in book["marked"]])
        ret = 100.0 * (book["final"] / ecs.CANON_INITIAL - 1)
        print(f"\n{label}: {len(_subset(members)['members'])} sleeves   "
              f"{ret:+.1f}%   final ${book['final']:,.0f}   "
              f"MTM dd {mtm:.2f}%   closed dd {book['max_dd_pct']:.2f}%   "
              f"{book['trades']} trades   min equity ${low:,.0f}")
        if label == "candidate":
            print(f"  {'sleeve':30}{'P&L $':>10}")
            for name, row in sorted(book["by_sleeve"].items(),
                                    key=lambda kv: -kv[1]["pnl"]):
                print(f"  {name:30}{row['pnl']:>10,.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",
                        choices=("base", "loo", "nested", "eval", "realised"))
    parser.add_argument("--paths", type=int, default=250)
    parser.add_argument("--tail", type=int, default=40)
    parser.add_argument("--drop", default="")
    parser.add_argument("--sets", default="",
                        help="';'-separated cumulative drop sets")
    parser.add_argument("--tag", default="")
    parser.add_argument("--method", default="boot", choices=("boot", "order"))
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 4) - 4))
    args = parser.parse_args()
    live.arm()
    drop = tuple(x for x in args.drop.split(",") if x)
    if args.command == "realised":
        realised(drop)
    elif args.command == "base":
        base(args.paths, args.workers, args.block_days, args.method)
    elif args.command == "loo":
        loo(args.tail, args.workers, args.block_days, args.method, drop)
    elif args.command == "nested":
        sets = [tuple(x for x in part.split(",") if x)
                for part in args.sets.split(";")]
        nested(sets, args.tail, args.workers, args.block_days, args.method)
    else:
        evaluate(drop, args.paths, args.workers, args.block_days, args.method,
                 args.tag)


if __name__ == "__main__":
    main()
