"""Writes the trade-for-trade fixtures the Rust port is checked against.

WHY THIS EXISTS. `exness_combined_07-09-2026/parity.rs` replays a frozen bar list
through the streaming Rust engine and asserts it produces the SAME TRADES as
`exness_families.backtest` did over the same bars. That is the only property
that matters about the port: a sealed cell whose reimplementation fires on
different bars is a different strategy wearing a validated name.

The fixture carries the SESSION-FILTERED 30-MINUTE BARS `context` builds, not
one-minute rows, because bar aggregation is a separate question and mixing the
two would make a failure ambiguous.

THE BALANCE IS `INITIAL_BALANCE * SHOWN_EQUITY`, AND IT MUST BE, however much a
round one would simplify the comparison. `ef.quantity` returns 0 for an order
under the broker's `volume_min` and the trade then never enters the log at all --
so the trade list is a function of the balance it was taken on. It is also what
`sleeve_trades` passes, which is why the multiplier belongs here and not only in
the replay: at a flat $1,000 `jp225:swing_donchian` produces 52 trades and at its
own $2,500 it produces 81.

The Rust side cannot be given a different one. `FamilyEngine` decides which
trades EXIST on a SHADOW account seeded at `ADMISSION_BALANCE * shown_equity`,
which is this same figure, and then re-sizes the survivors against whatever the
book's shared balance happens to be. So the two agree on membership only when
these match, and `parity.rs` supplies its enormous balance to the second stage
alone -- which is what stops the BOOK's sizing confounding a test about the
SIGNAL.

THE BARS REACH A YEAR FURTHER BACK THAN THE WINDOW. The Rust engine is
streaming and cannot be handed a seeded indicator: a hundred sessions of `calm`
history have to be walked. `from` marks where the comparison starts.

    py -m sandbox.research._export_parity                 # every cell
    py -m sandbox.research._export_parity ethusd:idio_break
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from sandbox.research import exness_families as ef
from sandbox.research import exness_combined_strategies as cb

TS, O, H, L, C, V = range(6)

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "live_trade", "src",
                   "strategies", "idk", "fixtures")

#: The comparison window and the warm-up that precedes it.
FROM = "2025-01-01"
TO = "2026-08-21"
WARM = "2024-01-01"


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def export(key, rows):
    symbol, family = key.split(":")
    row = rows[key]
    ef.resolve(symbol, allow_stale=True)
    # Only this family's blocks: the full set is 116 arrays on top of the
    # original 56 and building all of it once crashed a machine.
    bars, ctx = ef.context(symbol, "full", only=(family,))
    shift = ctx["cfg"]["shift_hours"] * 3_600

    warm, lo, hi = stamp(WARM), stamp(FROM), stamp(TO)
    kept = [b for b in bars if warm <= b[TS] < hi]

    # THE BENCHMARK RIDES ALONG AS A SEVENTH COLUMN, for the one cell that reads
    # a second market. `ind.align` has already carried BTC's close onto ETHUSD's
    # timestamps inside `context`; re-deriving it on the Rust side from raw bars
    # is exactly what the fixture is meant to check, so it is exported rather
    # than recomputed.
    relative = ctx.get("relative")
    benchmark = relative["benchmark"] if relative else None
    index_of = {b[TS]: i for i, b in enumerate(bars)}

    def encode(bar):
        out = [bar[TS], bar[O], bar[H], bar[L], bar[C], bar[V]]
        if benchmark is not None:
            value = benchmark[index_of[bar[TS]]]
            out.append(None if value is None or value != value else value)
        return out

    # THE ADMISSION BALANCE IS `INITIAL_BALANCE * SHOWN_EQUITY`, which is what
    # `sleeve_trades` hands `ef.backtest` for this sleeve. A cell shown 2.5x is
    # admitted against $2,500, and exporting it at a flat $1,000 would freeze a
    # trade list the book never runs -- on `jp225:swing_donchian` that is 52
    # trades against 81.
    shown = cb.SHOWN_EQUITY.get(key, 1.0)
    result = ef.backtest(family, bars, ctx, row["params"], lo=lo, hi=hi,
                         initial=ef.INITIAL_BALANCE * shown,
                         include_trades=True)
    trades = [{"entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
               "side": t["side"], "entry": t["entry"],
               "distance": t["distance"], "reason": t["reason"]}
              for t in result["trade_log"]]

    payload = {
        "sleeve": key, "symbol": symbol, "family": family,
        "params": row["params"],
        "session": list(ctx["cfg"]["session"]),
        "per_session": ctx["periods"]["session"],
        "shift_hours": ctx["cfg"]["shift_hours"],
        "warm_from": warm, "from": lo, "to": hi,
        "bars": [encode(b) for b in kept],
        "trades": trades,
        "signals": result["signals"], "fills": result["fills"],
    }
    name = f"parity_{symbol}_{family}.json"
    path = os.path.abspath(os.path.join(OUT, name))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    print(f"{key:32s} {len(kept):7d} bars  {len(trades):5d} trades  -> {name}")
    _ = shift


def main():
    wanted = sys.argv[1:]
    # EVERY MEMBER IS A CELL SINCE 2026-09-04, so the whole book is exported.
    # The exclusion this used to carry was for the two true IMPORTS -- `nq:ofi`
    # and `nq:drift_vwap`, which were never `exness_families` cells and had no
    # sealed row to reproduce -- and both left with the NQ symbol. The filter is
    # kept so a future import does not silently produce an empty fixture.
    keys = [k for k in cb.BOOK if k not in ("nq:ofi", "nq:drift_vwap")]
    if wanted:
        keys = [k for k in keys if k in wanted]
    rows = cb.candidate_rows_exact(keys)
    for key in keys:
        if key not in rows:
            print(f"{key}: no sealed row found, skipped")
            continue
        export(key, rows)


if __name__ == "__main__":
    main()
