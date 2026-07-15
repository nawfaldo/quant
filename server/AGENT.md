# Rust server

Actix Web service for the frontend in `../web`. The Cargo package and binary are
still named `backend_rust`; the repository directory is `server/`.

## Responsibilities

- Serve the JSON and little-endian binary API consumed by `web/src/api.ts`.
- Read QuestDB market data on demand without prefetching every table.
- Persist settings, environments, backtests, trades, March state, and MT5 state
  in SQLite.
- Run backtests, portfolio combinations, Monte Carlo simulations, and tuning.
- Aggregate optional Bookmap ticks and coordinate the MT5 execution bridge.

## Layout

```text
server/src/
├── main.rs               # environment, database initialization, and bind address
├── lib.rs                # public module surface
├── server/               # Actix setup, route handlers, QuestDB client, and market cache
├── database/             # SQLite connection, models, schema, and persistence
├── backtest/             # data, execution, tuning, FX, and Monte Carlo
├── strategies/           # strategy implementations
├── sizing/               # position-sizing implementations
├── live/                 # live strategy, Bookmap, state, and MT5 bridge
└── bin/signal_runner.rs  # standalone pipe-based live runner
```

## Commands

Run from `server/`:

```bash
cargo fmt
cargo check
cargo test --lib
cargo run
cargo run --bin signal_runner
```

Use targeted tests while iterating, then run `cargo check` and `cargo test --lib`
before handoff. Do not run expensive builds or tuning sweeps alongside QuestDB
and the web development server on this 8 GB machine.

## Runtime configuration

- `PORT` — HTTP port, default `8080`
- `BIND_HOST` — bind address, default `127.0.0.1`
- `ACTIX_WORKERS` — worker count, default `2`
- `APP_DB_PATH` — SQLite path, default `server/app.db`
- `QUESTDB_URL` — QuestDB origin, default `http://127.0.0.1:9000`
- `BOOKMAP_WS_URL` — optional Bookmap WebSocket; disabled by default off Windows
- `MT5_BRIDGE_TOKEN` — required when binding outside loopback
- `MT5_MAGIC_NUMBER` and `MT5_DEVIATION_POINTS` — MT5 execution settings
- `RUST_LOG` — tracing filter

Do not add machine-specific absolute paths or secrets to source control.

## Contracts to preserve

- Treat `web/src/api.ts` and `web/src/types.ts` as consumers of the server
  contract. Coordinate changes across both components.
- Preserve response status codes, JSON field names, and binary layout/endianness.
- Keep request limits and structured JSON errors intact.
- Keep timestamps as fake-UTC ET. Do not apply an `America/New_York` conversion.
- Keep QuestDB reads bounded and on demand. Never log binary route bodies or
  unbounded result sets.
- Preserve the MT5 command idempotency and token checks. A non-loopback bind must
  never run with an empty bridge token.
