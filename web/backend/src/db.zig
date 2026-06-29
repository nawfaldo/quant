const std = @import("std");
const builtin = @import("builtin");
const c = @cImport(@cInclude("sqlite3.h"));

// ── Database paths ─────────────────────────────────────────────────────────────
// app.db — owned by the web backend process; holds settings, march tables, AND
// the backtests/trades/montecarlo the web app runs and saves itself. The
// standalone CLI backtester's backtest.db is no longer read here: the web app is
// self-contained (it runs backtests via bt_run.zig and saves them into app.db).

const APP_DB_PATH = switch (builtin.os.tag) {
    .macos   => "/Users/nawfaldo/Bunker/Quant/web/backend/app.db",
    .windows => "C:/Users/JawirGaming66/Quant/web/backend/app.db",
    else     => "/mnt/c/Users/JawirGaming66/Quant/web/backend/app.db",
};

// ── Shared helpers ─────────────────────────────────────────────────────────────

fn spanOrEmpty(ptr: ?[*:0]const u8) []const u8 {
    return if (ptr) |p| std.mem.span(p) else "";
}

fn bindText(stmt: ?*c.sqlite3_stmt, idx: c_int, s: []const u8) void {
    _ = c.sqlite3_bind_text(stmt, idx, s.ptr, @intCast(s.len), c.SQLITE_STATIC);
}

fn copyCol(stmt: ?*c.sqlite3_stmt, col: c_int, buf: []u8) usize {
    const ptr = c.sqlite3_column_text(stmt, col);
    if (ptr == null) return 0;
    const blen: usize = @intCast(c.sqlite3_column_bytes(stmt, col));
    const n = @min(blen, buf.len);
    @memcpy(buf[0..n], ptr[0..n]);
    return n;
}

// ── Backtests (read from app.db) ───────────────────────────────────────────────

pub const TRADE_MAGIC: u32 = 0x54524445;
pub const TRADE_ROW_BYTES: usize = 25;

pub fn getBacktests(a: std.mem.Allocator) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 3000);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql = "SELECT id, strategy, run_at, first_ts, last_ts, total_days, initial_bal, final_bal, net_growth, max_drawdown, num_trades, symbol, avg_drawdown, sharpe, total_win, total_loss, win_rate, win_count, profit_factor, expectancy, max_lose_streak, avg_size, min_size, max_size, avg_weekly, avg_monthly, avg_weekly_pct, avg_monthly_pct, instrument, max_drawdown_dollars, max_drawdown_peak_date, max_drawdown_trough_date, avg_drawdown_dollars, max_intraday_drawdown, max_intraday_drawdown_dollars, max_intraday_drawdown_date, avg_intraday_drawdown, avg_intraday_drawdown_dollars, max_daily_loss, max_daily_loss_date, avg_daily_loss FROM backtests ORDER BY run_at DESC";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    var out: std.ArrayList(u8) = .empty;
    try out.appendSlice(a, "[");
    var first = true;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        if (!first) try out.appendSlice(a, ",");
        first = false;
        const id = c.sqlite3_column_int64(stmt, 0);
        const strategy = spanOrEmpty(c.sqlite3_column_text(stmt, 1));
        const run_at = spanOrEmpty(c.sqlite3_column_text(stmt, 2));
        const first_ts = spanOrEmpty(c.sqlite3_column_text(stmt, 3));
        const last_ts = spanOrEmpty(c.sqlite3_column_text(stmt, 4));
        const total_days = c.sqlite3_column_int(stmt, 5);
        const initial_bal = c.sqlite3_column_double(stmt, 6);
        const final_bal = c.sqlite3_column_double(stmt, 7);
        const net_growth = c.sqlite3_column_double(stmt, 8);
        const max_drawdown = c.sqlite3_column_double(stmt, 9);
        const num_trades = c.sqlite3_column_int(stmt, 10);
        const symbol = spanOrEmpty(c.sqlite3_column_text(stmt, 11));
        const avg_drawdown = c.sqlite3_column_double(stmt, 12);
        const sharpe = c.sqlite3_column_double(stmt, 13);
        const total_win = c.sqlite3_column_double(stmt, 14);
        const total_loss = c.sqlite3_column_double(stmt, 15);
        const win_rate = c.sqlite3_column_double(stmt, 16);
        const win_count = c.sqlite3_column_int(stmt, 17);
        const profit_factor = c.sqlite3_column_double(stmt, 18);
        const expectancy = c.sqlite3_column_double(stmt, 19);
        const max_lose_streak = c.sqlite3_column_int(stmt, 20);
        const avg_size = c.sqlite3_column_double(stmt, 21);
        const min_size = c.sqlite3_column_double(stmt, 22);
        const max_size = c.sqlite3_column_double(stmt, 23);
        const avg_weekly = c.sqlite3_column_double(stmt, 24);
        const avg_monthly = c.sqlite3_column_double(stmt, 25);
        const avg_weekly_pct = c.sqlite3_column_double(stmt, 26);
        const avg_monthly_pct = c.sqlite3_column_double(stmt, 27);
        const instrument = spanOrEmpty(c.sqlite3_column_text(stmt, 28));
        const max_drawdown_dollars = c.sqlite3_column_double(stmt, 29);
        const max_drawdown_peak_date = spanOrEmpty(c.sqlite3_column_text(stmt, 30));
        const max_drawdown_trough_date = spanOrEmpty(c.sqlite3_column_text(stmt, 31));
        const avg_drawdown_dollars = c.sqlite3_column_double(stmt, 32);
        const max_intraday_drawdown = c.sqlite3_column_double(stmt, 33);
        const max_intraday_drawdown_dollars = c.sqlite3_column_double(stmt, 34);
        const max_intraday_drawdown_date = spanOrEmpty(c.sqlite3_column_text(stmt, 35));
        const avg_intraday_drawdown = c.sqlite3_column_double(stmt, 36);
        const avg_intraday_drawdown_dollars = c.sqlite3_column_double(stmt, 37);
        const max_daily_loss = c.sqlite3_column_double(stmt, 38);
        const max_daily_loss_date = spanOrEmpty(c.sqlite3_column_text(stmt, 39));
        const avg_daily_loss = c.sqlite3_column_double(stmt, 40);

        const part1 = try std.fmt.allocPrint(a,
            \\{{"id":{d},"strategy":"{s}","run_at":"{s}","first_ts":"{s}","last_ts":"{s}","total_days":{d},"initial_bal":{d:.2},"final_bal":{d:.2},"net_growth":{d:.2},"max_drawdown":{d:.4},"num_trades":{d},"symbol":"{s}","avg_drawdown":{d:.4},"sharpe":{d:.4},"total_win":{d:.2},"total_loss":{d:.2},"win_rate":{d:.4},"win_count":{d},"profit_factor":{d:.4},"expectancy":{d:.4},"max_lose_streak":{d},"avg_size":{d:.4},"min_size":{d:.4},"max_size":{d:.4},"avg_weekly":{d:.2},"avg_monthly":{d:.2},"avg_weekly_pct":{d:.4},"avg_monthly_pct":{d:.4},"instrument":"{s}"
        , .{
            id, strategy, run_at, first_ts, last_ts, total_days, initial_bal, final_bal, net_growth, max_drawdown, num_trades, symbol, avg_drawdown, sharpe, total_win, total_loss, win_rate, win_count, profit_factor, expectancy, max_lose_streak, avg_size, min_size, max_size, avg_weekly, avg_monthly, avg_weekly_pct, avg_monthly_pct, instrument
        });
        defer a.free(part1);

        const part2 = try std.fmt.allocPrint(a,
            \\,"max_drawdown_dollars":{d:.2},"max_drawdown_peak_date":"{s}","max_drawdown_trough_date":"{s}","avg_drawdown_dollars":{d:.2},"max_intraday_drawdown":{d:.4},"max_intraday_drawdown_dollars":{d:.2},"max_intraday_drawdown_date":"{s}","avg_intraday_drawdown":{d:.4},"avg_intraday_drawdown_dollars":{d:.2},"max_daily_loss":{d:.2},"max_daily_loss_date":"{s}","avg_daily_loss":{d:.2}}}
        , .{
            max_drawdown_dollars, max_drawdown_peak_date, max_drawdown_trough_date, avg_drawdown_dollars, max_intraday_drawdown, max_intraday_drawdown_dollars, max_intraday_drawdown_date, avg_intraday_drawdown, avg_intraday_drawdown_dollars, max_daily_loss, max_daily_loss_date, avg_daily_loss
        });
        defer a.free(part2);

        try out.appendSlice(a, part1);
        try out.appendSlice(a, part2);
    }
    try out.appendSlice(a, "]");
    return out.toOwnedSlice(a);
}

pub fn getTradesBin(a: std.mem.Allocator, backtest_id: i64) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 3000);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql =
        "SELECT side," ++
        "  CAST(strftime('%s', entry_ts) AS INTEGER)," ++
        "  CAST(strftime('%s', exit_ts)  AS INTEGER)," ++
        "  entry_price, exit_price, pnl, contracts" ++
        " FROM trades WHERE backtest_id = ? ORDER BY entry_ts";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    if (c.sqlite3_bind_int64(stmt, 1, backtest_id) != c.SQLITE_OK)
        return error.BindFailed;

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendNTimes(a, 0, 8);

    var count: u32 = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        const side = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
        const et: u32 = @intCast(c.sqlite3_column_int64(stmt, 1));
        const xt: u32 = @intCast(c.sqlite3_column_int64(stmt, 2));
        const ep: f32 = @floatCast(c.sqlite3_column_double(stmt, 3));
        const xp: f32 = @floatCast(c.sqlite3_column_double(stmt, 4));
        const pnl: f32 = @floatCast(c.sqlite3_column_double(stmt, 5));
        const qty: f32 = @floatCast(c.sqlite3_column_double(stmt, 6));
        const side_byte: u8 = if (std.mem.eql(u8, side, "long")) 0 else 1;

        try out.ensureUnusedCapacity(a, TRADE_ROW_BYTES);
        const dst = out.items.len;
        out.items.len += TRADE_ROW_BYTES;
        out.items[dst] = side_byte;
        std.mem.writeInt(u32, out.items[dst + 1 ..][0..4], et, .little);
        std.mem.writeInt(u32, out.items[dst + 5 ..][0..4], xt, .little);
        std.mem.writeInt(u32, out.items[dst + 9 ..][0..4], @bitCast(ep), .little);
        std.mem.writeInt(u32, out.items[dst + 13 ..][0..4], @bitCast(xp), .little);
        std.mem.writeInt(u32, out.items[dst + 17 ..][0..4], @bitCast(pnl), .little);
        std.mem.writeInt(u32, out.items[dst + 21 ..][0..4], @bitCast(qty), .little);
        count += 1;
    }

    std.mem.writeInt(u32, out.items[0..4], TRADE_MAGIC, .little);
    std.mem.writeInt(u32, out.items[4..8], count, .little);
    return out.toOwnedSlice(a);
}

pub const MC_MAGIC: u32 = 0x4D435054;

pub fn getMonteCarloBin(a: std.mem.Allocator, backtest_id: i64) ![]const u8 {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db_ptr, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db_ptr);
    _ = c.sqlite3_busy_timeout(db_ptr, 3000);

    var mc_id: i64 = 0;
    var sims: u32 = 0;
    var initial_balance: f32 = 0;
    var p5: f32 = 0;
    var p25: f32 = 0;
    var p50: f32 = 0;
    var p75: f32 = 0;
    var p95: f32 = 0;
    var p_profit: f32 = 0;
    var p_ruin: f32 = 0;
    var dd_p5: f32 = 0;
    var dd_p25: f32 = 0;
    var dd_p50: f32 = 0;
    var dd_p75: f32 = 0;
    var dd_p95: f32 = 0;
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT id, initial_balance, final_p5, final_p25, final_p50, final_p75, final_p95, p_profit, p_ruin, sims, dd_p5, dd_p25, dd_p50, dd_p75, dd_p95 FROM montecarlo WHERE source_id = ? ORDER BY run_at DESC LIMIT 1", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, backtest_id) != c.SQLITE_OK) return error.BindFailed;
        if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return error.NotFound;
        mc_id = c.sqlite3_column_int64(stmt, 0);
        initial_balance = @floatCast(c.sqlite3_column_double(stmt, 1));
        p5  = @floatCast(c.sqlite3_column_double(stmt, 2));
        p25 = @floatCast(c.sqlite3_column_double(stmt, 3));
        p50 = @floatCast(c.sqlite3_column_double(stmt, 4));
        p75 = @floatCast(c.sqlite3_column_double(stmt, 5));
        p95 = @floatCast(c.sqlite3_column_double(stmt, 6));
        p_profit = @floatCast(c.sqlite3_column_double(stmt, 7));
        p_ruin   = @floatCast(c.sqlite3_column_double(stmt, 8));
        sims     = @intCast(c.sqlite3_column_int64(stmt, 9));
        dd_p5  = @floatCast(c.sqlite3_column_double(stmt, 10));
        dd_p25 = @floatCast(c.sqlite3_column_double(stmt, 11));
        dd_p50 = @floatCast(c.sqlite3_column_double(stmt, 12));
        dd_p75 = @floatCast(c.sqlite3_column_double(stmt, 13));
        dd_p95 = @floatCast(c.sqlite3_column_double(stmt, 14));
    }

    var steps: u32 = 0;
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT COUNT(*) FROM montecarlo_paths WHERE mc_id = ? AND path_idx = 0", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, mc_id) != c.SQLITE_OK) return error.BindFailed;
        if (c.sqlite3_step(stmt) == c.SQLITE_ROW)
            steps = @intCast(c.sqlite3_column_int64(stmt, 0));
    }
    if (steps == 0) return error.NotFound;

    var step_values: std.ArrayList(u8) = .empty;
    defer step_values.deinit(a);
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT step FROM montecarlo_paths WHERE mc_id = ? AND path_idx = 0 ORDER BY step", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, mc_id) != c.SQLITE_OK) return error.BindFailed;
        while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
            const s: u32 = @intCast(c.sqlite3_column_int64(stmt, 0));
            var bytes: [4]u8 = undefined;
            std.mem.writeInt(u32, &bytes, s, .little);
            try step_values.appendSlice(a, &bytes);
        }
    }

    var equity_data: std.ArrayList(u8) = .empty;
    defer equity_data.deinit(a);
    var path_rows: u32 = 0;
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT equity FROM montecarlo_paths WHERE mc_id = ? ORDER BY path_idx, step", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, mc_id) != c.SQLITE_OK) return error.BindFailed;
        while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
            const eq: f32 = @floatCast(c.sqlite3_column_double(stmt, 0));
            var bytes: [4]u8 = undefined;
            std.mem.writeInt(u32, &bytes, @bitCast(eq), .little);
            try equity_data.appendSlice(a, &bytes);
            path_rows += 1;
        }
    }

    const num_paths: u32 = path_rows / steps;

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendNTimes(a, 0, 68);
    std.mem.writeInt(u32, out.items[0..4],  MC_MAGIC,                    .little);
    std.mem.writeInt(u32, out.items[4..8],  num_paths,                   .little);
    std.mem.writeInt(u32, out.items[8..12], steps,                       .little);
    std.mem.writeInt(u32, out.items[12..16], @as(u32, @bitCast(initial_balance)), .little);
    std.mem.writeInt(u32, out.items[16..20], @as(u32, @bitCast(p5)),     .little);
    std.mem.writeInt(u32, out.items[20..24], @as(u32, @bitCast(p25)),    .little);
    std.mem.writeInt(u32, out.items[24..28], @as(u32, @bitCast(p50)),    .little);
    std.mem.writeInt(u32, out.items[28..32], @as(u32, @bitCast(p75)),    .little);
    std.mem.writeInt(u32, out.items[32..36], @as(u32, @bitCast(p95)),    .little);
    std.mem.writeInt(u32, out.items[36..40], @as(u32, @bitCast(p_profit)), .little);
    std.mem.writeInt(u32, out.items[40..44], @as(u32, @bitCast(p_ruin)),  .little);
    std.mem.writeInt(u32, out.items[44..48], sims,                        .little);
    std.mem.writeInt(u32, out.items[48..52], @as(u32, @bitCast(dd_p5)),   .little);
    std.mem.writeInt(u32, out.items[52..56], @as(u32, @bitCast(dd_p25)),  .little);
    std.mem.writeInt(u32, out.items[56..60], @as(u32, @bitCast(dd_p50)),  .little);
    std.mem.writeInt(u32, out.items[60..64], @as(u32, @bitCast(dd_p75)),  .little);
    std.mem.writeInt(u32, out.items[64..68], @as(u32, @bitCast(dd_p95)),  .little);
    try out.appendSlice(a, step_values.items);
    try out.appendSlice(a, equity_data.items);
    return out.toOwnedSlice(a);
}

// ── Backtest persistence (app.db) ──────────────────────────────────────────────
// The web app runs backtests in-process (bt_run.zig) and saves them here, so the
// schema mirrors the columns the read paths above SELECT.

const BACKTEST_SCHEMA =
    \\CREATE TABLE IF NOT EXISTS backtests (
    \\  id INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  strategy TEXT NOT NULL DEFAULT '', run_at TEXT NOT NULL DEFAULT '',
    \\  first_ts TEXT NOT NULL DEFAULT '', last_ts TEXT NOT NULL DEFAULT '',
    \\  total_days INTEGER NOT NULL DEFAULT 0, initial_bal REAL NOT NULL DEFAULT 0,
    \\  final_bal REAL NOT NULL DEFAULT 0, net_growth REAL NOT NULL DEFAULT 0,
    \\  max_drawdown REAL NOT NULL DEFAULT 0, num_trades INTEGER NOT NULL DEFAULT 0,
    \\  symbol TEXT NOT NULL DEFAULT '', avg_drawdown REAL NOT NULL DEFAULT 0,
    \\  sharpe REAL NOT NULL DEFAULT 0, total_win REAL NOT NULL DEFAULT 0,
    \\  total_loss REAL NOT NULL DEFAULT 0, win_rate REAL NOT NULL DEFAULT 0,
    \\  win_count INTEGER NOT NULL DEFAULT 0, profit_factor REAL NOT NULL DEFAULT 0,
    \\  expectancy REAL NOT NULL DEFAULT 0, max_lose_streak INTEGER NOT NULL DEFAULT 0,
    \\  avg_size REAL NOT NULL DEFAULT 0, min_size REAL NOT NULL DEFAULT 0,
    \\  max_size REAL NOT NULL DEFAULT 0, avg_weekly REAL NOT NULL DEFAULT 0,
    \\  avg_monthly REAL NOT NULL DEFAULT 0, avg_weekly_pct REAL NOT NULL DEFAULT 0,
    \\  avg_monthly_pct REAL NOT NULL DEFAULT 0, instrument TEXT NOT NULL DEFAULT '',
    \\  max_drawdown_dollars REAL NOT NULL DEFAULT 0,
    \\  max_drawdown_peak_date TEXT NOT NULL DEFAULT '',
    \\  max_drawdown_trough_date TEXT NOT NULL DEFAULT '',
    \\  avg_drawdown_dollars REAL NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown REAL NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown_dollars REAL NOT NULL DEFAULT 0,
    \\  max_intraday_drawdown_date TEXT NOT NULL DEFAULT '',
    \\  avg_intraday_drawdown REAL NOT NULL DEFAULT 0,
    \\  avg_intraday_drawdown_dollars REAL NOT NULL DEFAULT 0,
    \\  max_daily_loss REAL NOT NULL DEFAULT 0,
    \\  max_daily_loss_date TEXT NOT NULL DEFAULT '',
    \\  avg_daily_loss REAL NOT NULL DEFAULT 0
    \\);
    \\CREATE TABLE IF NOT EXISTS trades (
    \\  id INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  backtest_id INTEGER NOT NULL, side TEXT NOT NULL DEFAULT 'long',
    \\  entry_ts TEXT NOT NULL DEFAULT '', exit_ts TEXT NOT NULL DEFAULT '',
    \\  entry_price REAL NOT NULL DEFAULT 0, exit_price REAL NOT NULL DEFAULT 0,
    \\  pnl REAL NOT NULL DEFAULT 0, contracts REAL NOT NULL DEFAULT 0
    \\);
    \\CREATE INDEX IF NOT EXISTS idx_trades_bt ON trades(backtest_id);
    \\CREATE TABLE IF NOT EXISTS montecarlo (
    \\  id INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  run_at TEXT NOT NULL DEFAULT '', source_id INTEGER NOT NULL,
    \\  initial_balance REAL NOT NULL DEFAULT 0,
    \\  final_p5 REAL NOT NULL DEFAULT 0, final_p25 REAL NOT NULL DEFAULT 0,
    \\  final_p50 REAL NOT NULL DEFAULT 0, final_p75 REAL NOT NULL DEFAULT 0,
    \\  final_p95 REAL NOT NULL DEFAULT 0, p_profit REAL NOT NULL DEFAULT 0,
    \\  p_ruin REAL NOT NULL DEFAULT 0, sims INTEGER NOT NULL DEFAULT 0,
    \\  dd_p5 REAL NOT NULL DEFAULT 0, dd_p25 REAL NOT NULL DEFAULT 0,
    \\  dd_p50 REAL NOT NULL DEFAULT 0, dd_p75 REAL NOT NULL DEFAULT 0,
    \\  dd_p95 REAL NOT NULL DEFAULT 0
    \\);
    \\CREATE INDEX IF NOT EXISTS idx_mc_src ON montecarlo(source_id);
    \\CREATE TABLE IF NOT EXISTS montecarlo_paths (
    \\  mc_id INTEGER NOT NULL, path_idx INTEGER NOT NULL,
    \\  step INTEGER NOT NULL, equity REAL NOT NULL DEFAULT 0
    \\);
    \\CREATE INDEX IF NOT EXISTS idx_mcp ON montecarlo_paths(mc_id, path_idx, step);
;

// Create the backtest tables in app.db if absent. Called once at startup.
pub fn initBacktestSchema() !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(APP_DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_exec(db, "PRAGMA journal_mode=WAL;", null, null, null);
    if (c.sqlite3_exec(db, BACKTEST_SCHEMA, null, null, null) != c.SQLITE_OK) return error.DbSchema;
    // Migrations for DBs created before dd_p* columns were added to montecarlo.
    _ = c.sqlite3_exec(db, "ALTER TABLE montecarlo ADD COLUMN dd_p5 REAL NOT NULL DEFAULT 0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE montecarlo ADD COLUMN dd_p25 REAL NOT NULL DEFAULT 0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE montecarlo ADD COLUMN dd_p50 REAL NOT NULL DEFAULT 0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE montecarlo ADD COLUMN dd_p75 REAL NOT NULL DEFAULT 0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE montecarlo ADD COLUMN dd_p95 REAL NOT NULL DEFAULT 0;", null, null, null);
}

// Scalar report fields for one saved run (mirrors the backtests columns).
pub const SaveMeta = struct {
    strategy: []const u8,
    symbol: []const u8,
    instrument: []const u8,
    first_ts: []const u8,
    last_ts: []const u8,
    total_days: i64,
    num_trades: i64,
    initial_bal: f64,
    final_bal: f64,
    net_growth: f64,
    max_drawdown: f64,
    avg_drawdown: f64,
    sharpe: f64,
    total_win: f64,
    total_loss: f64,
    win_rate: f64,
    win_count: i64,
    profit_factor: f64,
    expectancy: f64,
    max_lose_streak: i64,
    avg_size: f64,
    min_size: f64,
    max_size: f64,
    avg_weekly: f64,
    avg_monthly: f64,
    avg_weekly_pct: f64,
    avg_monthly_pct: f64,
    max_drawdown_dollars: f64,
    max_drawdown_peak_date: []const u8,
    max_drawdown_trough_date: []const u8,
    avg_drawdown_dollars: f64,
    max_intraday_drawdown: f64,
    max_intraday_drawdown_dollars: f64,
    max_intraday_drawdown_date: []const u8,
    avg_intraday_drawdown: f64,
    avg_intraday_drawdown_dollars: f64,
    max_daily_loss: f64,
    max_daily_loss_date: []const u8,
    avg_daily_loss: f64,
};

pub const SaveTrade = struct {
    side_long: bool,
    entry_ts: []const u8, // "YYYY-MM-DD HH:MM"
    exit_ts: []const u8,
    entry_price: f64,
    exit_price: f64,
    pnl: f64,
    contracts: f64,
};

pub const SaveMonteCarlo = struct {
    sims: i64,
    initial_balance: f64,
    p5: f64,
    p25: f64,
    p50: f64,
    p75: f64,
    p95: f64,
    p_profit: f64,
    p_ruin: f64,
    dd_p5: f64,
    dd_p25: f64,
    dd_p50: f64,
    dd_p75: f64,
    dd_p95: f64,
    num_paths: usize,
    num_steps: usize,
    steps: []const u32, // x-axis trade counts, len == num_steps
    equity: []const f64, // num_paths * num_steps, row-major
};

// Persist one run (report + trades + optional Monte Carlo) into app.db in a
// single transaction. Returns the new backtest id.
pub fn saveBacktest(meta: SaveMeta, trades: []const SaveTrade, mc: ?SaveMonteCarlo) !i64 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(APP_DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 3000);
    _ = c.sqlite3_exec(db, BACKTEST_SCHEMA, null, null, null);

    if (c.sqlite3_exec(db, "BEGIN;", null, null, null) != c.SQLITE_OK) return error.DbBegin;
    errdefer _ = c.sqlite3_exec(db, "ROLLBACK;", null, null, null);

    const bt_id = blk: {
        const sql =
            "INSERT INTO backtests (strategy, run_at, first_ts, last_ts, total_days, initial_bal, final_bal," ++
            " net_growth, max_drawdown, num_trades, symbol, avg_drawdown, sharpe, total_win, total_loss, win_rate," ++
            " win_count, profit_factor, expectancy, max_lose_streak, avg_size, min_size, max_size, avg_weekly," ++
            " avg_monthly, avg_weekly_pct, avg_monthly_pct, instrument, max_drawdown_dollars, max_drawdown_peak_date," ++
            " max_drawdown_trough_date, avg_drawdown_dollars, max_intraday_drawdown, max_intraday_drawdown_dollars," ++
            " max_intraday_drawdown_date, avg_intraday_drawdown, avg_intraday_drawdown_dollars, max_daily_loss," ++
            " max_daily_loss_date, avg_daily_loss)" ++
            " VALUES (?, datetime('now'), ?,?,?,?,?, ?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)";
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);

        var i: c_int = 1;
        bindText(stmt, i, meta.strategy);
        i += 1; // run_at is datetime('now'), not bound
        bindText(stmt, i, meta.first_ts);
        i += 1;
        bindText(stmt, i, meta.last_ts);
        i += 1;
        _ = c.sqlite3_bind_int64(stmt, i, meta.total_days);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.initial_bal);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.final_bal);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.net_growth);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_drawdown);
        i += 1;
        _ = c.sqlite3_bind_int64(stmt, i, meta.num_trades);
        i += 1;
        bindText(stmt, i, meta.symbol);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_drawdown);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.sharpe);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.total_win);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.total_loss);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.win_rate);
        i += 1;
        _ = c.sqlite3_bind_int64(stmt, i, meta.win_count);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.profit_factor);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.expectancy);
        i += 1;
        _ = c.sqlite3_bind_int64(stmt, i, meta.max_lose_streak);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_size);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.min_size);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_size);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_weekly);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_monthly);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_weekly_pct);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_monthly_pct);
        i += 1;
        bindText(stmt, i, meta.instrument);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_drawdown_dollars);
        i += 1;
        bindText(stmt, i, meta.max_drawdown_peak_date);
        i += 1;
        bindText(stmt, i, meta.max_drawdown_trough_date);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_drawdown_dollars);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_intraday_drawdown);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_intraday_drawdown_dollars);
        i += 1;
        bindText(stmt, i, meta.max_intraday_drawdown_date);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_intraday_drawdown);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_intraday_drawdown_dollars);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.max_daily_loss);
        i += 1;
        bindText(stmt, i, meta.max_daily_loss_date);
        i += 1;
        _ = c.sqlite3_bind_double(stmt, i, meta.avg_daily_loss);

        if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.InsertFailed;
        break :blk c.sqlite3_last_insert_rowid(db);
    };

    // Trades.
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db, "INSERT INTO trades (backtest_id, side, entry_ts, exit_ts, entry_price, exit_price, pnl, contracts) VALUES (?,?,?,?,?,?,?,?)", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        for (trades) |t| {
            _ = c.sqlite3_reset(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, bt_id);
            bindText(stmt, 2, if (t.side_long) "long" else "short");
            bindText(stmt, 3, t.entry_ts);
            bindText(stmt, 4, t.exit_ts);
            _ = c.sqlite3_bind_double(stmt, 5, t.entry_price);
            _ = c.sqlite3_bind_double(stmt, 6, t.exit_price);
            _ = c.sqlite3_bind_double(stmt, 7, t.pnl);
            _ = c.sqlite3_bind_double(stmt, 8, t.contracts);
            if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.InsertFailed;
        }
    }

    // Monte Carlo summary + paths.
    if (mc) |m| {
        const mc_id = blk: {
            var stmt: ?*c.sqlite3_stmt = null;
            if (c.sqlite3_prepare_v2(db, "INSERT INTO montecarlo (run_at, source_id, initial_balance, final_p5, final_p25, final_p50, final_p75, final_p95, p_profit, p_ruin, sims, dd_p5, dd_p25, dd_p50, dd_p75, dd_p95) VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", -1, &stmt, null) != c.SQLITE_OK)
                return error.PrepFailed;
            defer _ = c.sqlite3_finalize(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, bt_id);
            _ = c.sqlite3_bind_double(stmt, 2, m.initial_balance);
            _ = c.sqlite3_bind_double(stmt, 3, m.p5);
            _ = c.sqlite3_bind_double(stmt, 4, m.p25);
            _ = c.sqlite3_bind_double(stmt, 5, m.p50);
            _ = c.sqlite3_bind_double(stmt, 6, m.p75);
            _ = c.sqlite3_bind_double(stmt, 7, m.p95);
            _ = c.sqlite3_bind_double(stmt, 8, m.p_profit);
            _ = c.sqlite3_bind_double(stmt, 9, m.p_ruin);
            _ = c.sqlite3_bind_int64(stmt, 10, m.sims);
            _ = c.sqlite3_bind_double(stmt, 11, m.dd_p5);
            _ = c.sqlite3_bind_double(stmt, 12, m.dd_p25);
            _ = c.sqlite3_bind_double(stmt, 13, m.dd_p50);
            _ = c.sqlite3_bind_double(stmt, 14, m.dd_p75);
            _ = c.sqlite3_bind_double(stmt, 15, m.dd_p95);
            if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.InsertFailed;
            break :blk c.sqlite3_last_insert_rowid(db);
        };

        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db, "INSERT INTO montecarlo_paths (mc_id, path_idx, step, equity) VALUES (?,?,?,?)", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        var pi: usize = 0;
        while (pi < m.num_paths) : (pi += 1) {
            var si: usize = 0;
            while (si < m.num_steps) : (si += 1) {
                _ = c.sqlite3_reset(stmt);
                _ = c.sqlite3_bind_int64(stmt, 1, mc_id);
                _ = c.sqlite3_bind_int64(stmt, 2, @intCast(pi));
                _ = c.sqlite3_bind_int64(stmt, 3, @intCast(m.steps[si]));
                _ = c.sqlite3_bind_double(stmt, 4, m.equity[pi * m.num_steps + si]);
                if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.InsertFailed;
            }
        }
    }

    if (c.sqlite3_exec(db, "COMMIT;", null, null, null) != c.SQLITE_OK) return error.DbCommit;
    return bt_id;
}

pub fn getTrades(a: std.mem.Allocator, backtest_id: i64) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db, c.SQLITE_OPEN_READONLY, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql =
        "SELECT side," ++
        "  CAST(strftime('%s', entry_ts) AS INTEGER)," ++
        "  CAST(strftime('%s', exit_ts)  AS INTEGER)," ++
        "  entry_price, exit_price, pnl, contracts" ++
        " FROM trades WHERE backtest_id = ? ORDER BY entry_ts";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    if (c.sqlite3_bind_int64(stmt, 1, backtest_id) != c.SQLITE_OK)
        return error.BindFailed;

    var out: std.ArrayList(u8) = .empty;
    try out.appendSlice(a, "[");
    var first = true;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        if (!first) try out.appendSlice(a, ",");
        first = false;
        const side = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
        const et = c.sqlite3_column_int64(stmt, 1);
        const xt = c.sqlite3_column_int64(stmt, 2);
        const ep = c.sqlite3_column_double(stmt, 3);
        const xp = c.sqlite3_column_double(stmt, 4);
        const pnl = c.sqlite3_column_double(stmt, 5);
        const qty = c.sqlite3_column_int(stmt, 6);
        const row = try std.fmt.allocPrint(a, "{{\"side\":\"{s}\",\"et\":{d},\"xt\":{d},\"ep\":{d:.4},\"xp\":{d:.4},\"pnl\":{d:.2},\"qty\":{d}}}", .{ side, et, xt, ep, xp, pnl, qty });
        defer a.free(row);
        try out.appendSlice(a, row);
    }
    try out.appendSlice(a, "]");
    return out.toOwnedSlice(a);
}

// ── Combine: load trades as engine.Trade structs ──────────────────────────────
// Used by bt_combine.zig to merge several saved backtests into one portfolio.

const engine = @import("bt/engine.zig");

pub const CombineSource = struct {
    initial_bal: f64,
    strategy: []const u8,   // heap-allocated, caller frees
    symbol: []const u8,     // heap-allocated, caller frees
    instrument: []const u8, // heap-allocated, caller frees
    trades: []engine.Trade, // heap-allocated, caller frees
};

// Load the initial_bal and full trade list for one saved backtest.
// The caller must free source.strategy, source.symbol, source.instrument, and source.trades with `a`.
pub fn loadCombineSource(a: std.mem.Allocator, backtest_id: i64) !CombineSource {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db_ptr, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db_ptr);
    _ = c.sqlite3_busy_timeout(db_ptr, 3000);

    var initial_bal: f64 = 0;
    var strat_buf: [64]u8 = undefined;
    var strat_len: usize = 0;
    var sym_buf: [32]u8 = undefined;
    var sym_len: usize = 0;
    var inst_buf: [32]u8 = undefined;
    var inst_len: usize = 0;
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT initial_bal, strategy, symbol, instrument FROM backtests WHERE id = ?", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, backtest_id) != c.SQLITE_OK) return error.BindFailed;
        if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return error.NotFound;
        initial_bal = c.sqlite3_column_double(stmt, 0);
        strat_len = copyCol(stmt, 1, &strat_buf);
        sym_len = copyCol(stmt, 2, &sym_buf);
        inst_len = copyCol(stmt, 3, &inst_buf);
    }

    var list: std.ArrayList(engine.Trade) = .empty;
    errdefer list.deinit(a);
    {
        var stmt: ?*c.sqlite3_stmt = null;
        if (c.sqlite3_prepare_v2(db_ptr, "SELECT side, entry_ts, exit_ts, entry_price, exit_price, pnl, contracts FROM trades WHERE backtest_id = ? ORDER BY exit_ts", -1, &stmt, null) != c.SQLITE_OK)
            return error.PrepFailed;
        defer _ = c.sqlite3_finalize(stmt);
        if (c.sqlite3_bind_int64(stmt, 1, backtest_id) != c.SQLITE_OK) return error.BindFailed;
        while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
            const side_i = c.sqlite3_column_int(stmt, 0);
            var t: engine.Trade = undefined;
            t.side = if (side_i == 0) .long else .short;
            const ets = copyCol(stmt, 1, &t.entry_ts);
            if (ets < 16) @memset(t.entry_ts[ets..], ' ');
            const xts = copyCol(stmt, 2, &t.exit_ts);
            if (xts < 16) @memset(t.exit_ts[xts..], ' ');
            t.entry_price = c.sqlite3_column_double(stmt, 3);
            t.exit_price  = c.sqlite3_column_double(stmt, 4);
            t.pnl         = c.sqlite3_column_double(stmt, 5);
            t.contracts   = c.sqlite3_column_double(stmt, 6);
            try list.append(a, t);
        }
    }

    const strategy = try a.dupe(u8, strat_buf[0..strat_len]);
    const symbol = try a.dupe(u8, sym_buf[0..sym_len]);
    const instrument = try a.dupe(u8, inst_buf[0..inst_len]);
    return .{
        .initial_bal = initial_bal,
        .strategy    = strategy,
        .symbol      = symbol,
        .instrument  = instrument,
        .trades      = try list.toOwnedSlice(a),
    };
}

// ── March live-trading DB (app.db) ────────────────────────────────────────────
// Tables: strategies, mt5_accounts, mt5_account_strategies, live_trades.
// Shares app.db with settings.zig (WAL mode, safe for concurrent access).

const MARCH_SCHEMA =
    \\CREATE TABLE IF NOT EXISTS strategies (
    \\  name       TEXT PRIMARY KEY,
    \\  active     INTEGER NOT NULL DEFAULT 0,
    \\  updated_at TEXT    NOT NULL DEFAULT ''
    \\);
    \\CREATE TABLE IF NOT EXISTS mt5_accounts (
    \\  id         INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  name       TEXT    NOT NULL DEFAULT '',
    \\  login      TEXT    NOT NULL DEFAULT '',
    \\  password   TEXT    NOT NULL DEFAULT '',
    \\  server     TEXT    NOT NULL DEFAULT '',
    \\  created_at TEXT    NOT NULL DEFAULT ''
    \\);
    \\CREATE TABLE IF NOT EXISTS mt5_account_strategies (
    \\  id         INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  account_id INTEGER NOT NULL,
    \\  strategy   TEXT    NOT NULL,
    \\  symbol     TEXT    NOT NULL DEFAULT '',
    \\  active     INTEGER NOT NULL DEFAULT 0,
    \\  created_at TEXT    NOT NULL DEFAULT ''
    \\);
    \\CREATE TABLE IF NOT EXISTS live_trades (
    \\  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  strategy_name           TEXT    NOT NULL,
    \\  side                    TEXT    NOT NULL DEFAULT 'long',
    \\  contract                REAL    NOT NULL,
    \\  zig_entry_price         REAL    NOT NULL DEFAULT 0.0,
    \\  zig_close_price         REAL    NOT NULL DEFAULT 0.0,
    \\  mt5_entry_price         REAL    NOT NULL DEFAULT 0.0,
    \\  mt5_entry_price_spread  REAL    NOT NULL DEFAULT 0.0,
    \\  mt5_close_price         REAL    NOT NULL DEFAULT 0.0,
    \\  zig_open_time           TEXT    NOT NULL DEFAULT '',
    \\  mt5_open_time           TEXT    NOT NULL DEFAULT '',
    \\  zig_close_time          TEXT    NOT NULL DEFAULT '',
    \\  mt5_close_time          TEXT    NOT NULL DEFAULT '',
    \\  created_at              TEXT    NOT NULL DEFAULT ''
    \\);
;

const KNOWN_STRATEGIES = [_][]const u8{ "rth_vwap", "orb_buy", "min_loop" };

pub fn open() !?*c.sqlite3 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(APP_DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    _ = c.sqlite3_exec(db, "PRAGMA journal_mode=WAL;", null, null, null);
    if (c.sqlite3_exec(db, MARCH_SCHEMA, null, null, null) != c.SQLITE_OK) {
        _ = c.sqlite3_close(db);
        return error.DbSchema;
    }
    // Migrations for DBs created before certain columns existed (harmless if already present).
    _ = c.sqlite3_exec(db, "ALTER TABLE mt5_account_strategies ADD COLUMN active INTEGER NOT NULL DEFAULT 0;", null, null, null);
    // Seed known strategies (INSERT OR IGNORE preserves existing active state).
    for (KNOWN_STRATEGIES) |name| {
        var buf: [256]u8 = undefined;
        const sql = std.fmt.bufPrintZ(&buf,
            "INSERT OR IGNORE INTO strategies (name, active, updated_at) VALUES ('{s}', 0, '');",
            .{name}) catch continue;
        _ = c.sqlite3_exec(db, sql.ptr, null, null, null);
    }
    return db;
}

pub fn close(db: ?*c.sqlite3) void {
    _ = c.sqlite3_close(db);
}

// ── Strategies ────────────────────────────────────────────────────────────────

pub const Strategy = struct {
    name: [64]u8 = [_]u8{0} ** 64,
    name_len: usize = 0,
    active: bool = false,
};

pub fn listStrategies(db: ?*c.sqlite3, out: []Strategy) usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT name, active FROM strategies ORDER BY name;", -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        var s = &out[count];
        s.name_len = copyCol(stmt, 0, &s.name);
        s.active = c.sqlite3_column_int(stmt, 1) != 0;
        count += 1;
    }
    return count;
}

pub fn setActive(db: ?*c.sqlite3, name: []const u8, active: bool) bool {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "UPDATE strategies SET active = ?, updated_at = datetime('now') WHERE name = ?;", -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int(stmt, 1, if (active) 1 else 0);
    bindText(stmt, 2, name);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

// ── Live trades ────────────────────────────────────────────────────────────────

pub fn logTradeOpen(db: ?*c.sqlite3, strategy_name: []const u8, side: []const u8, contract: f64, zig_entry_price: f64, zig_open_time: []const u8) i64 {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "INSERT INTO live_trades (strategy_name, side, contract, zig_entry_price, zig_open_time, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'));", -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, strategy_name);
    bindText(stmt, 2, side);
    _ = c.sqlite3_bind_double(stmt, 3, contract);
    _ = c.sqlite3_bind_double(stmt, 4, zig_entry_price);
    bindText(stmt, 5, zig_open_time);

    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(db);
}

pub fn logTradeClose(db: ?*c.sqlite3, strategy_name: []const u8, zig_close_price: f64, zig_close_time: []const u8) i64 {
    var sel: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT id FROM live_trades WHERE strategy_name = ? AND zig_close_time = '' ORDER BY id DESC LIMIT 1;", -1, &sel, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(sel);

    bindText(sel, 1, strategy_name);
    if (c.sqlite3_step(sel) != c.SQLITE_ROW) return -1;
    const id = c.sqlite3_column_int64(sel, 0);

    var upd: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "UPDATE live_trades SET zig_close_price = ?, zig_close_time = ? WHERE id = ?;", -1, &upd, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(upd);

    _ = c.sqlite3_bind_double(upd, 1, zig_close_price);
    bindText(upd, 2, zig_close_time);
    _ = c.sqlite3_bind_int64(upd, 3, id);
    if (c.sqlite3_step(upd) != c.SQLITE_DONE) return -1;
    return id;
}

pub const LiveTrade = struct {
    id: i64,
    strategy_name: [64]u8 = [_]u8{0} ** 64,
    strategy_name_len: usize = 0,
    side: [16]u8 = [_]u8{0} ** 16,
    side_len: usize = 0,
    contract: f64,
    zig_entry_price: f64,
    zig_close_price: f64,
    mt5_entry_price: f64,
    mt5_close_price: f64,
    zig_open_time: [64]u8 = [_]u8{0} ** 64,
    zig_open_time_len: usize = 0,
    zig_close_time: [64]u8 = [_]u8{0} ** 64,
    zig_close_time_len: usize = 0,
    mt5_open_time: [64]u8 = [_]u8{0} ** 64,
    mt5_open_time_len: usize = 0,
    mt5_close_time: [64]u8 = [_]u8{0} ** 64,
    mt5_close_time_len: usize = 0,
};

pub fn listTrades(db: ?*c.sqlite3, out: []LiveTrade) usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT id, strategy_name, side, contract, zig_entry_price, zig_close_price, mt5_entry_price, mt5_close_price, zig_open_time, zig_close_time, mt5_open_time, mt5_close_time FROM live_trades ORDER BY id DESC LIMIT 200;", -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        var t = &out[count];
        t.id = c.sqlite3_column_int64(stmt, 0);
        t.strategy_name_len = copyCol(stmt, 1, &t.strategy_name);
        t.side_len = copyCol(stmt, 2, &t.side);
        t.contract = c.sqlite3_column_double(stmt, 3);
        t.zig_entry_price = c.sqlite3_column_double(stmt, 4);
        t.zig_close_price = c.sqlite3_column_double(stmt, 5);
        t.mt5_entry_price = c.sqlite3_column_double(stmt, 6);
        t.mt5_close_price = c.sqlite3_column_double(stmt, 7);
        t.zig_open_time_len = copyCol(stmt, 8, &t.zig_open_time);
        t.zig_close_time_len = copyCol(stmt, 9, &t.zig_close_time);
        t.mt5_open_time_len = copyCol(stmt, 10, &t.mt5_open_time);
        t.mt5_close_time_len = copyCol(stmt, 11, &t.mt5_close_time);
        count += 1;
    }
    return count;
}

// ── MT5 accounts ───────────────────────────────────────────────────────────────

pub const Mt5Account = struct {
    id: i64 = 0,
    name: [64]u8 = [_]u8{0} ** 64,
    name_len: usize = 0,
    login: [32]u8 = [_]u8{0} ** 32,
    login_len: usize = 0,
    server: [64]u8 = [_]u8{0} ** 64,
    server_len: usize = 0,
};

pub fn addMt5Account(db: ?*c.sqlite3, name: []const u8, login: []const u8, password: []const u8, server: []const u8) i64 {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "INSERT INTO mt5_accounts (name, login, password, server, created_at) VALUES (?, ?, ?, ?, datetime('now'));", -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, name);
    bindText(stmt, 2, login);
    bindText(stmt, 3, password);
    bindText(stmt, 4, server);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(db);
}

pub fn listMt5Accounts(db: ?*c.sqlite3, out: []Mt5Account) usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT id, name, login, server FROM mt5_accounts ORDER BY id;", -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        var a = &out[count];
        a.id = c.sqlite3_column_int64(stmt, 0);
        a.name_len = copyCol(stmt, 1, &a.name);
        a.login_len = copyCol(stmt, 2, &a.login);
        a.server_len = copyCol(stmt, 3, &a.server);
        count += 1;
    }
    return count;
}

pub fn deleteMt5Account(db: ?*c.sqlite3, id: i64) bool {
    _ = c.sqlite3_exec(db, "BEGIN;", null, null, null);
    var ok = true;

    var s1: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "DELETE FROM mt5_account_strategies WHERE account_id = ?;", -1, &s1, null) == c.SQLITE_OK) {
        _ = c.sqlite3_bind_int64(s1, 1, id);
        if (c.sqlite3_step(s1) != c.SQLITE_DONE) ok = false;
        _ = c.sqlite3_finalize(s1);
    } else ok = false;

    var s2: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "DELETE FROM mt5_accounts WHERE id = ?;", -1, &s2, null) == c.SQLITE_OK) {
        _ = c.sqlite3_bind_int64(s2, 1, id);
        if (c.sqlite3_step(s2) != c.SQLITE_DONE) ok = false;
        _ = c.sqlite3_finalize(s2);
    } else ok = false;

    _ = c.sqlite3_exec(db, if (ok) "COMMIT;" else "ROLLBACK;", null, null, null);
    return ok;
}

// ── Per-account strategies ─────────────────────────────────────────────────────

pub const AccountStrategy = struct {
    id: i64 = 0,
    strategy: [64]u8 = [_]u8{0} ** 64,
    strategy_len: usize = 0,
    symbol: [32]u8 = [_]u8{0} ** 32,
    symbol_len: usize = 0,
    active: bool = false,
};

pub fn addAccountStrategy(db: ?*c.sqlite3, account_id: i64, strategy: []const u8, symbol: []const u8) i64 {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "INSERT INTO mt5_account_strategies (account_id, strategy, symbol, created_at) VALUES (?, ?, ?, datetime('now'));", -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, account_id);
    bindText(stmt, 2, strategy);
    bindText(stmt, 3, symbol);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(db);
}

pub fn listAccountStrategies(db: ?*c.sqlite3, account_id: i64, out: []AccountStrategy) usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT id, strategy, symbol, active FROM mt5_account_strategies WHERE account_id = ? ORDER BY id;", -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, account_id);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        var s = &out[count];
        s.id = c.sqlite3_column_int64(stmt, 0);
        s.strategy_len = copyCol(stmt, 1, &s.strategy);
        s.symbol_len = copyCol(stmt, 2, &s.symbol);
        s.active = c.sqlite3_column_int(stmt, 3) != 0;
        count += 1;
    }
    return count;
}

pub fn deleteAccountStrategy(db: ?*c.sqlite3, id: i64) bool {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "DELETE FROM mt5_account_strategies WHERE id = ?;", -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, id);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

pub fn setAccountStrategyActive(db: ?*c.sqlite3, id: i64, active: bool) bool {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "UPDATE mt5_account_strategies SET active = ? WHERE id = ?;", -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int(stmt, 1, if (active) 1 else 0);
    _ = c.sqlite3_bind_int64(stmt, 2, id);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

pub fn accountStrategyName(db: ?*c.sqlite3, id: i64, buf: []u8) ?usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT strategy FROM mt5_account_strategies WHERE id = ?;", -1, &stmt, null) != c.SQLITE_OK) return null;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, id);
    if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return null;
    return copyCol(stmt, 0, buf);
}

pub fn anyActiveForStrategy(db: ?*c.sqlite3, strategy: []const u8) bool {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT 1 FROM mt5_account_strategies WHERE strategy = ? AND active = 1 LIMIT 1;", -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, strategy);
    return c.sqlite3_step(stmt) == c.SQLITE_ROW;
}

pub fn listActiveStrategyNames(db: ?*c.sqlite3, out: [][64]u8) usize {
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT DISTINCT strategy FROM mt5_account_strategies WHERE active = 1;", -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        out[count] = [_]u8{0} ** 64;
        _ = copyCol(stmt, 0, &out[count]);
        count += 1;
    }
    return count;
}

pub fn deleteBacktest(backtest_id: i64) !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(APP_DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 3000);

    if (c.sqlite3_exec(db, "BEGIN;", null, null, null) != c.SQLITE_OK) return error.DbBegin;
    errdefer _ = c.sqlite3_exec(db, "ROLLBACK;", null, null, null);

    // Delete montecarlo_paths associated with this backtest's mc
    {
        var stmt: ?*c.sqlite3_stmt = null;
        const sql = "DELETE FROM montecarlo_paths WHERE mc_id IN (SELECT id FROM montecarlo WHERE source_id = ?)";
        if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
            defer _ = c.sqlite3_finalize(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, backtest_id);
            _ = c.sqlite3_step(stmt);
        }
    }

    // Delete from montecarlo
    {
        var stmt: ?*c.sqlite3_stmt = null;
        const sql = "DELETE FROM montecarlo WHERE source_id = ?";
        if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
            defer _ = c.sqlite3_finalize(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, backtest_id);
            _ = c.sqlite3_step(stmt);
        }
    }

    // Delete from trades
    {
        var stmt: ?*c.sqlite3_stmt = null;
        const sql = "DELETE FROM trades WHERE backtest_id = ?";
        if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
            defer _ = c.sqlite3_finalize(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, backtest_id);
            _ = c.sqlite3_step(stmt);
        }
    }

    // Delete from backtests
    {
        var stmt: ?*c.sqlite3_stmt = null;
        const sql = "DELETE FROM backtests WHERE id = ?";
        if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
            defer _ = c.sqlite3_finalize(stmt);
            _ = c.sqlite3_bind_int64(stmt, 1, backtest_id);
            _ = c.sqlite3_step(stmt);
        }
    }

    if (c.sqlite3_exec(db, "COMMIT;", null, null, null) != c.SQLITE_OK) return error.DbCommit;
}

