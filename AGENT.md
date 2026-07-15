# Quant

Quant research and trading workspace with four top-level areas:

- `server/` — Rust/Actix Web API, backtesting, tuning, market data, and live execution
- `web/` — React, TypeScript, Vite, and Bun frontend
- `data_manipulation/` — Python importers and market-data maintenance scripts
- `mt5/` — MetaTrader 5 execution bridge

Read the nearest nested `AGENT.md` before changing `server/` or `web/`.
`data_manipulation/.agents/AGENTS.md` contains additional guidance for that tree.

## Common commands

Run commands from the component directory unless shown otherwise.

```bash
cd server && cargo fmt && cargo check && cargo test --lib
cd web && bun run build
```

QuestDB normally listens on `127.0.0.1:9000`, the Rust server on
`127.0.0.1:8080`, and the Vite development server on `127.0.0.1:5173`.

## Resource limits

This workspace runs on an 8 GB M2 MacBook Pro. Memory is the limiting resource.

- Never print binary API bodies, database files, or unbounded QuestDB results.
- Do not run QuestDB, the Rust server, the web dev server, and a build or tuning
  sweep at the same time.
- Keep Actix at its default two workers unless a task explicitly requires more.
- Prefer small tuning grids locally. Large sweeps belong on the remote PC.
- Stop services that are not needed before compiling or tuning.

The market-data cache deliberately builds blobs on demand. Do not replace this
with eager startup prefetching; constructing every timeframe and VWAP together
has previously exhausted this machine's memory.

## Data and time conventions

- QuestDB is the source for market bars and ticks; SQLite `server/app.db` stores
  application state, environments, backtests, trades, and March configuration.
- Market timestamps are New York wall-clock values encoded as fake UTC. Preserve
  that convention end to end; do not apply an `America/New_York` conversion.
- Never commit databases, generated build output, dependency directories,
  credentials, local `.env` files, or large market-data artifacts.

## Working-tree safety

The repository may contain active migrations and uncommitted experiments.
Preserve unrelated edits, avoid destructive Git commands, and keep changes
scoped to the requested component.
