"""Does the modelled history score strategies the way real fills do?

    python -m sandbox.research.modelled_cost_check <sibling> [window]

Takes a SIBLING with full real Exness history -- usdjpy for the FX pairs, de40
for fr40/stoxx50, jp225 for aus200, ethusd for ethbtc -- pretends it has only
its last `KEEP_DAYS` of real minutes, rebuilds the rest with
`fill_models.modelled_minutes` from the OTHER peers, and scores the same cells
twice over the same in-sample window: once on the real fills, once on the
modelled ones. The cells are the sibling's sealed survivors (their real
parameters) plus a fixed sample from the window study's families.

Writes `results/modelled_cost_check_<sibling>_<window>.json`.
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone

import platform
platform._wmi = None

from sandbox.research import cfd_families as ef
from sandbox.research.fill_models import exness as fx
from sandbox.research.fill_models import modelled_minutes as mm

KEEP_DAYS = 92
SIBLINGS = {
    "usdjpy": "forex", "gbpusd": "forex", "audusd": "forex",
    "de40": "index", "uk100": "index", "jp225": "index", "ethusd": "crypto",
}
PEERS = {"forex": ("audusd", "eurjpy", "gbpjpy", "gbpusd", "usdjpy"),
         "index": ("de40", "uk100", "jp225", "hk50"),
         "crypto": ("ethusd", "btc")}
START = {"forex": "2020-01", "index": "2022-08", "crypto": "2021-01"}
SAMPLE_FAMILIES = ("donchian", "orb", "vwap", "volatility_breakout",
                   "regime_breakout", "xma_cross", "oh_asia_break",
                   "oh_hour_drift", "oh_night_extension")
PER_FAMILY = 4


def pretend(symbol):
    """Make `symbol` look like a short-history symbol for this process."""
    kind = SIBLINGS[symbol]
    mm.MODELLED[symbol] = (tuple(p for p in PEERS[kind] if p != symbol),
                           START[kind])
    mm.KIND[symbol] = kind
    original = fx._read_minutes.__wrapped__ if hasattr(
        fx._read_minutes, "__wrapped__") else fx._read_minutes

    def read(name):
        if name != symbol:
            return original(name)
        ef.MODELLED_COST = False
        try:
            stamps, opens, spreads = original(name)
        finally:
            ef.MODELLED_COST = True
        keep = stamps >= stamps[-1] - KEEP_DAYS * 86_400
        return fx._with_modelled_history(name, stamps[keep], opens[keep],
                                         spreads[keep])

    read.__wrapped__ = original
    fx._read_minutes = read
    tag = ef._model_tag
    ef._model_tag = lambda name: tag(name) + f":pretend{KEEP_DAYS}"


def cells(symbol, bar):
    rows = json.load(open(os.path.join(ef.RESULTS, "exness", "SURVIVORS.json"),
                          encoding="utf-8"))["survivors"]
    out = []
    if ef.window_of(ef.INSTRUMENTS[symbol]) == "rth":
        for row in rows:
            if row["symbol"] == symbol and row["timeframe"] == f"{bar}m" \
                    and row["family"] in ef.FAMILIES:
                sealed = json.load(open(os.path.join(ef.RESULTS, "exness",
                                                     row["file"]),
                                        encoding="utf-8"))["params"]
                out.append((row["family"], ef.rehydrate(sealed), "survivor"))
    grid = ef.axes(symbol, bar, set(SAMPLE_FAMILIES))
    rng = random.Random(7)
    for family in sorted(grid):
        pool = ef.candidates(grid[family])
        for params in rng.sample(pool, min(PER_FAMILY, len(pool))):
            out.append((family, params, "sample"))
    return out


def score(symbol, bar, jobs, lo, hi):
    ef.install_fills(symbol, bar, quiet=True)
    families = {f for f, _, _ in jobs}
    bars, ctx = ef.context(symbol, "validate", bar, families)
    out = []
    for family, params, kind in jobs:
        stat = ef.backtest(family, bars, ctx, params, lo=lo, hi=hi,
                           include_trades=True)
        log = stat.pop("trade_log")
        cost = [1e4 * (t["gross"] - t["points"]) / t["entry"]
                for t in log if t["entry"]]
        out.append({"family": family, "kind": kind,
                    "return_pct": stat["return_pct"], "trades": stat["trades"],
                    "pf": stat.get("pf"),
                    "cost_bp": statistics.fmean(cost) if cost else None})
    return out


def main():
    symbol = sys.argv[1]
    window = sys.argv[2] if len(sys.argv) > 2 else "rth"
    bar = 30
    ef.BAR_MINUTES = bar
    ef.resolve(symbol, allow_stale=True, window=window)
    ef.install_fills(symbol, bar, quiet=True)
    start = datetime.strptime(START[SIBLINGS[symbol]], "%Y-%m").replace(
        tzinfo=timezone.utc)
    first = datetime(start.year + (start.month > 1), 1, 1, tzinfo=timezone.utc)
    lo = max(ef.is_start(symbol), int(first.timestamp()))
    jobs = cells(symbol, bar)
    real = score(symbol, bar, jobs, lo, ef.IS_END)

    pretend(symbol)
    for table in (ef.TICK_SPREAD_BP, ef.ENTRY_PRICE, ef.EXIT_PRICE):
        table.pop(symbol, None)
    fx.release_minutes()
    ef.resolve(symbol, allow_stale=True, window=window)
    model = score(symbol, bar, jobs, lo, ef.IS_END)

    rows = [dict(r, model_return_pct=m["return_pct"], model_trades=m["trades"],
                 model_cost_bp=m["cost_bp"]) for r, m in zip(real, model)]
    path = os.path.join(ef.RESULTS,
                        f"modelled_cost_check_{symbol}_{window}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"symbol": symbol, "window": window, "keep_days": KEEP_DAYS,
                   "in_sample_from": lo, "rows": rows}, handle, indent=1)
    both = [r for r in rows if r["cost_bp"] and r["model_cost_bp"]]
    cost_ratio = statistics.median(r["model_cost_bp"] / r["cost_bp"] for r in both)
    diffs = [r["model_return_pct"] - r["return_pct"] for r in rows]
    sign = sum((r["model_return_pct"] > 0) == (r["return_pct"] > 0) for r in rows)
    print(f"{symbol} {window}: {len(rows)} cells | cost/trade model/real "
          f"{cost_ratio:.2f}x | return diff median {statistics.median(diffs):+.1f} "
          f"pts (abs {statistics.median(abs(d) for d in diffs):.1f}) | "
          f"same sign {sign}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
