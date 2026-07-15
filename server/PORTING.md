# Zig to Rust parity checklist

This file is the completion contract for the migration. A route existing in
Actix does not count as ported unless its behavior and persisted data match the
Zig implementation.

| Zig source | Rust destination | Status | Remaining work |
| --- | --- | --- | --- |
| `main.zig`, `server/http.zig`, `server/router.zig` | `main.rs`, `server/http.rs`, `server/routes/` | Partial | Route coverage exists; add contract tests for every response and error. |
| `server/questdb.zig`, `server/cache.zig` | `server/questdb.rs`, `server/market_data.rs` | Partial | Match cache lifetime, tick top-up, query limits, and binary output exactly. |
| `settings.zig` | `database/settings.rs`, `database/schema.rs`, `server/routes/settings.rs` | Partial | Verify migrations, defaults, validation, and all legacy database states. |
| `db.zig` | `database/` | Partial | Persistence uses async SeaORM and is split by domain; finish golden legacy-database comparisons. |
| `bt/data.zig` | `backtest/data.rs`, `server/market_data.rs`, `server/questdb.rs` | Partial | Match timestamp boundaries, ordering, and missing-data behavior. |
| `bt/engine.zig` | `backtest/engine.rs`, `backtest/prepare.rs`, `backtest/types.rs` | Partial | Default Night/Noise trade and drawdown fixtures match; finish configurable-strategy paths. |
| `bt/fx.zig` | `backtest/fx.rs` | Partial | Repricing, streaming, same-bar stops, reports, and persistence are ported; run golden parity against an `fx_nq_ticks` fixture. |
| `bt/montecarlo.zig` | `backtest/monte_carlo.rs` | Partial | Stationary-block API output and paths match Zig; expose and test the IID/configurable library surface. |
| `bt/run.zig` | `server/routes/backtests.rs`, `backtest/engine.rs` | Partial | Match all run configuration, validation, report, and persistence details. |
| `bt/combine.zig` | `backtest/combine.rs` | Partial | Port portfolio accounting, sizing, FX, and drawdown calculations exactly. |
| `bt/tune.zig` | `backtest/tuning/runner.rs` | Partial | Every parameter set now runs over one loaded dataset with progress; add bounded workers and cancellation. |
| `bt/tune_report.zig`, `bt/tune_score.zig` | `backtest/tuning/report.rs`, `backtest/tuning/score.rs` | Partial | Core metrics, scoring, and rankings are ported; match the full summary, CSV, Markdown, and heatmap contracts. |
| `sizings/vol_target.zig` | `sizing/volatility_target.rs` | Partial | EWMA sizing is wired into runs and tuning; add golden state and sizing parity fixtures. |
| `strategies/idk/night_drift.zig` | `strategies/idk/night_drift.rs` | Partial | Production-default API fixture and live state are ported; finish optimizer parameters and golden live-decision fixtures. |
| `strategies/idk/noise_momentum.zig` | `strategies/idk/noise_momentum.rs` | Partial | Production-default API fixture matches; port optimizer parameters and optional exits. |
| `march/march_api.zig` | `server/routes/march.rs`, `live/state.rs`, `live/mt5/` | Partial | Noise-area, activation, warm-up, live state, trade transitions, and the durable MT5 EA queue are ported; add a Windows EA integration fixture. |
| `march/ws.zig` | `live/bookmap.rs` | Partial | WebSocket reconnects, NQ parsing, one-minute aggregation, and live tick dispatch are ported; validate against the Windows Bookmap addon. |
| `signal_runner.zig` | `bin/signal_runner.rs` | Partial | The line protocol and Night Drift bar feed are ported; add a side-by-side long historical stream fixture. |
| `night_optimize.zig`, `noise_optimize.zig` | — | Missing | Add Rust optimization binaries. |
| `night_validate.zig`, `noise_validate.zig` | — | Missing | Add Rust parity-validation binaries and golden comparisons. |
| `strategy_tests.zig` | Rust tests | Incomplete | Port the Zig strategy and accounting test suite. |

The migration is complete only when every row is **Complete**, the Rust parity
tests pass against shared fixtures, and the frontend can switch implementations
without special cases.
