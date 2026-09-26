"""Export a Rust parity fixture for one sealed `cfd_families` cell.

The Rust port in `live_trade/src/strategies/idk/exness_combined_8-23-2026.rs`
reimplements `cfd_families.backtest` as a streaming engine. Structure tests
cannot show that the two agree bar for bar; this can. It writes the SAME 30-minute
bars the Python indexes, plus the trades that cell produced over them, so the
Rust side can replay the bars and compare entry timestamps, sides and stop
distances against the recorded log.

The bars are the session-filtered ones `context` builds, so the Rust engine is
fed exactly the array the Python indexed rather than a 1-minute stream it has to
re-aggregate. Aggregation is tested separately and is not what this is asking
about.
"""
import json
import os
import sys
from datetime import datetime, timezone

from sandbox.research import cfd_families as ef

# The sealed cells, transcribed from `exness_combined_strategies.json`.
CELLS = {
    "usdjpy:volume_thrust": {
        "symbol": "usdjpy",
        "family": "volume_thrust",
        "params": {
            "direction": "breakout", "exit_mode": "trail_1.5",
            "last_entry_minute": 720, "stop_day": 0.4, "threshold_atr": 0.5,
            "trend": "none", "vol_mode": "none", "volume_mult": 2.0,
            "volume_period": 540,
        },
    },
    "audusd:zscore": {
        "symbol": "audusd",
        "family": "zscore",
        "params": {
            "direction": "fade", "exit_mode": "trail_1.5",
            "last_entry_minute": 780, "period": 135, "stop_day": 0.4,
            "threshold_z": 1.5, "trend": "ema_20d", "vol_mode": "none",
        },
    },
    "jp225:swing_donchian": {
        "symbol": "jp225",
        "family": "swing_donchian",
        "params": {
            "channel": 300, "direction": "breakout", "exit_mode": "rr_2",
            "stop_day": 0.4, "trend": "ema_50d", "vol_mode": "none",
        },
    },
    "ethusd:confluence": {
        "symbol": "ethusd",
        "family": "confluence",
        "params": {
            "exit_mode": "trail_1.5", "last_entry_minute": 900, "mode": "fade",
            "pool": "exhaustion", "stop_day": 0.2, "trend": "ema_20d",
            "vol_mode": "calm", "votes": 3,
        },
    },
    "ethusd:volatility_breakout": {
        "symbol": "ethusd",
        "family": "volatility_breakout",
        "params": {
            "direction": "breakout", "exit_mode": "trail_1.5", "fraction": 0.5,
            "last_entry_minute": 840, "stop_day": 0.2, "trend": "ema_50d",
            "vol_mode": "calm",
        },
    },
    "gbpjpy:trap": {
        "symbol": "gbpjpy",
        "family": "trap",
        "params": {
            "exit_mode": "trail_1.5", "last_entry_minute": 690,
            "level": "donchian", "stop_day": 0.7, "trend": "none",
            "vol_mode": "calm", "window": 8,
        },
    },
    "gbpusd:obv_break": {
        "symbol": "gbpusd",
        "family": "obv_break",
        "params": {
            "channel": 280, "direction": "breakout", "exit_mode": "trail_1.5",
            "last_entry_minute": 780, "stop_day": 0.2, "trend": "none",
            "vol_mode": "calm",
        },
    },
    "msft:xma_cross": {
        "symbol": "msft",
        "family": "xma_cross",
        "params": {
            "direction": "breakout", "exit_mode": "rr_1", "fast": 20,
            "kind": "tema", "last_entry_minute": 720, "slow": 50,
            "stop_day": 0.4, "trend": "ema_20d", "vol_mode": "none",
        },
    },
    "nq:volatility_breakout": {
        "symbol": "nq",
        "family": "volatility_breakout",
        "params": {
            "direction": "breakout", "exit_mode": "trail_1.5", "fraction": 0.3,
            "last_entry_minute": 780, "stop_day": 0.2, "trend": "ema_20d",
            "vol_mode": "none",
        },
    },
    "tsla:floor_pivot": {
        "symbol": "tsla",
        "family": "floor_pivot",
        "params": {
            "buffer_atr": 0.0,
            "direction": "fade",
            "exit_mode": "trail_1.5",
            "last_entry_minute": 690,
            "level": "first",
            "stop_day": 0.2,
            "trend": "ema_20d",
            "vol_mode": "none",
        },
    },
    "jp225:swing_break": {
        "symbol": "jp225",
        "family": "swing_break",
        "params": {
            "direction": "breakout",
            "exit_mode": "trail_1.5",
            "last_entry_minute": 420,
            "stop_day": 0.2,
            "trend": "ema_20d",
            "vol_mode": "calm",
            "wing": 5,
        },
    },
    "ukoil:xma_cross": {
        "symbol": "ukoil",
        "family": "xma_cross",
        "params": {
            "direction": "breakout",
            "exit_mode": "trail_1.5",
            "fast": 12,
            "kind": "tema",
            "last_entry_minute": 750,
            "slow": 60,
            "stop_day": 0.2,
            "trend": "ema_50d",
            "vol_mode": "none",
        },
    },
}

#: The replay window. Everything before it is warm-up the Rust engine has to
#: walk anyway, because the indicators are streaming and cannot be seeded.
FROM = "2025-01-01"
TO = "2026-08-21"

#: How far back the fixture carries bars.
#:
#: The Rust port is a STREAMING engine and cannot be handed a warmed context, so
#: it walks its own history. 100 sessions is the deepest lookback any cell reads
#: -- the `calm` gate's long volatility window -- and this is comfortably past
#: it. It cannot be trimmed to exactly 100: `es.ema` is seeded at the first close
#: of the WHOLE series and converges from there, so a short run-up leaves the
#: 50-session trend gate sitting at a different level from Python's and cells
#: start disagreeing for a reason that is not a bug.
WARM_FROM = "2024-01-01"


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def export(key, out_dir):
    cell = CELLS[key]
    symbol = cell["symbol"]
    ef.resolve(symbol, allow_stale=True)
    bars, ctx = ef.context(symbol, "validate", 30)

    warm, lo, hi = stamp(WARM_FROM), stamp(FROM), stamp(TO)
    window = [b for b in bars if warm <= b[ef.TS] < hi]

    # `ef.INITIAL_BALANCE`, which is the balance `sleeve_trades` uses and so the
    # one that decides WHICH TRADES EXIST. `quantity` returns 0 for an order
    # under the broker's `volume_min` and the trade never enters the log at all
    # -- 64 of jp225:swing_donchian's 117 signals disappear that way.
    #
    # An earlier version of this exporter ran at 1e9 to take sizing out of the
    # comparison. That was the wrong call: the admission filter is PART of the
    # strategy, not part of the accounting, and running the fixture without it
    # meant the Rust port could drop it and still pass.
    result = ef.backtest(cell["family"], bars, ctx, cell["params"],
                         lo=lo, hi=hi, initial=ef.INITIAL_BALANCE,
                         include_trades=True)

    payload = {
        "sleeve": key,
        "symbol": symbol,
        "family": cell["family"],
        "params": cell["params"],
        "session": list(ctx["cfg"]["session"]),
        "per_session": ctx["periods"]["session"],
        "shift_hours": ctx["cfg"]["shift_hours"],
        "warm_from": warm,
        "from": lo,
        "to": hi,
        # `[ts, open, high, low, close, volume]`, session-filtered, on the
        # SHIFTED clock already -- `all_bars` applies the shift when it loads.
        #
        # FULL PRECISION, and rounding them is not an option. `digits` is the
        # broker's quote precision and these bars are `SAMPLE BY` aggregates of
        # a minute table, which carry more: rounding TSLA to 2 places moved a
        # fill from 408.747 to 408.75 and every stop distance with it. A parity
        # fixture that has been rounded is no longer a parity fixture.
        "bars": [[int(b[ef.TS]), b[ef.O], b[ef.H], b[ef.L], b[ef.C], b[ef.V]]
                 for b in window],
        "trades": [
            {
                "entry_ts": int(t["entry_ts"]),
                "exit_ts": int(t["exit_ts"]),
                "side": int(t["side"]),
                "entry": t["entry"],
                "distance": t["distance"],
                "reason": t["reason"],
            }
            for t in result["trade_log"]
        ],
        "signals": result["signals"],
        "fills": result["fills"],
    }
    name = key.replace(":", "_")
    path = os.path.join(out_dir, f"parity_{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    size = os.path.getsize(path) / 1e6
    print(f"{key:<24} {len(payload['bars']):>6} bars  "
          f"{len(payload['trades']):>4} trades  {size:.2f} MB  -> {path}",
          flush=True)


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out_dir, exist_ok=True)
    for key in CELLS:
        export(key, out_dir)
