"""Writes the trade-for-trade fixtures the Rust FundedNext book is checked against.

The Rust port lives in `live_trade/src/strategies/fundednext/oct-21-2026/`, the
book `fundednext_combined_strategies` sealed as 9e0e0b402357 (80 sleeves at
risk 0.03, each at its ladder leverage). Its `parity.rs` replays each fixture's
bars through the streaming engine and asserts the SAME TRADES
`cfd_families.backtest` produced here:

  * the bars are the cell's own candles at its own bar size, EVERY bar of the
    day (the engine filters its session itself, as it does on the live minute
    feed), built exactly as `fundednext_combined_strategies._log_group` builds
    them -- under `CFD_BROKER_HOURS=1`, so a vendor candle the broker did not
    quote is already gone;
  * fills are IDEALISED but GATED: `EXNESS_FILLS_ONLY=1` refuses a signal
    whose fill candle has no broker entry price, so the backtest is handed the
    real map's keys with the candle's own open as the price. `unfillable`
    lists the in-session candles outside it, for the Rust gate. The fixture
    proves the SIGNAL; the whole-book comparison proves the fill model;
  * the admission balance is `fn.ACCOUNT` ($6,000), what the pool logs used;
  * the bars reach a year before `from`, because the Rust engine streams and
    has to walk its own warm-up.

Every cell runs under the protocol it was searched with
(`fundednext_combined_strategies._protocol`), each protocol in its own child
interpreter -- those flags bind at import.

`canon` writes `sandbox/results/fundednext_canon_trades.json`: the three
windows replayed by `fundednext_combined_strategies.replay` at the sealed risk
from the pool logs (`GROUP_DIR`), every settled trade with its lots and P&L, for
`fundednext_book` to diff against.

    py -m sandbox.research._fundednext_parity              # every member
    py -m sandbox.research._fundednext_parity us500:aroon@30m-barlb
    py -m sandbox.research._fundednext_parity canon
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RESULTS = os.path.join(HERE, "..", "results")
SEAL = os.path.join(RESULTS, "fundednext_challenge_seal.json")
POOL = os.path.join(RESULTS, "filtered", "leveraged_fundednext")
FIXTURES = os.path.join(ROOT, "live_trade", "src", "strategies", "fundednext",
                        "oct-21-2026", "fixtures")
CANON_OUT = os.path.join(RESULTS, "fundednext_canon_trades.json")

FIX_WARM, FIX_FROM, FIX_TO = "2023-01-01", "2024-01-01", "2026-10-13"

PROTOCOL_VARS = ("FN_TT", "EXNESS_BAR_LOOKBACKS", "EXNESS_WINDOW",
                 "EXNESS_IS_FIRST_YEAR", "EXNESS_TT_STOP_WIDEN",
                 "CFD_BROKER_HOURS", "EXNESS_FILLS_ONLY")


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def seal():
    with open(SEAL, encoding="utf-8") as handle:
        return json.load(handle)


def members():
    return seal()["members"]


def cell_of(member):
    """The pool cell, completed the way `pool_cells` completes an ncf one."""
    with open(os.path.join(POOL, member["file"]), encoding="utf-8") as handle:
        cell = json.load(handle)
    if "bar_minutes" not in cell:
        cell["bar_minutes"] = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}[cell["timeframe"]]
        cell["ncf_window"] = cell["protocol"]["execution"]["window"]
    return cell


def key_of(member):
    return f"{member['symbol']}:{member['family']}"


def protocol(member, cell):
    """`fundednext_combined_strategies._protocol`, without importing it."""
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


def _child_env(env_items):
    env = {k: v for k, v in os.environ.items() if k not in PROTOCOL_VARS}
    env.update(dict(env_items))
    env.update(CFD_BROKER="fundednext", CFD_SPLIT="2024-2025",
               CFD_OOS_END="2026-10-13", PYTHONIOENCODING="utf-8",
               EXNESS_HOLD_SKIP="1")
    return env


def export(wanted, workers):
    chosen = [m for m in members() if not wanted or key_of(m) in wanted]
    groups = {}
    for member in chosen:
        groups.setdefault(protocol(member, cell_of(member)), []).append(member)
    os.makedirs(FIXTURES, exist_ok=True)
    tmp = tempfile.mkdtemp()

    def run(item):
        n, (env_items, group) = item
        out = os.path.join(tmp, f"out{n}.json")
        spec = json.dumps({"members": group, "out": out})
        done = subprocess.run([sys.executable, "-m", "sandbox.research._fundednext_parity",
                               "_worker", spec], cwd=ROOT, env=_child_env(env_items))
        if done.returncode or not os.path.exists(out):
            return [f"FAILED protocol {dict(env_items)}: {[key_of(m) for m in group]}"]
        with open(out, encoding="utf-8") as handle:
            return json.load(handle)

    with ThreadPoolExecutor(max(1, workers)) as pool:
        for lines in pool.map(run, enumerate(sorted(groups.items()))):
            for line in lines:
                print(f"  {line}", flush=True)


def _worker(spec):
    import platform
    platform._wmi = None
    from sandbox.research import fundednext_combined_strategies as fn
    ef = fn.ef
    TS, O = ef.TS, ef.O
    lines = []
    for member in spec["members"]:
        cell = cell_of(member)
        symbol, family = cell["symbol"], cell["family"]
        bar = cell["bar_minutes"]
        tf = member["family"].split("@", 1)[1]
        ef.BAR_MINUTES = bar
        ef.resolve(symbol, allow_stale=True)
        ef.prewarm(symbol, "validate", bar)
        if os.environ.get("FN_TT") == "1":
            bars, ctx = ef.context(symbol, "validate", bar)
        else:
            bars, ctx = ef.context(symbol, "validate", bar, {family})
        warm, lo, hi = stamp(FIX_WARM), stamp(FIX_FROM), stamp(FIX_TO)
        full = [b for b in ef.all_bars(symbol, "validate", bar) if warm <= b[TS] < hi]
        entries = ef.ENTRY_PRICE.get(symbol) or {}
        # The real map's KEYS, each at the candle's own open: the FILLS_ONLY
        # gate as the account sees it, the price as the bar says.
        gate = {b[TS]: b[O] for b in ef.all_bars(symbol, "validate", bar) if b[TS] in entries}
        result = ef.backtest(family, bars, ctx, ef.rehydrate(cell["params"]),
                             lo=lo, hi=hi, initial=fn.ACCOUNT, include_trades=True,
                             tick_spreads={}, entry_prices=gate, exit_prices={})
        cfg = ctx["cfg"]
        unfillable = [b[TS] for b in bars if warm <= b[TS] < hi and b[TS] not in gate]
        payload = {
            "sleeve": key_of(member), "symbol": symbol, "family": family,
            "bar": bar, "params": cell["params"], "k": member["k"],
            "protocol": dict(protocol(member, cell)),
            "session": list(cfg["session"]),
            "per_session": ctx["periods"]["session"],
            "vol_target": ctx["vol_target"],
            "shift_hours": cfg["shift_hours"],
            "window": ef.window_of(cfg),
            "rth": list(cfg.get("rth") or ()),
            "bar_lookbacks": bool(getattr(ef, "BAR_LOOKBACKS", False)),
            "table": cfg["table"],
            "broker_table": ef.broker_minute_table(symbol),
            "spec": {k: cfg.get(k) for k in (
                "broker", "table", "multiplier", "contract_size", "tick_size",
                "tick_value", "volume_min", "volume_step", "volume_max",
                "fx_to_usd", "point", "swap_mode", "swap_long", "swap_short",
                "swap_rollover3days", "spread_bp", "calendar", "asset_class")},
            "commission": ef.commission_rule(symbol),
            "warm_from": warm, "from": lo, "to": hi,
            "bars": [list(b[:6]) for b in full],
            "unfillable": unfillable,
            "trades": [{"entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
                        "side": t["side"], "entry": t["entry"],
                        "distance": t["distance"], "reason": t["reason"],
                        "quantity": t["quantity"], "pnl": t["pnl"]}
                       for t in result["trade_log"]],
            "signals": result["signals"], "fills": result["fills"],
        }
        name = f"parity_{symbol}_{family}_{tf}.json"
        with open(os.path.join(FIXTURES, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        lines.append(f"{key_of(member):46s} {len(full):7d} bars "
                     f"{len(payload['trades']):5d} trades  unfillable "
                     f"{len(unfillable):6d}  -> {name}")
    with open(spec["out"], "w", encoding="utf-8") as handle:
        json.dump(lines, handle)


def canon():
    """The three windows, every settled trade, from the pool logs."""
    env = dict(os.environ, CFD_BROKER="fundednext", CFD_SPLIT="2024-2025",
               CFD_OOS_END="2026-10-13", PYTHONIOENCODING="utf-8")
    for name in PROTOCOL_VARS:
        env.pop(name, None)
    subprocess.run([sys.executable, "-m", "sandbox.research._fundednext_parity",
                    "_canon"], cwd=ROOT, env=env, check=True)


def _canon():
    import platform
    platform._wmi = None
    from sandbox.research import fundednext_combined_strategies as fn
    sealed = seal()
    risk = sealed["risk"]
    state = fn.load_state(sealed["members"], fn._stamp(fn.LOG_START), fn.ef.OOS_END)
    out = {"seal": sealed["id"], "risk_scale": risk, "initial_balance": fn.ACCOUNT,
           "members": [key_of(m) for m in sealed["members"]],
           "scale": {key_of(m): m["k"] for m in sealed["members"]}, "windows": {}}
    for label, lo, hi in fn.WINDOWS:
        lo_ts = fn._stamp(lo)
        hi_ts = fn.ef.OOS_END if hi is None else fn._stamp(hi)
        book = fn.replay(state, risk, lo=lo_ts, hi=hi_ts)
        settled = sorted(book["settled"], key=lambda t: (t["entry_ts"], t["sleeve"]))
        out["windows"][label] = {
            "from": lo_ts, "to": hi_ts,
            "summary": {k: book[k] for k in ("final", "return_pct", "max_dd_pct",
                                             "mtm_dd_pct", "trades")},
            "by_sleeve": {k: {"pnl": v["pnl"], "trades": v["trades"]}
                          for k, v in book["by_sleeve"].items()},
            "trades": [{"s": t["sleeve"], "e": t["entry_ts"], "x": t["exit_ts"],
                        "side": t["side"], "entry": t["entry"],
                        "lots": t["lots"], "pnl": t["pnl"],
                        "points": t["points"]} for t in settled],
        }
        print(f"{label:14s} {book['trades']:6d} trades  {book['return_pct']:+9.2f}%  "
              f"closed dd {book['max_dd_pct']:.2f}%  MTM dd {book['mtm_dd_pct']:.2f}%")
    with open(CANON_OUT, "w", encoding="utf-8") as handle:
        json.dump(out, handle, separators=(",", ":"))
    print(f"wrote {CANON_OUT}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "_worker":
        _worker(json.loads(sys.argv[2]))
    elif command == "_canon":
        _canon()
    elif command == "canon":
        canon()
    else:
        cores = os.cpu_count() or 4
        default = max(1, cores // 2) if sys.platform == "win32" else cores
        # Each child holds a whole context; at 5m that is over a gigabyte, so
        # a 16 GB machine is held to fewer children than it has half-cores.
        export(set(sys.argv[1:]), int(os.environ.get("FN_PARITY_WORKERS", default)))
