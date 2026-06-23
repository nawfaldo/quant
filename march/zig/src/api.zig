// march/zig/src/api.zig
//
// Native HTTP API server for the march live-trading system using std.Io.net.
// Works cross-platform (specifically Windows).
//
// Endpoints:
//   GET  /health                     → {"status":"ok"}
//   GET  /strategies                 → [{"name":"rth_vwap","active":false},…]
//   PUT  /strategies/:name/on        → {"ok":true}
//   PUT  /strategies/:name/off       → {"ok":true}
//   POST /bar                        → feed a bar, get signal back + calls Python API if actionable
//
// POST /bar body (JSON):
//   {"strategy":"rth_vwap","ts":"2024-01-15 09:30","open":17500.5,"high":17510,"low":17498,"close":17505.3,"volume":1234}
//
// Python API (called by this server):
//   POST http://127.0.0.1:5001/execute  {"action":"long"|"short"|"flat"|"close"}

const std = @import("std");
const db = @import("db.zig");
const engine = @import("engine.zig");
const data = @import("data.zig");

const RthVwap = @import("strategies/rth_vwap.zig").RthVwap;
const OrbBuy = @import("strategies/30m_buy.zig").OrbBuy;
const BuyHold = @import("strategies/buy_hold.zig").BuyHold;

// ── Config ────────────────────────────────────────────────────────────────────

const API_PORT: u16 = 4000;
const PYTHON_API_URL = "http://127.0.0.1:5001/execute";

// ── Strategy registry (global in-process state) ────────────────────────────────
// One instance per strategy; reset when toggled off.

const StrategyTag = enum { rth_vwap, orb_buy, buy_hold };

const StrategyInstance = union(StrategyTag) {
    rth_vwap: RthVwap,
    orb_buy: OrbBuy,
    buy_hold: BuyHold,

    fn update(self: *StrategyInstance, bar: engine.Bar, ts: data.Ts) engine.Signal {
        return switch (self.*) {
            .rth_vwap => |*s| s.update(bar, ts),
            .orb_buy  => |*s| s.update(bar, ts),
            .buy_hold => |*s| s.update(bar, ts),
        };
    }
};

const MAX_STRATEGIES = 8;
var g_strategies: [MAX_STRATEGIES]?StrategyInstance = [_]?StrategyInstance{null} ** MAX_STRATEGIES;
var g_strategy_names: [MAX_STRATEGIES][64]u8 = [_][64]u8{[_]u8{0} ** 64} ** MAX_STRATEGIES;
var g_strategy_count: usize = 0;
var g_mutex = std.Io.Mutex.init;

// Previous signals per slot (to detect transitions).
var g_prev_signals: [MAX_STRATEGIES]engine.Signal = [_]engine.Signal{.flat} ** MAX_STRATEGIES;

// ── Strategy slot helpers ──────────────────────────────────────────────────────

fn findSlot(name: []const u8) ?usize {
    for (0..g_strategy_count) |i| {
        const active_name = std.mem.sliceTo(&g_strategy_names[i], 0);
        if (std.mem.eql(u8, active_name, name)) return i;
    }
    return null;
}

fn tagFromName(name: []const u8) ?StrategyTag {
    if (std.mem.eql(u8, name, "rth_vwap")) return .rth_vwap;
    if (std.mem.eql(u8, name, "orb_buy"))  return .orb_buy;
    if (std.mem.eql(u8, name, "buy_hold")) return .buy_hold;
    return null;
}

fn activateStrategy(io: std.Io, name: []const u8) void {
    g_mutex.lockUncancelable(io);
    defer g_mutex.unlock(io);

    const tag = tagFromName(name) orelse return;
    // Find existing slot or allocate new one.
    const slot = findSlot(name) orelse blk: {
        if (g_strategy_count >= MAX_STRATEGIES) return;
        const idx = g_strategy_count;
        g_strategy_count += 1;
        const n = @min(name.len, 63);
        @memcpy(g_strategy_names[idx][0..n], name[0..n]);
        g_strategy_names[idx][n] = 0;
        break :blk idx;
    };
    // (Re)initialize to reset state.
    g_strategies[slot] = switch (tag) {
        .rth_vwap => .{ .rth_vwap = .{} },
        .orb_buy  => .{ .orb_buy  = .{} },
        .buy_hold => .{ .buy_hold = .{} },
    };
    g_prev_signals[slot] = .flat;
}

fn deactivateStrategy(io: std.Io, name: []const u8) void {
    g_mutex.lockUncancelable(io);
    defer g_mutex.unlock(io);

    const slot = findSlot(name) orelse return;
    g_strategies[slot] = null;
}

// ── Win32 HTTP client (for calling Python API) ────────────────────────────────
// Uses WinHTTP to POST a JSON body to the Python API without extra deps.

const windows = std.os.windows;

const HINTERNET = *anyopaque;
extern "winhttp" fn WinHttpOpen(
    pszAgentW: ?[*:0]const u16,
    dwAccessType: u32,
    pszProxyW: ?[*:0]const u16,
    pszProxyBypassW: ?[*:0]const u16,
    dwFlags: u32,
) callconv(.winapi) ?HINTERNET;
extern "winhttp" fn WinHttpConnect(
    hSession: HINTERNET,
    pswzServerName: [*:0]const u16,
    nServerPort: u16,
    dwReserved: u32,
) callconv(.winapi) ?HINTERNET;
extern "winhttp" fn WinHttpOpenRequest(
    hConnect: HINTERNET,
    pwszVerb: [*:0]const u16,
    pwszObjectName: [*:0]const u16,
    pwszVersion: ?[*:0]const u16,
    pwszReferrer: ?[*:0]const u16,
    ppwszAcceptTypes: ?*?[*:0]const u16,
    dwFlags: u32,
) callconv(.winapi) ?HINTERNET;
extern "winhttp" fn WinHttpSendRequest(
    hRequest: HINTERNET,
    lpszHeaders: ?[*:0]const u16,
    dwHeadersLength: u32,
    lpOptional: ?*const anyopaque,
    dwOptionalLength: u32,
    dwTotalLength: u32,
    dwContext: usize,
) callconv(.winapi) windows.BOOL;
extern "winhttp" fn WinHttpReceiveResponse(hRequest: HINTERNET, lpReserved: ?*anyopaque) callconv(.winapi) windows.BOOL;
extern "winhttp" fn WinHttpCloseHandle(hInternet: HINTERNET) callconv(.winapi) windows.BOOL;

fn utf8ToUtf16Z(comptime s: []const u8) [s.len:0]u16 {
    var buf: [s.len:0]u16 = undefined;
    for (s, 0..) |ch, i| buf[i] = ch;
    buf[s.len] = 0;
    return buf;
}

fn callPythonExecute(action: []const u8) void {
    // Build JSON body.
    var body_buf: [128]u8 = undefined;
    const body = std.fmt.bufPrint(&body_buf, "{{\n  \"action\": \"{s}\"\n}}", .{action}) catch return;

    const host_w   = comptime utf8ToUtf16Z("127.0.0.1");
    const verb_w   = comptime utf8ToUtf16Z("POST");
    const path_w   = comptime utf8ToUtf16Z("/execute");
    const ct_hdr_w = comptime utf8ToUtf16Z("Content-Type: application/json\r\n");

    const WINHTTP_ACCESS_TYPE_NO_PROXY: u32 = 1;
    const agent_w = comptime utf8ToUtf16Z("march-zig/1.0");

    const hsess = WinHttpOpen(&agent_w, WINHTTP_ACCESS_TYPE_NO_PROXY, null, null, 0) orelse return;
    defer _ = WinHttpCloseHandle(hsess);

    const hconn = WinHttpConnect(hsess, &host_w, 5001, 0) orelse return;
    defer _ = WinHttpCloseHandle(hconn);

    const hreq = WinHttpOpenRequest(hconn, &verb_w, &path_w, null, null, null, 0) orelse return;
    defer _ = WinHttpCloseHandle(hreq);

    if (!WinHttpSendRequest(hreq, &ct_hdr_w, @intCast(ct_hdr_w.len - 1), body.ptr, @intCast(body.len), @intCast(body.len), 0).toBool()) return;
    _ = WinHttpReceiveResponse(hreq, null);
}

// ── JSON helpers ──────────────────────────────────────────────────────────────

fn writeStrategiesJson(buf: []u8, strategies: []db.Strategy, count: usize) []u8 {
    var pos: usize = 0;
    buf[pos] = '['; pos += 1;
    for (strategies[0..count], 0..) |s, i| {
        if (i > 0) { buf[pos] = ','; pos += 1; }
        const piece = std.fmt.bufPrint(buf[pos..],
            "{{\"name\":\"{s}\",\"active\":{s}}}",
            .{ s.name[0..s.name_len], if (s.active) "true" else "false" }) catch break;
        pos += piece.len;
    }
    buf[pos] = ']'; pos += 1;
    return buf[0..pos];
}

// ── Bar parser (from JSON body) ────────────────────────────────────────────────

const ParsedBar = struct { bar: engine.Bar, ts: data.Ts, strategy: [64]u8, strategy_len: usize };

fn parseBarJson(json: []const u8) ?ParsedBar {
    var result: ParsedBar = undefined;
    result.strategy_len = 0;
    var bar: engine.Bar = .{ .open = 0, .high = 0, .low = 0, .close = 0, .volume = 0 };

    // strategy
    if (jsonStr(json, "strategy", &result.strategy)) |slen| {
        result.strategy_len = slen;
    } else return null;

    // ts → Ts ([16]u8 "YYYY-MM-DD HH:MM")
    var ts_str: [64]u8 = undefined;
    const ts_len = jsonStr(json, "ts", &ts_str) orelse return null;
    if (ts_len < 16) return null;
    @memcpy(&result.ts, ts_str[0..16]);

    bar.open   = jsonNum(json, "open")   orelse return null;
    bar.high   = jsonNum(json, "high")   orelse return null;
    bar.low    = jsonNum(json, "low")    orelse return null;
    bar.close  = jsonNum(json, "close")  orelse return null;
    const vol  = jsonNum(json, "volume") orelse 0.0;
    bar.volume = @intFromFloat(vol);
    result.bar = bar;
    return result;
}

// Pull `"key":"value"` → copies value into buf, returns length.
fn jsonStr(json: []const u8, key: []const u8, buf: []u8) ?usize {
    var key_buf: [72]u8 = undefined;
    const pat = std.fmt.bufPrint(&key_buf, "\"{s}\":\"", .{key}) catch return null;
    const start = std.mem.indexOf(u8, json, pat) orelse return null;
    const after = start + pat.len;
    const end = std.mem.indexOfScalar(u8, json[after..], '"') orelse return null;
    const n = @min(end, buf.len);
    @memcpy(buf[0..n], json[after..][0..n]);
    return n;
}

// Pull `"key":number` → f64.
fn jsonNum(json: []const u8, key: []const u8) ?f64 {
    var key_buf: [72]u8 = undefined;
    const pat = std.fmt.bufPrint(&key_buf, "\"{s}\":", .{key}) catch return null;
    const start = std.mem.indexOf(u8, json, pat) orelse return null;
    const after = start + pat.len;
    // Read until comma, }, or whitespace.
    var end: usize = 0;
    while (after + end < json.len) {
        const ch = json[after + end];
        if (ch == ',' or ch == '}' or ch == ' ' or ch == '\n' or ch == '\r') break;
        end += 1;
    }
    return std.fmt.parseFloat(f64, json[after..][0..end]) catch null;
}

// ── HTTP response helper ──────────────────────────────────────────────────────

fn sendResponse(
    io: std.Io,
    conn: std.Io.net.Stream,
    write_buf: []u8,
    status: []const u8,
    content_type: []const u8,
    body: []const u8,
) !void {
    var writer = conn.writer(io, write_buf);
    const w = &writer.interface;

    try w.print("HTTP/1.1 {s}\r\n", .{status});
    try w.print("Content-Type: {s}\r\n", .{content_type});
    try w.print("Content-Length: {d}\r\n", .{body.len});
    try w.print("Connection: close\r\n\r\n", .{});
    if (body.len > 0) {
        try w.writeAll(body);
    }
    try w.flush();
}

// ── Router ────────────────────────────────────────────────────────────────────

fn handleRoute(
    io: std.Io,
    conn: std.Io.net.Stream,
    write_buf: []u8,
    method: []const u8,
    path: []const u8,
    body: []const u8,
) !void {
    if (std.mem.eql(u8, path, "/health")) {
        return sendResponse(io, conn, write_buf, "200 OK", "application/json", "{\"status\":\"ok\"}");
    }

    if (std.mem.eql(u8, path, "/strategies") and std.mem.eql(u8, method, "GET")) {
        const sqlite = db.open() catch {
            return sendResponse(io, conn, write_buf, "500 Internal Server Error", "application/json", "{\"error\":\"db\"}");
        };
        defer db.close(sqlite);

        var strategies: [16]db.Strategy = undefined;
        const count = db.listStrategies(sqlite, &strategies);

        var json_buf: [2048]u8 = undefined;
        const json = writeStrategiesJson(&json_buf, &strategies, count);
        return sendResponse(io, conn, write_buf, "200 OK", "application/json", json);
    }

    if (std.mem.eql(u8, path, "/bar") and std.mem.eql(u8, method, "POST")) {
        if (body.len == 0) {
            return sendResponse(io, conn, write_buf, "400 Bad Request", "application/json", "{\"error\":\"no body\"}");
        }

        const parsed = parseBarJson(body) orelse {
            return sendResponse(io, conn, write_buf, "400 Bad Request", "application/json", "{\"error\":\"bad json\"}");
        };

        const strat_name = parsed.strategy[0..parsed.strategy_len];

        g_mutex.lockUncancelable(io);
        defer g_mutex.unlock(io);

        const slot = findSlot(strat_name) orelse {
            return sendResponse(io, conn, write_buf, "200 OK", "application/json", "{\"signal\":\"inactive\"}");
        };

        var inst = g_strategies[slot] orelse {
            return sendResponse(io, conn, write_buf, "200 OK", "application/json", "{\"signal\":\"inactive\"}");
        };

        const signal = inst.update(parsed.bar, parsed.ts);
        g_strategies[slot] = inst; // write back

        const sig_str: []const u8 = switch (signal) {
            .long  => "long",
            .short => "short",
            .flat  => "flat",
            .close => "close",
        };

        const prev = g_prev_signals[slot];
        if (signal != prev) {
            g_prev_signals[slot] = signal;
            switch (signal) {
                .long, .short, .close => {
                    _ = std.Thread.spawn(.{}, callPythonExecute, .{sig_str}) catch {};
                },
                .flat => {},
            }
        }

        var resp_buf: [64]u8 = undefined;
        const resp = std.fmt.bufPrint(&resp_buf, "{{\"signal\":\"{s}\"}}", .{sig_str}) catch "{\"signal\":\"?\"}";
        return sendResponse(io, conn, write_buf, "200 OK", "application/json", resp);
    }

    // PUT /strategies/:name/on  or  /strategies/:name/off
    if (std.mem.startsWith(u8, path, "/strategies/") and std.mem.eql(u8, method, "PUT")) {
        var active = false;
        var name_part: []const u8 = "";
        if (std.mem.endsWith(u8, path, "/on")) {
            active = true;
            name_part = path["/strategies/".len .. path.len - "/on".len];
        } else if (std.mem.endsWith(u8, path, "/off")) {
            active = false;
            name_part = path["/strategies/".len .. path.len - "/off".len];
        }

        if (name_part.len == 0) {
            return sendResponse(io, conn, write_buf, "400 Bad Request", "application/json", "{\"error\":\"missing name\"}");
        }

        const sqlite = db.open() catch {
            return sendResponse(io, conn, write_buf, "500 Internal Server Error", "application/json", "{\"error\":\"db\"}");
        };
        defer db.close(sqlite);

        const ok = db.setActive(sqlite, name_part, active);
        if (!ok) {
            return sendResponse(io, conn, write_buf, "404 Not Found", "application/json", "{\"error\":\"not found\"}");
        }

        if (active) {
            activateStrategy(io, name_part);
        } else {
            deactivateStrategy(io, name_part);
        }

        return sendResponse(io, conn, write_buf, "200 OK", "application/json", "{\"ok\":true}");
    }

    return sendResponse(io, conn, write_buf, "404 Not Found", "application/json", "{\"error\":\"not found\"}");
}

// ── main ──────────────────────────────────────────────────────────────────────

pub fn main(init: std.process.Init) !void {
    const io = init.io;

    // Ensure DB + schema are ready at startup.
    const startup_db = try db.open();
    db.close(startup_db);

    const addr = try std.Io.net.IpAddress.parse("127.0.0.1", API_PORT);
    var srv = try addr.listen(io, .{ .reuse_address = true });
    defer srv.deinit(io);

    std.debug.print("march API listening on http://127.0.0.1:{d}\n", .{API_PORT});
    std.debug.print("Python API target: {s}\n", .{PYTHON_API_URL});

    var read_buf: [8192]u8 = undefined;
    var write_buf: [8192]u8 = undefined;

    while (true) {
        const conn = srv.accept(io) catch |err| {
            std.debug.print("accept error: {any}\n", .{err});
            continue;
        };
        handleConnection(io, conn, &read_buf, &write_buf) catch |err| {
            std.debug.print("connection error: {any}\n", .{err});
        };
    }
}

fn handleConnection(
    io: std.Io,
    conn: std.Io.net.Stream,
    read_buf: []u8,
    write_buf: []u8,
) !void {
    defer conn.close(io);

    var reader = conn.reader(io, read_buf);
    var r = &reader.interface;

    const req_line_opt = try r.takeDelimiter('\n');
    const req_line = req_line_opt orelse return;
    const line_trim = std.mem.trimEnd(u8, req_line, "\r");
    var parts = std.mem.tokenizeScalar(u8, line_trim, ' ');
    const method = parts.next() orelse "";
    const path = parts.next() orelse "";

    var content_length: usize = 0;
    while (true) {
        const hdr_opt = try r.takeDelimiter('\n');
        const hdr_raw = hdr_opt orelse break;
        const hdr = std.mem.trimEnd(u8, hdr_raw, "\r");
        if (hdr.len == 0) break;

        if (std.ascii.startsWithIgnoreCase(hdr, "content-length:")) {
            const val_part = std.mem.trim(u8, hdr["content-length:".len..], " ");
            content_length = std.fmt.parseInt(usize, val_part, 10) catch 0;
        }
    }

    var body: []const u8 = &.{};
    if (content_length > 0) {
        body = try r.take(content_length);
    }

    try handleRoute(io, conn, write_buf, method, path, body);
}
