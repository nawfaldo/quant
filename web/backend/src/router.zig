const std = @import("std");
const http = @import("http.zig");
const cache = @import("cache.zig");
const db = @import("db.zig");
const settings = @import("settings.zig");
const march = @import("march_api.zig");
const bt_run = @import("bt_run.zig");
const bt_combine = @import("bt_combine.zig");
const bt_tune = @import("bt_tune.zig");

const alloc = std.heap.page_allocator;

fn sendJson(req: *http.Ctx, body: []const u8) !void {
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

pub fn onRequest(req: *http.Ctx) !void {
    const path = req.path orelse "/";

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
        const maybe = cache.fetchTf(req.io, alloc, tf, q_symbol, from, to) catch |err| {
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
        const body = cache.fetchVwap(req.io, alloc) catch |err| {
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

    if (std.mem.eql(u8, path, "/api/march/settings")) {
        if (std.mem.eql(u8, req.method orelse "", "POST")) {
            const body = req.body orelse {
                req.setStatusNumeric(400);
                try req.sendJson("{\"error\":\"no body\"}");
                return;
            };
            const symbol        = jsonField(body, "symbol");
            const tf            = jsonField(body, "tf");
            const from          = jsonField(body, "from");
            const to            = jsonField(body, "to");
            const mode          = jsonField(body, "mode");
            const bottom_open   = jsonField(body, "bottomOpen");
            const layout        = jsonField(body, "layout");
            const bottom_height = jsonField(body, "bottomHeight");
            settings.marchSave(symbol, tf, from, to, mode, bottom_open, layout, bottom_height) catch |err| {
                std.debug.print("march settings save error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"save failed\"}");
                return;
            };
            try req.sendJson("{\"ok\":true}");
        } else {
            const body = settings.marchGet(alloc) catch |err| {
                std.debug.print("march settings get error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"read failed\"}");
                return;
            };
            defer alloc.free(body);
            try sendJson(req, body);
        }
        return;
    }

    if (std.mem.eql(u8, path, "/api/march/layouts")) {
        if (std.mem.eql(u8, req.method orelse "", "POST")) {
            const body = req.body orelse {
                req.setStatusNumeric(400);
                try req.sendJson("{\"error\":\"no body\"}");
                return;
            };
            settings.marchLayoutsSave(body) catch |err| {
                std.debug.print("march layouts save error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"save failed\"}");
                return;
            };
            try req.sendJson("{\"ok\":true}");
        } else {
            const body = settings.marchLayoutsGet(alloc) catch |err| {
                std.debug.print("march layouts get error: {}\n", .{err});
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"read failed\"}");
                return;
            };
            defer alloc.free(body);
            try sendJson(req, body);
        }
        return;
    }

    // Run a backtest on demand (the Test page). POST { strategy, symbol, … }.
    if (std.mem.eql(u8, path, "/api/run")) {
        if (!std.mem.eql(u8, req.method orelse "", "POST")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use POST\"}");
            return;
        }
        try bt_run.handle(req);
        return;
    }

    // Save an on-demand run into app.db. POST with the same params as /api/run.
    if (std.mem.eql(u8, path, "/api/run/save")) {
        if (!std.mem.eql(u8, req.method orelse "", "POST")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use POST\"}");
            return;
        }
        try bt_run.handleSave(req);
        return;
    }

    // Combine several saved backtests into one portfolio result.
    if (std.mem.eql(u8, path, "/api/combine")) {
        if (!std.mem.eql(u8, req.method orelse "", "POST")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use POST\"}");
            return;
        }
        try bt_combine.handle(req);
        return;
    }

    if (std.mem.eql(u8, path, "/api/combine/save")) {
        if (!std.mem.eql(u8, req.method orelse "", "POST")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use POST\"}");
            return;
        }
        try bt_combine.handleSave(req);
        return;
    }

    // Tune (grid-search) position-sizing parameters. POST with the same base
    // fields as /api/run, but baseLot and vol params can be comma-separated
    // lists for sweep.
    if (std.mem.eql(u8, path, "/api/tune")) {
        if (!std.mem.eql(u8, req.method orelse "", "POST")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use POST\"}");
            return;
        }
        try bt_tune.handle(req);
        return;
    }

    if (std.mem.eql(u8, path, "/api/tune/status")) {
        if (!std.mem.eql(u8, req.method orelse "", "GET")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use GET\"}");
            return;
        }
        try bt_tune.handleStatus(req);
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

    // GET /api/backtests/:id/fx — on-demand fx-execution re-pricing of a saved run.
    if (std.mem.startsWith(u8, path, "/api/backtests/") and std.mem.endsWith(u8, path, "/fx")) {
        const mid = path["/api/backtests/".len .. path.len - "/fx".len];
        const backtest_id = std.fmt.parseInt(i64, mid, 10) catch {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid id\"}");
            return;
        };
        try bt_run.handleBacktestFx(req, backtest_id);
        return;
    }

    if (std.mem.startsWith(u8, path, "/api/backtests/")) {
        if (!std.mem.eql(u8, req.method orelse "", "DELETE")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"use DELETE\"}");
            return;
        }
        const id_str = path["/api/backtests/".len..];
        const backtest_id = std.fmt.parseInt(i64, id_str, 10) catch {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid id\"}");
            return;
        };
        db.deleteBacktest(backtest_id) catch |err| {
            std.debug.print("delete db error: {}\n", .{err});
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"delete db failed\"}");
            return;
        };
        try req.sendJson("{\"status\":\"ok\"}");
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

        // Optional ISO date bounds. `from` alone (open-ended) backs the live
        // "Latest" mode; `from`+`to` backs a static historical range.
        const q_from = queryParam(query, "from") orelse "";
        const q_to   = queryParam(query, "to")   orelse "";
        const from = if (isIsoDate(q_from)) q_from else "";
        const to   = if (isIsoDate(q_to))   q_to   else "";

        var valid_symbol = false;
        if (std.mem.eql(u8, q_symbol, "nq") or std.mem.eql(u8, q_symbol, "es")) {
            valid_symbol = true;
        }

        if (!valid_symbol) {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"unknown symbol for march\"}");
            return;
        }

        const body = cache.fetchMarchCandles(req.io, alloc, q_symbol, tf, from, to) catch |err| {
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

        const body = cache.fetchMarchTicks(req.io, alloc, q_symbol, since) catch |err| {
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

    // Delegate march live-trading routes (strategies, bar, trades, mt5 accounts)
    // to march/api.zig. Returns true if the path was handled.
    if (std.mem.startsWith(u8, path, "/api/march/strategies") or
        std.mem.eql(u8, path, "/api/march/bar") or
        std.mem.eql(u8, path, "/api/march/trades") or
        std.mem.startsWith(u8, path, "/api/march/mt5/"))
    {
        if (try march.handleRequest(req)) return;
    }

    try req.sendJson("{\"message\":\"hello from zig zap backend\"}");
}
