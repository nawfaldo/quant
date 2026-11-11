"""Replay `results/filtered/fundednext/` on the The5ers model ->
`results/filtered/the5ers_replay/`.

Every FundedNext filtered cell, its params FIXED (no search), priced as The5ers
High Stakes $5K: The5ers' commission / swap / leverage / break hours, fills on
the5ers_*_1m. Scored on FundedNext's own windows only, 2024-2025 and 2026,
each from a fresh $5,000, signalled off the same vendor tables as FundedNext.

Symbols The5ers does not quote (nth25, swi20, us2000) are skipped. One child
per protocol (window / TT / bar-counted lookbacks), as `_leverage_fundednext`.
TWOHOST_RUN set: PC and Mac share the groups.

    python -m sandbox.research._replay_the5ers [--workers N]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import pickle
import subprocess
import sys

# BEFORE ANY cfd_families IMPORT: broker, split and costs bind at import.
os.environ["NCF_BROKER"] = "the5ers"
os.environ["CFD_BROKER"] = "the5ers"
os.environ.setdefault("CFD_SPLIT", "2024-2025")
os.environ.setdefault("CFD_OOS_END", os.environ.get("T5_DATA_END", "2026-11-07"))
_argv = sys.argv
sys.argv = [sys.argv[0], "--broker", "the5ers"]
from sandbox.research import new_cfd_families as ncf  # noqa: E402
sys.argv = _argv
for _k, _v in ncf.ENV.items():
    os.environ.setdefault(_k, _v)
if os.environ.get("FN_TT") == "1":
    import platform
    platform._wmi = None
    from sandbox.research import cfd_tt_families  # noqa: E402,F401
    os.environ["CFD_BROKER"] = "the5ers"
from sandbox.research import cfd_families as ef  # noqa: E402
from sandbox.research import twohost  # noqa: E402

if ef.BROKER != "the5ers":
    raise SystemExit("cfd_families imported for another broker; run in a fresh interpreter")
BASE_RISK, BASE_MARGIN = ef.RISK_FRACTION, ef.MARGIN_FRACTION

RESULTS = ef.RESULTS
#: T5_SRC: another pool in the same format (2026-11-08: the The5ers search
#: survivors `results/the5ers`, screened like FundedNext's)
SRC = os.environ.get("T5_SRC") or os.path.join(RESULTS, "filtered", "fundednext")
#: T5_MODE=lev (2026-11-08, "do leverage thing like fundednext"): as
#: `_leverage_fundednext` -- k set on 2024-2025 to a 10% MTM drawdown, then
#: 2024-2025 / 2026 / 2024-2026 at k, plus the whole-number ladder 1x, 2x ...
#: up to the first step past the target. $5,000, The5ers costs and hours.
MODE = os.environ.get("T5_MODE", "replay")
TARGET = float(os.environ.get("T5_LEV_TARGET_DD", "10"))
LADDER_MAX = 30
DST = os.environ.get("T5_DST") or os.path.join(RESULTS, "filtered", "the5ers_replay")
SKIP = {"es", "nq", "jp225", "nth25", "swi20", "us2000"}
START = "2024-01-01"
#: (label, from, to): FundedNext's in-sample and holdout years only (the user,
#: 2026-11-08: "use the same year as fundednext"), vendor signal tables as FundedNext
WINDOWS = (("2024-2025", "2024-01-01", "2026-01-01"), ("2026", "2026-01-01", None))
PROTOCOL_VARS = ("FN_TT", "EXNESS_BAR_LOOKBACKS", "EXNESS_WINDOW",
                 "EXNESS_IS_FIRST_YEAR", "EXNESS_TT_STOP_WIDEN",
                 "CFD_BROKER_HOURS", "EXNESS_FILLS_ONLY")


def _stamp(text):
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def pool_cells():
    only = set(filter(None, os.environ.get("T5_ONLY", "").split(",")))  # a test: these files only
    out = []
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".json") or name == "SURVIVORS.json" or (only and name not in only):
            continue
        with open(os.path.join(SRC, name), encoding="utf-8") as handle:
            cell = json.load(handle)
        if cell["symbol"] in SKIP:
            continue
        if "bar_minutes" not in cell:
            cell["bar_minutes"] = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[cell["timeframe"]]
            cell["ncf_window"] = cell["protocol"]["execution"]["window"]
        out.append((name, cell))
    return out


def _protocol(name, cell):
    """As `fundednext_combined_strategies._protocol`: the env a cell was searched under."""
    env = {k: str(v) for k, v in (cell.get("protocol_change") or {}).items()}
    if (cell.get("search") == "cfd_tt_families"
            or str(cell.get("study_wave", "")).startswith("tiktok")):
        env["FN_TT"] = "1"
    stem = cell["timeframe"]
    if "-barlb" in stem:
        env["EXNESS_BAR_LOOKBACKS"] = "1"
    if stem.endswith("-off"):
        env["EXNESS_WINDOW"] = "off"
    env.update(CFD_BROKER_HOURS="1", EXNESS_FILLS_ONLY="1")
    if cell.get("ncf_window") not in (None, "rth"):
        env["EXNESS_WINDOW"] = cell["ncf_window"]
    return tuple(sorted(env.items()))


def _here():
    return tuple(sorted((k, os.environ[k]) for k in PROTOCOL_VARS if k in os.environ))


def _stats(r):
    out = {k: r.get(k) for k in ("return_pct", "max_dd_pct", "pf", "trades", "win_rate",
                                 "monthly_sharpe", "fill_rate")}
    # FundedNext's pool filter (2026-11-08, "export it like fundednext"): net
    # per-trade bp and its t, as `_leverage_fundednext._screen`
    x = [1e4 * t["points"] / t["entry"] for t in r.get("trade_log") or ()]
    n = len(x)
    m = sum(x) / n if n else 0.0
    sd = (sum((v - m) ** 2 for v in x) / (n - 1)) ** 0.5 if n > 1 else 0.0
    out.update(net_bp_per_trade=round(m, 3), net_t_stat=round(m * n ** 0.5 / sd, 2) if sd else 0.0)
    return out


def _lev_run(cell, bars, ctx, params, k, lo, hi):
    ef.RISK_FRACTION, ef.MARGIN_FRACTION = BASE_RISK * k, BASE_MARGIN / k
    try:
        return ef.backtest(cell["family"], bars, ctx, params, lo=lo, hi=hi, include_trades=True)
    finally:
        ef.RISK_FRACTION, ef.MARGIN_FRACTION = BASE_RISK, BASE_MARGIN


def _worst_day(log):
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    days = defaultdict(float)
    for t in log:
        days[(datetime.fromtimestamp(t["exit_ts"], timezone.utc) - timedelta(hours=4)).date()] += t["pnl"]
    return round(min(days.values()), 2) if days else 0.0


def _lev_cell(cell, bars, ctx, params, hi):
    """`_leverage_fundednext._lev_group` + `_ladder` for one cell."""
    lo, mid = _stamp("2024-01-01"), _stamp("2026-01-01")
    spans = (("2024-2025", lo, mid), ("2026", mid, hi), ("2024-2026", lo, hi))
    keep = ("return_pct", "max_dd_pct", "pf", "trades")
    dd1 = _lev_run(cell, bars, ctx, params, 1.0, lo, mid)["max_dd_pct"]
    k1 = TARGET / dd1 if dd1 > 0 else 1.0
    dd2 = _lev_run(cell, bars, ctx, params, k1, lo, mid)["max_dd_pct"]
    k = k1 * TARGET / dd2 if dd2 > 0 else k1
    row = {"k": round(k, 3), "is_dd_at_k1": dd1}
    for label, a, b in spans:
        r = _lev_run(cell, bars, ctx, params, k, a, b)
        row[label] = {x: r.get(x) for x in keep}
        if label == "2026":
            row["worst_day_2026_usd"] = _worst_day(r["trade_log"])
            row["fill_rate_2026"] = r.get("fill_rate")
    steps = []
    for kk in range(1, LADDER_MAX + 1):
        step = {"k": kk}
        for label, a, b in spans:
            r = _lev_run(cell, bars, ctx, params, float(kk), a, b)
            step[label] = {x: r.get(x) for x in keep}
            if label == "2026":
                step["worst_day_2026_usd"] = _worst_day(r["trade_log"])
        steps.append(step)
        if step["2024-2025"]["max_dd_pct"] > TARGET:
            break
    row["ladder"] = steps
    return row


def _group(job):
    (symbol, bar), items = job
    ef.BAR_MINUTES = bar
    try:
        ef.resolve(symbol, allow_stale=True)
        ef.prewarm(symbol, "validate", bar)
        if os.environ.get("FN_TT") == "1":
            bars, ctx = ef.context(symbol, "validate", bar)
        else:
            bars, ctx = ef.context(symbol, "validate", bar, {c["family"] for _, c in items})
    except (Exception, SystemExit) as error:  # noqa: BLE001
        return {"error": f"{symbol} {bar}m: {error}"}
    hi = ef.OOS_END
    out, errors = {}, []
    for name, cell in items:
        try:
            params = ef.rehydrate(cell["params"])
            if MODE == "lev":
                out[name] = _lev_cell(cell, bars, ctx, params, hi)
                continue
            row = {}
            # FundedNext's own windows, each from a fresh $5,000
            for label, a, b in WINDOWS:
                r = ef.backtest(cell["family"], bars, ctx, params, lo=_stamp(a),
                                hi=_stamp(b) if b else hi, include_trades=True)
                row[label] = _stats(r)
                if label == "2020-2026":
                    row["annual"] = r.get("annual") or {}
            out[name] = row
        except (Exception, SystemExit) as error:  # noqa: BLE001
            errors.append(f"{name}: {type(error).__name__}: {error}")
    return {"rows": out, "errors": errors}


def _jobs(cells, protocol):
    groups = {}
    for name, cell in cells:
        if _protocol(name, cell) == protocol:
            groups.setdefault((cell["symbol"], cell["bar_minutes"]), []).append((name, cell))
    # light bars first; 5m last (heaviest contexts)
    return sorted(groups.items(), key=lambda kv: (kv[0][1] == 5, -len(kv[1]), kv[0]))


def _unit(protocol, job):
    (symbol, bar), items = job
    # the same id on both hosts: SRC relative to results/, never an absolute path
    src = os.path.relpath(SRC, RESULTS).replace(os.sep, "/")
    return twohost.unit_id("t5replay" if MODE == "replay" else f"t5{MODE}", src, protocol, symbol, bar, sorted(n for n, _ in items))


def _unit_job(arg):
    unit, job = arg
    if not twohost.claim(unit):
        return {"rows": {}, "errors": [], "skip": True}
    got = _group(job)
    twohost.finish(unit, got)
    return got


def child(workers, out_path):
    here = _here()
    jobs = _jobs(pool_cells(), here)
    print(f"{sum(len(v) for _, v in jobs)} cells in {len(jobs)} groups, protocol {dict(here)}", flush=True)
    rows = {}
    if jobs:
        if twohost.enabled():
            order = jobs[::-1] if twohost.IS_MAC else jobs
            fn, items = _unit_job, [(_unit(here, j), j) for j in order]
        else:
            fn, items = _group, jobs
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
    protocols = sorted({_protocol(n, c) for n, c in cells.items()})
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.makedirs(DST, exist_ok=True)
    rows = {}
    for n, protocol in enumerate(protocols):
        part = os.path.join(DST, f".{MODE}{n}.pkl")
        if twohost.enabled() and os.path.exists(part):
            os.remove(part)
        if not os.path.exists(part):
            env = {k: v for k, v in os.environ.items() if k not in PROTOCOL_VARS}
            env.update(dict(protocol))
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run([sys.executable, "-m", "sandbox.research._replay_the5ers",
                            "_child", "--workers", str(workers), "--out", part],
                           env=env, cwd=root, check=True)
        with open(part, "rb") as handle:
            rows.update(pickle.load(handle))
        if twohost.enabled():
            os.remove(part)
    if twohost.enabled():
        if twohost.IS_MAC:
            print("twohost: Mac side finished; the PC collects")
            return
        units = [_unit(p, j) for p in protocols for j in _jobs(cells.items(), p)]
        rows = {}
        for got in twohost.collect(units).values():
            rows.update(got.get("rows", {}))
    if MODE == "lev":
        with open(os.path.join(DST, "LEV.json"), "w", encoding="utf-8") as handle:
            json.dump({"count": len(rows), "of": len(cells), "src": SRC, "target_dd_pct": TARGET,
                       "end": os.environ["CFD_OOS_END"], "balance": ncf.SETUP["balance"],
                       "rows": rows}, handle, indent=1)
        print(f"wrote leverage for {len(rows)} of {len(cells)} -> {DST}")
        return
    index = json.load(open(os.path.join(SRC, "SURVIVORS.json"), encoding="utf-8"))
    out = []
    for s in index["survivors"]:
        row = rows.get(s["file"])
        if row is None:
            continue
        out.append({**s, "the5ers": row})
    with open(os.path.join(DST, "REPLAY.json"), "w", encoding="utf-8") as handle:
        json.dump({"count": len(out), "of": len(cells), "start": START,
                   "end": os.environ["CFD_OOS_END"], "balance": ncf.SETUP["balance"],
                   "cells": out}, handle, indent=1)
    print(f"wrote {len(out)} of {len(cells)} -> {DST}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=("run", "_child"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.command == "_child":
        child(args.workers, args.out)
    else:
        parent(args.workers)
