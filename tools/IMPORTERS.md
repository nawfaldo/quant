# Importers

Every importer writes to `data/parquet/`. Four are ported; the rest refuse to
run and are listed in `NOT_PORTED.md`. `parquet_writer.py` is the write path.

## Free Binance BTCUSDT 1-minute update

The Binance updater uses the public spot market-data API without an API key. By
default it replaces the date shard containing the latest candle and
appends through yesterday:

```bash
python 'binance_fetch&import_1m.py' \
  --symbol BTCUSDT \
  --table btc_1m \
  --timezone America/New_York
```

`--timezone America/New_York` converts Binance's real UTC instants to New York
wall-clock timestamps stored with UTC labels, matching the existing market-data
tables. The updater validates all overlapping finalized candles before removing
a partition and treats the final overlap candle as potentially partial. Monthly
and daily sharding are both supported. DST fall-back duplicates and
spring-forward omissions are verified as expected wall-clock behavior.

## Automated LSE options download and import

`lse_fetch&import.py` downloads an inclusive local date range at 1-minute
resolution, validates the Parquet schema, replaces the corresponding DAY
partitions in all seven destination tables, performs DST-aware aggregation, and
verifies that no `(timestamp, osi)` key is duplicated:

```bash
python3 'lse_fetch&import.py' \
  --symbol QQQ \
  --table-prefix qqq_options \
  --timezone America/New_York \
  --date '01/07/26-14/07/26'
```

The reusable settings are:

- `--symbol` — required LSE underlying to fetch.
- `--date` — required inclusive `DD/MM/YY-DD/MM/YY` fetch range.
- `--table-prefix` — required table prefix before each timeframe suffix
  (for example, `qqq_options_1m` through `qqq_options_1d`).
- `--timezone` — required IANA timezone used for date windows and stored
  wall-clock timestamps.

The date format is `DD/MM/YY-DD/MM/YY`. Re-running the same or an overlapping
range is safe: existing partitions in that range are replaced before import.

```bash
python3 'lse_fetch&import.py' \
  --symbol SPY \
  --table-prefix spy_options \
  --timezone America/Chicago \
  --date '01/07/26-14/07/26'
```

## Dukascopy WTI/USOIL 1-minute update

`dukascopy_fetch&import.py` downloads 1-minute bars from Dukascopy's free feed
and appends them to a stored table. Dukascopy is the only free WTI source
without gaps — `E_Light` runs from 2012-01 to the current minute, while
HistData's WTIUSD archive is missing 2023-12 through 2026-06:

```bash
python 'dukascopy_fetch&import.py' \
  --instrument E_Light \
  --table usoil_1m \
  --timezone America/New_York
```

Each month is cached as Parquet under `<VENDOR_DIR>/dukascopy/months/` -- outside the
repository, see `parquet_writer.VENDOR_DIR` -- so only the
live month is refetched on a rerun. Imports are incremental — only bars newer
than the table's `max(timestamp)` are sent — so re-running is cheap and cannot
duplicate rows. `--rebuild` drops the table and reloads everything.

The settings are:

- `--instrument` — Dukascopy symbol (`E_Light` for WTI, `E_Brent` for Brent;
  see `dukascopy_python.instruments` for the rest).
- `--start` / `--end` — inclusive `YYYY` or `YYYY-MM` bounds.
- `--side` — `bid` or `ask` (default `bid`).
- `--timezone` — wall clock stored as fake UTC, DST-aware (default
  `America/New_York`, matching the other market-data tables).
- `--no-import` — write the CSV without adding it to the store.

**dukascopy.com is DNS-blocked on this network.** Turn Cloudflare Warp on before
running or every request fails with a connect timeout.

## Exness broker 1-minute bars for the canon symbols

`exness_import_1m.py` copies the Exness MT5 terminal's own M1 history into
the store as `exness_<broker symbol>_1m` — `exness_ustec_1m`, `exness_gbpjpy_1m`,
and so on for the eleven instruments the canon book trades. It exists so a
sleeve can be *signalled* on its vendor table (`gbpjpy_1m` is Dukascopy,
`ethusd_1m` is Binance, `nq_1m` is Databento) and *filled* on the broker's own
prices, which is what actually happens live. `sandbox/research/
exness_broker_fills.py` is the consumer.

```bash
py tools/exness_import_1m.py                      # canon eleven
py tools/exness_import_1m.py --symbols gbpjpy nq  # a subset
```

The table is named for the **broker's** instrument, not the repository symbol,
so `nq` lands as `exness_ustec_1m`. That is deliberate: `nq_1m` is the
back-adjusted future and `USTEC` is a cash CFD, and the two names sitting side
by side stop anyone reading them as two views of one series.

Timestamps are New York wall clock relabelled as UTC, like every other bar
table. The MetaTrader5 Python API returns a genuine UTC epoch — verified by
scanning the hourly offset that minimises mean absolute close difference
against `gbpjpy_1m`, which bottoms sharply at +4h in August (0.005 against 0.10
either side), exactly New York's DST offset.

The settings are:

- `--symbols` — repository symbols; the default is the canon eleven.
- `--to-date` — last inclusive New York date (default: two days ago).
- `--first-year` — how far back to walk (default 1999).
- `--table-prefix` — default `exness_`.

Two things bite, both handled but worth knowing:

- **Max bars in chart caps the history.** At MetaTrader's default 100,000 an M1
  request returns only the last three months, and a request *larger* than the
  cap fails with "Invalid params" rather than truncating. Set `[Charts]
  MaxBars=2147483647` in the terminal's `config/common.ini` **while the
  terminal is closed** (it rewrites that file on exit) and restart it.
- **MT5 does not honour the requested range when it has no history.** Asked for
  a year the server cannot serve, `copy_rates_range` returns one stale bar
  dated outside the window instead of an empty array. The importer filters every
  row against the window it asked for; without that the backward walk never
  terminates and a fabricated row upserts over a real one.

Tables are created `WAL ... DEDUP UPSERT KEYS(timestamp)`, so a re-run is
idempotent and can extend or repair a table in place.

## Removed

`questdb_csv_importer.py` and `questdb_parquet_importer.py` are gone. Both
existed only to stream ILP into QuestDB, so nothing of them survives its
retirement; they are in git history under `data_manipulation/`.
