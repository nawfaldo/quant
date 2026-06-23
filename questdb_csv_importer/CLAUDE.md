# questdb_csv_importer

A single-file Zig CLI tool that reads a CSV and streams it into QuestDB via the InfluxDB Line Protocol (ILP) over TCP. It has two modes: raw import (any CSV) and aggregate mode (OHLCV data → multiple timeframe tables in one pass).

## Build & Run

```bash
zig build                          # produces zig-out/bin/questdb_csv_importer
zig build run -- <args>            # build + run in one step
```

## Flags

| Flag | Default | Description |
|---|---|---|
| `--table NAME` | CSV filename stem | Base table name |
| `--host HOST` | `127.0.0.1` | QuestDB host |
| `--port PORT` | `9009` | ILP TCP port |
| `--ts-col NAME\|N` | `0` | Timestamp column (name or index) |
| `--ts-col2 NAME\|N` | _(none)_ | Time column to merge with `--ts-col` |
| `--delim CHAR` | `,` | CSV delimiter |
| `--tz-hours N` | `0` | Shift every timestamp by N hours (e.g. `1` for CT→ET) |
| `--aggregate` / `-a` | off | OHLCV aggregate mode |

## Two Modes

### Raw import
Streams every CSV row as-is into a single QuestDB table. Column types are auto-detected from the first data row (float, integer, or string). Timestamp column is excluded from fields and used as the ILP timestamp.

```bash
./questdb_csv_importer data.csv --table my_table
```

### Aggregate mode (`--aggregate`)
Reads 1-minute OHLCV bars and writes all 7 timeframes simultaneously into separate tables in one pass. Tables are named `{base}_1m`, `{base}_5m`, `{base}_15m`, `{base}_30m`, `{base}_1h`, `{base}_4h`, `{base}_1d`.

OHLCV column detection is case-insensitive: `open/Open/OPEN`, `high`, `low`, `close`, `volume`/`vol`.

Aggregation rules per bar:
- **open** = first open in the period
- **high** = max high
- **low** = min low
- **close** = last close
- **volume** = sum (written as integer in ILP)

Gaps in the data (missing minutes) are handled naturally — bars only exist for timestamps present in the input. No phantom bars are created.

```bash
./questdb_csv_importer nq_1m.csv --table nq --aggregate
# → writes nq_1m, nq_5m, nq_15m, nq_30m, nq_1h, nq_4h, nq_1d
```

If the filename or `--table` value already ends with a timeframe suffix (e.g. `nq_1m`), it is stripped automatically so table names don't double up (`nq_1m_1m`).

### Split date + time columns (`--ts-col2`)
Some CSVs store date and time in separate columns. `--ts-col2` merges them with a space before timestamp parsing.

```bash
./questdb_csv_importer nq-1m_bk_fixed.csv \
  --table nq \
  --delim ';' \
  --ts-col date \
  --ts-col2 time \
  --aggregate
```

This handles the NQ dataset format: `date;time;open;high;low;close;volume` where date is `D/M/YYYY` (day-first) and time is `HH:MM`.

## Timestamp Formats Supported

| Format | Example |
|---|---|
| Unix integer (auto-scaled) | `1704187800` / `1704187800000` / `1704187800000000000` |
| US slash | `11/12/2008 02:17` |
| ISO with space | `2024-01-02 09:30:00` |
| ISO 8601 | `2024-01-02T09:30:00.000000` |

All timestamps are converted to nanoseconds before sending to QuestDB. Unix integers are heuristically scaled by magnitude (seconds/milliseconds/microseconds/nanoseconds).

> **Known bug — Unix seconds mis-scaled:** Any seconds-precision Unix timestamp after 2001-09-09 is `> 1_000_000_000` and falls into the millisecond branch (`* 1_000_000`) instead of the seconds branch (`* 1_000_000_000`), placing it ~1000× too far in the future. Does not affect the NQ dataset (which uses the slash date+time format), but the CLAUDE.md Unix seconds examples above are silently wrong for modern dates.

## Timezone model

`dateTimeToUnix` (and the slash/ISO parsers) treat parsed wall-clock numbers as **UTC with no offset applied**. `--tz-hours N` is the only shift, and it is a flat additive offset in nanoseconds applied after parsing.

**Consequence: stored timestamps are not true UTC.** They are the source wall-clock time shifted by `--tz-hours`, stored as fake-UTC. The rest of the pipeline (backtester, web frontend) must read them as UTC and not re-apply any timezone conversion.

For the NQ dataset (Chicago time, `--tz-hours 1`):
- Stored value = Chicago wall clock + 1 h = New York wall clock, labeled as UTC
- CT and ET observe US DST in lockstep → the flat +1 h is correct year-round
- All downstream consumers (backtest session gates, chart display) use UTC arithmetic and therefore see correct New York times without any further conversion

## Architecture

Everything lives in `src/main.zig` (~720 lines). No external dependencies beyond the Zig standard library.

**Key constants:**
- `BATCH_SIZE = 10_000` — rows buffered before a TCP flush
- `PROGRESS_INTERVAL = 50_000` — how often to print a progress line
- `MAX_COLS = 64` — max columns per CSV row
- `NUM_TF = 7` — number of timeframes in aggregate mode

**Key types:**
- `Config` — parsed CLI options
- `OhlcvCols` — column indices for open/high/low/close/volume (detected from header)
- `CandleState` — in-progress OHLCV bar for one timeframe; one instance per timeframe held in a `[NUM_TF]CandleState` array during the aggregate loop

**Key functions:**
- `aggProcessRow` — core of aggregate mode; for each input row, checks all 7 timeframe buckets and flushes completed candles
- `barStartNs` — floors a nanosecond timestamp to the start of a bar using `@divFloor`
- `parseTs` — combines ts_col (+ optional ts_col2) and calls `parseTimestamp`
- `writeCandle` — emits one ILP line for a completed candle
- `writeRow` — emits one ILP line for raw import mode
- `detectOhlcv` — scans column names case-insensitively to find OHLCV indices

## QuestDB Connection

Connects to QuestDB via a plain TCP socket on port 9009 (ILP). All tables and columns are created automatically on first insert — no schema setup needed. The TCP write buffer is 256 KB; it is flushed every `BATCH_SIZE` rows and once at the end.

## Current Dataset

NQ 1-minute bars from 2008–2026, stored at:
`/Users/nawfaldo/Downloads/nq-1m_bk_fixed.csv`

Format: semicolon-delimited, ~5.86M rows, columns: `date;time;open;high;low;close;volume`

Source timestamps are in Chicago time (CT). Import with `--tz-hours 1` to shift to New York time (ET).

Import command:
```bash
./zig-out/bin/questdb_csv_importer \
  /Users/nawfaldo/Downloads/nq-1m_bk_fixed.csv \
  --table nq \
  --delim ';' \
  --ts-col date \
  --ts-col2 time \
  --tz-hours 1 \
  --aggregate
```
