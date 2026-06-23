const std = @import("std");
const zap = @import("zap");
const cache = @import("cache.zig");
const db = @import("db.zig");
const settings = @import("settings.zig");

const alloc = std.heap.page_allocator;

fn cors(req: zap.Request) void {
    req.setHeader("Access-Control-Allow-Origin", "*") catch {};
    req.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS") catch {};
    req.setHeader("Access-Control-Allow-Headers", "Content-Type") catch {};
}

fn sendJson(req: zap.Request, body: []const u8) !void {
    try req.setContentType(.JSON);
    try req.sendBody(body);
}

// Extracts the value for `key` from a raw query string (e.g. "tf=1m&foo=bar").
fn queryParam(query: []const u8, key: []const u8) ?[]const u8 {
    var pos: usize = 0;
    while (pos < query.len) {
        const eq  = std.mem.indexOfScalarPos(u8, query, pos, '=') orelse break;
        const amp = std.mem.indexOfScalarPos(u8, query, eq + 1, '&') orelse query.len;
        if (std.mem.eql(u8, query[pos..eq], key)) return query[eq + 1 .. amp];
        pos = amp + 1;
    }
    return null;
}

// A date param is only trusted if it's a plain ISO date (YYYY-MM-DD) — this
// also keeps the value safe to splice into the QuestDB query string.
fn isIsoDate(s: []const u8) bool {
    if (s.len != 10) return false;
    for (s, 0..) |ch, i| {
        if (i == 4 or i == 7) {
            if (ch != '-') return false;
        } else if (ch < '0' or ch > '9') return false;
    }
    return true;
}

// Extracts a JSON string field value — e.g. jsonField(`{"a":"hello"}`, "a") → "hello".
// Returns empty slice if the key is missing.
fn jsonField(body: []const u8, key: []const u8) []const u8 {
    const needle = std.fmt.allocPrint(alloc, "\"{s}\":", .{key}) catch return "";
    defer alloc.free(needle);
    const kpos = std.mem.indexOf(u8, body, needle) orelse return "";
    var p = kpos + needle.len;
    while (p < body.len and (body[p] == ' ' or body[p] == '\t')) : (p += 1) {}
    if (p >= body.len or body[p] != '"') return "";
    p += 1;
    const end = std.mem.indexOfScalarPos(u8, body, p, '"') orelse return "";
    return body[p..end];
}

pub fn onRequest(req: zap.Request) !void {
    const path = req.path orelse "/";
    cors(req);

    if (std.mem.eql(u8, req.method orelse "", "OPTIONS")) {
        try req.sendBody("");
        return;
    }

    if (std.mem.eql(u8, path, "/api/candles/bin")) {
        const query = req.query orelse "";
        var tf_buf: [16]u8 = undefined;
        // No tf given => fall back to the default_timeframe stored in app.db.
        const tf = queryParam(query, "tf") orelse settings.defaultTf(&tf_buf);
        var from_buf: [32]u8 = undefined;
        var to_buf:   [32]u8 = undefined;
        const range = settings.dateRange(&from_buf, &to_buf);
        // Explicit ?from=&to= win; otherwise fall back to the app.db window.
        const q_from = queryParam(query, "from") orelse "";
        const q_to   = queryParam(query, "to")   orelse "";
        const from = if (isIsoDate(q_from)) q_from else range.from;
        const to   = if (isIsoDate(q_to))   q_to   else range.to;
        const q_symbol = queryParam(query, "symbol") orelse "nq";
        var valid_symbol = false;
        for (cache.VALID_SYMBOLS) |s| {
            if (std.mem.eql(u8, q_symbol, s)) { valid_symbol = true; break; }
        }
        if (!valid_symbol) {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"unknown symbol\"}");
            return;
        }
        const maybe = cache.fetchTf(alloc, tf, q_symbol, from, to) catch |err| {
            std.debug.print("candles fetch error: {}\n", .{err});
            req.setStatusNumeric(503);
            try req.sendJson("{\"error\":\"fetch failed\"}");
            return;
        };
        const body = maybe orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"unknown tf\"}");
            return;
        };
        defer alloc.free(body);
        req.setHeader("Content-Type", "application/octet-stream") catch {};
        try req.sendBody(body);
        return;
    }

    if (std.mem.eql(u8, path, "/api/vwap/bin")) {
        const body = cache.fetchVwap(alloc) catch |err| {
            std.debug.print("vwap fetch error: {}\n", .{err});
            req.setStatusNumeric(503);
            try req.sendJson("{\"error\":\"fetch failed\"}");
            return;
        };
        defer alloc.free(body);
        req.setHeader("Content-Type", "application/octet-stream") catch {};
        try req.sendBody(body);
        return;
    }

    if (std.mem.eql(u8, path, "/api/settings")) {
        if (std.mem.eql(u8, req.method orelse "", "POST")) {
            const body = req.body orelse {
                req.setStatusNumeric(400);
                try req.sendJson("{\"error\":\"no body\"}");
                return;
            };
            const from = jsonField(body, "from_date");
            const to   = jsonField(body, "to_date");
            settings.save(from, to) catch |err| {
                std.debug.print("settings save error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"save failed\"}");
                return;
            };
            try req.sendJson("{\"ok\":true}");
        } else {
            const body = settings.get(alloc) catch |err| {
                std.debug.print("settings get error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"read failed\"}");
                return;
            };
            defer alloc.free(body);
            try sendJson(req, body);
        }
        return;
    }

    if (std.mem.eql(u8, path, "/api/backtests")) {
        const body = db.getBacktests(alloc) catch |err| {
            std.debug.print("db error: {}\n", .{err});
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"db failed\"}");
            return;
        };
        defer alloc.free(body);
        try sendJson(req, body);
        return;
    }

    if (std.mem.startsWith(u8, path, "/api/trades/")) {
        const id_str      = path["/api/trades/".len..];
        const backtest_id = std.fmt.parseInt(i64, id_str, 10) catch {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid id\"}");
            return;
        };
        const body = db.getTradesBin(alloc, backtest_id) catch |err| {
            std.debug.print("db error: {}\n", .{err});
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"db failed\"}");
            return;
        };
        defer alloc.free(body);
        req.setHeader("Content-Type", "application/octet-stream") catch {};
        try req.sendBody(body);
        return;
    }

    if (std.mem.startsWith(u8, path, "/api/montecarlo/")) {
        const id_str = path["/api/montecarlo/".len..];
        const backtest_id = std.fmt.parseInt(i64, id_str, 10) catch {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid id\"}");
            return;
        };
        const body = db.getMonteCarloBin(alloc, backtest_id) catch |err| {
            if (err == error.NotFound) {
                req.setStatusNumeric(404);
                try req.sendJson("{\"error\":\"no montecarlo data\"}");
            } else {
                std.debug.print("montecarlo error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"db failed\"}");
            }
            return;
        };
        defer alloc.free(body);
        req.setHeader("Content-Type", "application/octet-stream") catch {};
        try req.sendBody(body);
        return;
    }

    if (std.mem.eql(u8, path, "/api/march/candles/bin")) {
        const query = req.query orelse "";
        const q_symbol = queryParam(query, "symbol") orelse "nq";
        const tf = queryParam(query, "tf") orelse "1m";

        var valid_symbol = false;
        if (std.mem.eql(u8, q_symbol, "nq") or std.mem.eql(u8, q_symbol, "es")) {
            valid_symbol = true;
        }

        if (!valid_symbol) {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"unknown symbol for march\"}");
            return;
        }

        const body = cache.fetchMarchCandles(alloc, q_symbol, tf) catch |err| {
            std.debug.print("march candles fetch error: {}\n", .{err});
            req.setStatusNumeric(503);
            try req.sendJson("{\"error\":\"fetch failed\"}");
            return;
        };
        defer alloc.free(body);
        req.setHeader("Content-Type", "application/octet-stream") catch {};
        try req.sendBody(body);
        return;
    }

    if (std.mem.eql(u8, path, "/api/march/ticks")) {
        const query = req.query orelse "";
        const q_symbol = queryParam(query, "symbol") orelse "nq";
        const q_since = queryParam(query, "since");

        var valid_symbol = false;
        if (std.mem.eql(u8, q_symbol, "nq") or std.mem.eql(u8, q_symbol, "es")) {
            valid_symbol = true;
        }

        if (!valid_symbol) {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"unknown symbol for march\"}");
            return;
        }

        var since: ?i64 = null;
        if (q_since) |s| {
            since = std.fmt.parseInt(i64, s, 10) catch null;
        }

        const body = cache.fetchMarchTicks(alloc, q_symbol, since) catch |err| {
            std.debug.print("march ticks fetch error: {}\n", .{err});
            req.setStatusNumeric(503);
            try req.sendJson("{\"error\":\"fetch failed\"}");
            return;
        };
        defer alloc.free(body);
        try sendJson(req, body);
        return;
    }

    if (std.mem.eql(u8, path, "/health")) {
        try req.sendJson("{\"status\":\"ok\"}");
        return;
    }

    try req.sendJson("{\"message\":\"hello from zig zap backend\"}");
}
