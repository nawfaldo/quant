"""Screen TikTok PASS/PASS* cells against canon: add one, swap one, then combine.

Every configuration is replayed through `exness_combined_strategies.replay` at
canon settings (live fills, $500 cold per window, risk 0.130, min lot forced),
on four windows: 2022-26, 2022-24, 2025-26, 2026. Canon trades come from the
`canon19_2226` report cache; TikTok trades from `_tt_canon_logs`.

A change is ACCEPTED when it raises the final balance in all four windows and
raises no window's MTM drawdown by more than `DD_TOL` points. Accepted changes
are ranked by the summed log growth over the four windows.

    py -m sandbox.research._tt_canon_screen
"""
import platform

platform._wmi = None

import json
import math
import multiprocessing
import os
import pickle
from datetime import datetime, timezone

from sandbox.research import exness_combined_strategies as ecs

HERE = os.path.dirname(__file__)
CACHE = os.path.join(HERE, "..", ".cache")
OUT = os.path.join(HERE, "..", "results", "tt_canon_screen.json")
DD_TOL = float(os.environ.get("DD_TOL", "1.0"))
TOP_SWAP = int(os.environ.get("TOP_SWAP", "20"))
GREEDY_STEPS = int(os.environ.get("GREEDY_STEPS", "6"))


def ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


END = ts("2026-08-21")
WINDOWS = {"2022-26": (ts("2022-01-01"), END), "2022-24": (ts("2022-01-01"), ts("2025-01-01")),
           "2025-26": (ts("2025-01-01"), END), "2026": (ts("2026-01-01"), END)}

_S = {}


def arm():
    ecs.ef.IS_END = ts("2022-01-01")
    ecs.TICK_COSTS = True
    ecs.BROKER_STOPS = False
    ecs.FORCE_MINIMUM_LOT = True
    ecs.UNCAPPED = True
    ecs.GLOBAL_SIZING_CAP = 1500.0
    ecs.MAX_DD_CONCENTRATION = None
    ecs.FAIR_CAP = False


def load():
    arm()
    with open(os.path.join(CACHE, "mc_books_canon19_2226_2022.pkl"), "rb") as h:
        canon = pickle.load(h)
    members = {f"{m['symbol']}:{m['family']}": m for m in canon["members"]}
    logs = dict(canon["logs"])
    bars, ctx = dict(canon["bars_by"]), dict(canon["ctx_by"])
    tt = {}
    for mode in ("normal", "widestop"):
        path = os.path.join(CACHE, f"tt_canon_logs_{mode}.pkl")
        with open(path, "rb") as h:
            got = pickle.load(h)
        for key, c in got["cells"].items():
            tt[key] = c
            members[key] = {"symbol": c["symbol"], "family": c["family"],
                            "params": c["params"], "asset_class": "tt"}
            logs[key] = c["log"]
        for sym, b in got["bars"].items():
            bars.setdefault(sym, b)
        for sym, c in got["ctx"].items():
            ctx.setdefault(sym, c)
    return {"members": members, "logs": logs, "bars": bars, "ctx": ctx,
            "canon": [f"{m['symbol']}:{m['family']}" for m in canon["members"]],
            "tt": tt, "marking": {}}


def _init():
    _S.update(load())


def score_book(keys):
    members = [_S["members"][k] for k in keys]
    out = {}
    for name, (lo, hi) in WINDOWS.items():
        b = ecs.replay(members, _S["logs"], _S["bars"], _S["ctx"],
                       scale=ecs.SLEEVE_SCALE, sizing_cap=ecs.sizing_caps(members),
                       risk_scale=ecs.CANON_RISK_SCALE, gross_cap=ecs.CANON_GROSS_CAP,
                       fair_cap=False, initial=ecs.CANON_INITIAL, lo=lo, hi=hi,
                       marking_cache=_S["marking"])
        out[name] = {"ret": round(b["return_pct"], 2), "dd": round(b["mtm_dd_pct"], 2),
                     "final": b["final"]}
    return out


def job(item):
    label, keys = item
    return label, keys, score_book(keys)


def verdict(res, base):
    growth = sum(math.log(max(res[w]["final"], 1e-9) / base[w]["final"]) for w in WINDOWS)
    up = all(res[w]["final"] > base[w]["final"] for w in WINDOWS)
    worst_dd = max(res[w]["dd"] - base[w]["dd"] for w in WINDOWS)
    return growth, up and worst_dd <= DD_TOL, worst_dd


def line(label, res, base):
    g, ok, wdd = verdict(res, base)
    cells = "  ".join(f"{w} {res[w]['ret']:+9,.0f}% dd {res[w]['dd']:5.2f}" for w in WINDOWS)
    return f"{'OK ' if ok else '   '}{label:<60} {cells}  growth {g:+.3f}  worst dd+ {wdd:+.2f}"


def run_all(pool, items):
    return list(pool.imap_unordered(job, items, chunksize=1))


if __name__ == "__main__":
    state = load()
    canon, tt = state["canon"], sorted(state["tt"])
    workers = max(1, (os.cpu_count() or 4) - 4)
    report = {"dd_tol": DD_TOL, "stages": {}}
    with multiprocessing.Pool(workers, _init) as pool:
        base = run_all(pool, [("canon", canon)])[0][2]
        print(line("canon (19)", base, base), flush=True)
        report["base"] = base

        # stage 1: add one / drop one
        adds = run_all(pool, [(f"+{k}", canon + [k]) for k in tt])
        drops = run_all(pool, [(f"-{k}", [c for c in canon if c != k]) for k in canon])
        report["stages"]["add"] = [(l, k, r) for l, k, r in adds]
        report["stages"]["drop"] = [(l, k, r) for l, k, r in drops]
        adds.sort(key=lambda x: -verdict(x[2], base)[0])
        print(f"\nADD ONE ({len(adds)})", flush=True)
        for l, k, r in adds:
            print(line(l, r, base), flush=True)
        drops.sort(key=lambda x: -verdict(x[2], base)[0])
        print(f"\nDROP ONE ({len(drops)})", flush=True)
        for l, k, r in drops:
            print(line(l, r, base), flush=True)

        # stage 2: swap one -- the top TOP_SWAP adds against every canon sleeve
        top = [k[-1] for _, k, _ in adds[:TOP_SWAP]]
        swaps = run_all(pool, [(f"{c} -> {t}", [x for x in canon if x != c] + [t])
                               for t in top for c in canon])
        report["stages"]["swap"] = [(l, k, r) for l, k, r in swaps]
        swaps.sort(key=lambda x: -verdict(x[2], base)[0])
        print(f"\nSWAP ONE (top {len(top)} adds x {len(canon)} canon sleeves), best 40",
              flush=True)
        for l, k, r in swaps[:40]:
            print(line(l, r, base), flush=True)

        # stage 3: greedy -- from canon, take the best accepted move (add any TT
        # cell, drop any member, or swap) until nothing accepted improves it
        book, cur, path = list(canon), base, []
        pool_tt = [k for _, k, r in adds if verdict(r, base)[1]][:TOP_SWAP] or top
        pool_tt = [k[-1] if isinstance(k, list) else k for k in pool_tt]
        for step in range(GREEDY_STEPS):
            moves = [(f"+{t}", book + [t]) for t in pool_tt if t not in book]
            moves += [(f"-{m}", [x for x in book if x != m]) for m in book]
            moves += [(f"{m} -> {t}", [x for x in book if x != m] + [t])
                      for t in pool_tt if t not in book for m in book if m not in pool_tt]
            got = run_all(pool, moves)
            ok = [(verdict(r, cur)[0], l, k, r) for l, k, r in got if verdict(r, cur)[1]]
            if not ok:
                print(f"\nGREEDY step {step + 1}: no accepted move -- stop", flush=True)
                break
            g, l, k, r = max(ok, key=lambda x: x[0])
            book, cur = k, r
            path.append({"move": l, "book": k, "result": r})
            print(f"\nGREEDY step {step + 1}: {l}", flush=True)
            print(line(f"book ({len(book)})", r, base), flush=True)
        report["greedy"] = path

    report["tt_parity"] = {k: {"sealed": c["sealed_oos"], "rerun": c["rerun_oos"],
                               "trades_2022_26": len(c["log"])}
                           for k, c in state["tt"].items()}
    with open(OUT, "w", encoding="utf-8") as h:
        json.dump(report, h, indent=1, default=str)
    print(f"\nwrote {OUT}")
