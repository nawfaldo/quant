"""Trade logs for every TikTok PASS / PASS* survivor, ready for canon's replay.

Runs under `cfd_tt_families`' registry (the TT families are not in
`cfd_families`), at each cell's own bar size, with the live fill model
`install_fills` builds for that bar -- the same pricing the sweep sealed.
Each cell is also re-run on its sealed holdout as a parity check.

The wide-stop ETHUSD waves need their protocol env before import, so they run
as a separate invocation:

    py -m sandbox.research._tt_canon_logs normal
    EXNESS_IS_FIRST_YEAR=ethusd:2021 EXNESS_TT_STOP_WIDEN=1,2,4 \
        py -m sandbox.research._tt_canon_logs widestop
"""
import platform

platform._wmi = None

import json
import multiprocessing
import os
import pickle
import sys
from datetime import datetime, timezone

from sandbox.research import cfd_tt_families as tt

cf = tt.cf
TS = cf.TS
HERE = os.path.dirname(__file__)
SURV = os.path.join(HERE, "..", "results", "exness")
CACHE = os.path.join(HERE, "..", ".cache")
LO = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp())
HI = int(datetime(2026, 8, 21, tzinfo=timezone.utc).timestamp())   # CANON_DATA_END


def _tuple(v):
    if isinstance(v, list):
        return tuple(_tuple(x) for x in v)
    if isinstance(v, dict):
        return {k: _tuple(x) for k, x in v.items()}
    return v


def cells(mode):
    rows = json.load(open(os.path.join(SURV, "SURVIVORS.json"),
                          encoding="utf-8"))["survivors"]
    out = []
    for r in rows:
        wave = str(r.get("study_wave", ""))
        if not wave.startswith("tiktok") or r.get("null_verdict") not in ("PASS", "PASS*"):
            continue
        if ("widestop" in wave) != (mode == "widestop"):
            continue
        out.append(r)
    return out


def run_group(job):
    symbol, bar, files = job
    cf.resolve(symbol, allow_stale=True)
    cf.BAR_MINUTES = bar
    bars, ctx = cf.context(symbol, "validate", bar)
    cf.install_fills(symbol, bar, quiet=True)
    out = []
    for name in files:
        cell = json.load(open(os.path.join(SURV, name), encoding="utf-8"))
        params = _tuple(cell["params"])
        fam = cell["family"]
        key = f"{symbol}:{fam}@{cell['timeframe']}"
        chk = cf.backtest(fam, bars, ctx, params, lo=cf.IS_END, hi=cf.OOS_END)
        res = cf.backtest(fam, bars, ctx, params, lo=LO, hi=HI, include_trades=True)
        log = []
        for t in res["trade_log"]:
            t = dict(t)
            t["symbol"], t["sleeve"] = symbol, key
            log.append(t)
        sealed = cell["out_of_sample"]
        out.append({"key": key, "symbol": symbol, "family": f"{fam}@{cell['timeframe']}",
                     "tt_family": fam, "timeframe": cell["timeframe"],
                     "params": params, "wave": cell["study_wave"],
                     "null": cell["null_control"]["verdict"],
                     "sealed_oos": [sealed["return_pct"], sealed["trades"]],
                     "rerun_oos": [round(chk["return_pct"], 2), chk["trades"]],
                     "log": log})
    cfg = {"cfg": ctx["cfg"], "symbol": symbol}
    cf.BAR_MINUTES = 30
    b30 = [b for b in cf.all_bars(symbol, "validate", 30)
           if cf.in_session(symbol, b[TS])]
    return out, symbol, cfg, b30


if __name__ == "__main__":
    mode = sys.argv[1]
    groups = {}
    for r in cells(mode):
        groups.setdefault((r["symbol"], int(r["timeframe"][:-1])), []).append(r["file"])
    jobs = [(s, b, f) for (s, b), f in sorted(groups.items(), key=lambda kv: kv[0][1])]
    print(f"{mode}: {sum(len(f) for *_, f in jobs)} cells in {len(jobs)} symbol/bar groups",
          flush=True)
    result = {"cells": {}, "ctx": {}, "bars": {}}
    with multiprocessing.Pool(int(os.environ.get("TT_WORKERS", "8"))) as pool:
        for out, symbol, cfg, b30 in pool.imap_unordered(run_group, jobs):
            for c in out:
                result["cells"][c["key"]] = c
                print(f"  {c['key']:<44} sealed {c['sealed_oos'][0]:+7.2f}% "
                      f"{c['sealed_oos'][1]:>4}  rerun {c['rerun_oos'][0]:+7.2f}% "
                      f"{c['rerun_oos'][1]:>4}  2022-26 trades {len(c['log'])}",
                      flush=True)
            result["ctx"][symbol] = cfg
            result["bars"][symbol] = b30
    path = os.path.join(CACHE, f"tt_canon_logs_{mode}.pkl")
    with open(path, "wb") as h:
        pickle.dump(result, h, protocol=5)
    print(f"wrote {path}")
