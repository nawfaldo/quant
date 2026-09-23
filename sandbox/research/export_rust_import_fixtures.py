"""Export Rust parity fixtures for the book's two IMPORTED sleeves.

`export_rust_parity_fixtures` covers the twelve `exness_families` cells. It
cannot cover `nq:ofi` or `nq:drift_vwap`, because neither is a family cell:
they are finished strategies `combined_book` imports whole, they run on
ONE-MINUTE bars rather than the study's 30-minute candles, and `nq:ofi` reads
the level-two feature table on top of OHLCV. So they were the only two members
of the book whose Rust ports were held to their Python only by hand-matched
constants, which is exactly the check that misses a reordered comparison or an
off-by-one warm-up.

WHAT EACH FIXTURE CARRIES. The one-minute bars the Python indexes, plus every
trade it produced over them. For `nq:ofi` each bar also carries the eight
feature columns the Rust strategy actually reads --
`bid_add_volume`, `bid_cancel_volume`, `ask_add_volume`, `ask_cancel_volume`,
`spread`, `top5_imbalance`, `trade_delta` and `book_valid` -- and nothing else,
because a fixture that carries fields the engine never touches is a fixture
that fails for reasons unrelated to the strategy.

A MINUTE WITH NO FEATURE ROW IS EXPORTED AS ONE ANYWAY, filled with Python's
`NO_FEATURES`, which is Rust's `OrderFlowFeatures::default()`. That case is not
an edge: `_statistics` folds such a minute into the EWMA as a real zero, and a
fixture that silently dropped it would let the port skip it and still pass.

WHY THE WINDOW IS SHORT. These are minute bars, so a full 20-month window is a
hundred megabytes of fixture per sleeve. Neither import carries a deep
multi-session gate -- `nq:ofi`'s per-slot z-score needs `NORM_SESSIONS` = 20
sessions and the Drift VWAP trend state needs a handful of 15-minute bars -- so
a warm-up of one quarter is many times the deepest lookback either reads, and
the compared window is the quarter after it.

WHY BOTH SIDES RUN AT AN ENORMOUS BALANCE. Neither import's Python path refuses
an order for size: `nq_orders` and `drift_vwap_order_rows` emit a symbolic
`units_per_dollar` and the shared replay decides the lots later. Sizing is a
separate question, tested separately in `tests.rs`; what this asks is whether
the two engines fire on the same bars.

    py -m sandbox.research.export_rust_import_fixtures live_trade/src/strategies/idk/fixtures
"""
import json
import os
import sys
from datetime import datetime, timezone

from sandbox import data, execution, get_strategy
from sandbox.data import C, D, DE, H, L, O, TS
from sandbox.research import combined_book as cb
from sandbox.research import drift_vwap_pullback as dv
from sandbox.strategies.ofi_momentum import NO_FEATURES

#: The compared window, and the warm-up carried in front of it. Both imports
#: are RTH-only minute strategies; a quarter of warm-up is far past the twenty
#: sessions `nq:ofi` normalises over.
WARM_FROM = "2025-10-01"
FROM = "2026-01-01"
TO = "2026-04-01"

#: The eight feature columns `nq_ofi.rs` reads, in the order the fixture packs
#: them. Adding a column here without adding it to the Rust loader is a fixture
#: the port silently ignores, so the loader asserts on the count.
FEATURE_COLUMNS = (
    "bid_add_volume",
    "bid_cancel_volume",
    "ask_add_volume",
    "ask_cancel_volume",
    "spread",
    "top5_imbalance",
    "trade_delta",
    "book_valid",
)


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def _window(bars, warm, hi):
    return [bar for bar in bars if warm <= bar[TS] < hi]


def export_ofi(out_dir):
    """`nq:ofi`: level-two minute bars, their features, and its trades."""
    strategy = get_strategy(cb.NQ_SLEEVES["ofi"])
    # Repriced on THIS book's broker, exactly as `combined_book.nq_orders`
    # does it: 0.0 spread, 0.0 commission, 0.2 points of slippage. Using the
    # registry's own `Execution` would compare against a cost model the book
    # never charges.
    ex = cb.nq_execution(strategy.execution)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    params = strategy.all_params(None)

    warm, lo, hi = stamp(WARM_FROM), stamp(FROM), stamp(TO)
    window = _window(bars, warm, hi)

    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    fills = [
        fill
        for fill in execution.resolve(bars, signals, ex)
        if fill.stop > 0 and fill.price > 0 and lo <= fill.entry_ts < hi
    ]

    features = context["features"]
    packed = []
    for bar in window:
        row = features.get(bar[TS], NO_FEATURES)
        packed.append([
            row["bid_add_volume"],
            row["bid_cancel_volume"],
            row["ask_add_volume"],
            row["ask_cancel_volume"],
            row["spread"],
            row["top5_imbalance"],
            row["trade_delta"],
            1 if row["book_valid"] else 0,
        ])

    payload = {
        "sleeve": "nq:ofi",
        "symbol": "nq",
        "source": strategy.bars,
        "warm_from": warm,
        "from": lo,
        "to": hi,
        "entry_cost": ex.entry_cost,
        "feature_columns": list(FEATURE_COLUMNS),
        # LEVEL-TWO BARS CARRY NO VOLUME COLUMN. `data.TS, O, H, L, C, D, DE`
        # is the whole tuple: `D` is the minute's traded delta and `DE` its
        # depth-event count. `nq_ofi.rs` reads neither -- it takes its flow from
        # `order_flow` -- so they are exported for provenance and the volume
        # slot is a literal zero rather than a guess.
        "bars": [
            [int(b[TS]), b[O], b[H], b[L], b[C], 0.0, b[D], b[DE]] for b in window
        ],
        "features": packed,
        "trades": [
            {
                "entry_ts": int(f.entry_ts),
                "exit_ts": int(f.exit_ts),
                "side": cb.side_sign(f.side),
                "entry": f.price,
                "stop": f.stop,
            }
            for f in fills
        ],
    }
    return _write(payload, out_dir)


def export_drift(out_dir):
    """`nq:drift_vwap`: native NQ minute bars and its trades.

    NATIVE ONLY, NOT THE HYBRID. `combined_book.drift_vwap_orders` splits the
    window at the first level-two minute and runs two independent causal
    segments, because `nq_1m` and the L2 tick feed sit at different absolute
    price levels and joining them fabricates a gap. That switch is a property
    of the SOURCE, not of the strategy, so a parity fixture that straddled it
    would be asking two questions at once. The compared window is chosen to sit
    entirely inside one segment.
    """
    warm, lo, hi = stamp(WARM_FROM), stamp(FROM), stamp(TO)
    # LEVEL TWO BY DEFAULT: it is the feed the book runs this sleeve on
    # after the handover, and the native series hides a separate defect
    # (24h minutes reaching the 15-minute trend candles).
    source = os.environ.get("DRIFT_SOURCE", "level_two") or None
    minutes = dv.load_minutes("nq", WARM_FROM, TO, source=source)
    if not minutes:
        raise SystemExit("no nq_1m minutes; is QuestDB populated?")

    bars_5m = dv.aggregate(minutes, 5, rth_only=True)
    bars_15m = dv.aggregate(minutes, 15, rth_only=False)
    states = dv.trend_states(bars_15m)
    account = dv.Account(
        initial=1_000_000.0,
        risk=cb.DRIFT_VWAP_RISK,
        spread=cb.NQ_SPREAD_POINTS,
        slippage=cb.SLIPPAGE_POINTS,
        commission_per_lot=cb.NQ_COMMISSION_PER_LOT,
    )
    rules = dv.Rules()
    result = dv.backtest(
        bars_5m,
        states,
        account=account,
        rules=rules,
        from_ts=lo,
        to_ts=hi,
        sizing=dv.Sizing(mode="equity_risk", risk=cb.DRIFT_VWAP_RISK),
        volatility={},
    )

    payload = {
        "sleeve": "nq:drift_vwap",
        "symbol": "nq",
        "source": os.environ.get("DRIFT_SOURCE", "level_two") or "nq_1m",
        "warm_from": warm,
        "from": lo,
        "to": hi,
        "entry_cost": account.entry_cost,
        # ONE-MINUTE bars, unaggregated. The Rust port builds its own 5- and
        # 15-minute candles from the minute stream, and handing it the
        # aggregates instead would test the strategy while skipping the
        # aggregation the live engine actually performs.
        "bars": [
            [int(bar.ts), bar.open, bar.high, bar.low, bar.close, bar.volume]
            for bar in minutes
            if warm <= bar.ts < hi
        ],
        "trades": [
            {
                "entry_ts": int(trade["entry_ts"]),
                "exit_ts": int(trade["exit_ts"]),
                "side": cb.side_sign(trade.get("side")),
                "entry": float(trade["entry_price"]),
                "net_points": float(trade["net_points"]),
            }
            for trade in result["trades"]
        ],
    }
    return _write(payload, out_dir)


def _write(payload, out_dir):
    name = payload["sleeve"].replace(":", "_")
    path = os.path.join(out_dir, f"parity_{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    size = os.path.getsize(path) / 1e6
    print(
        f"{payload['sleeve']:<18} {len(payload['bars']):>7} bars  "
        f"{len(payload['trades']):>4} trades  {size:.2f} MB  -> {path}",
        flush=True,
    )
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    if only in ("", "ofi"):
        export_ofi(out)
    if only in ("", "drift"):
        export_drift(out)
