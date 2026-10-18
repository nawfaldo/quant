"""Equal-drawdown leverage for `results/filtered/fundednext/` ->
`results/filtered/leveraged_fundednext/`.

Every strategy is re-sized so its 2024-2025 marked-to-market drawdown is
`FN_LEV_TARGET_DD` (10%), then replayed on 2026, 2024-2025 and 2024-2026, each
from a fresh $6,000. k is set on 2024-2025 ONLY, so 2026 is an honest test of
the leveraged size (k from 2026's own drawdown would put every strategy at the
target by construction and lever the luckiest 2026 paths most).

Leverage k multiplies `RISK_FRACTION` (1.5% stop risk) and lifts the 4x notional
ceiling by the same k, so a low-drawdown strategy can actually reach the
target. Re-run, not multiplied: compounding and the minimum lot are real. One
correction step: k1 = target / dd(k=1), k = k1 * target / dd(k1).

Runs under the same rules as the replay: CFD_BALANCE=6000, measured $3/lot,
CFD_BROKER_HOURS=1, EXNESS_FILLS_ONLY=1, OOS to CFD_OOS_END. One child per
protocol (window / TT / bar-counted lookbacks), as `fundednext_combined_strategies`.

    python -m sandbox.research._leverage_fundednext [--workers N]
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import pickle
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sandbox.research import fundednext_combined_strategies as f
from sandbox.research import twohost

ef = f.ef
TARGET = float(os.environ.get("FN_LEV_TARGET_DD", "10"))
SRC = os.environ.get("FN_LEV_SRC") or os.path.join(f.RESULTS, "filtered", "fundednext")
DST = os.path.join(f.RESULTS, "filtered", "leveraged_fundednext")
BASE_RISK, BASE_MARGIN = ef.RISK_FRACTION, ef.MARGIN_FRACTION
#: FN_LEV_SCREEN=1: one 2026 run at 1x per cell -> trades and net per-trade t
SCREEN = os.environ.get("FN_LEV_SCREEN") == "1"


def pool_cells():
    """`[(name, cell)]` in SRC (FN_LOG_FILES narrows it), ncf cells given their
    bar and window -- independent of the combined module's own pool."""
    only = set(filter(None, os.environ.get("FN_LOG_FILES", "").split(",")))
    out = []
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".json") or name == "SURVIVORS.json" or (only and name not in only):
            continue
        with open(os.path.join(SRC, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        if cell["symbol"] in f.EXCLUDE_SYMBOLS:
            continue
        if "bar_minutes" not in cell:
            cell["bar_minutes"] = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[cell["timeframe"]]
            cell["ncf_window"] = cell["protocol"]["execution"]["window"]
        out.append((name, cell))
    return out


def _screen(cell, bars, ctx, lo, hi):
    r = _run(cell, bars, ctx, 1.0, lo, hi)
    x = [1e4 * t["points"] / t["entry"] for t in r["trade_log"]]
    n = len(x)
    m = sum(x) / n if n else 0.0
    sd = (sum((v - m) ** 2 for v in x) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {"n": n, "bp": round(m, 3), "t": round(m * math.sqrt(n) / sd, 2) if sd else 0.0,
            "ret": r["return_pct"], "dd": r["max_dd_pct"], "pf": r.get("pf")}


def _run(cell, bars, ctx, k, lo, hi):
    ef.RISK_FRACTION, ef.MARGIN_FRACTION = BASE_RISK * k, BASE_MARGIN / k
    return ef.backtest(cell["family"], bars, ctx, ef.rehydrate(cell["params"]),
                       lo=lo, hi=hi, include_trades=True)


def _worst_day(log):
    """Worst New York day of closed-trade P&L, in dollars."""
    days = defaultdict(float)
    for t in log:
        day = (datetime.fromtimestamp(t["exit_ts"], timezone.utc)
               - timedelta(hours=4)).date()
        days[day] += t["pnl"]
    return round(min(days.values()), 2) if days else 0.0


def _stats(r):
    return {"return_pct": r["return_pct"], "max_dd_pct": r["max_dd_pct"],
            "pf": r.get("pf"), "trades": r["trades"]}


#: FN_LEV_LADDER=1: whole-number leverage 1x, 2x, 3x ... up to and including the
#: first step whose 2024-2025 drawdown passes the target (at most LADDER_MAX).
LADDER = os.environ.get("FN_LEV_LADDER") == "1"
LADDER_MAX = 30


def _ladder(cell, bars, ctx, lo, mid, hi):
    steps = []
    for k in range(1, LADDER_MAX + 1):
        step = {"k": k}
        for label, a, b in (("2024-2025", lo, mid), ("2026", mid, hi),
                            ("2024-2026", lo, hi)):
            r = _run(cell, bars, ctx, float(k), a, b)
            step[label] = _stats(r)
            if label == "2026":
                step["worst_day_2026_usd"] = _worst_day(r["trade_log"])
        steps.append(step)
        if step["2024-2025"]["max_dd_pct"] > TARGET:
            break
    return steps


def _lev_group(job):
    (symbol, bar), items = job
    ef.BAR_MINUTES = bar
    try:
        ef.resolve(symbol, allow_stale=True)
        ef.prewarm(symbol, "validate", bar)
        if os.environ.get("FN_TT") == "1":
            bars, ctx = ef.context(symbol, "validate", bar)
        else:
            bars, ctx = ef.context(symbol, "validate", bar,
                                   {cell["family"] for _, cell in items})
    except (Exception, SystemExit) as error:  # noqa: BLE001
        return {"error": f"{symbol} {bar}m: {error}"}
    lo, mid, hi = f._stamp("2024-01-01"), ef.IS_END, ef.OOS_END
    out, errors = {}, []
    for name, cell in items:
        if SCREEN:
            try:
                out[name] = _screen(cell, bars, ctx, mid, hi)
            except (Exception, SystemExit) as error:  # noqa: BLE001
                errors.append(f"{name}: {type(error).__name__}: {error}")
            continue
        if LADDER:
            try:
                out[name] = _ladder(cell, bars, ctx, lo, mid, hi)
            except (Exception, SystemExit) as error:  # noqa: BLE001
                errors.append(f"{name}: {type(error).__name__}: {error}")
            continue
        try:
            dd1 = _run(cell, bars, ctx, 1.0, lo, mid)["max_dd_pct"]
            k1 = TARGET / dd1 if dd1 > 0 else 1.0
            dd2 = _run(cell, bars, ctx, k1, lo, mid)["max_dd_pct"]
            k = k1 * TARGET / dd2 if dd2 > 0 else k1
            row = {"k": round(k, 3), "is_dd_at_k1": dd1}
            for label, a, b in (("2024-2025", lo, mid), ("2026", mid, hi),
                                ("2024-2026", lo, hi)):
                r = _run(cell, bars, ctx, k, a, b)
                row[label] = _stats(r)
                if label == "2026":
                    row["worst_day_2026_usd"] = _worst_day(r["trade_log"])
                    row["fill_rate_2026"] = r.get("fill_rate")
            out[name] = row
        except (Exception, SystemExit) as error:  # noqa: BLE001
            errors.append(f"{name}: {type(error).__name__}: {error}")
    return {"rows": out, "errors": errors}


MODE = "screen" if SCREEN else "ladder" if LADDER else "lev"


def _jobs(cells, protocol):
    """`[((symbol, bar), items)]` of one protocol, in a fixed order."""
    groups = {}
    for name, cell in cells:
        if f._protocol(name, cell) == protocol:
            groups.setdefault((cell["symbol"], cell["bar_minutes"]), []).append((name, cell))
    return sorted(groups.items(), key=lambda kv: (kv[0][1], -len(kv[1]), kv[0][0]))


def _unit(protocol, job):
    (symbol, bar), items = job
    return twohost.unit_id(MODE, protocol, symbol, bar, sorted(n for n, _ in items))


def _unit_job(arg):
    """twohost: claim the group, work it, store the result -- or skip it."""
    unit, job = arg
    if not twohost.claim(unit):
        return {"rows": {}, "errors": [], "skip": True}
    got = _lev_group(job)
    twohost.finish(unit, got)
    return got


def child(workers, out_path):
    here = f._here()
    jobs = _jobs(pool_cells(), here)
    print(f"{sum(len(v) for _, v in jobs)} cells in {len(jobs)} groups, "
          f"protocol {dict(here) or 'native'}", flush=True)
    rows = {}
    if jobs:
        if twohost.enabled():
            # the Mac walks the list from the other end, so the hosts meet in the middle
            order = jobs[::-1] if twohost.IS_MAC else jobs
            fn, items = _unit_job, [(_unit(here, j), j) for j in order]
        else:
            fn, items = _lev_group, jobs
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(max(1, min(workers, len(jobs))), maxtasksperchild=1) as pool:
            for got in pool.imap_unordered(fn, items):
                if "error" in got:
                    print(f"  skipped -- {got['error']}", flush=True)
                    continue
                for line in got["errors"]:
                    print(f"  skipped -- {line}", flush=True)
                rows.update(got["rows"])
    with open(out_path, "wb") as handle:
        pickle.dump(rows, handle, protocol=5)
    print(f"  {len(rows)} done here", flush=True)


def parent(workers):
    cells = dict(pool_cells())
    protocols = sorted({f._protocol(n, c) for n, c in cells.items()})
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.makedirs(DST, exist_ok=True)
    rows = {}
    for n, protocol in enumerate(protocols):
        part = os.path.join(DST, f".{MODE}{n}.pkl")
        if twohost.enabled() and os.path.exists(part):
            os.remove(part)  # the claim board, not part files, says what is done
        if not os.path.exists(part):
            env = {k: v for k, v in os.environ.items() if k not in f.PROTOCOL_VARS}
            env.update(dict(protocol))
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run([sys.executable, "-m", "sandbox.research._leverage_fundednext",
                            "_child", "--workers", str(workers), "--out", part],
                           env=env, cwd=root, check=True)
        with open(part, "rb") as handle:
            rows.update(pickle.load(handle))
        if twohost.enabled():
            os.remove(part)
    if twohost.enabled():
        if twohost.IS_MAC:
            print("twohost: Mac side finished; the PC collects and exports")
            return
        units = [_unit(p, j) for p in protocols for j in _jobs(cells.items(), p)]
        rows = {}
        for got in twohost.collect(units).values():
            rows.update(got.get("rows", {}))
    if SCREEN:
        with open(os.path.join(DST, "screen.json"), "w", encoding="utf-8") as handle:
            json.dump(rows, handle)
        print(f"screened {len(rows)}")
        return
    if LADDER:
        _export_ladder(rows)
        return
    index = json.load(open(os.path.join(SRC, "SURVIVORS.json"), encoding="utf-8"))
    out = []
    for s in index["survivors"]:
        row = rows.get(s["file"])
        if row is None:
            continue
        lev = {"target_dd_pct": TARGET, **row}
        cell = json.load(open(os.path.join(SRC, s["file"]), encoding="utf-8"))
        cell["leverage"] = lev
        with open(os.path.join(DST, s["file"]), "w", encoding="utf-8") as handle:
            json.dump(cell, handle, indent=1)
        out.append({**s, "k": row["k"],
                    "lev_2026_return_pct": row["2026"]["return_pct"],
                    "lev_2026_max_dd_pct": row["2026"]["max_dd_pct"],
                    "lev_2024_2025_return_pct": row["2024-2025"]["return_pct"],
                    "lev_2024_2025_max_dd_pct": row["2024-2025"]["max_dd_pct"],
                    "lev_2024_2026_return_pct": row["2024-2026"]["return_pct"],
                    "lev_2024_2026_max_dd_pct": row["2024-2026"]["max_dd_pct"],
                    "lev_worst_day_2026_usd": row["worst_day_2026_usd"]})
    out.sort(key=lambda s: -s["lev_2026_return_pct"])
    with open(os.path.join(DST, "SURVIVORS.json"), "w", encoding="utf-8") as handle:
        json.dump({"count": len(out), "target_dd_pct": TARGET,
                   "k_set_on": "2024-2025 max MTM drawdown",
                   "ranked_by": "leveraged 2026 return", "survivors": out},
                  handle, indent=2)
    print(f"wrote {len(out)} of {len(cells)} -> {DST}")


def _export_ladder(rows):
    """Adds `leverage.ladder` to every file in DST and `ladder` (k, 2026
    return / dd, 2024-2025 dd per step) plus `ladder_max_k` -- the last step
    still inside the target -- to the index."""
    path = os.path.join(DST, "SURVIVORS.json")
    index = json.load(open(path, encoding="utf-8"))
    for s in index["survivors"]:
        steps = rows.get(s["file"])
        if steps is None:
            continue
        cell_path = os.path.join(DST, s["file"])
        cell = json.load(open(cell_path, encoding="utf-8"))
        cell["leverage"]["ladder"] = steps
        with open(cell_path, "w", encoding="utf-8") as handle:
            json.dump(cell, handle, indent=1)
        inside = [st["k"] for st in steps if st["2024-2025"]["max_dd_pct"] <= TARGET]
        s["ladder_max_k"] = max(inside) if inside else 0
        s["ladder"] = [{"k": st["k"],
                        "is_dd": st["2024-2025"]["max_dd_pct"],
                        "ret_2026": st["2026"]["return_pct"],
                        "dd_2026": st["2026"]["max_dd_pct"],
                        "ret_2024_2026": st["2024-2026"]["return_pct"]} for st in steps]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2)
    print(f"ladder written for {len(rows)} -> {DST}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=("run", "_child"))
    parser.add_argument("--workers", type=int, default=f.default_workers())
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.command == "_child":
        child(args.workers, args.out)
    else:
        parent(args.workers)
