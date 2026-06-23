const std = @import("std");
const c = @cImport(@cInclude("sqlite3.h"));

const DB_PATH = "/Users/nawfaldo/Bunker/Quant/backtest/backtest.db";

// Trades binary wire format:
//   header: u32 magic ("TRDE" little-endian) | u32 row_count
//   rows  : row_count * { u8 side (0=long,1=short), u32 et, u32 xt,
//                         f32 ep, f32 xp, f32 pnl, u32 qty }  (25 B each, little-endian)
pub const TRADE_MAGIC: u32 = 0x54524445;
pub const TRADE_ROW_BYTES: usize = 25;

fn spanOrEmpty(ptr: ?[*:0]const u8) []const u8 {
    return if (ptr) |p| std.mem.span(p) else "";
}

pub fn getBacktests(a: std.mem.Allocator) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(DB_PATH, &db, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 3000); // wait up to 3 s if the writer holds a lock

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
    if (c.sqlite3_open_v2(DB_PATH, &db, c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
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
    try out.appendNTimes(a, 0, 8); // header placeholder, filled in after the loop

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

pub fn getTrades(a: std.mem.Allocator, backtest_id: i64) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(DB_PATH, &db, c.SQLITE_OPEN_READONLY, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    // strftime('%s', ...) lets SQLite do the UTC→unix-seconds conversion
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
