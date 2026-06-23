const std = @import("std");
const engine = @import("engine.zig");
const montecarlo = @import("montecarlo.zig");

const c = @cImport(@cInclude("sqlite3.h"));

const DB_PATH = "/Users/nawfaldo/Bunker/Quant/backtest/backtest.db";

const SCHEMA =
    \\CREATE TABLE IF NOT EXISTS backtests (
    \\  id               INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  run_at           TEXT    NOT NULL,
    \\  strategy         TEXT    NOT NULL,
    \\  symbol           TEXT    NOT NULL DEFAULT 'nq',
    \\  instrument       TEXT    NOT NULL DEFAULT 'nq mini',
    \\  first_ts         TEXT    NOT NULL,
    \\  last_ts          TEXT    NOT NULL,
    \\  total_days       INTEGER NOT NULL,
    \\  initial_bal      REAL    NOT NULL,
    \\  final_bal        REAL    NOT NULL,
    \\  net_growth       REAL    NOT NULL,
    \\  avg_drawdown     REAL    NOT NULL DEFAULT 0,
    \\  max_drawdown     REAL    NOT NULL,
    \\  sharpe           REAL    NOT NULL DEFAULT 0,
    \\  total_win        REAL    NOT NULL DEFAULT 0,
    \\  total_loss       REAL    NOT NULL DEFAULT 0,
    \\  win_rate         REAL    NOT NULL DEFAULT 0,
    \\  win_count        INTEGER NOT NULL DEFAULT 0,
    \\  profit_factor    REAL    NOT NULL DEFAULT 0,
    \\  expectancy       REAL    NOT NULL DEFAULT 0,
    \\  max_lose_streak  INTEGER NOT NULL DEFAULT 0,
    \\  avg_size         REAL    NOT NULL DEFAULT 0,
    \\  min_size         REAL    NOT NULL DEFAULT 0,
    \\  max_size         REAL    NOT NULL DEFAULT 0,
    \\  avg_weekly       REAL    NOT NULL DEFAULT 0,
    \\  avg_monthly      REAL    NOT NULL DEFAULT 0,
    \\  avg_weekly_pct                REAL    NOT NULL DEFAULT 0,
    \\  avg_monthly_pct               REAL    NOT NULL DEFAULT 0,
    \\  num_trades                    INTEGER NOT NULL,
    \\  max_drawdown_dollars          REAL    NOT NULL DEFAULT 0,
    \\  max_drawdown_peak_date        TEXT    NOT NULL DEFAULT '',
    \\  max_drawdown_trough_date      TEXT    NOT NULL DEFAULT '',
    \\  avg_drawdown_dollars          REAL    NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown         REAL    NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown_dollars REAL    NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown_date    TEXT    NOT NULL DEFAULT '',
    \\  avg_intraday_drawdown         REAL    NOT NULL DEFAULT 0,
    \\  avg_intraday_drawdown_dollars REAL    NOT NULL DEFAULT 0,
    \\  max_daily_loss                REAL    NOT NULL DEFAULT 0,
    \\  max_daily_loss_date           TEXT    NOT NULL DEFAULT '',
    \\  avg_daily_loss                REAL    NOT NULL DEFAULT 0,
    \\  base_size                     REAL    NOT NULL DEFAULT 0,
    \\  leverage                      REAL    NOT NULL DEFAULT 1,
    \\  sizing_mode                   INTEGER NOT NULL DEFAULT 0,
    \\  vol_target                    REAL    NOT NULL DEFAULT 0,
    \\  vol_halflife                  REAL    NOT NULL DEFAULT 0,
    \\  vol_max_mult                  REAL    NOT NULL DEFAULT 0,
    \\  vol_min_days                  INTEGER NOT NULL DEFAULT 0,
    \\  date_from                     TEXT    NOT NULL DEFAULT '',
    \\  date_to                       TEXT    NOT NULL DEFAULT '',
    \\  spread                        REAL    NOT NULL DEFAULT 0,
    \\  slippage                      REAL    NOT NULL DEFAULT 0
    \\);
    \\CREATE TABLE IF NOT EXISTS trades (
    \\  id           INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  backtest_id  INTEGER NOT NULL REFERENCES backtests(id),
    \\  side         TEXT    NOT NULL,
    \\  entry_ts     TEXT    NOT NULL,
    \\  exit_ts      TEXT    NOT NULL,
    \\  entry_price  REAL    NOT NULL,
    \\  exit_price   REAL    NOT NULL,
    \\  pnl          REAL    NOT NULL,
    \\  contracts    REAL    NOT NULL
    \\);
    \\CREATE TABLE IF NOT EXISTS montecarlo (
    \\  id               INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  run_at           TEXT    NOT NULL,
    \\  source_id        INTEGER NOT NULL,
    \\  source_strategy  TEXT    NOT NULL,
    \\  source_symbol    TEXT    NOT NULL,
    \\  mode             TEXT    NOT NULL,
    \\  sims             INTEGER NOT NULL,
    \\  block_mean       REAL    NOT NULL,
    \\  num_trades       INTEGER NOT NULL,
    \\  initial_balance  REAL    NOT NULL,
    \\  ruin_frac        REAL    NOT NULL,
    \\  hist_final       REAL    NOT NULL,
    \\  hist_max_dd      REAL    NOT NULL,
    \\  final_p5         REAL    NOT NULL,
    \\  final_p25        REAL    NOT NULL,
    \\  final_p50        REAL    NOT NULL,
    \\  final_p75        REAL    NOT NULL,
    \\  final_p95        REAL    NOT NULL,
    \\  maxdd_p5         REAL    NOT NULL,
    \\  maxdd_p25        REAL    NOT NULL,
    \\  maxdd_p50        REAL    NOT NULL,
    \\  maxdd_p75        REAL    NOT NULL,
    \\  maxdd_p95        REAL    NOT NULL,
    \\  p_profit         REAL    NOT NULL,
    \\  p_ruin           REAL    NOT NULL
    \\);
    \\CREATE TABLE IF NOT EXISTS montecarlo_paths (
    \\  id        INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  mc_id     INTEGER NOT NULL REFERENCES montecarlo(id),
    \\  path_idx  INTEGER NOT NULL,
    \\  step      INTEGER NOT NULL,
    \\  equity    REAL    NOT NULL
    \\);
    \\CREATE INDEX IF NOT EXISTS idx_mc_paths ON montecarlo_paths (mc_id, path_idx, step);
;

// Each migration is run separately so a "duplicate column" failure on an already-applied
// one does not block the rest.
fn runMigrations(db_ptr: ?*c.sqlite3) void {
    const ms = [_][*c]const u8{
        "ALTER TABLE backtests ADD COLUMN symbol          TEXT    NOT NULL DEFAULT 'nq';",
        "ALTER TABLE backtests ADD COLUMN instrument      TEXT    NOT NULL DEFAULT 'nq mini';",
        "ALTER TABLE backtests ADD COLUMN avg_drawdown    REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN sharpe          REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN total_win       REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN total_loss      REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN win_rate        REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN win_count       INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN profit_factor   REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN expectancy      REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_lose_streak INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN avg_contracts   REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN min_contracts   REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_contracts   REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests RENAME COLUMN avg_contracts TO avg_size;",
        "ALTER TABLE backtests RENAME COLUMN min_contracts TO min_size;",
        "ALTER TABLE backtests RENAME COLUMN max_contracts TO max_size;",
        "ALTER TABLE backtests ADD COLUMN avg_weekly      REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN avg_monthly     REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN avg_weekly_pct  REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN avg_monthly_pct REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_drawdown_dollars          REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_drawdown_peak_date        TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN max_drawdown_trough_date      TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN avg_drawdown_dollars          REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_intraday_drawdown         REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_intraday_drawdown_dollars REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_intraday_drawdown_date    TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN avg_intraday_drawdown         REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN avg_intraday_drawdown_dollars REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_daily_loss                REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN max_daily_loss_date           TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN avg_daily_loss                REAL    NOT NULL DEFAULT 0;",
        // Run parameters — persisted so /combine can re-run a saved config.
        "ALTER TABLE backtests ADD COLUMN base_size       REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN leverage        REAL    NOT NULL DEFAULT 1;",
        "ALTER TABLE backtests ADD COLUMN sizing_mode     INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN vol_target      REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN vol_halflife    REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN vol_max_mult    REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN vol_min_days    INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN date_from       TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN date_to         TEXT    NOT NULL DEFAULT '';",
        "ALTER TABLE backtests ADD COLUMN spread          REAL    NOT NULL DEFAULT 0;",
        "ALTER TABLE backtests ADD COLUMN slippage        REAL    NOT NULL DEFAULT 0;",
    };
    for (ms) |m| _ = c.sqlite3_exec(db_ptr, m, null, null, null);
}

pub const Summary = struct {
    final_balance: f64,
    net_growth: f64,
    avg_drawdown: f64,
    max_drawdown: f64,
    total_days: i64,
    sharpe: f64,
    total_win: f64,
    total_loss: f64,
    win_rate: f64,
    win_count: usize,
    profit_factor: f64,
    expectancy: f64,
    max_lose_streak: usize,
    avg_size: f64,
    min_size: f64,
    max_size: f64,
    avg_weekly: f64,
    avg_monthly: f64,
    avg_weekly_pct: f64,
    avg_monthly_pct: f64,
    max_drawdown_dollars: f64,
    max_drawdown_peak_date: [10]u8,
    max_drawdown_trough_date: [10]u8,
    avg_drawdown_dollars: f64,
    max_intraday_drawdown: f64,
    max_intraday_drawdown_dollars: f64,
    max_intraday_drawdown_date: [10]u8,
    avg_intraday_drawdown: f64,
    avg_intraday_drawdown_dollars: f64,
    max_daily_loss: f64,
    max_daily_loss_date: [10]u8,
    avg_daily_loss: f64,
};

// The run's input parameters — persisted alongside the report so a saved config
// can be re-run later (used by /combine). `sizing_mode` is 0 = none, 1 = vol
// target; `date_from`/`date_to` are "YYYY-MM-DD" (empty = full history). Combined
// runs save an all-zero Params (they are not re-runnable as a single strategy).
pub const Params = struct {
    base_size: f64 = 0,
    leverage: f64 = 1,
    sizing_mode: i64 = 0,
    vol_target: f64 = 0,
    vol_halflife: f64 = 0,
    vol_max_mult: f64 = 0,
    vol_min_days: i64 = 0,
    date_from: []const u8 = "",
    date_to: []const u8 = "",
    spread: f64 = 0,
    slippage: f64 = 0,
};

pub fn save(strategy_name: []const u8, symbol: []const u8, result: engine.Result, summary: Summary, params: Params) !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db);

    // WAL mode: faster concurrent writes, still safe.
    _ = c.sqlite3_exec(db, "PRAGMA journal_mode=WAL;", null, null, null);

    if (c.sqlite3_exec(db, SCHEMA, null, null, null) != c.SQLITE_OK) return error.DbSchema;
    runMigrations(db);
    if (c.sqlite3_exec(db, "BEGIN;", null, null, null) != c.SQLITE_OK) return error.DbBegin;
    errdefer _ = c.sqlite3_exec(db, "ROLLBACK;", null, null, null);

    // Current UTC time as ISO-8601.
    var ts_buf: [32]u8 = undefined;
    const run_at = fmtNow(&ts_buf);

    // Null-terminate strategy name, symbol, and timestamps for C strings.
    var strat_buf: [64]u8 = undefined;
    const strat_z = zStr(&strat_buf, strategy_name);
    var sym_buf: [32]u8 = undefined;
    const sym_z = zStr(&sym_buf, symbol);
    var first_buf: [17]u8 = undefined;
    var last_buf: [17]u8 = undefined;
    const first_z = zTs(&first_buf, result.first_ts);
    const last_z = zTs(&last_buf, result.last_ts);

    const insert_run =
        \\INSERT INTO backtests
        \\  (run_at,strategy,symbol,first_ts,last_ts,
        \\   total_days,initial_bal,final_bal,net_growth,avg_drawdown,max_drawdown,
        \\   sharpe,total_win,total_loss,win_rate,win_count,
        \\   profit_factor,expectancy,max_lose_streak,
        \\   avg_size,min_size,max_size,
        \\   avg_weekly,avg_monthly,avg_weekly_pct,avg_monthly_pct,
        \\   num_trades,instrument,
        \\   max_drawdown_dollars,max_drawdown_peak_date,max_drawdown_trough_date,
        \\   avg_drawdown_dollars,
        \\   max_intraday_drawdown,max_intraday_drawdown_dollars,max_intraday_drawdown_date,
        \\   avg_intraday_drawdown,avg_intraday_drawdown_dollars,
        \\   max_daily_loss,max_daily_loss_date,avg_daily_loss,
        \\   base_size,leverage,sizing_mode,vol_target,vol_halflife,vol_max_mult,vol_min_days,
        \\   date_from,date_to,spread,slippage)
        \\VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
        \\        ?,?,?,?,?,?,?,?,?,?,?);
    ;
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, insert_run, -1, &stmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_text(stmt, 1, run_at.ptr, @intCast(run_at.len), c.SQLITE_STATIC);
    // Bind length excludes the trailing NUL the z* helpers append.
    _ = c.sqlite3_bind_text(stmt, 2, strat_z.ptr, @intCast(strat_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_text(stmt, 3, sym_z.ptr, @intCast(sym_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_text(stmt, 4, first_z.ptr, @intCast(first_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_text(stmt, 5, last_z.ptr, @intCast(last_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_int64(stmt, 6, summary.total_days);
    _ = c.sqlite3_bind_double(stmt, 7, result.initial_balance);
    _ = c.sqlite3_bind_double(stmt, 8, summary.final_balance);
    _ = c.sqlite3_bind_double(stmt, 9, summary.net_growth);
    _ = c.sqlite3_bind_double(stmt, 10, summary.avg_drawdown);
    _ = c.sqlite3_bind_double(stmt, 11, summary.max_drawdown);
    _ = c.sqlite3_bind_double(stmt, 12, summary.sharpe);
    _ = c.sqlite3_bind_double(stmt, 13, summary.total_win);
    _ = c.sqlite3_bind_double(stmt, 14, summary.total_loss);
    _ = c.sqlite3_bind_double(stmt, 15, summary.win_rate);
    _ = c.sqlite3_bind_int64(stmt, 16, @intCast(summary.win_count));
    _ = c.sqlite3_bind_double(stmt, 17, summary.profit_factor);
    _ = c.sqlite3_bind_double(stmt, 18, summary.expectancy);
    _ = c.sqlite3_bind_int64(stmt, 19, @intCast(summary.max_lose_streak));
    _ = c.sqlite3_bind_double(stmt, 20, summary.avg_size);
    _ = c.sqlite3_bind_double(stmt, 21, summary.min_size);
    _ = c.sqlite3_bind_double(stmt, 22, summary.max_size);
    _ = c.sqlite3_bind_double(stmt, 23, summary.avg_weekly);
    _ = c.sqlite3_bind_double(stmt, 24, summary.avg_monthly);
    _ = c.sqlite3_bind_double(stmt, 25, summary.avg_weekly_pct);
    _ = c.sqlite3_bind_double(stmt, 26, summary.avg_monthly_pct);
    _ = c.sqlite3_bind_int64(stmt, 27, @intCast(result.trades.len));
    const inst = engine.instrumentName();
    _ = c.sqlite3_bind_text(stmt, 28, inst.ptr, @intCast(inst.len), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_double(stmt, 29, summary.max_drawdown_dollars);
    var peak_buf: [11]u8 = undefined;
    const peak_z = zDate(&peak_buf, summary.max_drawdown_peak_date);
    _ = c.sqlite3_bind_text(stmt, 30, peak_z.ptr, @intCast(peak_z.len - 1), c.SQLITE_STATIC);
    var trough_buf: [11]u8 = undefined;
    const trough_z = zDate(&trough_buf, summary.max_drawdown_trough_date);
    _ = c.sqlite3_bind_text(stmt, 31, trough_z.ptr, @intCast(trough_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_double(stmt, 32, summary.avg_drawdown_dollars);
    _ = c.sqlite3_bind_double(stmt, 33, summary.max_intraday_drawdown);
    _ = c.sqlite3_bind_double(stmt, 34, summary.max_intraday_drawdown_dollars);
    var intra_buf: [11]u8 = undefined;
    const intra_z = zDate(&intra_buf, summary.max_intraday_drawdown_date);
    _ = c.sqlite3_bind_text(stmt, 35, intra_z.ptr, @intCast(intra_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_double(stmt, 36, summary.avg_intraday_drawdown);
    _ = c.sqlite3_bind_double(stmt, 37, summary.avg_intraday_drawdown_dollars);
    _ = c.sqlite3_bind_double(stmt, 38, summary.max_daily_loss);
    var dloss_buf: [11]u8 = undefined;
    const dloss_z = zDate(&dloss_buf, summary.max_daily_loss_date);
    _ = c.sqlite3_bind_text(stmt, 39, dloss_z.ptr, @intCast(dloss_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_double(stmt, 40, summary.avg_daily_loss);

    // Run parameters (indices 41–51), appended so the indices above are unchanged.
    _ = c.sqlite3_bind_double(stmt, 41, params.base_size);
    _ = c.sqlite3_bind_double(stmt, 42, params.leverage);
    _ = c.sqlite3_bind_int64(stmt, 43, params.sizing_mode);
    _ = c.sqlite3_bind_double(stmt, 44, params.vol_target);
    _ = c.sqlite3_bind_double(stmt, 45, params.vol_halflife);
    _ = c.sqlite3_bind_double(stmt, 46, params.vol_max_mult);
    _ = c.sqlite3_bind_int64(stmt, 47, params.vol_min_days);
    var dfrom_buf: [16]u8 = undefined;
    const dfrom_z = zStr(&dfrom_buf, params.date_from);
    _ = c.sqlite3_bind_text(stmt, 48, dfrom_z.ptr, @intCast(dfrom_z.len - 1), c.SQLITE_STATIC);
    var dto_buf: [16]u8 = undefined;
    const dto_z = zStr(&dto_buf, params.date_to);
    _ = c.sqlite3_bind_text(stmt, 49, dto_z.ptr, @intCast(dto_z.len - 1), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_double(stmt, 50, params.spread);
    _ = c.sqlite3_bind_double(stmt, 51, params.slippage);

    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.DbInsertRun;
    const backtest_id = c.sqlite3_last_insert_rowid(db);

    const insert_trade =
        \\INSERT INTO trades (backtest_id,side,entry_ts,exit_ts,entry_price,exit_price,pnl,contracts)
        \\VALUES (?,?,?,?,?,?,?,?);
    ;
    var tstmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, insert_trade, -1, &tstmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(tstmt);

    var entry_buf: [17]u8 = undefined;
    var exit_buf: [17]u8 = undefined;

    for (result.trades) |t| {
        _ = c.sqlite3_reset(tstmt);
        const side_str: []const u8 = if (t.side == .long) "long" else "short";
        const ets = zTs(&entry_buf, t.entry_ts);
        const xts = zTs(&exit_buf, t.exit_ts);
        _ = c.sqlite3_bind_int64(tstmt, 1, backtest_id);
        _ = c.sqlite3_bind_text(tstmt, 2, side_str.ptr, @intCast(side_str.len), c.SQLITE_STATIC);
        _ = c.sqlite3_bind_text(tstmt, 3, ets.ptr, @intCast(ets.len - 1), c.SQLITE_STATIC);
        _ = c.sqlite3_bind_text(tstmt, 4, xts.ptr, @intCast(xts.len - 1), c.SQLITE_STATIC);
        _ = c.sqlite3_bind_double(tstmt, 5, t.entry_price);
        _ = c.sqlite3_bind_double(tstmt, 6, t.exit_price);
        _ = c.sqlite3_bind_double(tstmt, 7, t.pnl);
        _ = c.sqlite3_bind_double(tstmt, 8, t.contracts); // vol-managed size
        if (c.sqlite3_step(tstmt) != c.SQLITE_DONE) return error.DbInsertTrade;
    }

    if (c.sqlite3_exec(db, "COMMIT;", null, null, null) != c.SQLITE_OK) return error.DbCommit;
}

pub const BacktestEntry = struct {
    id: i64,
    strategy: [64]u8 = [_]u8{0} ** 64,
    strategy_len: usize = 0,
    symbol: [32]u8 = [_]u8{0} ** 32,
    symbol_len: usize = 0,
    instrument: [32]u8 = [_]u8{0} ** 32,
    instrument_len: usize = 0,
    // Saved run parameters (for /combine re-run). Zero base_size means the row
    // predates parameter persistence and cannot be re-run.
    base_size: f64 = 0,
    leverage: f64 = 1,
    sizing_mode: i64 = 0,
    vol_target: f64 = 0,
    vol_halflife: f64 = 0,
    vol_max_mult: f64 = 0,
    vol_min_days: i64 = 0,
    date_from: [10]u8 = [_]u8{0} ** 10,
    date_from_len: usize = 0,
    date_to: [10]u8 = [_]u8{0} ** 10,
    date_to_len: usize = 0,
    spread: f64 = 0,
    slippage: f64 = 0,
};

pub fn list(entries: []BacktestEntry) !usize {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db_ptr) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db_ptr);

    // Create tables if needed so SELECT doesn't fail on a fresh db.
    _ = c.sqlite3_exec(db_ptr, SCHEMA, null, null, null);
    runMigrations(db_ptr);

    const sql =
        \\SELECT id, strategy, symbol, instrument,
        \\       base_size, leverage, sizing_mode, vol_target, vol_halflife,
        \\       vol_max_mult, vol_min_days, date_from, date_to, spread, slippage
        \\FROM backtests ORDER BY id;
    ;
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < entries.len) {
        var e = &entries[count];
        e.* = .{ .id = c.sqlite3_column_int64(stmt, 0) };
        copyTextCol(&e.strategy, &e.strategy_len, stmt, 1);
        copyTextCol(&e.symbol, &e.symbol_len, stmt, 2);
        copyTextCol(&e.instrument, &e.instrument_len, stmt, 3);
        e.base_size = c.sqlite3_column_double(stmt, 4);
        e.leverage = c.sqlite3_column_double(stmt, 5);
        e.sizing_mode = c.sqlite3_column_int64(stmt, 6);
        e.vol_target = c.sqlite3_column_double(stmt, 7);
        e.vol_halflife = c.sqlite3_column_double(stmt, 8);
        e.vol_max_mult = c.sqlite3_column_double(stmt, 9);
        e.vol_min_days = c.sqlite3_column_int64(stmt, 10);
        copyTextCol(&e.date_from, &e.date_from_len, stmt, 11);
        copyTextCol(&e.date_to, &e.date_to_len, stmt, 12);
        e.spread = c.sqlite3_column_double(stmt, 13);
        e.slippage = c.sqlite3_column_double(stmt, 14);
        count += 1;
    }
    return count;
}

// Copy a TEXT column into a fixed buffer, setting `*len` to the trimmed length.
fn copyTextCol(buf: []u8, len: *usize, stmt: ?*c.sqlite3_stmt, col: c_int) void {
    const ptr = c.sqlite3_column_text(stmt, col);
    if (ptr == null) return;
    const blen: usize = @intCast(c.sqlite3_column_bytes(stmt, col));
    const n = @min(blen, buf.len);
    @memcpy(buf[0..n], ptr[0..n]);
    len.* = trimLen(buf[0..n]);
}

pub fn delete(id: i64) !void {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db_ptr) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db_ptr);

    _ = c.sqlite3_exec(db_ptr, "PRAGMA journal_mode=WAL;", null, null, null);
    if (c.sqlite3_exec(db_ptr, "BEGIN;", null, null, null) != c.SQLITE_OK) return error.DbBegin;
    errdefer _ = c.sqlite3_exec(db_ptr, "ROLLBACK;", null, null, null);

    const del_trades = "DELETE FROM trades WHERE backtest_id = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, del_trades, -1, &stmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt);
    _ = c.sqlite3_bind_int64(stmt, 1, id);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.DbDelete;

    const del_bt = "DELETE FROM backtests WHERE id = ?;";
    var stmt2: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, del_bt, -1, &stmt2, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt2);
    _ = c.sqlite3_bind_int64(stmt2, 1, id);
    if (c.sqlite3_step(stmt2) != c.SQLITE_DONE) return error.DbDelete;

    if (c.sqlite3_exec(db_ptr, "COMMIT;", null, null, null) != c.SQLITE_OK) return error.DbCommit;
}

// Load the realized per-trade PnL series for a saved backtest into `out`
// (exit-ordered), returning the number of trades read. Used by /montecarlo.
pub fn loadTradePnls(id: i64, out: []f64) !usize {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db_ptr) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db_ptr);

    const sql = "SELECT pnl FROM trades WHERE backtest_id = ? ORDER BY exit_ts, id;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, sql, -1, &stmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt);
    _ = c.sqlite3_bind_int64(stmt, 1, id);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        out[count] = c.sqlite3_column_double(stmt, 0);
        count += 1;
    }
    return count;
}

// Read the initial balance recorded for a saved backtest.
pub fn loadInitialBalance(id: i64) !f64 {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db_ptr) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db_ptr);

    const sql = "SELECT initial_bal FROM backtests WHERE id = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, sql, -1, &stmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt);
    _ = c.sqlite3_bind_int64(stmt, 1, id);

    if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return error.NotFound;
    return c.sqlite3_column_double(stmt, 0);
}

// Persist a Monte Carlo result (one row in `montecarlo`, linked to its source
// backtest by `source_id`).
// Remove any Monte Carlo rows (and their sample paths) for a source backtest,
// so a re-run can replace them. Best-effort within the caller's transaction.
fn deleteExistingMonteCarlo(db_ptr: ?*c.sqlite3, source_id: i64) void {
    const stmts = [_][*c]const u8{
        "DELETE FROM montecarlo_paths WHERE mc_id IN (SELECT id FROM montecarlo WHERE source_id = ?);",
        "DELETE FROM montecarlo WHERE source_id = ?;",
    };
    for (stmts) |sql| {
        var st: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, sql, -1, &st, null) == c.SQLITE_OK) {
            _ = c.sqlite3_bind_int64(st, 1, source_id);
            _ = c.sqlite3_step(st);
        }
        _ = c.sqlite3_finalize(st);
    }
}

// Returns the new `montecarlo` row id. If `paths` is given, its sample equity
// curves are written to `montecarlo_paths` in the same transaction. Any prior
// Monte Carlo for the same source backtest is replaced (see deleteExistingMonteCarlo).
pub fn saveMonteCarlo(source_id: i64, source_strategy: []const u8, source_symbol: []const u8, mc: montecarlo.Result, paths: ?montecarlo.Paths) !i64 {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db_ptr) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db_ptr);

    _ = c.sqlite3_exec(db_ptr, SCHEMA, null, null, null);
    runMigrations(db_ptr);
    _ = c.sqlite3_exec(db_ptr, "BEGIN;", null, null, null);

    // One Monte Carlo per source backtest: re-running /montecarlo on the same id
    // replaces the previous result (and its sample paths) instead of accumulating
    // rows. Paths are deleted first since SQLite does not cascade by default.
    deleteExistingMonteCarlo(db_ptr, source_id);

    const sql =
        \\INSERT INTO montecarlo (
        \\  run_at, source_id, source_strategy, source_symbol, mode, sims,
        \\  block_mean, num_trades, initial_balance, ruin_frac, hist_final, hist_max_dd,
        \\  final_p5, final_p25, final_p50, final_p75, final_p95,
        \\  maxdd_p5, maxdd_p25, maxdd_p50, maxdd_p75, maxdd_p95,
        \\  p_profit, p_ruin
        \\) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
    ;
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_ptr, sql, -1, &stmt, null) != c.SQLITE_OK) return error.DbPrepare;
    defer _ = c.sqlite3_finalize(stmt);

    var nowbuf: [32]u8 = undefined;
    const now = fmtNow(&nowbuf);
    const mode_str: []const u8 = switch (mc.mode) {
        .stationary_block => "stationary_block",
        .iid => "iid",
    };
    _ = c.sqlite3_bind_text(stmt, 1, now.ptr, @intCast(now.len), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_int64(stmt, 2, source_id);
    _ = c.sqlite3_bind_text(stmt, 3, source_strategy.ptr, @intCast(source_strategy.len), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_text(stmt, 4, source_symbol.ptr, @intCast(source_symbol.len), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_text(stmt, 5, mode_str.ptr, @intCast(mode_str.len), c.SQLITE_STATIC);
    _ = c.sqlite3_bind_int64(stmt, 6, @intCast(mc.sims));
    _ = c.sqlite3_bind_double(stmt, 7, mc.block_mean);
    _ = c.sqlite3_bind_int64(stmt, 8, @intCast(mc.num_trades));
    _ = c.sqlite3_bind_double(stmt, 9, mc.initial_balance);
    _ = c.sqlite3_bind_double(stmt, 10, mc.ruin_frac);
    _ = c.sqlite3_bind_double(stmt, 11, mc.historical_final);
    _ = c.sqlite3_bind_double(stmt, 12, mc.historical_max_drawdown);
    _ = c.sqlite3_bind_double(stmt, 13, mc.final_balance[0]);
    _ = c.sqlite3_bind_double(stmt, 14, mc.final_balance[1]);
    _ = c.sqlite3_bind_double(stmt, 15, mc.final_balance[2]);
    _ = c.sqlite3_bind_double(stmt, 16, mc.final_balance[3]);
    _ = c.sqlite3_bind_double(stmt, 17, mc.final_balance[4]);
    _ = c.sqlite3_bind_double(stmt, 18, mc.max_drawdown[0]);
    _ = c.sqlite3_bind_double(stmt, 19, mc.max_drawdown[1]);
    _ = c.sqlite3_bind_double(stmt, 20, mc.max_drawdown[2]);
    _ = c.sqlite3_bind_double(stmt, 21, mc.max_drawdown[3]);
    _ = c.sqlite3_bind_double(stmt, 22, mc.max_drawdown[4]);
    _ = c.sqlite3_bind_double(stmt, 23, mc.p_profit);
    _ = c.sqlite3_bind_double(stmt, 24, mc.p_ruin);

    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.DbInsert;
    const mc_id = c.sqlite3_last_insert_rowid(db_ptr);

    if (paths) |pth| {
        const psql = "INSERT INTO montecarlo_paths (mc_id, path_idx, step, equity) VALUES (?,?,?,?);";
        var pstmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, psql, -1, &pstmt, null) != c.SQLITE_OK) return error.DbPrepare;
        defer _ = c.sqlite3_finalize(pstmt);
        for (0..pth.n_paths) |pi| {
            for (0..pth.n_steps) |st| {
                _ = c.sqlite3_bind_int64(pstmt, 1, mc_id);
                _ = c.sqlite3_bind_int64(pstmt, 2, @intCast(pi));
                _ = c.sqlite3_bind_int64(pstmt, 3, @intCast(pth.steps[st]));
                _ = c.sqlite3_bind_double(pstmt, 4, pth.equity[pi * pth.n_steps + st]);
                if (c.sqlite3_step(pstmt) != c.SQLITE_DONE) return error.DbInsert;
                _ = c.sqlite3_reset(pstmt);
            }
        }
    }

    _ = c.sqlite3_exec(db_ptr, "COMMIT;", null, null, null);
    return mc_id;
}

// Length of `s` with trailing NUL / space padding stripped. Older rows were
// written with a trailing NUL baked into TEXT columns (see zStr), so reads must
// trim it before the value is compared (e.g. symbol → table lookup in /combine).
fn trimLen(s: []const u8) usize {
    var n = s.len;
    while (n > 0 and (s[n - 1] == 0 or s[n - 1] == ' ')) n -= 1;
    return n;
}

// Null-terminate a Zig slice into a fixed buffer for C APIs.
fn zStr(buf: []u8, s: []const u8) []u8 {
    const n = @min(s.len, buf.len - 1);
    @memcpy(buf[0..n], s[0..n]);
    buf[n] = 0;
    return buf[0 .. n + 1];
}

// Null-terminate a [10]u8 date ("YYYY-MM-DD") for C APIs.
fn zDate(buf: *[11]u8, d: [10]u8) []u8 {
    @memcpy(buf[0..10], &d);
    buf[10] = 0;
    return buf[0..11];
}

// Null-terminate a [16]u8 timestamp.
fn zTs(buf: *[17]u8, ts: [16]u8) []u8 {
    @memcpy(buf[0..16], &ts);
    buf[16] = 0;
    return buf[0..17];
}

fn fmtNow(buf: *[32]u8) []u8 {
    var ts = std.c.timespec{ .sec = 0, .nsec = 0 };
    _ = std.c.clock_gettime(std.c.CLOCK.REALTIME, &ts);
    const secs: u64 = @intCast(ts.sec);
    const es = std.time.epoch.EpochSeconds{ .secs = secs };
    const ed = es.getEpochDay();
    const ymd = ed.calculateYearDay();
    const md = ymd.calculateMonthDay();
    const ds = es.getDaySeconds();
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2} {d:0>2}:{d:0>2}:{d:0>2}", .{
        ymd.year,
        md.month.numeric(),
        @as(u8, md.day_index) + 1,
        ds.getHoursIntoDay(),
        ds.getMinutesIntoHour(),
        ds.getSecondsIntoMinute(),
    }) catch buf[0..0];
}
