const std = @import("std");
const builtin = @import("builtin");

const c = @cImport(@cInclude("sqlite3.h"));

// march.db now lives alongside the web backend (the march server runs inside the
// web backend process). Absolute so it's found regardless of the launch CWD.
const DB_PATH = switch (builtin.os.tag) {
    .macos   => "/Users/nawfaldo/Bunker/Quant/web/backend/march.db",
    .windows => "C:/Users/JawirGaming66/Quant/web/backend/march.db",
    else     => "march.db",
};

const SCHEMA =
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
    \\CREATE TABLE IF NOT EXISTS trades (
    \\  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    \\  strategy_name           TEXT    NOT NULL,
    \\  side                    TEXT    NOT NULL DEFAULT 'long',
    \\  contract                REAL    NOT NULL,
    \\  zig_entry_price         REAL    NOT NULL DEFAULT 0.0,
    \\  zig_close_price         REAL    NOT NULL DEFAULT 0.0,
    \\  mt5_entry_price         REAL NOT NULL DEFAULT 0.0,
    \\  mt5_entry_price_spread  REAL NOT NULL DEFAULT 0.0,
    \\  mt5_close_price         REAL NOT NULL DEFAULT 0.0,
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
    if (c.sqlite3_open(DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    _ = c.sqlite3_exec(db, "PRAGMA journal_mode=WAL;", null, null, null);
    if (c.sqlite3_exec(db, SCHEMA, null, null, null) != c.SQLITE_OK) {
        _ = c.sqlite3_close(db);
        return error.DbSchema;
    }
    // Migration: add `active` to mt5_account_strategies for DBs created before
    // the column existed. Fails harmlessly (and is ignored) if already present.
    _ = c.sqlite3_exec(db, "ALTER TABLE mt5_account_strategies ADD COLUMN active INTEGER NOT NULL DEFAULT 0;", null, null, null);
    // Migration: add new columns to trades table
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN zig_entry_price REAL NOT NULL DEFAULT 0.0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN zig_close_price REAL NOT NULL DEFAULT 0.0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN mt5_entry_price REAL NOT NULL DEFAULT 0.0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN mt5_entry_price_spread REAL NOT NULL DEFAULT 0.0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN mt5_close_price REAL NOT NULL DEFAULT 0.0;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades ADD COLUMN side TEXT NOT NULL DEFAULT 'long';", null, null, null);
    // Migration: remove python time columns
    _ = c.sqlite3_exec(db, "ALTER TABLE trades DROP COLUMN python_open_time;", null, null, null);
    _ = c.sqlite3_exec(db, "ALTER TABLE trades DROP COLUMN python_close_time;", null, null, null);
    // Seed known strategies (INSERT OR IGNORE keeps existing active state).
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

pub const Strategy = struct {
    name: [64]u8 = [_]u8{0} ** 64,
    name_len: usize = 0,
    active: bool = false,
};

pub fn listStrategies(db: ?*c.sqlite3, out: []Strategy) usize {
    const sql = "SELECT name, active FROM strategies ORDER BY name;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        var s = &out[count];
        const ptr = c.sqlite3_column_text(stmt, 0);
        if (ptr != null) {
            const blen: usize = @intCast(c.sqlite3_column_bytes(stmt, 0));
            const n = @min(blen, s.name.len);
            @memcpy(s.name[0..n], ptr[0..n]);
            s.name_len = n;
        }
        s.active = c.sqlite3_column_int(stmt, 1) != 0;
        count += 1;
    }
    return count;
}

pub fn setActive(db: ?*c.sqlite3, name: []const u8, active: bool) bool {
    var name_buf: [65]u8 = undefined;
    const n = @min(name.len, name_buf.len - 1);
    @memcpy(name_buf[0..n], name[0..n]);
    name_buf[n] = 0;

    const sql = "UPDATE strategies SET active = ?, updated_at = datetime('now') WHERE name = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int(stmt, 1, if (active) 1 else 0);
    _ = c.sqlite3_bind_text(stmt, 2, name_buf[0..n :0].ptr, @intCast(n), c.SQLITE_STATIC);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

pub fn logTradeOpen(sqlite: ?*c.sqlite3, strategy_name: []const u8, side: []const u8, contract: f64, zig_entry_price: f64, zig_open_time: []const u8) i64 {
    const sql = "INSERT INTO trades (strategy_name, side, contract, zig_entry_price, zig_open_time, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'));";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(sqlite, sql, -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, strategy_name);
    bindText(stmt, 2, side);
    _ = c.sqlite3_bind_double(stmt, 3, contract);
    _ = c.sqlite3_bind_double(stmt, 4, zig_entry_price);
    bindText(stmt, 5, zig_open_time);

    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(sqlite);
}

pub fn logTradeClose(sqlite: ?*c.sqlite3, strategy_name: []const u8, zig_close_price: f64, zig_close_time: []const u8) i64 {
    // Find the last open trade for this strategy
    const select_sql = "SELECT id FROM trades WHERE strategy_name = ? AND zig_close_time = '' ORDER BY id DESC LIMIT 1;";
    var select_stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(sqlite, select_sql, -1, &select_stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(select_stmt);

    bindText(select_stmt, 1, strategy_name);
    if (c.sqlite3_step(select_stmt) != c.SQLITE_ROW) return -1;
    const id = c.sqlite3_column_int64(select_stmt, 0);

    // Update its close time and close price
    const update_sql = "UPDATE trades SET zig_close_price = ?, zig_close_time = ? WHERE id = ?;";
    var update_stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(sqlite, update_sql, -1, &update_stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(update_stmt);

    _ = c.sqlite3_bind_double(update_stmt, 1, zig_close_price);
    bindText(update_stmt, 2, zig_close_time);
    _ = c.sqlite3_bind_int64(update_stmt, 3, id);
    if (c.sqlite3_step(update_stmt) != c.SQLITE_DONE) return -1;

    return id;
}

// ── MT5 accounts ───────────────────────────────────────────────────────────────

/// Bind a slice as a text parameter. The slice must stay alive until the
/// statement is stepped (SQLITE_STATIC = sqlite does not copy).
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

pub const Mt5Account = struct {
    id: i64 = 0,
    name: [64]u8 = [_]u8{0} ** 64,
    name_len: usize = 0,
    login: [32]u8 = [_]u8{0} ** 32,
    login_len: usize = 0,
    server: [64]u8 = [_]u8{0} ** 64,
    server_len: usize = 0,
};

/// Insert an MT5 account; returns the new row id, or -1 on failure.
pub fn addMt5Account(db: ?*c.sqlite3, name: []const u8, login: []const u8, password: []const u8, server: []const u8) i64 {
    const sql = "INSERT INTO mt5_accounts (name, login, password, server, created_at) VALUES (?, ?, ?, ?, datetime('now'));";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, name);
    bindText(stmt, 2, login);
    bindText(stmt, 3, password);
    bindText(stmt, 4, server);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(db);
}

pub fn listMt5Accounts(db: ?*c.sqlite3, out: []Mt5Account) usize {
    const sql = "SELECT id, name, login, server FROM mt5_accounts ORDER BY id;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
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
    // Remove the account and its attached strategies together.
    _ = c.sqlite3_exec(db, "BEGIN;", null, null, null);
    var ok = true;

    const sql1 = "DELETE FROM mt5_account_strategies WHERE account_id = ?;";
    var s1: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql1, -1, &s1, null) == c.SQLITE_OK) {
        _ = c.sqlite3_bind_int64(s1, 1, id);
        if (c.sqlite3_step(s1) != c.SQLITE_DONE) ok = false;
        _ = c.sqlite3_finalize(s1);
    } else ok = false;

    const sql2 = "DELETE FROM mt5_accounts WHERE id = ?;";
    var s2: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql2, -1, &s2, null) == c.SQLITE_OK) {
        _ = c.sqlite3_bind_int64(s2, 1, id);
        if (c.sqlite3_step(s2) != c.SQLITE_DONE) ok = false;
        _ = c.sqlite3_finalize(s2);
    } else ok = false;

    _ = c.sqlite3_exec(db, if (ok) "COMMIT;" else "ROLLBACK;", null, null, null);
    return ok;
}

// ── Per-account strategies (strategy + symbol) ─────────────────────────────────

pub const AccountStrategy = struct {
    id: i64 = 0,
    strategy: [64]u8 = [_]u8{0} ** 64,
    strategy_len: usize = 0,
    symbol: [32]u8 = [_]u8{0} ** 32,
    symbol_len: usize = 0,
    active: bool = false,
};

/// Attach a strategy + symbol to an account; returns the new row id, or -1.
pub fn addAccountStrategy(db: ?*c.sqlite3, account_id: i64, strategy: []const u8, symbol: []const u8) i64 {
    const sql = "INSERT INTO mt5_account_strategies (account_id, strategy, symbol, created_at) VALUES (?, ?, ?, datetime('now'));";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return -1;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, account_id);
    bindText(stmt, 2, strategy);
    bindText(stmt, 3, symbol);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return -1;
    return c.sqlite3_last_insert_rowid(db);
}

pub fn listAccountStrategies(db: ?*c.sqlite3, account_id: i64, out: []AccountStrategy) usize {
    const sql = "SELECT id, strategy, symbol, active FROM mt5_account_strategies WHERE account_id = ? ORDER BY id;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
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
    const sql = "DELETE FROM mt5_account_strategies WHERE id = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, id);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

/// Turn one account-strategy row on/off.
pub fn setAccountStrategyActive(db: ?*c.sqlite3, id: i64, active: bool) bool {
    const sql = "UPDATE mt5_account_strategies SET active = ? WHERE id = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int(stmt, 1, if (active) 1 else 0);
    _ = c.sqlite3_bind_int64(stmt, 2, id);
    return c.sqlite3_step(stmt) == c.SQLITE_DONE;
}

/// Look up the strategy name for a row id (needed to drive the live engine).
/// Returns the length written into `buf`, or null if the row does not exist.
pub fn accountStrategyName(db: ?*c.sqlite3, id: i64, buf: []u8) ?usize {
    const sql = "SELECT strategy FROM mt5_account_strategies WHERE id = ?;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return null;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_int64(stmt, 1, id);
    if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return null;
    return copyCol(stmt, 0, buf);
}

/// True if any account currently has `strategy` switched on. Used to decide
/// whether the global live-engine instance should stay active.
pub fn anyActiveForStrategy(db: ?*c.sqlite3, strategy: []const u8) bool {
    const sql = "SELECT 1 FROM mt5_account_strategies WHERE strategy = ? AND active = 1 LIMIT 1;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return false;
    defer _ = c.sqlite3_finalize(stmt);

    bindText(stmt, 1, strategy);
    return c.sqlite3_step(stmt) == c.SQLITE_ROW;
}

/// Distinct strategy names that have at least one account switched on. Used at
/// startup to re-arm the live engine after a restart. Names are null-padded.
pub fn listActiveStrategyNames(db: ?*c.sqlite3, out: [][64]u8) usize {
    const sql = "SELECT DISTINCT strategy FROM mt5_account_strategies WHERE active = 1;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
    defer _ = c.sqlite3_finalize(stmt);

    var count: usize = 0;
    while (c.sqlite3_step(stmt) == c.SQLITE_ROW and count < out.len) {
        out[count] = [_]u8{0} ** 64;
        _ = copyCol(stmt, 0, &out[count]);
        count += 1;
    }
    return count;
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

pub fn listTrades(db_conn: ?*c.sqlite3, out: []LiveTrade) usize {
    const sql = "SELECT id, strategy_name, side, contract, zig_entry_price, zig_close_price, mt5_entry_price, mt5_close_price, zig_open_time, zig_close_time, mt5_open_time, mt5_close_time FROM trades ORDER BY id DESC LIMIT 200;";
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db_conn, sql, -1, &stmt, null) != c.SQLITE_OK) return 0;
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
