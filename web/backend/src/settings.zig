const std = @import("std");
const builtin = @import("builtin");
const c = @cImport(@cInclude("sqlite3.h"));

const APP_DB_PATH = switch (builtin.os.tag) {
    .macos   => "/Users/nawfaldo/Bunker/Quant/web/backend/app.db",
    .windows => "C:/Users/JawirGaming66/Quant/web/backend/app.db",
    else     => "/mnt/c/Users/JawirGaming66/Quant/web/backend/app.db",
};

const DEFAULT_FROM = "2026-01-01";
const DEFAULT_TO   = "2026-04-30";
const DEFAULT_TF   = "5m";

// March page defaults (persisted under the same key/value settings table).
const DEFAULT_MARCH_SYMBOL = "nq";
const DEFAULT_MARCH_TF     = "1m";
const DEFAULT_MARCH_FROM   = "2026-06-18";
const DEFAULT_MARCH_TO     = "2026-06-25";
const DEFAULT_MARCH_MODE   = "latest"; // "latest" (stream) | "range" (static)
const DEFAULT_MARCH_LAYOUT = "single";
const DEFAULT_MARCH_BOTTOM_HEIGHT = "400";

fn spanOrEmpty(ptr: ?[*:0]const u8) []const u8 {
    return if (ptr) |p| std.mem.span(p) else "";
}

pub fn init() !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READWRITE | c.SQLITE_OPEN_CREATE | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);
    _ = c.sqlite3_busy_timeout(db, 5000);

    if (c.sqlite3_exec(db,
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        null, null, null) != c.SQLITE_OK) return error.CreateFailed;

    const seed_rc = c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('from_date', '" ++ DEFAULT_FROM ++ "')",
        null, null, null);
    if (seed_rc != c.SQLITE_OK) {
        std.debug.print("settings seed failed rc={d}: {s}\n", .{ seed_rc, c.sqlite3_errmsg(db) });
        return error.SeedFailed;
    }

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('to_date', '" ++ DEFAULT_TO ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('default_timeframe', '" ++ DEFAULT_TF ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_symbol', '" ++ DEFAULT_MARCH_SYMBOL ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_tf', '" ++ DEFAULT_MARCH_TF ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_from', '" ++ DEFAULT_MARCH_FROM ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_to', '" ++ DEFAULT_MARCH_TO ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_mode', '" ++ DEFAULT_MARCH_MODE ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_layout', '" ++ DEFAULT_MARCH_LAYOUT ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;

    if (c.sqlite3_exec(db,
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('march_bottom_height', '" ++ DEFAULT_MARCH_BOTTOM_HEIGHT ++ "')",
        null, null, null) != c.SQLITE_OK) return error.SeedFailed;
}

// Returns the configured default timeframe (e.g. "5m"), read from app.db into
// `buf`. Falls back to DEFAULT_TF on any error. Used by the candles route when
// a request omits the `tf` query param.
pub fn defaultTf(buf: []u8) []const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return DEFAULT_TF;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db,
        "SELECT value FROM settings WHERE key = 'default_timeframe'", -1, &stmt, null) != c.SQLITE_OK)
        return DEFAULT_TF;
    defer _ = c.sqlite3_finalize(stmt);

    if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return DEFAULT_TF;
    const val = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
    if (val.len == 0 or val.len > buf.len) return DEFAULT_TF;
    @memcpy(buf[0..val.len], val);
    return buf[0..val.len];
}

pub const DateRange = struct { from: []const u8, to: []const u8 };

// Reads from_date/to_date from app.db into the provided buffers and returns
// slices into them. Falls back to DEFAULT_FROM/DEFAULT_TO on any error. Used by
// the candles route to bound the QuestDB scan to the configured window.
pub fn dateRange(from_buf: []u8, to_buf: []u8) DateRange {
    var from: []const u8 = DEFAULT_FROM;
    var to:   []const u8 = DEFAULT_TO;

    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return .{ .from = from, .to = to };
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db,
        "SELECT key, value FROM settings WHERE key IN ('from_date', 'to_date')", -1, &stmt, null) != c.SQLITE_OK)
        return .{ .from = from, .to = to };
    defer _ = c.sqlite3_finalize(stmt);

    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        const key = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
        const val = spanOrEmpty(c.sqlite3_column_text(stmt, 1));
        if (std.mem.eql(u8, key, "from_date") and val.len > 0 and val.len <= from_buf.len) {
            @memcpy(from_buf[0..val.len], val);
            from = from_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "to_date") and val.len > 0 and val.len <= to_buf.len) {
            @memcpy(to_buf[0..val.len], val);
            to = to_buf[0..val.len];
        }
    }
    return .{ .from = from, .to = to };
}

pub fn get(a: std.mem.Allocator) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT key, value FROM settings", -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    // Copy strings out of SQLite memory before finalize.
    var from_buf: [32]u8 = undefined;
    var to_buf:   [32]u8 = undefined;
    var tf_buf:   [16]u8 = undefined;
    var from_len: usize  = 0;
    var to_len:   usize  = 0;
    var tf_len:   usize  = 0;
    var from_set = false;
    var to_set   = false;
    var tf_set   = false;

    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        const key = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
        const val = spanOrEmpty(c.sqlite3_column_text(stmt, 1));
        if (std.mem.eql(u8, key, "from_date")) {
            const n = @min(val.len, from_buf.len);
            @memcpy(from_buf[0..n], val[0..n]);
            from_len = n;
            from_set = true;
        } else if (std.mem.eql(u8, key, "to_date")) {
            const n = @min(val.len, to_buf.len);
            @memcpy(to_buf[0..n], val[0..n]);
            to_len = n;
            to_set = true;
        } else if (std.mem.eql(u8, key, "default_timeframe")) {
            const n = @min(val.len, tf_buf.len);
            @memcpy(tf_buf[0..n], val[0..n]);
            tf_len = n;
            tf_set = true;
        }
    }

    const from = if (from_set) from_buf[0..from_len] else DEFAULT_FROM;
    const to   = if (to_set)   to_buf[0..to_len]     else DEFAULT_TO;
    const tf   = if (tf_set)   tf_buf[0..tf_len]     else DEFAULT_TF;

    return std.fmt.allocPrint(a,
        "{{\"from_date\":\"{s}\",\"to_date\":\"{s}\",\"default_timeframe\":\"{s}\"}}",
        .{ from, to, tf },
    );
}

pub fn save(from_date: []const u8, to_date: []const u8) !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READWRITE | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql = "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_text(stmt, 1, "from_date", -1, null);
    _ = c.sqlite3_bind_text(stmt, 2, from_date.ptr, @intCast(from_date.len), null);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.SaveFailed;

    _ = c.sqlite3_reset(stmt);
    _ = c.sqlite3_bind_text(stmt, 1, "to_date", -1, null);
    _ = c.sqlite3_bind_text(stmt, 2, to_date.ptr, @intCast(to_date.len), null);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.SaveFailed;
}

// ── March page settings (symbol / tf / from / to / mode) ─────────────────────

pub fn marchGet(a: std.mem.Allocator) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db, "SELECT key, value FROM settings WHERE key LIKE 'march_%'", -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    var symbol_buf: [8]u8  = undefined;
    var tf_buf:     [8]u8  = undefined;
    var from_buf:   [16]u8 = undefined;
    var to_buf:     [16]u8 = undefined;
    var mode_buf:   [8]u8  = undefined;
    var bottom_open_buf: [8]u8 = undefined;
    var layout_buf: [16]u8 = undefined;
    var bottom_height_buf: [8]u8 = undefined;
    var symbol: []const u8 = DEFAULT_MARCH_SYMBOL;
    var tf:     []const u8 = DEFAULT_MARCH_TF;
    var from:   []const u8 = DEFAULT_MARCH_FROM;
    var to:     []const u8 = DEFAULT_MARCH_TO;
    var mode:   []const u8 = DEFAULT_MARCH_MODE;
    var bottom_open: []const u8 = "true";
    var layout: []const u8 = DEFAULT_MARCH_LAYOUT;
    var bottom_height: []const u8 = DEFAULT_MARCH_BOTTOM_HEIGHT;

    while (c.sqlite3_step(stmt) == c.SQLITE_ROW) {
        const key = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
        const val = spanOrEmpty(c.sqlite3_column_text(stmt, 1));
        if (val.len == 0) continue;
        if (std.mem.eql(u8, key, "march_symbol") and val.len <= symbol_buf.len) {
            @memcpy(symbol_buf[0..val.len], val);
            symbol = symbol_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_tf") and val.len <= tf_buf.len) {
            @memcpy(tf_buf[0..val.len], val);
            tf = tf_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_from") and val.len <= from_buf.len) {
            @memcpy(from_buf[0..val.len], val);
            from = from_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_to") and val.len <= to_buf.len) {
            @memcpy(to_buf[0..val.len], val);
            to = to_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_mode") and val.len <= mode_buf.len) {
            @memcpy(mode_buf[0..val.len], val);
            mode = mode_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_bottom_open") and val.len <= bottom_open_buf.len) {
            @memcpy(bottom_open_buf[0..val.len], val);
            bottom_open = bottom_open_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_layout") and val.len <= layout_buf.len) {
            @memcpy(layout_buf[0..val.len], val);
            layout = layout_buf[0..val.len];
        } else if (std.mem.eql(u8, key, "march_bottom_height") and val.len <= bottom_height_buf.len) {
            @memcpy(bottom_height_buf[0..val.len], val);
            bottom_height = bottom_height_buf[0..val.len];
        }
    }

    return std.fmt.allocPrint(a,
        "{{\"symbol\":\"{s}\",\"tf\":\"{s}\",\"from\":\"{s}\",\"to\":\"{s}\",\"mode\":\"{s}\",\"bottomOpen\":\"{s}\",\"layout\":\"{s}\",\"bottomHeight\":\"{s}\"}}",
        .{ symbol, tf, from, to, mode, bottom_open, layout, bottom_height },
    );
}

// ── March per-layout panel configs ───────────────────────────────────────────
// Stored as one opaque JSON blob under key 'march_layouts'. The frontend owns
// the shape ({ "<layout-id>": [ {symbol, tf, mode, from, to, indicators}, ... ] });
// the backend just round-trips the string so adding panel fields needs no Zig change.

pub fn marchLayoutsGet(a: std.mem.Allocator) ![]const u8 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READONLY | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(db,
        "SELECT value FROM settings WHERE key = 'march_layouts'", -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    if (c.sqlite3_step(stmt) != c.SQLITE_ROW) return a.dupe(u8, "{}");
    const val = spanOrEmpty(c.sqlite3_column_text(stmt, 0));
    if (val.len == 0) return a.dupe(u8, "{}");
    return a.dupe(u8, val);
}

pub fn marchLayoutsSave(blob: []const u8) !void {
    if (blob.len == 0) return;
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READWRITE | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql = "INSERT OR REPLACE INTO settings (key, value) VALUES ('march_layouts', ?)";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    _ = c.sqlite3_bind_text(stmt, 1, blob.ptr, @intCast(blob.len), null);
    if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.SaveFailed;
}

pub fn marchSave(symbol: []const u8, tf: []const u8, from: []const u8, to: []const u8, mode: []const u8, bottom_open: []const u8, layout: []const u8, bottom_height: []const u8) !void {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(APP_DB_PATH, &db,
        c.SQLITE_OPEN_READWRITE | c.SQLITE_OPEN_FULLMUTEX, null) != c.SQLITE_OK)
        return error.DbOpenFailed;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql = "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK)
        return error.PrepFailed;
    defer _ = c.sqlite3_finalize(stmt);

    const pairs = [_]struct { k: [*:0]const u8, v: []const u8 }{
        .{ .k = "march_symbol", .v = symbol },
        .{ .k = "march_tf",     .v = tf },
        .{ .k = "march_from",   .v = from },
        .{ .k = "march_to",     .v = to },
        .{ .k = "march_mode",   .v = mode },
        .{ .k = "march_bottom_open", .v = bottom_open },
        .{ .k = "march_layout", .v = layout },
        .{ .k = "march_bottom_height", .v = bottom_height },
    };

    for (pairs) |p| {
        if (p.v.len == 0) continue; // skip empty fields (don't clobber stored value)
        _ = c.sqlite3_reset(stmt);
        _ = c.sqlite3_bind_text(stmt, 1, p.k, -1, null);
        _ = c.sqlite3_bind_text(stmt, 2, p.v.ptr, @intCast(p.v.len), null);
        if (c.sqlite3_step(stmt) != c.SQLITE_DONE) return error.SaveFailed;
    }
}
