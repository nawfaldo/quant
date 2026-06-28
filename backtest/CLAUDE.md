# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Build: `zig build`
- Run: `zig build run` (launches the interactive CLI)
- Build artifact only: `zig build install` (output in `zig-out/bin/backtester`)

Requires Zig >= 0.16.0. There is no test suite, lint config, or formatter step wired into `build.zig`; use `zig fmt src/` directly if you need formatting.

## Runtime dependencies

**QuestDB** (market data source):
- PGWire on `127.0.0.1:8812`, credentials `admin/quest`, database `qdb`
- Tables follow the pattern `{symbol}_{timeframe}`. Available symbols: `nq`, `gbpusd`, `eurusd`. Available timeframes: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`. Each table has columns `timestamp` (`timestamp`), `open high low close` (`double`), `volume` (`long`).
- **Timezone model:** Timestamps are stored as New York (ET) wall-clock times baked into fake-UTC by the importer (`--tz-hours 1` shifts Chicago CT → ET, then writes as UTC nanoseconds). `data.zig:formatTs` decodes them with plain UTC epoch arithmetic, so the `Ts` strings the strategy receives (`"YYYY-MM-DD HH:MM"`) directly show New York times. **Never apply a timezone offset in the backtest layer** — the values are already ET.
- Each strategy declares `pub const timeframe: []const u8` (e.g. `"5m"`, `"1d"`). The runtime `engine.symbol` (e.g. `"nq"`, `"gbpusd"`, `"eurusd"`) is set by the CLI from the Symbol question before each run. `engine.tableFor(Strat)` combines them at runtime: `"{symbol}_{timeframe}"`.
- Connection constants (`PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASS`, `PG_DB`) are at the top of `src/data.zig`

**SQLite** (results store):
- Linked against `/opt/homebrew/Cellar/sqlite/3.53.1` (include + lib paths in `build.zig`)
- Database file: `/Users/nawfaldo/Bunker/Quant/backtest/backtest.db` (created on first save, path hardcoded as `DB_PATH` in `src/db.zig`)
- Schema: `backtests` (one row per run, all report fields) + `trades` (one row per trade, FK → backtest)
- `backtests` columns: `id`, `run_at`, `strategy`, `symbol`, `instrument`, `first_ts`, `last_ts`, `total_days`, `initial_bal`, `final_bal`, `net_growth`, `avg_drawdown`, `max_drawdown`, `sharpe`, `total_win`, `total_loss`, `win_rate`, `win_count`, `profit_factor`, `expectancy`, `max_lose_streak`, `avg_contracts`, `min_contracts`, `max_contracts`, `avg_weekly`, `avg_monthly`, `avg_weekly_pct`, `avg_monthly_pct`, `num_trades` — plus the run-parameter columns used to re-run a saved config in `/combine`: `base_size`, `leverage`, `sizing_mode` (0 none / 1 vol target), `vol_target`, `vol_halflife`, `vol_max_mult`, `vol_min_days`, `date_from`, `date_to`, `spread`, `slippage`
- `runMigrations()` in `db.zig` runs each `ALTER TABLE ADD COLUMN` separately (errors ignored) so existing databases are upgraded safely on first use

## Architecture

Single-binary backtester. Every symbol — including NQ — is modeled as a **forex CFD**: **$1/point per 1.0 lot**. `engine.instrument` (`Instrument` enum) only has `.forex`; `lotMult()` always returns `1.0`. `sizeFor()` keeps fractional lots (no whole-contract rounding). `engine.instrumentName()` returns `"forex"` for the report and DB. `engine.pointValue()` returns `1.0` so the CLI can visualize transaction costs. No "Instrument?" prompt is shown for any symbol. Thirteen modules under `src/`:

- `main.zig` — entry point; delegates entirely to `cli.run`.
- `cli.zig` — interactive fullscreen TUI (alternate screen + scroll region). Raw terminal, ISIG always off. Engine runs in a worker thread; main thread polls stdin for Ctrl+C to cancel. Only `/exit` exits the process.
- `engine.zig` — orchestrates fetch → backtest loop → `Result`. **Warm-up buffer:** `pub var warmup_days` (default 90) widens the fetch backward by that many calendar days when `from` is set, so strategies can prime internal state (e.g. OrbBuy's EWMA vol estimate) before the evaluation window. `fetchDataset` computes the widened start via `warmupFrom` (Hinnant civil-date math); `realStartIndex` finds the first bar `>= from`; `backtest` feeds the warm-up bars `[0, start)` to `strat.update()` but discards their signals (no trades opened) and `backtestOn` reports `first_ts`/stats from `start` onward. Set `warmup_days = 0` to disable; no effect when `from` is null. Defines `Bar`, `Signal {long,short,flat,close}`, `Side`, `Trade`, `Result`. `run` = `fetchDataset` + `backtestOn`; the two are split so the tuner can fetch once and replay many times. **Per-run config / concurrency:** the engine's run config (symbol, instrument, from/to, spread, slippage, warmup_days) lives in module globals that `/run` and `/tune` set before calling the global entry points (`run`/`fetchDataset`/`backtestOn`), which snapshot them via `globalConfig()`. For `/combine`, which runs several configs **at the same time on different threads**, there are config-threading variants — `runWith(io, gpa, strat, cfg)`, `fetchDatasetCfg`, `backtestOnCfg` — that take an explicit `engine.Config` and touch no shared mutable state (the table name is built into a stack buffer, not `g_table_buf`; instrument/cost reads go through the `cfg`). So concurrent runs are race-free and deterministic. `data.fetch` is already thread-safe (own socket + per-call buffers), and the allocator is shared safely (as the tuner already relies on). **Drawdown:** `backtest` tracks a running `equity` (realized balance, booked at each trade close) and, at the top of every bar, marks the open position's unrealized PnL at that bar's close to form the live mark-to-market equity; from that it computes `max_drawdown` and `avg_drawdown` (mean of the per-bar drawdown series), returned via `Result`. `report.zig`/`summarize` read these instead of recomputing — so drawdown reflects the whole holding period, not just trade closes. `columnsFor(Strat)` derives the fetch columns. `pub var from`/`pub var to` (`?[]const u8`, default `null` = full history) and `pub var symbol` (`[]const u8`, default `"nq"`) are set by the CLI before each run; `cancelled` is a `std.atomic.Value(bool)` checked each bar (and between tuner combos). `tableFor(comptime Strat)` builds the QuestDB table name at runtime as `"{symbol}_{Strat.timeframe}"`. Transaction costs: `pub var spread` (full bid/ask spread in points, default 0.4) + fixed slippage `pub var slippage` (default 0.2) via `applyFillCost(raw, lots, bar, buying)` applied at every fill — a buy fill pays `spread/2 + slippage` higher, a sell that much lower. Both are **set by the CLI Spread/Slippage questions** before each run (see CLI flow); the `pub var` defaults only apply if those questions are skipped.
- `data.zig` — QuestDB PGWire binary client. Extended-query pipeline (Parse + Bind[binary] + Describe + Execute + Sync). Floats decoded as 8-byte IEEE-754 BE, timestamps as int64 µs since 2000-01-01. Supports AuthOk, Cleartext, MD5. SQL 512B stack buffer; 16 MB heap read buffer.
- `strategy.zig` — thin re-export hub. All other modules import from here so import paths stay stable when strategies are added.
- `strategies/5m_orb.zig` — `Orb` struct (port of Zarattini & Aziz, "Can Day Trading Really Be Profitable?" SSRN 4416622). 5-min bars, RTH only (09:30–16:00 ET). **Bidirectional** — direction is set by the **first** 5-min candle (09:30, completes 09:35): `close > open` → long, `close < open` → short, doji → no trade. Signal emitted on that bar; engine's 1-bar delay opens at the **09:35 open**. Stop = first candle's extreme (low for long, high for short); **fills intrabar** the instant price touches it (checked against the bar's high/low) at the exact stop level — or the bar's open if it gapped past — via the strategy's `exit_fill` field (see Execution model). Take-profit = 10R from the actual entry (the 09:35 open); also fills intrabar at the exact target (or the gapped open). Flatten at 15:55 → fills at 16:00 open (time exit keeps the 1-bar delay, `exit_fill` null). One trade per day; no re-entry. Same sizing model as RthVwap (`sizing_mode` + `vol`).
- `strategies/buy_hold.zig` — `BuyHold` struct. Daily bars (`{symbol}_1d`), long-only benchmark: emits `.long` every bar so the engine opens at the first in-window bar's next open and holds to the final bar's close (one trade). Fixed `contracts` lots, no sizing/leverage. Questions (in `/run` only): symbol, initial balance (default 1000), base lot (default 0.1), date (default 2018-2025). Not tunable — `/tune` rejects it.
- `strategies/rth_vwap.zig` — `RthVwap` struct (port of Zarattini & Aziz's VWAP Trend Trading). 1-min bars (`{symbol}_1m`), RTH only (09:30–16:00 ET). Session VWAP = Σ(HLC₃ × vol)/Σ vol reset each day at the first RTH bar. Continuous long/short: enters on the first RTH bar (close vs VWAP), flips whenever a later close lands on the other side of the VWAP, flattens at 16:00 (emits `.close` at 15:59 → fills at 16:00 open). Same sizing model as Orb (`sizing_mode` + `vol`). Shares the Orb `/run` and `/tune` flows; tunable.
- `sizings/vol_target.zig` — position-sizing module. `Mode` enum (`.none` / `.vol_target`) plus `VolTarget` (Harvey et al. 2018 volatility targeting): EWMA-of-daily-returns vol estimator with `onBar(close, day_changed)` (maintains σ̂) and `multiplier()` (returns `min(target/σ̂, max_mult)`, or 1.0 until `min_days` warm-up). Params: `target`, `halflife`, `max_mult`, `min_days`. Strategies hold a `sizing_mode` + `vol` field and consult `vol.multiplier()` at entry when the mode is `.vol_target`. The CLI sets the params field-by-field (see `parseVolFloat`/`parseVolUint` in `cli.zig`).
- `tune.zig` — `/tune` grid search, **parallelized across all CPU cores**. Used by Orb and RthVwap (both consume `OrbGrid` / `OrbCombo` / `runOrb` / `printReportOrb`; the comptime strategy type is passed in by the CLI). Grid sweeps `base_contracts`; the chosen `sizing_mode` + `vol` params are carried on `OrbGrid` as fixed values (not swept) and applied to every combo. Report columns: Growth, MaxDD, Score, baseCon. (BuyHold has no params, so `/tune` rejects it.)
- `report.zig` — prints equity summary to stdout (`print`) and provides a no-print variant for the tuner (`summarize` → `db.Summary`). Fields printed: Instrument (`engine.instrumentName()`), Initial Balance, Final Balance, Net Growth %, Sharpe Ratio (annualized, Rf=0, daily returns bucketed by exit date via Welford online algorithm), Max Drawdown %, Avg Drawdown %, Max Intraday DD %, Avg Intraday DD %, Total Win $, Total Loss $, Max Daily Loss $ (worst single-day realized PnL, with losing-day count), Avg Daily Loss $ (mean over losing days; daily PnL bucketed by trade exit date — display-only, not persisted), Win Rate % (wins/total trades), Profit Factor (Total Win / |Total Loss|), Expectancy ($ per trade), Max Lose Streak, Avg Contracts (min/max), Total Days, Avg Weekly Gain $ (%), Avg Monthly Gain $ (%). Total days via Julian Day Number subtraction on first/last bar timestamps. **Drawdown is NOT computed here** — it is read from `result.max_drawdown` / `result.avg_drawdown` (all-time trailing) and `result.max_intraday_drawdown` / `result.avg_intraday_drawdown` (intraday trailing: peak resets each calendar day; max = deepest single-day dip, avg = mean of each day's worst dip, one sample per trading day), which the engine measures on the bar-by-bar mark-to-market equity curve (realized balance + open position's unrealized PnL at each bar's close). This captures dips *during* a held position (e.g. a buy-and-hold riding through a crash), which a trade-close-only equity curve would miss entirely. The intraday metrics are display-only (printed, not persisted to `backtest.db`). Marked at bar close, so intrabar extremes are not captured; requires the strategy to fetch the `close` column. `print` returns the full `db.Summary` (all 20 fields). `summarize` (tuner-only, never saved) returns the same struct with zeroes for fields it doesn't compute. All dollar amounts are formatted with thousands separators via `fmtDollars` (e.g. `$5,284.40`, negatives `-$1,234.56`).
- `montecarlo.zig` — Monte Carlo resampling of a backtest's realized per-trade PnL series (consumed by `/montecarlo`). Default method is the **stationary block bootstrap** (Politis & Romano 1994): draws contiguous blocks of trades of random geometric length (expected length = `block_mean`, default `n^(1/3)` floored at 2; per-step jump probability `1/block_mean`) so losing-streak autocorrelation is preserved — plain IID resampling (`mode = .iid`, jump prob 1.0) would destroy it and *understate* drawdowns. PnL is additive in dollars (engine sizing is balance-independent), so each resampled equity curve is `initial_balance + cumulative sampled PnL`. `run(gpa, pnls, initial_balance, cfg)` runs `cfg.sims` (default 10k) sims, each rebuilding the curve and tracking max drawdown (% of running peak, same definition as `report.zig`) and "ruin" (equity ever ≤ `ruin_frac × initial`, default 0.5). Returns a `Result` with p5/p25/p50/p75/p95 percentiles of final balance and max drawdown, `p_profit`, `p_ruin`, and the single realized path's `historical_final`/`historical_max_drawdown` for reference. Pure/no-I/O, clock-seeded by default (`seed = 0`). It is a **sequence-risk / position-sizing** tool, not strategy validation — it assumes the trades represent a real, stationary edge and only reshuffles them. `report.printMonteCarlo` formats the report (reuses `fmtDollars`).
- `db.zig` — SQLite persistence. `db.save(strategy_name, symbol, result, summary, params)` opens `backtest.db`, creates tables if needed, runs `runMigrations()` to add any missing columns to existing databases, then inserts one `backtests` row (all 20 report fields + metadata + the `db.Params` run parameters) and one `trades` row per trade (side, entry/exit ts, entry/exit price, PnL, contracts) in a single transaction. The `instrument` label and the `db.Params` columns (`base_size`, `leverage`, `sizing_mode`, vol params, `date_from`/`date_to`, `spread`, `slippage`) are bound at the end so the earlier column indices are unchanged; a combined run saves an all-zero `Params` (not re-runnable as one strategy, so `base_size` 0 marks it un-pickable in `/combine`). `db.list(entries)` reads all rows from `backtests` and returns them as `BacktestEntry` values (id, strategy name, symbol, instrument, **and the saved run parameters** so `/combine` can re-run them). `db.delete(id)` deletes a backtest row and its associated trades in a transaction. **Monte Carlo:** `db.loadTradePnls(id, out)` reads a saved backtest's exit-ordered trade PnLs and `db.loadInitialBalance(id)` its starting balance (both used by `/montecarlo`); `db.saveMonteCarlo(source_id, strategy, symbol, mc)` persists a `montecarlo.Result` into the separate `montecarlo` table (run_at, source_id + labels, mode, sims, block_mean, num_trades, initial_balance, ruin_frac, historical final/DD, the five final-balance + five max-DD percentiles, p_profit, p_ruin).

## CLI flow

The TUI runs in the terminal's alternate screen (`\x1b[?1049h`) with a fixed scroll region so the command bar stays pinned to the bottom and output scrolls above it. The bar occupies the last `BAR_HEIGHT` rows; a scroll region is set to `[1, term_rows - BAR_HEIGHT]` so printed output naturally accumulates above the bar.

Strategy and symbol selection are shared by `/run` and `/tune`. `g_strategy_id` and `g_symbol_idx` (global usizes) are set from the first two questions and gate subsequent prompt branching. `engine.symbol` (lowercase prefix, e.g. `"nq"`) is written from `SYMBOL_PREFIXES[g_symbol_idx]` just before the engine is started.

```
/run
  → "Strategy?  1. 5M_ORB  2. BUY_HOLD  3. RTH_VWAP"   ← 1 and 3 share the same flow
  → "Symbol?  1. NQ  2. GBPUSD  3. EURUSD"
     (No instrument prompt — every symbol is a $1/pt forex CFD)

  ── if strategy 1 (5M_ORB) or 3 (RTH_VWAP) ──────────────────────────────
  → "Initial balance: $?"
  → "Lots? "
  → "Leverage? (enter for 1) "
  → "Sizing?  1. none  2. vol target"
      ↳ 1 → no further sizing questions (fixed lots)
      ↳ 2 → four separate Enter-able questions, each showing its default:
            "Vol target? (annualized vol, enter for 0.20) "
            "Vol halflife? (EWMA trading days, enter for 20) "
            "Vol max mult? (size multiplier cap, enter for 3.0) "
            "Vol min days? (warm-up days, enter for 30) "
            empty Enter on any one keeps that param's default
  → "Date range? (YYYY-YYYY, enter for 2018-2025) "
      ↳ empty Enter  → sets engine.from="2018-01-01", engine.to="2025-12-31"
      ↳ e.g. "2020-2023" → engine.from="2020-01-01", engine.to="2023-12-31"
  → Spread / Slippage questions (see below)
  → engine runs in worker thread (Ctrl+C cancels)

  ── if strategy 2 (BUY_HOLD) ──────────────────────────────────────────────
  → "Initial balance? (enter for 1000) "   ← Enter accepts default
  → "Base lot? (enter for 0.1) "           ← Enter accepts default
  → "Date range? (enter for 2018-2025) "
  → Spread / Slippage questions (see below)
  → engine runs in worker thread (Ctrl+C cancels)

  ── Spread / Slippage (every /run and /tune flow, after date range) ───────
  → "Spread? (buy fill A->B, enter for <ref>-><buy>) "
  → "Slippage? (buy fill A->B, enter for <ref>-><buy>) "
      • Single-line prompt. Input is a BUY fill price move "A->B"; per-fill
        move = |B-A|. Slippage charges the full value (engine.slippage = |B-A|);
        spread charges half per fill (engine.spread = 2×|B-A|). The shown default
        is the representative buy fill at the default value, so Enter accepts it.
        A plain number (e.g. "0.2", "0" to disable) still works as a fallback.
      • Defaults and the price scale follow the SYMBOL, not the instrument:
        NQ → 4.0 spread / 0.2 slippage on a ~20000 ref price (indexScale());
        forex pairs → 0.0002 / 0.0001 on a ~1.10000 ref. The answered echo
        shows the resulting buy AND sell fills.

  ── shared ────────────────────────────────────────────────────────────────
  → report printed
  → "Save result? (y/n)"  ← single keypress, no Enter
      y → saved to backtest.db (all report fields + symbol stored)
      n → continue
```

`/tune` mirrors `/run` but all tuneable params are entered as comma-separated lists:

```
  ── if strategy 1 (5M_ORB) or 3 (RTH_VWAP) ──────────────────────────────
  strategy → symbol → balance → base contracts (list) → leverage → sizing → [4 vol params] → date range → spread → slippage → grid search
```

The sizing questions are identical to `/run` (four separate Enter-able vol params when vol target is chosen). In `/tune` the vol-target params are **fixed** (single values, not swept) — `base_contracts` remains the only swept dimension. The chosen mode/params apply to every combo.

Lists are capped at `MAX_GRID` (32) values each for numeric params.

`/delete` lists all saved backtests (each row prefixed `#<id>` with strategy name, symbol, instrument) and prompts "Select id:". The entered value is matched against the actual `backtests.id` (linear scan over the listed entries), not a sequential position; an unknown id reports "No backtest with that id." The selected backtest and its trades are removed from `backtest.db` in a transaction.

`/combine` runs **several saved configs together as one portfolio**. It first asks "Initial balance? (enter for 1000)", then lists the pickable saved runs (only rows that carry run parameters — `base_size > 0` — and aren't themselves `COMBINED`; each row shows strategy/symbol/instrument plus a param hint: base size, sizing mode, date range). You pick ids one at a time; an empty Enter (once ≥1 is picked) runs the combination, and auto-runs when every pickable row is chosen. `runCombine` snapshots the picked `BacktestEntry` values into `g_combine_entries`, then an outer worker thread (so the main thread keeps polling stdin for Ctrl+C) runs **every picked config concurrently — one thread per source** (`combineCompute` → `sourceThread` → `runOneSource`), progress shown as "running N strategies together (k done)". Each source builds an explicit `engine.Config` from its saved params (symbol/instrument/from/to/spread/slippage) and calls `engine.runWith` — **no engine globals are mutated**, so the concurrent runs don't race (each also opens its own QuestDB connection via `data.fetch`). `runOneSource` rebuilds the strategy struct (`contracts = base_size × leverage`, `sizing_mode` + `vol`). Position sizing is balance-independent and each run is deterministic, so running concurrently at the combine-level balance reproduces each saved run's exact trades (verified: concurrent results are bit-identical to sequential). The fresh trade logs are merged (exit-sorted) into one synthetic `engine.Result`; `combine.markToMarket` re-fetches each source's bars and marks the whole combined book to market for a real portfolio drawdown (falling back to `combine.realizedDrawdown` if no bars can be fetched). The merged result is printed via `report.print` (instrument label = the shared instrument, or forex/"lots" when sources mix) and offered for save under the generic `COMBINED`/`mixed` label with empty params. **Note:** the dollar figures are real, but every percentage (net growth, drawdown %) is relative to the single entered balance, which should reflect the *total* capital the simultaneous positions require (≈ the sum of the sources' balances), since combine does not re-size to a shared account.

`/montecarlo` resamples a **single** saved backtest's realized trades to show the distribution of outcomes its edge could have produced (see `montecarlo.zig`). It lists all saved backtests exactly like `/delete` (each row `#<id> strategy symbol instrument`, reusing `g_delete_entries`/`g_delete_count`) and prompts "Select id:" (`awaiting_mc_pick`); the id is matched against `backtests.id` by linear scan. `runMonteCarlo` then loads the run's initial balance (`db.loadInitialBalance`) and exit-ordered trade PnLs (`db.loadTradePnls`, capped at 200k), calls `montecarlo.run` with defaults (10k sims, stationary block bootstrap), prints the report via `report.printMonteCarlo`, and offers "Save Monte Carlo result? (y/n)" — `y` persists via `db.saveMonteCarlo` into the `montecarlo` table. Unlike `/run`/`/combine`, MC is **not** threaded (it is sub-millisecond), so it runs synchronously in the prompt handler with no Ctrl+C-during-compute polling; the prompt state itself is still in the Ctrl+C cancel switch.

`initial_balance` lives on the strategy struct, not on the engine. It is set by the CLI before each run and snapshotted into `engine.Result.initial_balance` so `report.zig` and `db.zig` can read it without extra parameters.

## Strategy contract (compile-time duck typing)

`engine.run` takes `strat: anytype` and reads three compile-time declarations off the strategy struct:

```zig
pub const timeframe: []const u8 = "5m";  // timeframe suffix; symbol is engine.symbol (runtime)
pub const columns = .{ .open=bool, .high=bool, .low=bool, .close=bool, .volume=bool };
pub fn update(self: *@This(), bar: engine.Bar, ts: engine.Ts) engine.Signal;
```

`timeframe` is the table suffix (e.g. `"5m"`, `"1d"`). The full table name is built at runtime by `engine.tableFor(Strat)` as `"{engine.symbol}_{timeframe}"`, so switching symbols requires no strategy code change. `columns` controls which OHLCV fields are fetched — `open` is always force-fetched regardless (needed for fill price). `update` is called once per bar in chronological order.

To add a new strategy:
1. Create `src/strategies/<name>.zig` with the three required decls above.
2. Re-export the new type from `src/strategy.zig`.
3. Add it to `STRATEGIES` in `cli.zig` (and a `STRAT_*` index constant). Wire its `/run` question flow: after the shared `awaiting_symbol` state resolves, branch to either the shared Orb states or a dedicated sub-flow (see the `awaiting_bh_*` states for BuyHold), then call `runAndReport(StratType, …, save_name, symbol_label)` — the generic worker/poll/report/save helper. Add any new prompt states to the Ctrl+C cancel switch.
4. If tuneable, add `Grid`/`Combo`/`run`/`printReport` variants in `tune.zig` and the `/tune` wiring; otherwise reject it in `awaiting_tune_strategy` (as BuyHold does).

To add a new symbol: append to `SYMBOL_LABELS` and `SYMBOL_PREFIXES` in `cli.zig` (one line each). The tables must already exist in QuestDB.

## Orb strategy (`strategies/5m_orb.zig`)

Opening-Range Breakout (Zarattini & Aziz, SSRN 4416622), **bidirectional**, 5-min bars. Key rules:

- **Direction**: set by the **first** 5-min candle (09:30 bar, completes at 09:35). `close > open` → long; `close < open` → short; `close == open` (doji) → no trade that day.
- **Entry**: signal emitted on the 09:30 bar; engine's 1-bar fill delay opens at the **open of the 09:35 bar**.
- **Stop loss**: first candle's extreme — `low` for a long, `high` for a short. **Fills intrabar** the instant price touches the level (checked against the bar's `high`/`low`, not the close), at the exact stop price — or the bar's `open` if it gapped past the stop. No 1-bar delay: the strategy sets `exit_fill` and the engine closes on the same bar. Stop is checked before target, so a bar that spans both books the loss.
- **Take profit (10R)**: `risk = |entry − stop|` (where `entry` is the actual 09:35 open). Long TP = `entry + 10 × risk`; short TP = `entry − 10 × risk`. Same intrabar fill model as the stop — fills at the exact target (or the gapped `open`) on the touching bar via `exit_fill`.
- **Time exit**: 15:55 bar → fills at the 16:00 open (1-bar delay).
- One trade per day; no re-entry after a stop-out, target, or time exit.
- **Position sizing** — `sizing_mode` (`sizing.Mode`) selects how each entry is sized; `vol` (`sizing.VolTarget`) holds the vol-target params/state. On every bar the strategy snapshots `base_contracts` (captured on the first `update`) and, when `sizing_mode == .vol_target`, calls `vol.onBar(close, day_changed)` to maintain σ̂. At entry it sets `self.contracts = base_contracts × (vol.multiplier() or 1.0)` just before emitting the signal, because the engine reads `strat.contracts` at signal time. With `.none` the multiplier is 1.0 (fixed lots). Mode + params are chosen via the `/run` and `/tune` "Sizing?" prompts.

## Execution model

The backtest loop in `engine.backtest`:
1. Calls `strat.update(bar, ts)` for each bar.
2. On a `.long`/`.short` signal that differs from the current position, closes the current position (if any) and opens a new one at **the next bar's open** (1-bar fill delay). `.flat` is ignored. `.close` closes without opening a new position.
3. Closes any open position at the final bar's close.

**Exact intrabar exits (opt-in):** by default a `.close` fills at the next bar's open (1-bar delay), same as entries. A strategy can instead request an *exact same-bar* exit price — e.g. an intrabar stop/target that must fill the moment price touches the level — by exposing an optional `exit_fill: ?f64` field. The helper `engine.closeFill` checks for it: when it is non-null on a `.close`, the position is closed at exactly that price on the **current** bar and the field is cleared (so a stale value can't leak into a later close); when null (or the field is absent) it falls back to the next-open path. The `@hasField` check is **comptime**, so strategies without the field (RthVwap, BuyHold) compile to the unchanged next-open behavior. Only 5M_ORB uses it (intrabar stop/TP); its time exit leaves `exit_fill` null to keep the 1-bar delay.

PnL uses `lotMult()` (always `1.0` — $1/point per lot, every symbol) and the per-trade size `pos.contracts`, captured from `sizeFor(strat.contracts)` when the position opens (fractional lots, no rounding). Running `equity` is updated after each closed trade. Each `Trade` carries its own `contracts` (lot size), so PnL, the DB (`trades.contracts`, REAL), and the report all reflect the actual per-trade size. Fill prices are adjusted by `applyFillCost` (half-spread + fixed slippage) before PnL calculation.

## I/O style

Uses Zig 0.16's `std.Io` interface (`Io.File.stdout().writer(io, &buf)`, `addr.connect(io, ...)`). All writers are stack-buffered and must be explicitly `.flush()`ed. Do not use `std.debug.print` or `std.io` (pre-0.16 namespace). Current time is obtained via `std.c.clock_gettime(std.c.CLOCK.REALTIME, &ts)` — `std.time.timestamp()` does not exist in 0.16.

## CLI input handling

- ISIG is permanently disabled in raw mode — Ctrl+C produces byte `3` in the read buffer, never a POSIX signal. This prevents `zig build` from being killed.
- While the engine runs, the main thread polls stdin via `posix.poll` (10 ms timeout) and stores `engine.cancelled` atomically on byte `3`.
- Ctrl+C in any `awaiting_*` prompt state cancels the prompt and returns to idle. The states cover the full `/run` flow (`awaiting_strategy`, `awaiting_symbol`, `awaiting_balance`, `awaiting_base_contracts`, `awaiting_leverage`, `awaiting_sizing`, `awaiting_vol_target`/`_halflife`/`_maxmult`/`_mindays`, `awaiting_from`, `awaiting_spread`, `awaiting_slippage`), the BuyHold sub-flow (`awaiting_bh_balance`/`_lots`/`_from`/`_spread`/`_slippage`), the `/tune` equivalents (`awaiting_tune_strategy`, `awaiting_tune_symbol`, `awaiting_tune_balance`, …, `awaiting_tune_from`, `awaiting_tune_spread`, `awaiting_tune_slippage`), and `awaiting_delete`. When adding a prompt state, also add it to the Ctrl+C cancel switch in `cli.zig`.
- `/exit` is the only way to exit the process.
- Tab completes the first matching command when the input starts with `/`.
