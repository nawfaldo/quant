# QuestDB CSV and Parquet importers

Both importers stream InfluxDB Line Protocol to QuestDB on TCP port 9009.

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
- `--table-prefix` — required QuestDB prefix before each timeframe suffix
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

## CSV

Raw import:

```bash
python3 questdb_csv_importer.py data.csv --table my_table --ts-col timestamp
```

NQ-style split date/time input, shifted from Chicago to New York wall-clock:

```bash
python3 questdb_csv_importer.py nq-1m.csv \
  --table nq \
  --delim ';' \
  --ts-col date \
  --ts-col2 time \
  --tz-hours 1 \
  --aggregate
```

Aggregate mode writes `_1m`, `_5m`, `_15m`, `_30m`, `_1h`, `_4h`, and `_1d`.

## Parquet

Import one file or all same-schema `.parquet` files in a directory:

```bash
python3 questdb_parquet_importer.py '/path/to/parquet-folder' \
  --table qqq_options \
  --ts-col ts \
  --tag-cols underlying,opt_type \
  --aggregate \
  --timezone America/New_York
```

Parquet aggregate mode groups each option contract independently using `osi`
and writes `_1m`, `_5m`, `_15m`, `_30m`, `_1h`, `_4h`, and `_1d` tables.

`--timezone` converts a real UTC instant to the selected timezone's wall-clock
time and stores that wall-clock value as UTC. It uses IANA timezone rules, so
daylight-saving changes are automatic.

Use `--dry-run` on either script to validate input and print sample ILP without
connecting to QuestDB.
