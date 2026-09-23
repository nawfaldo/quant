# Rust live-trading service

Actix Web service for the frontend in `../web`. The Cargo package, the main
binary, and the repository directory are all named `live_trade`.

## Responsibilities

- Serve the JSON and little-endian binary API consumed by `web/src/api.ts`.
- Read Parquet market data on demand without prefetching every table.
- Persist settings, environments, backtests, trades, March state, and MT5 state
  in SQLite.
- Run backtests, portfolio combinations, and Monte Carlo simulations.
- Aggregate optional Bookmap ticks and coordinate the MT5 execution bridge.
- Run the live portfolio: warm each sleeve from history, then turn its actions
  into MT5 execution commands.

## One book, one cost model, one instrument

Three things are fixed rather than configurable, and each was made fixed after
the configurable version turned out to be inert:

- The STRATEGIES are the twenty-two sleeves below and nothing else.
- The COST MODEL is Exness Pro (`backtest::engine::costs`). Environments used to
  carry editable spread/slippage/commission; the engine overrode all three.
- The INSTRUMENT is Forex, 0.01 lots. An `Instrument` enum used to carry futures
  multipliers on arms `prepare` refused.

A fourth, `benchmark_reproduction`, replayed frozen Python fixtures from the
previous book; `prepare` rejected it outright, so every branch behind it was
unreachable. The tuner was the same story: it refused any strategy but a retired
one, and swept sizing knobs no sleeve reads.

## The only strategies are the 2026-09-07 Exness book

`strategies/idk/exness_combined_07-09-2026/` holds all twenty-two sleeves and is
the entire strategy surface. Nothing else is live in either the backtest or the
live runtime, and code that supports another strategy is dead by definition — the
overlay, the VIX and session-close series, and the canonical replay were all
removed on that basis. Before adding a hook "for the next strategy", note that
the last set of those all turned into dead branches.

Each sleeve is ONE FILE in `sleeves/`, holding everything the book knows about
it: names, market, contract, risk scale, shown equity and its fitted `Params`.
Every member is an `exness_families` cell since 2026-09-04 -- the two
hand-written NQ imports that carried their own engine left when the symbol was
barred, so there is one machine rather than three. `sleeves::spec` is the only
`match` over sleeves left; every other per-sleeve answer is a field read off what
it returns. Adding a sleeve is a new file plus one arm, and a missing arm is a
build error rather than a sleeve that inherits another's settings.

The directory name is deliberate: the book is a sealed selection made on one day
against one data snapshot, so a rebuild is a different book, not a new version of
this one. It is not a valid module path, which is why `strategies/idk/mod.rs`
reaches it through `#[path]`.

## Layout

```text
live_trade/src/
├── main.rs               # environment, database initialization, and bind address
├── lib.rs                # public module surface
├── api/                  # Actix setup, route handlers, Parquet store, and market cache
├── database/             # SQLite connection, models, schema, and persistence
├── backtest/             # data, execution, reporting, FX, and Monte Carlo
│   └── engine/           # the streaming replay loop, plus its cost and sizing models
├── strategies/idk/       # the 2026-09-07 book
│   └── .../sleeves/      # one file per sleeve: twenty-two of each
├── live/                 # the portfolio runtime, its per-slot execution, and the MT5 bridge
└── tools/                # diagnostic drivers, declared as `[[bin]]` in Cargo.toml
```

`src/bin/` deliberately does not exist: every binary target has an explicit
`[[bin]]` path so the tree shows one library and its tools rather than a
directory Cargo treats as magic.

## Commands

Run from `live_trade/`:

```bash
cargo fmt
cargo clippy --all-targets
cargo test --lib
cargo run
cargo run --release --bin exness_book -- 2025-01-01 2026-08-20 400
cargo run --release --bin exness_sleeve_probe -- nq_sar
```

Use targeted tests while iterating, then run `cargo clippy --all-targets` and
`cargo test --lib` before handoff. Do not run expensive builds or research sweeps
alongside the web development server on this machine.

## Runtime configuration

- `PORT` — HTTP port, default `4000`
- `BIND_HOST` — bind address, default `127.0.0.1`
- `ACTIX_WORKERS` — worker count, default `2`
- `APP_DB_PATH` — SQLite path, default `live_trade/app.db`
- `MARKET_DATA_DIR` — Parquet store root, default `../data/parquet`
- `MT5_BRIDGE_TOKEN` — required when binding outside loopback
- `MT5_MAGIC_NUMBER` and `MT5_DEVIATION_POINTS` — MT5 execution settings
- `RUST_LOG` — tracing filter

Research-only, unset in production:

- `SLEEVE_EXPOSURE_SCHEDULE` — JSON file of per-sleeve exposure steps, re-read
  once per run so a sweep can rewrite it between candidates
- `EXNESS_GROSS_CAP` — overrides the book's 8x gross cap; `0` disables it
- `DUMP_TRADES` and `SLEEVE` — per-trade dump path for `exness_book`, and the
  sleeve `parity` reports on

Do not add machine-specific absolute paths or secrets to source control.

## Contracts to preserve

- Treat `web/src/api.ts` and `web/src/types.ts` as consumers of the server
  contract. Coordinate changes across both components.
- Preserve response status codes, JSON field names, and binary layout/endianness.
- Backtest costs are NOT configurable. `GET /api/environments/{id}/rules` reports
  the one Exness Pro rule, built from the tables `backtest::engine::costs`
  charges. It used to be a table of spread/slippage/commission rows a user could
  edit, every one of which the engine overrode before replaying a single bar.
- Keep request limits and structured JSON errors intact.
- Keep timestamps as fake-UTC ET. Do not apply an `America/New_York` conversion.
- Keep Parquet reads bounded and on demand. Never log binary route bodies or
  unbounded result sets.
- Preserve the MT5 command idempotency and token checks. A non-loopback bind must
  never run with an empty bridge token.
- `strategies::market_symbol` and `live::portfolio::strategy_symbol` must agree,
  or a backtest and its live twin trade different instruments.
