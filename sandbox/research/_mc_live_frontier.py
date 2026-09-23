"""Search membership AND risk for the most return inside a drawdown band.

THE OBJECTIVE. Maximise return subject to the Monte Carlo drawdown band
`p5 >= 10%` and `p95 <= 25%`, measured on live execution over 2022-01-01 ..
2026-08-21 -- the window where the band currently FAILS (p95 27.2%), which makes
it the binding one. The 2025-2026 band is checked on the winner at the end.

TWO STAGES, IN THIS ORDER, AND THE ORDER IS THE POINT. Members are chosen on
DRAWDOWN EFFICIENCY -- drawdown bought per point of return surrendered -- and
only then is `risk_scale` calibrated to put p95 back on the 25% ceiling.
Searching sizing jointly with selection scores a cell for return at a size the
search itself chose, which is how a sizing knob inside a selection grid
manufactures its own winner ([[usoil-intraday-fails-twice]]). Here sizing is a
one-dimensional calibration applied AFTER membership is fixed, and it is not
allowed to reorder the members.

WHERE THE EXTRA RETURN COMES FROM. Not from finding better sleeves. Cutting p95
from 27% to, say, 20% frees drawdown budget the band was already willing to
spend; raising risk until p95 returns to 25% converts that budget into return.
The membership stage is what makes the room, the risk stage is what spends it.

THE FIVE NQ SLEEVES ARE HELD FIXED and never dropped. Two reasons, one of
evidence and one of machinery. The 2025-2026 leave-one-out put every one of them
on the damper side -- removing `nq:drift_vwap`, `nq:ofi` or
`nq:volatility_breakout` made tail drawdown WORSE (+1.97, +1.32, +1.34 points)
while also costing return -- and canon's own record says the same. And they are
the only EXTERNAL members, so dropping one changes the external market set,
which is the key of `replay`'s marking cache: a search that varied it would hold
several full price grids per worker at once and take the machine down
([[multi-symbol-select-leaks-into-one-worker]]).

WHAT THIS CANNOT ESTABLISH. The candidate pool was screened on 2025-2026
([[exness-survivor-pool-is-oos-conditioned]]) and this search scores it on a
window that contains those months, so an added sleeve is chosen on data that is
partly its own selection sample. The band it lands in is fitted the same way
([[selection-gate-manufactures-drawdown-and-consistency]]). This produces a
candidate, not a verdict.

    py -m sandbox.research._mc_live_frontier cache
    py -m sandbox.research._mc_live_frontier base --paths 250
    py -m sandbox.research._mc_live_frontier probe          <- 1 path per cell
    py -m sandbox.research._mc_live_frontier screen --names a:b,c:d --tail 30
    py -m sandbox.research._mc_live_frontier risk --add a:b --drop c:d
    py -m sandbox.research._mc_live_frontier eval --add a:b --paths 1000
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

STATE_PATH = os.path.join(mc.RESULTS, "_mc_live_frontier.json")

#: Held fixed; see the module note.
FIXED = tuple(k for k in ecs.BOOK if k in ecs.EXTERNAL)

BAND_LOW, BAND_HIGH = 10.0, 25.0

#: The two eras, scored SEPARATELY and each from its own `CANON_INITIAL`.
#:
#: A change must raise return in BOTH or it is not an improvement, it is a bet
#: on one regime. Chaining them into one compounding run would not let that be
#: said: the second era's return would then depend on the equity the first
#: happened to leave, so a cell that only worked early would flatter the whole
#: line and a cell that only worked late would be hidden by it.
#:
#: `is` ends where the parameter search's data ended, 2024-12-31, so `is` is
#: fitted and `oos` is not. The split is the sleeves' own, not this study's.
ERAS = {
    "is": ("2022-01-01", "2025-01-01"),
    "oos": ("2025-01-01", ecs.CANON_DATA_END),
    "full": ("2022-01-01", ecs.CANON_DATA_END),
}

_STATE = None
_BLOCKS = None
_BLOCK_DAYS = mc.BLOCK_DAYS


def _bounds(era):
    from datetime import datetime, timezone

    lo, hi = ERAS[era]
    return tuple(int(datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
                     .timestamp()) for v in (lo, hi))


# --------------------------------------------------------------------------- #
# the payload
# --------------------------------------------------------------------------- #


def cache_path():
    return os.path.join(ecs.CACHE_DIR,
                        f"frontier_pool{'_' + live.START[:4] if live.START else ''}.pkl")


def build_cache(force=False):
    """`BOOK` plus every addable candidate, all trade logs on live execution.

    One payload rather than one per membership, because a path is scored by
    REPLAYING a subset of it: the logs are generated once at canon settings and
    the search only chooses which of them reach `replay`.
    """
    path = cache_path()
    if os.path.exists(path) and not force:
        print(f"cache present: {path} ({os.path.getsize(path) / 1e6:.0f} MB)")
        return
    started = time.time()
    book = {f"{m['symbol']}:{m['family']}": m for m in live.members()}
    pool = {f"{r['symbol']}:{r['family']}": r for r in ecs.candidates()}
    rows = dict(book)
    for key, row in pool.items():
        rows.setdefault(key, row)
    logs, bars_by, ctx_by = {}, {}, {}
    for index, (key, member) in enumerate(sorted(rows.items()), start=1):
        if "params" in member:
            member["params"] = ecs._retuple(member["params"])
        if key in ecs.EXTERNAL:
            logs[key] = ecs.external_trades(key, window=live.window())
        else:
            ecs.ef.resolve(member["symbol"], allow_stale=True)
            _r, log, bars, ctx = ecs.sleeve_trades(member)
            logs[key] = log
            bars_by[member["symbol"]] = bars
            ctx_by[member["symbol"]] = {"cfg": ctx["cfg"],
                                        "symbol": ctx.get("symbol",
                                                          member["symbol"])}
        print(f"  [{index}/{len(rows)}] {key}: {len(logs[key])} trades",
              flush=True)
    payload = {"members": rows, "logs": logs,
               "bars_by": bars_by, "ctx_by": ctx_by}
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as handle:
        pickle.dump(payload, handle, protocol=5)
    os.replace(tmp, path)
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.0f} MB) "
          f"in {time.time() - started:.0f}s, {len(rows)} cells")


# --------------------------------------------------------------------------- #
# workers
# --------------------------------------------------------------------------- #


def _init(path, block_days, era):
    global _STATE, _BLOCKS, _BLOCK_DAYS
    live.arm()
    with open(path, "rb") as handle:
        _STATE = pickle.load(handle)
    _BLOCK_DAYS = block_days
    # PINNED TO THE FULL SPAN whatever era is being scored, so the external
    # price grid and its 30-day volatility warm-up are identical in both. An
    # era-length grid would leave the OOS run's throttle cold for its first
    # month and make the two eras incomparable for a reason that is not the
    # book ([[cold-start-oos-fakes-regime-edges]]).
    mc.pin_external_window(live.window())
    lo, hi = _bounds(era)
    _BLOCKS = mc.blocks_of(block_days, lo=lo, hi=hi)
    _replay(_membership((), ()), lo=_BLOCKS[0][0], hi=_BLOCKS[0][1])


def _membership(drop, add):
    """`BOOK` minus `drop` plus `add`, with duplicates refused.

    A sleeve named in `add` that is ALREADY in `BOOK` used to be appended a
    second time, so `replay` ran its trade log twice and reported a book that
    cannot exist. It looked plausible -- more return, more drawdown -- which is
    exactly why it needs to raise rather than be silently de-duplicated: the
    caller asked for something impossible and should be told.
    """
    keep = [k for k in ecs.BOOK if k not in set(drop)]
    clash = [k for k in add if k in keep]
    if clash:
        raise SystemExit(f"already in BOOK, cannot add again: "
                         f"{', '.join(clash)}")
    return [_STATE["members"][k] for k in keep + list(add)]


def _replay(members, lo=None, hi=None, initial=None, risk_scale=None):
    """`mc.run_book`, with `risk_scale` open and the marking cache shared."""
    keys = {f"{m['symbol']}:{m['family']}" for m in members}
    logs = {k: v for k, v in _STATE["logs"].items() if k in keys}
    return ecs.replay(
        members, logs, _STATE["bars_by"], _STATE["ctx_by"],
        scale=ecs.SLEEVE_SCALE, sizing_cap=ecs.sizing_caps(members),
        risk_scale=ecs.CANON_RISK_SCALE if risk_scale is None else risk_scale,
        gross_cap=ecs.CANON_GROSS_CAP, fair_cap=ecs.FAIR_CAP,
        initial=ecs.CANON_INITIAL if initial is None else initial,
        lo=lo, hi=hi, marking_cache=mc._MARKING)


def _chain(members, order, risk_scale):
    """`mc.chain`, against a membership rather than a whole cached state."""
    equity, elapsed = ecs.CANON_INITIAL, 0.0
    curve, returns, sleeves = [], [], {}
    trades = skipped = 0
    closed_peak, closed_dd = equity, 0.0
    for lo, hi in order:
        if equity <= 0:
            returns.append(0.0)
            elapsed += hi - lo
            curve.append((elapsed, equity))
            continue
        book = _replay(members, lo=lo, hi=hi, initial=equity,
                       risk_scale=risk_scale)
        for stamp, value in book["marked"]:
            curve.append((elapsed + (stamp - lo), value))
        elapsed += hi - lo
        curve.append((elapsed, book["final"]))
        returns.append(100.0 * (book["final"] - equity) / equity)
        for name, row in book["by_sleeve"].items():
            sleeves[name] = sleeves.get(name, 0.0) + row["pnl"]
        trades += book["trades"]
        skipped += sum(book["below_broker_minimum"].values())
        for _stamp, value in book["curve"]:
            closed_peak = max(closed_peak, value)
            if closed_peak > 0:
                closed_dd = max(closed_dd, (closed_peak - value) / closed_peak)
        equity = book["final"]
    return ({"final": equity, "max_dd_pct": 100.0 * closed_dd,
             "trades": trades, "below_broker_minimum": skipped,
             "refused_by_gross_cap": 0, "by_sleeve": sleeves},
            returns, curve)


def _job(args):
    """One (membership, risk, seed) path. Seed 0 means the REAL order."""
    drop, add, risk_scale, seed = args
    if seed == 0:
        order = list(_BLOCKS)
    else:
        rng = random.Random(seed)
        order = [rng.choice(_BLOCKS) for _ in _BLOCKS]
    members = _membership(drop, add)
    book, returns, curve = _chain(members, order, risk_scale)
    row = mc.metrics(book, returns, curve, ecs.CANON_INITIAL, _BLOCK_DAYS)
    # CONSISTENCY TRAVELS WITH THE PATH. A search that optimises it has to see
    # it per path, not as a realised-book fact afterwards -- the same mistake
    # that let a refill guarded on one ordering hide a 20-point tail
    # ([[refill-guarded-on-one-path-hides-its-tail]]).
    return {"drop": list(drop), "add": list(add), "risk": risk_scale,
            "seed": seed, "mtm_dd_pct": row["mtm_dd_pct"],
            "return_pct": row["return_pct"], "trades": row["trades"],
            "below_broker_minimum": row["below_broker_minimum"],
            "positive_months": row["positive_months"],
            "positive_month_share": row["positive_month_share"],
            "worst_month_pct": row["worst_month_pct"],
            "longest_losing_months": row["longest_losing_months"],
            "under_water_days": row["under_water_days"],
            # RUIN NEEDS THE LOW-WATER MARK, NOT THE DRAWDOWN. A 30% fall from a
            # peak reached after tripling is not the same event as a 30% fall
            # from the starting balance, and only `min_equity_ratio` separates
            # them.
            "min_equity_ratio": row["min_equity_ratio"],
            "final": row["final"]}


def _run(jobs, workers, block_days, label, era="full"):
    started = time.time()
    out, step = [], max(1, len(jobs) // 10)
    with multiprocessing.Pool(workers, _init,
                              (cache_path(), block_days, era)) as pool:
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


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _load():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _save(payload):
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _band(rows):
    """The drawdown and return ladders, and whether the band is met.

    THE FULL LADDER RATHER THAN THREE POINTS, because the question asked of
    this book is where p10 THROUGH p95 sit, and a p5/p50/p95 summary cannot
    answer it: a distribution can clear 25% at p95 and still put a quarter of
    its paths over 20%, and the three-point form hides that.

    `ceiling_met` is the binding half of the band -- p95 at or under 25% -- and
    it is reported separately from `inside` because the floor is a preference,
    not a requirement: a p10 BELOW 10% is a book risking less than the budget
    allows, which is only a problem if return went down with it.
    """
    dds = sorted(r["mtm_dd_pct"] for r in rows)
    rets = sorted(r["return_pct"] for r in rows)
    q = mc.quantile
    return {"p5_dd": q(dds, 5), "p10_dd": q(dds, 10), "p25_dd": q(dds, 25),
            "p50_dd": q(dds, 50), "p75_dd": q(dds, 75), "p90_dd": q(dds, 90),
            "p95_dd": q(dds, 95), "p99_dd": q(dds, 99),
            # The gap past the reported point. A book can hold p95 and still
            # stack paths far beyond it, which the band alone would not show.
            "spread_dd": q(dds, 99) - q(dds, 95),
            "p5_ret": q(rets, 5), "p10_ret": q(rets, 10),
            "p50_ret": q(rets, 50), "p90_ret": q(rets, 90),
            "p95_ret": q(rets, 95),
            "p_over_25": 100.0 * sum(1 for v in dds if v > BAND_HIGH) / len(dds),
            "p_over_20": 100.0 * sum(1 for v in dds if v > 20.0) / len(dds),
            "paths": len(rows),
            "ceiling_met": q(dds, 95) <= BAND_HIGH,
            "inside": q(dds, 10) >= BAND_LOW and q(dds, 95) <= BAND_HIGH}


def _addable():
    book = set(ecs.BOOK)
    return sorted(k for k in _pool_keys() if k not in book)


def _pool_keys():
    with open(cache_path(), "rb") as handle:
        return sorted(pickle.load(handle)["members"])


def _droppable():
    return [k for k in ecs.BOOK if k not in FIXED]


# --------------------------------------------------------------------------- #
# stages
# --------------------------------------------------------------------------- #


def base(paths, workers, block_days, era="full"):
    """The current book, seed by seed. Its tail is what the screens use."""
    jobs = [((), (), None, seed) for seed in range(1, paths + 1)]
    rows = _run(jobs, workers, block_days, "base", era)
    rows.sort(key=lambda r: -r["mtm_dd_pct"])
    state = _load()
    state["base"] = {"paths": paths, "band": _band(rows),
                     "by_seed": [{"seed": r["seed"],
                                  "mtm_dd_pct": round(r["mtm_dd_pct"], 3),
                                  "return_pct": round(r["return_pct"], 2)}
                                 for r in rows]}
    _save(state)
    band = state["base"]["band"]
    print(f"\ncurrent book, {paths} paths: dd p10 {band['p10_dd']:.2f}  "
          f"p25 {band['p25_dd']:.2f}  p50 {band['p50_dd']:.2f}  "
          f"p75 {band['p75_dd']:.2f}  p90 {band['p90_dd']:.2f}  "
          f"p95 {band['p95_dd']:.2f}   return p50 {band['p50_ret']:.0f}%")


def probe(workers, block_days):
    """One REAL-ORDER path per single change, IN EACH ERA SEPARATELY.

    92 cells on 30 tail orders is an hour; on the realised order it is two
    minutes an era. This cannot rank dampers -- one path says nothing about a
    tail -- so it is used ONLY to find the changes that raise return in BOTH
    eras, and every survivor is re-scored on the distribution by `screen`.

    A cell that helps one era and hurts the other is not a smaller version of a
    good cell, it is a regime bet, and the whole reason for splitting the eras
    is to refuse it here rather than discover it later.
    """
    results = {}
    for era in ("is", "oos"):
        jobs = [((), (), None, 0)]
        jobs += [((), (name,), None, 0) for name in _addable()]
        jobs += [((name,), (), None, 0) for name in _droppable()]
        rows = _run(jobs, workers, block_days, f"probe {era}", era)
        ref = next(r for r in rows if not r["add"] and not r["drop"])
        by_change = {}
        for row in rows:
            if not row["add"] and not row["drop"]:
                continue
            label = (("+" + row["add"][0]) if row["add"]
                     else ("-" + row["drop"][0]))
            by_change[label] = (row["return_pct"] - ref["return_pct"],
                                row["mtm_dd_pct"] - ref["mtm_dd_pct"])
        results[era] = {"ref": ref, "changes": by_change}
        print(f"\n{era.upper()} realised order, current book: "
              f"{ref['return_pct']:+.1f}%   MTM dd {ref['mtm_dd_pct']:.2f}%",
              flush=True)

    both = [label for label in results["is"]["changes"]
            if results["is"]["changes"][label][0] > 0
            and results["oos"]["changes"][label][0] > 0]
    print(f"\n{len(both)} of {len(results['is']['changes'])} changes raise "
          f"return in BOTH eras\n")
    print(f"{'change':34}{'IS d ret':>10}{'IS d dd':>9}"
          f"{'OOS d ret':>11}{'OOS d dd':>10}{'min d ret':>11}")
    scored = sorted(
        both,
        key=lambda l: -min(results["is"]["changes"][l][0],
                           results["oos"]["changes"][l][0]))
    for label in scored:
        i_ret, i_dd = results["is"]["changes"][label]
        o_ret, o_dd = results["oos"]["changes"][label]
        print(f"{label:34}{i_ret:>+9.1f}%{i_dd:>+9.2f}{o_ret:>+10.1f}%"
              f"{o_dd:>+10.2f}{min(i_ret, o_ret):>+10.1f}%")
    state = _load()
    state["probe"] = {
        "ref": {era: {"return_pct": results[era]["ref"]["return_pct"],
                      "mtm_dd_pct": results[era]["ref"]["mtm_dd_pct"]}
                for era in results},
        "both_positive": scored,
        "rows": {label: {"is_d_ret": results["is"]["changes"][label][0],
                         "is_d_dd": results["is"]["changes"][label][1],
                         "oos_d_ret": results["oos"]["changes"][label][0],
                         "oos_d_dd": results["oos"]["changes"][label][1]}
                 for label in results["is"]["changes"]}}
    _save(state)


def screen(names, tail, workers, block_days, era="full"):
    """Each named single change, on the block orders that hurt the book most."""
    state = _load()
    if "base" not in state:
        raise SystemExit("run `base` first")
    seeds = [r["seed"] for r in state["base"]["by_seed"][:tail]]
    ref = {r["seed"]: r for r in state["base"]["by_seed"] if r["seed"] in seeds}
    jobs = []
    for name in names:
        if name.startswith("-"):
            jobs += [((name[1:],), (), None, s) for s in seeds]
        else:
            jobs += [((), (name.lstrip("+"),), None, s) for s in seeds]
    rows = _run(jobs, workers, block_days, "screen", era)
    grouped = {}
    for row in rows:
        label = ("-" + row["drop"][0]) if row["drop"] else ("+" + row["add"][0])
        grouped.setdefault(label, []).append(row)
    ref_dd = sum(r["mtm_dd_pct"] for r in ref.values()) / len(ref)
    ref_ret = sum(r["return_pct"] for r in ref.values()) / len(ref)
    print(f"\ntail reference ({len(seeds)} worst orders): "
          f"mean dd {ref_dd:.2f}%   mean return {ref_ret:.0f}%")
    print(f"{'change':34}{'mean dd':>10}{'d dd':>9}{'mean ret':>11}"
          f"{'d ret':>10}{'ret per dd':>12}")
    ranked = []
    for label, group in grouped.items():
        deltas = [r["mtm_dd_pct"] - ref[r["seed"]]["mtm_dd_pct"] for r in group]
        d_dd = sum(deltas) / len(deltas)
        dd = sum(r["mtm_dd_pct"] for r in group) / len(group)
        ret = sum(r["return_pct"] for r in group) / len(group)
        ranked.append((d_dd, label, dd, ret, ret - ref_ret))
    for d_dd, label, dd, ret, d_ret in sorted(ranked):
        per = (d_ret / d_dd) if abs(d_dd) > 1e-9 else float("nan")
        print(f"{label:34}{dd:>10.2f}{d_dd:>+9.2f}{ret:>11.0f}"
              f"{d_ret:>+10.0f}{per:>12.1f}")
    state.setdefault("screen", {})[f"tail{tail}"] = [
        {"change": l, "d_dd": d, "mean_dd": dd, "mean_ret": r, "d_ret": dr}
        for d, l, dd, r, dr in sorted(ranked)]
    _save(state)


def combo(sets, tail, workers, block_days, era="full"):
    """Whole candidate BOOKS on the tail orders, not one change at a time.

    Single-change deltas do not add up: two cells that each diversify the book
    can be the same bet as each other, and the shared balance means the second
    one is sized against equity the first has already spent. A greedy path built
    from `screen` has to be re-measured as the book it actually produces
    ([[drop-test-not-standalone-return-values-a-sleeve]]).
    """
    state = _load()
    seeds = [r["seed"] for r in state["base"]["by_seed"][:tail]]
    ref = {r["seed"]: r for r in state["base"]["by_seed"] if r["seed"] in seeds}
    jobs = [((), tuple(s), None, seed) for s in sets for seed in seeds]
    rows = _run(jobs, workers, block_days, "combo", era)
    grouped = {}
    for row in rows:
        grouped.setdefault(tuple(row["add"]), []).append(row)
    ref_dd = sum(r["mtm_dd_pct"] for r in ref.values()) / len(ref)
    ref_ret = sum(r["return_pct"] for r in ref.values()) / len(ref)
    print(f"\ntail reference: mean dd {ref_dd:.2f}%   mean return "
          f"{ref_ret:.0f}%")
    print(f"{'added':56}{'mean dd':>10}{'d dd':>8}{'mean ret':>11}{'d ret':>9}")
    out = []
    for key in sorted(grouped, key=len):
        group = grouped[key]
        dd = sum(r["mtm_dd_pct"] for r in group) / len(group)
        ret = sum(r["return_pct"] for r in group) / len(group)
        out.append({"add": list(key), "mean_dd": dd, "mean_ret": ret})
        label = ", ".join(k.split(":")[0][:6] + ":" + k.split(":")[1][:10]
                          for k in key) or "-"
        print(f"{label[:55]:56}{dd:>10.2f}{dd - ref_dd:>+8.2f}{ret:>11.0f}"
              f"{ret - ref_ret:>+9.0f}")
    state.setdefault("combo", []).append({"tail": len(seeds), "rows": out})
    _save(state)


def risk(drop, add, levels, paths, workers, block_days, era="full"):
    """`risk_scale` swept for one FIXED membership, after it is chosen.

    The band's ceiling is what is being solved for: the highest risk whose p95
    still sits at or under 25%, with p5 still at or over 10%.
    """
    jobs = [(tuple(drop), tuple(add), level, seed)
            for level in levels for seed in range(1, paths + 1)]
    rows = _run(jobs, workers, block_days, "risk", era)
    grouped = {}
    for row in rows:
        grouped.setdefault(row["risk"], []).append(row)
    print(f"\ndropped: {', '.join(drop) or 'nothing'}")
    print(f"added:   {', '.join(add) or 'nothing'}")
    print(f"\n{'risk':>8}{'dd p10':>9}{'dd p25':>9}{'dd p50':>9}"
          f"{'dd p75':>9}{'dd p90':>9}{'dd p95':>9}"
          f"{'ret p10':>10}{'ret p50':>10}{'P(>25)':>9}  p95<=25")
    out = []
    for level in sorted(grouped):
        band = _band(grouped[level])
        out.append({"risk": level, **band})
        print(f"{level:>8.3f}{band['p10_dd']:>9.2f}{band['p25_dd']:>9.2f}"
              f"{band['p50_dd']:>9.2f}{band['p75_dd']:>9.2f}"
              f"{band['p90_dd']:>9.2f}{band['p95_dd']:>9.2f}"
              f"{band['p10_ret']:>9.0f}%{band['p50_ret']:>9.0f}%"
              f"{band['p_over_25']:>8.1f}%"
              f"  {'MET' if band['ceiling_met'] else '-'}")
    state = _load()
    state.setdefault("risk", []).append(
        {"drop": list(drop), "add": list(add), "paths": paths, "rows": out})
    _save(state)


def evaluate(drop, add, risk_scale, paths, workers, block_days, tag, era="full"):
    jobs = [(tuple(drop), tuple(add), risk_scale, seed)
            for seed in range(1, paths + 1)]
    rows = _run(jobs, workers, block_days, "eval", era)
    band = _band(rows)
    print(f"\ndropped: {', '.join(drop) or 'nothing'}")
    print(f"added:   {', '.join(add) or 'nothing'}")
    print(f"risk {risk_scale if risk_scale else ecs.CANON_RISK_SCALE}   "
          f"{paths} paths")
    print(f"  MTM dd   p5 {band['p5_dd']:.2f}  p10 {band['p10_dd']:.2f}  "
          f"p25 {band['p25_dd']:.2f}  p50 {band['p50_dd']:.2f}  "
          f"p75 {band['p75_dd']:.2f}  p90 {band['p90_dd']:.2f}  "
          f"p95 {band['p95_dd']:.2f}")
    print(f"  return   p5 {band['p5_ret']:.0f}%  p10 {band['p10_ret']:.0f}%  "
          f"p50 {band['p50_ret']:.0f}%  p90 {band['p90_ret']:.0f}%  "
          f"p95 {band['p95_ret']:.0f}%")
    print(f"  P(dd>20%) {band['p_over_20']:.1f}%   "
          f"P(dd>25%) {band['p_over_25']:.1f}%")
    print(f"  p95 <= 25%: {'MET' if band['ceiling_met'] else 'NOT met'}     "
          f"p10-p95 inside 10-25%: "
          f"{'MET' if band['inside'] else 'NOT met'}")
    state = _load()
    state.setdefault("eval", {})[tag or "last"] = {
        "drop": list(drop), "add": list(add), "risk": risk_scale,
        "band": band}
    _save(state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("cache", "base", "probe", "screen",
                                            "combo", "risk", "eval"))
    parser.add_argument("--paths", type=int, default=250)
    parser.add_argument("--tail", type=int, default=30)
    parser.add_argument("--names", default="")
    parser.add_argument("--sets", default="", help="';'-separated add sets")
    parser.add_argument("--drop", default="")
    parser.add_argument("--add", default="")
    parser.add_argument("--risk", type=float, default=None)
    parser.add_argument("--levels", default="0.135,0.17,0.20,0.24")
    parser.add_argument("--tag", default="")
    parser.add_argument("--block-days", type=int, default=mc.BLOCK_DAYS)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 8)
    parser.add_argument("--era", default="full", choices=("is","oos","full"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    live.arm()
    split = lambda s: tuple(x for x in s.replace(" ", "").split(",") if x)
    if args.command == "cache":
        build_cache(force=args.force)
    elif args.command == "base":
        base(args.paths, args.workers, args.block_days, args.era)
    elif args.command == "probe":
        probe(args.workers, args.block_days)
    elif args.command == "screen":
        screen(split(args.names), args.tail, args.workers, args.block_days,
               args.era)
    elif args.command == "combo":
        combo([split(p) for p in args.sets.split(";")], args.tail,
              args.workers, args.block_days, args.era)
    elif args.command == "risk":
        risk(split(args.drop), split(args.add),
             [float(v) for v in split(args.levels)], args.paths, args.workers,
             args.block_days, args.era)
    else:
        evaluate(split(args.drop), split(args.add), args.risk, args.paths,
                 args.workers, args.block_days, args.tag, args.era)


if __name__ == "__main__":
    main()
