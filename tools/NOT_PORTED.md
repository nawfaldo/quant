# Importers not yet on the Parquet store

QuestDB is retired. Every table was exported to `data/parquet/` and dropped, so
the database an unported importer connects to is **empty and stays empty** —
`SHOW TABLES` returns nothing. Writing to it succeeds and reaches nobody: no
reader looks there any more.

Each script listed below therefore refuses to run, with a message naming this
file, rather than appearing to work. The refusal is a single guard at the top of
`main`; everything below it — the vendor API handling, the clock conversions,
the gap rules — is untouched and is what a port should preserve.

## Ported already

| Script | What it writes |
| --- | --- |
| `exness_stream_ohlcv.py` | live Exness M1/M30 → `<table>/<date>.parquet` |
| `binance_stream_1m.py` | live Binance klines |
| `idk_market_live_data_feeds.py` | Dukascopy + Binance catch-up for the canon book |
| `exness_import_1m.py` | Exness MT5 history → `exness_<symbol>_1m` |

They all write through `parquet_writer.Sender`, which mimics the ILP
`Sender.row(...)`/`flush()` interface on purpose so the port is a two-line diff
per script. Read its docstring before porting another one.

## Still to port

| Script | Why it is more than an import swap |
| --- | --- |
| `binance_fetch&import_1m.py` | replaces a partition per run via `ALTER TABLE ... DROP PARTITION`; needs the equivalent "delete these date shards and rewrite them" |
| `binance_backfill_1m.py` | bootstraps a table and shells out to `questdb_parquet_importer.py` |
| `binance_fill_gaps.py` | finds missing minutes with a SQL self-join |
| `crypto_multivenue_1m.py` | same partition-replace workflow, several venues |
| `dukascopy_fetch&import.py` | same, plus a monthly-archive reconciliation pass |
| `databento_import.py` | owns the `dbento_nq_*` lifecycle: interrupted-file cleanup, replacement, and a WAL-drain preflight that has no Parquet equivalent |
| `build_nq_l2_features_1s.py` | streams `dbento_nq_depth` out of QuestDB and writes `nq_l2_features_1s`; also the `--replace-existing` source/date-range delete |
| `build_nq_liquidity_lines_1s.py` | streams the same raw tables read-only |
| `bookmap_stealer.py` | live collector, writes `bm_*` events and `nq_l2_features_1s` rows |

The three `dbento_*`/`bm_*` builders are the heavy ones: their inputs are the
5.06-billion-row depth table and the 96-million-row tick table, and the reader
side of that is already solved — `sandbox/parquet_store.iter_batches` prunes row
groups by footer statistics and reads one day out of the depth file in about a
second. A port reads through that and writes through `parquet_writer`.

## Retired

`questdb_csv_importer.py` and `questdb_parquet_importer.py` are deleted. Both
existed only to stream rows into QuestDB over ILP, so nothing of them survives
the move; they are in git history under `data_manipulation/` if a detail is ever
wanted.

`yfinance_fetch&import.py`, `yfinance_fetch&import_timeframe.py`,
`lse_fetch&import.py`, `batch_fetch&import.py` and `mt5_tick_import.py` are left
in place but dormant behind the same guard. They fetch real vendor data and are
worth reviving if their tables are ever wanted again — `vix_1d`, `jkse_1d` and
`ustec_tick` are all theirs, and all three are currently absent from the store.
