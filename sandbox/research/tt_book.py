"""The TikTok (`cfd_tt_families`) sleeves of the canon book.

WHY A SEPARATE PROCESS. `cfd_tt_families` REPLACES `cfd_families`' family
registry and context builder when it is imported, so a TT cell cannot be run in
the process that runs the canon's own cells. Every call here therefore does its
work in a child interpreter and hands back plain data. The wide-stop ETHUSD
wave also needs its protocol environment (`EXNESS_IS_FIRST_YEAR`,
`EXNESS_TT_STOP_WIDEN`) set BEFORE that import, which is the second reason: each
protocol gets its own child with its own environment, read off the sealed file's
`protocol_change`.

WHAT A LOG IS. Exactly what `exness_combined_strategies.sleeve_trades` returns
for a native cell: `cfd_families.backtest` over `[lo, hi)`, the admission
account starting at `lo` with `INITIAL_BALANCE`, at the cell's OWN bar size and
session, filled by the live model `install_fills` builds for that bar (or the
idealised bar fill when `live` is false). Each trade carries `symbol` and
`sleeve`, and the sleeve key is `symbol:family@tf`.

    py -m sandbox.research.tt_book parity                 # Rust fixtures
    py -m sandbox.research.tt_book logs 2025-01-01 2026-08-21
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SURVIVORS = os.path.join(HERE, "..", "results", "exness")
FIXTURES = os.path.join(ROOT, "live_trade", "src", "strategies", "idk", "fixtures")

#: Every TikTok sleeve the canon can seat: key -> sealed survivor file.
#: The key is `symbol:family@<bar>m`; the bar is part of the identity because
#: the same family is sealed at several bar sizes.
SLEEVES = {
    "ustec:lux_body_momentum@5m": "ustec_lux_body_momentum_5m.json",
    "usdjpy:qp_ma_cross@30m": "usdjpy_qp_ma_cross_30m.json",
    "eurjpy:qp_ma_cross@60m": "eurjpy_qp_ma_cross_60m.json",
    "gbpjpy:luxalgo_manipulation@120m": "gbpjpy_luxalgo_manipulation_120m.json",
    "gbpjpy:lux_supply_demand@30m": "gbpjpy_lux_supply_demand_30m.json",
    "de40:lux_sweep_reclaim@60m": "de40_lux_sweep_reclaim_60m.json",
    "usdjpy:lux_htf_manipulation@60m": "usdjpy_lux_htf_manipulation_60m.json",
    "eurjpy:lux_ny_vwap_pullback@15m": "eurjpy_lux_ny_vwap_pullback_15m.json",
    "ethusd:lux_body_momentum@15m": "ethusd_lux_body_momentum_15m_widestop_isethusd2021.json",
}


def _pool():
    """Every TikTok survivor the canon could seat (null PASS or PASS*), keyed
    like `SLEEVES`, read off `SURVIVORS.json`."""
    try:
        with open(os.path.join(SURVIVORS, "SURVIVORS.json"), encoding="utf-8") as handle:
            rows = json.load(handle)["survivors"]
    except OSError:
        return {}
    out = {}
    for r in rows:
        if (str(r.get("study_wave", "")).startswith("tiktok")
                and r.get("null_verdict") in ("PASS", "PASS*")):
            out.setdefault(f"{r['symbol']}:{r['family']}@{r['timeframe']}", r["file"])
    return out


#: The whole TikTok pool, canon members included.
POOL = {**_pool(), **SLEEVES}

#: The Rust fixtures' comparison window and warm-up, as `_export_parity`.
FIX_FROM, FIX_TO, FIX_WARM = "2025-01-01", "2026-08-21", "2024-01-01"


def is_tt(key):
    return "@" in key


def split(key):
    """`symbol:family@30m` -> (symbol, family, 30)."""
    symbol, rest = key.split(":", 1)
    family, tf = rest.split("@", 1)
    return symbol, family, int(tf.rstrip("m"))


def sealed(key):
    with open(os.path.join(SURVIVORS, POOL[key]), encoding="utf-8") as handle:
        return json.load(handle)


def protocol(key):
    """The environment this cell was sealed under, as a sorted tuple."""
    return tuple(sorted((sealed(key).get("protocol_change") or {}).items()))


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def _run(mode, keys, **job):
    """Run `mode` for `keys` in children -- one protocol per child, and a
    protocol's keys split over `TT_PARALLEL` children (default 1) that run at
    once, grouped by (symbol, bar) so a context is built once per child."""
    parallel = max(1, int(os.environ.get("TT_PARALLEL", "1")))
    groups = {}
    for key in keys:
        groups.setdefault(protocol(key), []).append(key)
    jobs = []
    for env_items, members in groups.items():
        members = sorted(members, key=lambda k: (split(k)[0], split(k)[2]))
        size = -(-len(members) // min(parallel, len(members)))
        for i in range(0, len(members), size):
            jobs.append((env_items, members[i:i + size]))
    merged = {"cells": {}, "bars30": {}, "ctx": {}}
    with tempfile.TemporaryDirectory() as tmp:
        running = []
        for n, (env_items, members) in enumerate(jobs):
            env = dict(os.environ)
            for name in ("EXNESS_IS_FIRST_YEAR", "EXNESS_TT_STOP_WIDEN"):
                env.pop(name, None)
            env.update(dict(env_items))
            env["PYTHONIOENCODING"] = "utf-8"
            out = os.path.join(tmp, f"out{n}.pkl")
            spec = json.dumps({"mode": mode, "keys": members, "out": out, **job})
            running.append((out, subprocess.Popen(
                [sys.executable, "-m", "sandbox.research.tt_book", "_worker", spec],
                cwd=ROOT, env=env)))
            while sum(proc.poll() is None for _o, proc in running) >= parallel:
                next(proc for _o, proc in running if proc.poll() is None).wait()
        for out, proc in running:
            if proc.wait() != 0:
                raise RuntimeError(f"tt_book child failed ({proc.returncode})")
            with open(out, "rb") as handle:
                got = pickle.load(handle)
            for part in merged:
                merged[part].update(got.get(part, {}))
    return merged


_MEMO = {}


def logs(keys, lo, hi, live=True):
    """`{"cells": {key: {"log", "params", ...}}, "bars30": {symbol: bars},
    "ctx": {symbol: {"cfg", "symbol"}}}` for `keys` over `[lo, hi)`."""
    memo = (tuple(sorted(keys)), lo, hi, bool(live))
    if memo not in _MEMO:
        _MEMO[memo] = _run("logs", sorted(keys), lo=lo, hi=hi, live=bool(live))
    return _MEMO[memo]


def export_parity(keys=None):
    keys = list(SLEEVES) if not keys else keys
    got = _run("parity", keys)
    for key in keys:
        print(f"{key:36s} {got['cells'][key]}")


# --------------------------------------------------------------------------- #
# child
# --------------------------------------------------------------------------- #

def _worker(spec):
    import platform
    platform._wmi = None
    from sandbox.research import cfd_tt_families as tt   # installs the registry
    cf = tt.cf
    TS = cf.TS
    out = {"cells": {}, "bars30": {}, "ctx": {}}
    for key in spec["keys"]:
        symbol, family, bar = split(key)
        cell = sealed(key)
        params = _tuple(cell["params"])
        cf.resolve(symbol, allow_stale=True)
        cf.BAR_MINUTES = bar
        bars, ctx = cf.context(symbol, "validate", bar)
        if spec["mode"] == "logs":
            if spec["live"]:
                cf.install_fills(symbol, bar, quiet=True)
                fills = {}
            else:
                fills = {"tick_spreads": {}, "entry_prices": {}, "exit_prices": {}}
            result = cf.backtest(family, bars, ctx, params, lo=spec["lo"],
                                 hi=spec["hi"], initial=cf.INITIAL_BALANCE,
                                 include_trades=True, **fills)
            log = []
            for trade in result["trade_log"]:
                trade = dict(trade)
                trade["symbol"], trade["sleeve"] = symbol, key
                log.append(trade)
            out["cells"][key] = {
                "key": key, "symbol": symbol, "family": f"{family}@{bar}m",
                "tt_family": family, "bar": bar, "params": params,
                "log": log, "return_pct": result["return_pct"],
                "max_dd_pct": result["max_dd_pct"],
                "trades": result["trades"]}
            if symbol not in out["bars30"]:
                cf.BAR_MINUTES = 30
                out["bars30"][symbol] = [
                    b for b in cf.all_bars(symbol, "validate", 30)
                    if cf.in_session(symbol, b[TS])]
                out["ctx"][symbol] = {"cfg": ctx["cfg"], "symbol": symbol}
        else:
            warm, lo, hi = stamp(FIX_WARM), stamp(FIX_FROM), stamp(FIX_TO)
            # IDEALISED FILLS, as every other fixture: the fill model is proven
            # by the whole-book comparison, the fixture proves the SIGNAL.
            result = cf.backtest(family, bars, ctx, params, lo=lo, hi=hi,
                                 initial=cf.INITIAL_BALANCE, include_trades=True,
                                 tick_spreads={}, entry_prices={}, exit_prices={})
            kept = [b for b in bars if warm <= b[TS] < hi]
            payload = {
                "sleeve": key, "symbol": symbol, "family": family, "bar": bar,
                "params": cell["params"],
                "session": list(ctx["cfg"]["session"]),
                "per_session": ctx["periods"]["session"],
                "vol_target": ctx["vol_target"],
                "entry_days": None,
                "shift_hours": ctx["cfg"]["shift_hours"],
                "warm_from": warm, "from": lo, "to": hi,
                "bars": [list(b[:6]) for b in kept],
                "trades": [{"entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
                            "side": t["side"], "entry": t["entry"],
                            "distance": t["distance"], "reason": t["reason"]}
                           for t in result["trade_log"]],
                "signals": result["signals"], "fills": result["fills"],
            }
            name = f"parity_{symbol}_{family}_{bar}m.json"
            with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            out["cells"][key] = (f"{len(kept):7d} bars {len(payload['trades']):5d} "
                                 f"trades  per_session {payload['per_session']}  "
                                 f"vol_target {payload['vol_target']!r} -> {name}")
    with open(spec["out"], "wb") as handle:
        pickle.dump(out, handle, protocol=5)


def _tuple(v):
    if isinstance(v, list):
        return tuple(_tuple(x) for x in v)
    if isinstance(v, dict):
        return {k: _tuple(x) for k, x in v.items()}
    return v


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "_worker":
        _worker(json.loads(sys.argv[2]))
    elif command == "parity":
        export_parity(sys.argv[2:])
    elif command == "logs":
        got = logs(list(SLEEVES), stamp(sys.argv[2]), stamp(sys.argv[3]))
        for key, cell in sorted(got["cells"].items()):
            print(f"{key:36s} {cell['trades']:5d} trades  {cell['return_pct']:+8.2f}%")
