const std = @import("std");

const c = @cImport(@cInclude("sqlite3.h"));

const DB_PATH = "march.db";

const SCHEMA =
    \\CREATE TABLE IF NOT EXISTS strategies (
    \\  name       TEXT PRIMARY KEY,
    \\  active     INTEGER NOT NULL DEFAULT 0,
    \\  updated_at TEXT    NOT NULL DEFAULT ''
    \\);
;

const KNOWN_STRATEGIES = [_][]const u8{ "rth_vwap", "orb_buy", "buy_hold" };

pub fn open() !?*c.sqlite3 {
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open(DB_PATH, &db) != c.SQLITE_OK) return error.DbOpen;
    _ = c.sqlite3_exec(db, "PRAGMA journal_mode=WAL;", null, null, null);
    if (c.sqlite3_exec(db, SCHEMA, null, null, null) != c.SQLITE_OK) {
        _ = c.sqlite3_close(db);
        return error.DbSchema;
    }
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
