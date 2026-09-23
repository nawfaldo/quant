# Quant

Quant research and trading workspace with five top-level areas:

- `live_trade/` — Rust/Actix Web API, backtesting, tuning, market data, and live execution
- `web/` — React, TypeScript, Vite, and Bun frontend
- `tools/` — Python importers and market-data maintenance scripts
- `mt5/` — MetaTrader 5 execution bridge
- `sandbox/` — reusable Python strategy replicas, research, and validation

Read the nearest nested `AGENT.md` before changing `live_trade/` or `web/`.
`tools/.agents/AGENTS.md` contains additional guidance for that tree.

## Common commands

Run commands from the component directory unless shown otherwise.

```bash
cd live_trade && cargo fmt && cargo check && cargo test --lib
cd web && bun run build
```

The Rust server normally listens on `127.0.0.1:4000` (`PORT` overrides it; see
`live_trade/src/main.rs`) and the Vite development server on `127.0.0.1:5173`.

## Resource limits

The active machine is Windows with 16 GB RAM. Memory is the limiting resource,
and the store is 45 GB on disk.

- Never print binary API bodies, database files, or unbounded query results.
- Read a range, not a table. `dbento_nq_depth` is 5.06 billion rows in one
  41 GB file; asking for a day of it costs about a second because row groups
  are pruned by their footer statistics, and asking for all of it does not fit
  in memory.
- Never call `to_pylist()` on a large text column. `side` on the tick tables is
  96 million rows, and materialising it as Python strings is several gigabytes
  to answer a question worth one bit per row. Compare inside Arrow instead.
- Keep Actix at its default two workers unless a task explicitly requires more.
- Stop services that are not needed before compiling or tuning.

### pyarrow hangs on this machine unless you disarm WMI first

`platform.win32_ver()` on Python 3.14 asks WMI, WMI is broken on this box
(`Get-CimInstance Win32_OperatingSystem` times out), and pandas calls it at
import. pyarrow imports pandas lazily inside `pa.array()` and the dataset API,
so `pq.read_table` and every Python-to-Arrow conversion **block forever** with
no error and no CPU use.

`sandbox/parquet_store.py` and `tools/parquet_writer.py` both set
`platform._wmi = None` before importing pyarrow, which makes `_wmi_query` raise
immediately so `platform` takes its registry path. Import one of them first in
any script that touches Arrow. Repairing WMI would fix this properly and has not
been attempted.

The market-data cache deliberately builds blobs on demand. Do not replace this
with eager startup prefetching; constructing every timeframe and VWAP together
has previously exhausted this machine's memory.

## Data and time conventions

- `data/parquet/` is the source for market bars, ticks, depth and L2 features;
  SQLite `live_trade/app.db` stores application state, environments, backtests,
  trades, and March configuration.
- **QuestDB is retired.** Every table was exported to Parquet and dropped, so
  the running instance is empty and nothing reads it. A missing file is a
  missing table — there is no fallback. Do not add one.
- A table is `data/parquet/<table>.parquet`, or the directory
  `data/parquet/<table>/*.parquet`, or BOTH: the bulk history in one file and a
  live collector appending one shard per day beside it. Readers see the pair as
  one series, so a running feed never rewrites history.
- Read through the gateway, never with `pq.read_table` at a call site. Python
  goes through `sandbox/parquet_store.py`, Rust through
  `live_trade/src/api/parquet_store.rs`, and importers write through
  `tools/parquet_writer.py`. Each owns the timestamp decoding, the row-group
  pruning and the layout; a call site that opens a file itself gets all three
  wrong quietly.
- **Timestamps are stored as text**, `2008-12-11T02:38:00.000000Z`, because the
  CSV export wrote them that way. Two consequences that have already cost a
  debugging session each:
  - `pc.strptime(col, format="%Y-%m-%dT%H:%M:%S.%fZ")` returns **100% nulls**.
    Arrow's strptime has no `%f`. Cast to `timestamp(unit, tz="UTC")` instead —
    without the `tz` it refuses the trailing `Z`.
  - Fixed-width ISO-8601 sorts lexicographically in chronological order, which
    is what lets a text column be pruned by comparing text. Do not reformat a
    timestamp to a different width when writing a shard.
- Market timestamps are New York wall-clock values encoded as fake UTC. Preserve
  that convention end to end. `tz="UTC"` above is how Arrow is persuaded to
  accept the `Z`, not a zone conversion. Databento performs the UTC to New York
  conversion once during import; builders and the server must not convert those
  timestamps again.
- Treat `bm_*` and `dbento_*` tables as immutable raw market data. Feature
  builders must never truncate, delete from, or write rows to them. Only
  `databento_import.py` owns replacement of the `dbento_nq_*` tables.
- Derived one-second L2 features belong in separate `<symbol>_l2_features_1s`
  tables. The feature table is a strategy-optimized derivative, not a
  replacement for raw history: the Rust market-data paths still read
  `bm_nq_*` and `dbento_nq_*` for candles, ticks, volume profile and heatmap.
- Never commit databases, generated build output, dependency directories,
  credentials, local `.env` files, or market-data artifacts. `data/parquet/` is
  45 GB and is not in git.

### What is in the store, and what is missing

73 tables. The large ones are `dbento_nq_depth` (5.06 billion rows, 41 GB in one
file), `dbento_nq_ticks` (96.6 M), `nq_l2_features_1s` (8.7 M) and `bm_nq_ticks`
(7.6 M); the rest are `<symbol>_1m` and `<symbol>_30m` bars.

`vix_1d`, `vix_1h`, `ustec_tick` and `jkse_1d` were never exported and are gone.
Every path that reads them degrades rather than fails — the VIX-gated
`exogenous` families are refused as unrunnable, FX repricing returns "not
available", and the VIX chart overlay draws nothing. Dropping the matching
Parquet file into the store revives each of them untouched.

### Ingest

`tools/parquet_writer.py` is the write path. It mimics the ILP
`Sender.row(table, columns=, at=)` / `flush()` interface deliberately, so
porting an importer is a two-line diff — the import and the watermark read.
`tools/exness_import_1m.py` is the smallest worked example.

Rows are appended as date shards and a row REPLACES one already stored at the
same timestamp, so a collector restart cannot duplicate a minute. That
replaces QuestDB's `DEDUP UPSERT KEYS(timestamp)`, and it means the
`questdb-update-read-lag` hazard is gone: a shard is renamed into place
complete, so the first read after a write is already settled.

Four importers are ported: `exness_stream_ohlcv.py`, `binance_stream_1m.py`,
`idk_market_live_data_feeds.py` and `exness_import_1m.py`. The rest refuse to
run behind a guard that names `tools/NOT_PORTED.md`, which lists what each one
needs. Do not remove a guard without doing the port.

## Working-tree safety

The repository may contain active migrations and uncommitted experiments.
Preserve unrelated edits, avoid destructive Git commands, and keep changes
scoped to the requested component.
