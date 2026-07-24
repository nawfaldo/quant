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

The active import machine is Windows with 16 GB RAM. Memory and QuestDB merge
pressure are the limiting resources.

- Never print binary API bodies, database files, or unbounded QuestDB results.
- Avoid running tuning sweeps or large builds during a Databento import.
- Keep Actix at its default two workers unless a task explicitly requires more.
- Prefer small tuning grids while QuestDB is ingesting.
- Stop services that are not needed before compiling or tuning.

The market-data cache deliberately builds blobs on demand. Do not replace this
with eager startup prefetching; constructing every timeframe and VWAP together
has previously exhausted this machine's memory.

## Data and time conventions

- QuestDB is the source for market bars and ticks; SQLite `server/app.db` stores
  application state, environments, backtests, trades, and March configuration.
- Market timestamps are New York wall-clock values encoded as fake UTC. Preserve
  that convention end to end. Databento schema version 6 performs the UTC to
  New York conversion once during import; builders and the server must not
  convert those designated timestamps again.
- Treat `bm_*` and `dbento_*` QuestDB tables as immutable raw market data. Feature
  builders must never drop, truncate, delete from, update, or write rows to them.
  Only `databento_import.py` owns creation, interrupted-file cleanup, and
  replacement of the `dbento_nq_*` tables.
- Derived one-second L2 features belong in separate
  `<symbol>_l2_features_1s` tables.
  Backfill Bookmap and/or Databento features with
  `data_manipulation/build_nq_l2_features_1s.py`; the builder rejects raw-table
  targets and normally refuses to duplicate an existing source/date range.
  `--replace-existing` may delete and rebuild only the explicitly selected
  derived source/date range; never use `--source both --replace-existing` while
  the live Bookmap collector is running.
- `data_manipulation/bookmap_stealer.py` keeps its raw `bm_*` event log and
  browser WebSocket path while also emitting completed NQ seconds directly to
  `nq_l2_features_1s` with `source='bm'`. Historical Databento feature rows are
  rebuilt once with `--source dbento --replace-existing` after an event-level
  reimport. Live Bookmap rows do not require repeated builder runs. Reload or
  restart the Bookmap addon after collector code changes.
- `data_manipulation/databento_import.py` is the sole owner of the
  `dbento_nq_ticks` and `dbento_nq_depth` import lifecycle. It preserves every
  trade and converts consecutive MBP-10 snapshots into Bookmap-compatible
  absolute price-level events, including zero-size removals. It may clean rows
  for an incomplete source file before retrying. Schema version 6 uses the same
  columns, absolute-level replay semantics, nanosecond precision, and New York
  wall-clock convention as `bm_nq_ticks`/`bm_nq_depth`; source UTC timestamps are
  not retained in QuestDB or the manifest. Do not mutate those tables manually
  after a verified import.
- The Rust market-data paths still use `bm_nq_*` and `dbento_nq_*` for
  historical candles, ticks, volume profile, and heatmap. Do not delete those
  raw tables merely because `nq_l2_features_1s` exists. The feature table is a
  strategy-optimized derivative, not a complete replacement for raw history.
- Never commit databases, generated build output, dependency directories,
  credentials, local `.env` files, or large market-data artifacts.

One-time Databento feature rebuild after an event-level import:

```bash
python data_manipulation/build_nq_l2_features_1s.py \
  --symbol nq --source dbento --from 2026-05-27 --to 2026-07-17 \
  --workers 10 --flush-rows 20000 --replace-existing
```

The builder parallelizes independent day partitions with worker processes while
preserving timestamp/sequence order inside each day. Each replay worker holds two
QuestDB export streams, so the builder caps replay at five workers even when a
higher `--workers` value is requested; this prevents HTTP 429 responses. The
Databento importer may still use ten workers because its access pattern differs.

Event-level Databento import on a 16 GB machine:

```bash
python data_manipulation/databento_import.py \
  --input data_manipulation/databento --symbol nq --workers 10 --chunk-size 200000
```

The event import is expected to produce roughly 900 million absolute depth
updates and may require approximately 50–70 GiB including QuestDB WAL and merge
space. The retained `.dbn.zst` files are the recoverable source and must not be
modified or deleted during import. Import completion means all rows were
submitted to WAL; do not start a feature rebuild until `wal_pending_row_count`
is zero for both Databento tables. The builder enforces this preflight.

## Working-tree safety

The repository may contain active migrations and uncommitted experiments.
Preserve unrelated edits, avoid destructive Git commands, and keep changes
scoped to the requested component.
