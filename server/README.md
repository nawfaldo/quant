# server

Standalone Actix Web service for the frontend in `../web`.

This migration is still in progress. [`PORTING.md`](PORTING.md) records the
module-by-module status, and [`PARITY.md`](PARITY.md) records side-by-side Zig
and Rust results. They are the source of truth for completion.

## Run

```bash
cd server
cargo run
```

The server binds `127.0.0.1:8080`, so the existing frontend can use it without
configuration changes. Stop the Zig process first, or choose another port:

```bash
PORT=18080 cargo run
```

Environment variables:

- `PORT` — HTTP port, default `8080`
- `BIND_HOST` — listening interface, default `127.0.0.1`; use a trusted private
  interface if the EA runs on another machine
- `ACTIX_WORKERS` — Actix worker count, default `2` for this machine's memory budget
- `APP_DB_PATH` — SQLite database; defaults to `server/app.db`
- `QUESTDB_URL` — QuestDB HTTP origin, default `http://127.0.0.1:9000`
- `BOOKMAP_WS_URL` — optional Bookmap WebSocket; defaults to
  `ws://127.0.0.1:8765` on Windows and is disabled elsewhere
- `MT5_BRIDGE_TOKEN` — shared secret used by the MT5 execution EA; set the same
  value in the EA's `BridgeToken` input
- `MT5_MAGIC_NUMBER` — magic number for March positions, default `26032026`
- `MT5_DEVIATION_POINTS` — maximum execution deviation, default `20`
- `RUST_LOG` — tracing filter

## Current coverage

- Actix routing, CORS, request limits, error responses, and liveness
- SeaORM's async SQLite pool over the existing `app.db` schema, including
  settings, environments, rules, backtests,
  trades, Monte Carlo, March, and MT5 persistence
- QuestDB CSV transport and on-demand market-data queries
- candle, VWAP, March candle, trade, and Monte Carlo binary wire formats
- initial Night Drift and Noise Momentum strategy implementations
- initial fill-cost, sizing, drawdown, reporting, Monte Carlo, run/save,
  combine/save, and tuning implementations
- the Zig HTTP path and method surface

Path coverage is not behavioral parity. FX repricing and the noise-area overlay
now have native Rust implementations, but FX still needs a shared golden tick
fixture. The March live state machine and warm-up are ported and smoke-tested.
MT5 execution uses the durable pull-based EA in `../mt5`; Bookmap ingestion,
reconnects, tick aggregation, and the EA still need a shared Windows/Bookmap/MT5
integration fixture. Full
tuning artifact contracts, combined portfolio accounting, and standalone
optimizer and validator binaries also remain. See the checklist before treating
this service as a replacement for the Zig backend.

The pipe-based live runner is available as:

```bash
cargo run --bin signal_runner
```

## Verify

```bash
cargo check
cargo test --lib
```

The service deliberately retains on-demand market-data construction: startup
does not prefetch or cache all QuestDB timeframes.
