// web/backend/src/march/api.zig
//
// March live-trading engine — runs as a thread inside the web backend process.
// Routes are served on the main web port (8080) via router.zig; no separate
// port-4000 server exists anymore. Outgoing HTTP calls (Python :5001, QuestDB
// :9000) use std.Io.net so the code compiles on macOS/Linux too.
//
// The Bookmap WebSocket client (ws.zig) is Windows-only because Bookmap is
// Windows-only; it is gated with a comptime OS check inside wsClientLoop so
// it never analyzes ws2_32 externs on macOS/Linux.
//
// Endpoints (handled by handleRequest(), called from router.zig):
//   GET  /api/march/strategies                      → list strategies
//   PUT  /api/march/strategies/:name/on|off         → toggle strategy
//   POST /api/march/bar                             → feed bar, get signal
//   GET  /api/march/trades                          → list live trades
//   *    /api/march/mt5/accounts/**                 → MT5 account CRUD
//
// Python API (called on signal transitions):
//   POST http://127.0.0.1:5001/execute  {"action":"long"|"short"|"close","volume":…}

const std = @import("std");
const builtin = @import("builtin");
const db = @import("db.zig");
const engine = @import("engine.zig");
const data = @import("data.zig");
const sizing = @import("sizings/vol_target.zig");
const http = @import("../http.zig");
const questdb = @import("../questdb.zig");

const RthVwap = @import("strategies/rth_vwap.zig").RthVwap;
const OrbBuy = @import("strategies/30m_buy.zig").OrbBuy;
const MinLoop = @import("strategies/min_loop.zig").MinLoop;

// ── Strategy registry (global in-process state) ────────────────────────────────

const StrategyTag = enum { rth_vwap, orb_buy, min_loop };

const StrategyInstance = union(StrategyTag) {
    rth_vwap: RthVwap,
    orb_buy: OrbBuy,
    min_loop: MinLoop,

    fn update(self: *StrategyInstance, bar: engine.Bar, ts: data.Ts) engine.Signal {
        return switch (self.*) {
            .rth_vwap => |*s| s.update(bar, ts),
            .orb_buy  => |*s| s.update(bar, ts),
            .min_loop => |*s| s.update(bar, ts),
        };
    }

    fn liveVolume(self: *const StrategyInstance) f64 {
        return switch (self.*) {
            .rth_vwap => |*s| s.contracts * s.leverage,
            .orb_buy  => |*s| s.contracts * s.leverage,
            .min_loop => |*s| s.contracts * s.leverage,
        };
    }
};

const MAX_STRATEGIES = 8;
var g_strategies: [MAX_STRATEGIES]?StrategyInstance = [_]?StrategyInstance{null} ** MAX_STRATEGIES;
var g_strategy_names: [MAX_STRATEGIES][64]u8 = [_][64]u8{[_]u8{0} ** 64} ** MAX_STRATEGIES;
var g_strategy_count: usize = 0;
var g_mutex = std.Io.Mutex.init;

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
    if (std.mem.eql(u8, name, "min_loop")) return .min_loop;
    return null;
}

fn activateStrategy(io: std.Io, name: []const u8) void {
    g_mutex.lockUncancelable(io);
    defer g_mutex.unlock(io);

    const tag = tagFromName(name) orelse return;
    const slot = findSlot(name) orelse blk: {
        if (g_strategy_count >= MAX_STRATEGIES) return;
        const idx = g_strategy_count;
        g_strategy_count += 1;
        const n = @min(name.len, 63);
        @memcpy(g_strategy_names[idx][0..n], name[0..n]);
        g_strategy_names[idx][n] = 0;
        break :blk idx;
    };
    g_strategies[slot] = switch (tag) {
        .rth_vwap => .{ .rth_vwap = .{} },
        .orb_buy  => .{ .orb_buy  = .{} },
        .min_loop => .{ .min_loop = .{} },
    };
    g_prev_signals[slot] = .flat;

    if (g_strategies[slot]) |*inst| switch (inst.*) {
        .rth_vwap => |*s| if (s.sizing_mode == .vol_target) warmupVolTarget(io, &s.vol),
        .orb_buy  => |*s| if (s.sizing_mode == .vol_target) warmupVolTarget(io, &s.vol),
        .min_loop => {},
    };
}

fn deactivateStrategy(io: std.Io, name: []const u8) void {
    g_mutex.lockUncancelable(io);
    defer g_mutex.unlock(io);

    const slot = findSlot(name) orelse return;
    g_strategies[slot] = null;
}

// ── Outgoing HTTP: Python execute (cross-platform via std.Io.net) ─────────────

fn nameToBuf(name: []const u8) struct { buf: [64]u8, len: usize } {
    var buf: [64]u8 = [_]u8{0} ** 64;
    const n = @min(name.len, 64);
    @memcpy(buf[0..n], name[0..n]);
    return .{ .buf = buf, .len = n };
}

fn callPythonExecute(io: std.Io, action: []const u8, volume: f64, strategy: [64]u8, strategy_len: usize, trade_id: i64, closed_trade_id: i64) void {
    var body_buf: [256]u8 = undefined;
    const body = std.fmt.bufPrint(&body_buf,
        "{{\"action\":\"{s}\",\"volume\":{d},\"strategy\":\"{s}\",\"trade_id\":{d},\"closed_trade_id\":{d}}}",
        .{ action, volume, strategy[0..strategy_len], trade_id, closed_trade_id }) catch return;

    const addr = std.Io.net.IpAddress.parse("127.0.0.1", 5001) catch return;
    var stream = addr.connect(io, .{ .mode = .stream }) catch return;
    defer stream.close(io);

    var wbuf: [1024]u8 = undefined;
    var sw = stream.writer(io, &wbuf);
    const w = &sw.interface;
    w.print("POST /execute HTTP/1.1\r\nHost: 127.0.0.1:5001\r\nContent-Type: application/json\r\nContent-Length: {d}\r\nConnection: close\r\n\r\n", .{body.len}) catch return;
    w.writeAll(body) catch return;
    w.flush() catch return;
}

// ── QuestDB daily closes warm-up (cross-platform via questdb.zig) ─────────────

fn fetchDailyCloses(io: std.Io, out: []f64) usize {
    const alloc = std.heap.page_allocator;
    const reader = questdb.open(io, alloc, "SELECT close FROM nq_1d ORDER BY timestamp") catch return 0;
    defer reader.deinit();

    var count: usize = 0;
    var skip_header = true;
    while (reader.nextLine()) |line| {
        if (skip_header) { skip_header = false; continue; }
        if (count >= out.len) break;
        out[count] = std.fmt.parseFloat(f64, line) catch continue;
        count += 1;
    }
    return count;
}

fn warmupVolTarget(io: std.Io, vol: *sizing.VolTarget) void {
    var closes: [8192]f64 = undefined;
    const n = fetchDailyCloses(io, &closes);
    for (closes[0..n]) |c| vol.onBar(c, true);
    std.debug.print(
        "[SIZING] vol_target warmed from {d} nq_1d closes (needs > {d} returns to scale)\n",
        .{ n, vol.min_days },
    );
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

fn writeAccountsJson(buf: []u8, accounts: []db.Mt5Account, count: usize) []u8 {
    var pos: usize = 0;
    buf[pos] = '['; pos += 1;
    for (accounts[0..count], 0..) |a, i| {
        if (i > 0) { buf[pos] = ','; pos += 1; }
        const piece = std.fmt.bufPrint(buf[pos..],
            "{{\"id\":{d},\"name\":\"{s}\",\"login\":\"{s}\",\"server\":\"{s}\"}}",
            .{ a.id, a.name[0..a.name_len], a.login[0..a.login_len], a.server[0..a.server_len] }) catch break;
        pos += piece.len;
    }
    buf[pos] = ']'; pos += 1;
    return buf[0..pos];
}

fn writeAccountStrategiesJson(buf: []u8, items: []db.AccountStrategy, count: usize) []u8 {
    var pos: usize = 0;
    buf[pos] = '['; pos += 1;
    for (items[0..count], 0..) |s, i| {
        if (i > 0) { buf[pos] = ','; pos += 1; }
        const piece = std.fmt.bufPrint(buf[pos..],
            "{{\"id\":{d},\"strategy\":\"{s}\",\"symbol\":\"{s}\",\"active\":{s}}}",
            .{ s.id, s.strategy[0..s.strategy_len], s.symbol[0..s.symbol_len], if (s.active) "true" else "false" }) catch break;
        pos += piece.len;
    }
    buf[pos] = ']'; pos += 1;
    return buf[0..pos];
}

fn writeLiveTradesJson(buf: []u8, trades: []db.LiveTrade, count: usize) []u8 {
    var pos: usize = 0;
    buf[pos] = '['; pos += 1;
    for (trades[0..count], 0..) |t, i| {
        if (i > 0) { buf[pos] = ','; pos += 1; }
        const piece = std.fmt.bufPrint(buf[pos..],
            "{{\"id\":{d},\"strategy_name\":\"{s}\",\"side\":\"{s}\",\"contract\":{d:.4},\"zig_entry_price\":{d:.4},\"zig_close_price\":{d:.4},\"mt5_entry_price\":{d:.4},\"mt5_close_price\":{d:.4},\"zig_open_time\":\"{s}\",\"zig_close_time\":\"{s}\",\"mt5_open_time\":\"{s}\",\"mt5_close_time\":\"{s}\"}}",
            .{
                t.id,
                t.strategy_name[0..t.strategy_name_len],
                t.side[0..t.side_len],
                t.contract,
                t.zig_entry_price,
                t.zig_close_price,
                t.mt5_entry_price,
                t.mt5_close_price,
                t.zig_open_time[0..t.zig_open_time_len],
                t.zig_close_time[0..t.zig_close_time_len],
                t.mt5_open_time[0..t.mt5_open_time_len],
                t.mt5_close_time[0..t.mt5_close_time_len],
            }
        ) catch break;
        pos += piece.len;
    }
    buf[pos] = ']'; pos += 1;
    return buf[0..pos];
}

// ── Bar parser ────────────────────────────────────────────────────────────────

const ParsedBar = struct { bar: engine.Bar, ts: data.Ts, strategy: [64]u8, strategy_len: usize };

fn parseBarJson(json: []const u8) ?ParsedBar {
    var result: ParsedBar = undefined;
    result.strategy_len = 0;
    var bar: engine.Bar = .{ .open = 0, .high = 0, .low = 0, .close = 0, .volume = 0 };

    if (jsonStr(json, "strategy", &result.strategy)) |slen| {
        result.strategy_len = slen;
    } else return null;

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

fn jsonNum(json: []const u8, key: []const u8) ?f64 {
    var key_buf: [72]u8 = undefined;
    const pat = std.fmt.bufPrint(&key_buf, "\"{s}\":", .{key}) catch return null;
    const start = std.mem.indexOf(u8, json, pat) orelse return null;
    const after = start + pat.len;
    var end: usize = 0;
    while (after + end < json.len) {
        const ch = json[after + end];
        if (ch == ',' or ch == '}' or ch == ' ' or ch == '\n' or ch == '\r') break;
        end += 1;
    }
    return std.fmt.parseFloat(f64, json[after..][0..end]) catch null;
}

// ── Trade DB logging ───────────────────────────────────────────────────────────

const TradeDbResult = struct {
    open_id: i64,
    close_id: i64,
};

fn handleTradeDbLogging(strat_name: []const u8, prev: engine.Signal, signal: engine.Signal, volume: f64, zig_price: f64, ts: data.Ts) TradeDbResult {
    var result = TradeDbResult{ .open_id = -1, .close_id = -1 };
    const sqlite = db.open() catch return result;
    defer db.close(sqlite);

    if (prev == .long or prev == .short) {
        result.close_id = db.logTradeClose(sqlite, strat_name, zig_price, ts[0..]);
    }
    if (signal == .long or signal == .short) {
        const side_str: []const u8 = if (signal == .long) "long" else "short";
        result.open_id = db.logTradeOpen(sqlite, strat_name, side_str, volume, zig_price, ts[0..]);
    }
    return result;
}

// ── Bar builder (tick aggregation) ───────────────────────────────────────────

const ParsedTick = struct {
    ts_nanos: i64,
    price: f64,
    size: f64,
};

const CompletedBar = struct {
    bar: engine.Bar,
    bar_start: i64,
};

const BarBuilder = struct {
    tf_secs: i64,
    current_start: i64 = 0,
    bar: engine.Bar = .{},
    has_data: bool = false,

    fn onTick(self: *BarBuilder, price: f64, size: f64, tick_secs: i64) ?CompletedBar {
        const bar_start = @divFloor(tick_secs, self.tf_secs) * self.tf_secs;
        var completed: ?CompletedBar = null;

        if (self.has_data and bar_start != self.current_start) {
            completed = .{ .bar = self.bar, .bar_start = self.current_start };
            self.has_data = false;
        }

        if (!self.has_data) {
            self.current_start = bar_start;
            self.bar = .{
                .open = price,
                .high = price,
                .low = price,
                .close = price,
                .volume = @intFromFloat(size),
            };
            self.has_data = true;
        } else {
            self.bar.close = price;
            if (price > self.bar.high) self.bar.high = price;
            if (price < self.bar.low) self.bar.low = price;
            self.bar.volume += @intFromFloat(size);
        }

        return completed;
    }
};

fn unixSecsToTs(secs: i64) data.Ts {
    const day_secs = @mod(secs, @as(i64, 86400));
    const days = @divFloor(secs, @as(i64, 86400));
    const hours: u8 = @intCast(@divFloor(day_secs, @as(i64, 3600)));
    const minutes: u8 = @intCast(@divFloor(@mod(day_secs, @as(i64, 3600)), @as(i64, 60)));

    const z = days + 719468;
    const era_num = if (z >= 0) z else z - 146096;
    const era = @divFloor(era_num, @as(i64, 146097));
    const doe = z - era * 146097;
    const yoe = @divFloor(doe - @divFloor(doe, @as(i64, 1460)) + @divFloor(doe, @as(i64, 36524)) - @divFloor(doe, @as(i64, 146096)), @as(i64, 365));
    const y = yoe + era * 400;
    const doy = doe - (365 * yoe + @divFloor(yoe, @as(i64, 4)) - @divFloor(yoe, @as(i64, 100)));
    const mp = @divFloor(5 * doy + 2, @as(i64, 153));
    const d_val: u8 = @intCast(doy - @divFloor(153 * mp + 2, @as(i64, 5)) + 1);
    const m_raw = if (mp < 10) mp + 3 else mp - 9;
    const m_val: u8 = @intCast(m_raw);
    const year: u16 = @intCast(if (m_raw <= 2) y + 1 else y);

    var ts: data.Ts = undefined;
    ts[0] = '0' + @as(u8, @intCast(year / 1000));
    ts[1] = '0' + @as(u8, @intCast(year / 100 % 10));
    ts[2] = '0' + @as(u8, @intCast(year / 10 % 10));
    ts[3] = '0' + @as(u8, @intCast(year % 10));
    ts[4] = '-';
    ts[5] = '0' + m_val / 10;
    ts[6] = '0' + m_val % 10;
    ts[7] = '-';
    ts[8] = '0' + d_val / 10;
    ts[9] = '0' + d_val % 10;
    ts[10] = ' ';
    ts[11] = '0' + hours / 10;
    ts[12] = '0' + hours % 10;
    ts[13] = ':';
    ts[14] = '0' + minutes / 10;
    ts[15] = '0' + minutes % 10;
    return ts;
}

fn parseTicks(payload: []const u8, ticks: []ParsedTick) usize {
    var count: usize = 0;
    var pos: usize = 0;

    while (pos < payload.len and count < ticks.len) {
        const brace_rel = std.mem.indexOfScalar(u8, payload[pos..], '{') orelse break;
        const brace = pos + brace_rel;
        const close_rel = std.mem.indexOfScalar(u8, payload[brace..], '}') orelse break;
        const obj_end = brace + close_rel + 1;
        const obj = payload[brace..obj_end];

        var sym_buf: [16]u8 = undefined;
        if (jsonStr(obj, "sym", &sym_buf)) |sym_len| {
            if (sym_len != 2 or sym_buf[0] != 'N' or sym_buf[1] != 'Q') {
                pos = obj_end;
                continue;
            }
        }

        const ts_f = jsonNum(obj, "ts") orelse { pos = obj_end; continue; };
        const price = jsonNum(obj, "price") orelse { pos = obj_end; continue; };
        const size = jsonNum(obj, "size") orelse 1.0;

        ticks[count] = .{
            .ts_nanos = @intFromFloat(ts_f),
            .price = price,
            .size = size,
        };
        count += 1;
        pos = obj_end;
    }

    return count;
}

fn feedCompletedBar(io: std.Io, bar: engine.Bar, bar_start: i64, strat_name: []const u8) void {
    const ts = unixSecsToTs(bar_start);

    g_mutex.lockUncancelable(io);
    defer g_mutex.unlock(io);

    const slot = findSlot(strat_name) orelse return;
    var inst = g_strategies[slot] orelse return;

    const signal = inst.update(bar, ts);
    const volume = inst.liveVolume();
    g_strategies[slot] = inst;

    const prev = g_prev_signals[slot];
    const is_min_loop = std.mem.eql(u8, strat_name, "min_loop");
    const is_min_loop_trade_bar = if (is_min_loop) switch (inst) {
        .min_loop => |s| s.bar_count % 2 == 1,
        else => false,
    } else false;
    if (signal != prev or (is_min_loop and signal == .long and is_min_loop_trade_bar)) {
        g_prev_signals[slot] = signal;
        const db_res = handleTradeDbLogging(strat_name, prev, signal, volume, bar.close, ts);
        const sig_str: []const u8 = switch (signal) {
            .long => "long",
            .short => "short",
            .flat => "flat",
            .close => "close",
        };
        std.debug.print("[WS] {s} -> {s} (vol={d}, open_id={d}, close_id={d})\n", .{ strat_name, sig_str, volume, db_res.open_id, db_res.close_id });
        switch (signal) {
            .long, .short, .close => {
                const sn = nameToBuf(strat_name);
                _ = std.Thread.spawn(.{}, callPythonExecute, .{ io, sig_str, volume, sn.buf, sn.len, db_res.open_id, db_res.close_id }) catch {};
            },
            .flat => {},
        }
    }
}

// ── WebSocket tick feed (Windows-only: Bookmap live-push on :8765) ────────────
// ws.zig uses Winsock2 (ws2_32) which only exists on Windows. The comptime OS
// check at the top of wsClientLoop prevents ws.zig from being analyzed on macOS
// or Linux.

fn wsClientLoop(io: std.Io) void {
    if (comptime builtin.os.tag != .windows) return;
    const ws = @import("ws.zig");

    ws.initWsa() catch {
        std.debug.print("[WS] WSAStartup failed\n", .{});
        return;
    };
    defer ws.cleanupWsa();

    var builder_1m = BarBuilder{ .tf_secs = 60 };
    var builder_5m = BarBuilder{ .tf_secs = 300 };

    var frame_buf: [262144]u8 = undefined;
    var tick_buf: [2048]ParsedTick = undefined;

    std.debug.print("[WS] client starting, target ws://127.0.0.1:8765\n", .{});

    while (true) {
        var client = ws.WsClient.connectTo(0x7f000001, 8765) catch {
            io.sleep(std.Io.Duration.fromSeconds(1), .awake) catch {};
            continue;
        };

        client.upgrade() catch {
            client.close();
            io.sleep(std.Io.Duration.fromSeconds(1), .awake) catch {};
            continue;
        };

        std.debug.print("[WS] connected to Bookmap live-push\n", .{});

        builder_1m = BarBuilder{ .tf_secs = 60 };
        builder_5m = BarBuilder{ .tf_secs = 300 };

        while (true) {
            const frame = client.readFrame(&frame_buf) catch break;

            switch (frame.frame_type) {
                .ping => {
                    client.sendPong(frame.payload) catch break;
                },
                .close => break,
                .text => {
                    const count = parseTicks(frame.payload, &tick_buf);
                    for (tick_buf[0..count]) |tick| {
                        const tick_secs = @divFloor(tick.ts_nanos, @as(i64, 1_000_000_000));

                        if (builder_1m.onTick(tick.price, tick.size, tick_secs)) |c| {
                            feedCompletedBar(io, c.bar, c.bar_start, "rth_vwap");
                            feedCompletedBar(io, c.bar, c.bar_start, "min_loop");
                        }
                        if (builder_5m.onTick(tick.price, tick.size, tick_secs)) |c| {
                            feedCompletedBar(io, c.bar, c.bar_start, "orb_buy");
                        }
                    }
                },
                else => {},
            }
        }

        client.close();
        std.debug.print("[WS] disconnected, reconnecting in 1s\n", .{});
        io.sleep(std.Io.Duration.fromSeconds(1), .awake) catch {};
    }
}

// ── init: start engine (called once from main.zig on Windows) ─────────────────

pub fn init(io: std.Io) void {
    const startup_db = db.open() catch return;
    var active_names: [16][64]u8 = undefined;
    const active_count = db.listActiveStrategyNames(startup_db, &active_names);
    for (active_names[0..active_count]) |nm| {
        const name = std.mem.sliceTo(&nm, 0);
        activateStrategy(io, name);
        std.debug.print("[INIT] re-armed strategy '{s}' (active account exists)\n", .{name});
    }
    db.close(startup_db);

    _ = std.Thread.spawn(.{}, wsClientLoop, .{io}) catch |err| {
        std.debug.print("Failed to start WS client: {any}\n", .{err});
    };
}

// ── handleRequest: march routes served from the main web port (8080) ──────────
// Called by router.zig for /api/march/strategies, /api/march/bar,
// /api/march/trades, /api/march/mt5/accounts paths. Returns true if handled.

pub fn handleRequest(req: *http.Ctx) !bool {
    const path = req.path orelse return false;
    const method = req.method orelse "GET";
    const io = req.io;

    // GET /api/march/strategies
    if (std.mem.eql(u8, path, "/api/march/strategies")) {
        if (!std.mem.eql(u8, method, "GET")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"method\"}");
            return true;
        }
        const sqlite = db.open() catch {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"db\"}");
            return true;
        };
        defer db.close(sqlite);
        var strategies: [16]db.Strategy = undefined;
        const count = db.listStrategies(sqlite, &strategies);
        var json_buf: [2048]u8 = undefined;
        const json = writeStrategiesJson(&json_buf, &strategies, count);
        try req.sendJson(json);
        return true;
    }

    // PUT /api/march/strategies/:name/on|off
    if (std.mem.startsWith(u8, path, "/api/march/strategies/") and std.mem.eql(u8, method, "PUT")) {
        const rest = path["/api/march/strategies/".len..];
        var active = false;
        var name_part: []const u8 = "";
        if (std.mem.endsWith(u8, rest, "/on")) {
            active = true;
            name_part = rest[0 .. rest.len - "/on".len];
        } else if (std.mem.endsWith(u8, rest, "/off")) {
            name_part = rest[0 .. rest.len - "/off".len];
        }
        if (name_part.len == 0) {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"missing name\"}");
            return true;
        }
        const sqlite = db.open() catch {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"db\"}");
            return true;
        };
        defer db.close(sqlite);
        const ok = db.setActive(sqlite, name_part, active);
        if (!ok) {
            req.setStatusNumeric(404);
            try req.sendJson("{\"error\":\"not found\"}");
            return true;
        }
        if (active) {
            activateStrategy(io, name_part);
        } else {
            deactivateStrategy(io, name_part);
        }
        try req.sendJson("{\"ok\":true}");
        return true;
    }

    // POST /api/march/bar
    if (std.mem.eql(u8, path, "/api/march/bar") and std.mem.eql(u8, method, "POST")) {
        const body = req.body orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"no body\"}");
            return true;
        };
        const parsed = parseBarJson(body) orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"bad json\"}");
            return true;
        };
        const strat_name = parsed.strategy[0..parsed.strategy_len];

        g_mutex.lockUncancelable(io);
        defer g_mutex.unlock(io);

        const slot = findSlot(strat_name) orelse {
            try req.sendJson("{\"signal\":\"inactive\"}");
            return true;
        };
        var inst = g_strategies[slot] orelse {
            try req.sendJson("{\"signal\":\"inactive\"}");
            return true;
        };
        const signal = inst.update(parsed.bar, parsed.ts);
        const volume = inst.liveVolume();
        g_strategies[slot] = inst;

        const sig_str: []const u8 = switch (signal) {
            .long  => "long",
            .short => "short",
            .flat  => "flat",
            .close => "close",
        };

        const prev = g_prev_signals[slot];
        const is_min_loop = std.mem.eql(u8, strat_name, "min_loop");
        if (signal != prev or (is_min_loop and signal == .long)) {
            g_prev_signals[slot] = signal;
            const db_res = handleTradeDbLogging(strat_name, prev, signal, volume, parsed.bar.close, parsed.ts);
            switch (signal) {
                .long, .short, .close => {
                    const sn = nameToBuf(strat_name);
                    _ = std.Thread.spawn(.{}, callPythonExecute, .{ io, sig_str, volume, sn.buf, sn.len, db_res.open_id, db_res.close_id }) catch {};
                },
                .flat => {},
            }
        }

        var resp_buf: [64]u8 = undefined;
        const resp = std.fmt.bufPrint(&resp_buf, "{{\"signal\":\"{s}\"}}", .{sig_str}) catch "{\"signal\":\"?\"}";
        try req.sendJson(resp);
        return true;
    }

    // GET /api/march/trades
    if (std.mem.eql(u8, path, "/api/march/trades") and std.mem.eql(u8, method, "GET")) {
        const sqlite = db.open() catch {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"db\"}");
            return true;
        };
        defer db.close(sqlite);
        var trades: [256]db.LiveTrade = undefined;
        const count = db.listTrades(sqlite, &trades);
        const allocator = std.heap.page_allocator;
        const json_buf = allocator.alloc(u8, 256 * 400) catch {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"alloc\"}");
            return true;
        };
        defer allocator.free(json_buf);
        const json = writeLiveTradesJson(json_buf, &trades, count);
        try req.sendJson(json);
        return true;
    }

    // /api/march/mt5/accounts/**
    if (std.mem.startsWith(u8, path, "/api/march/mt5/accounts")) {
        try handleMt5Request(req, io, path, method, req.body orelse "");
        return true;
    }

    return false;
}

fn handleMt5Request(req: *http.Ctx, io: std.Io, path: []const u8, method: []const u8, body: []const u8) !void {
    var it = std.mem.tokenizeScalar(u8, path, '/');
    _ = it.next(); // "api"
    _ = it.next(); // "march"
    _ = it.next(); // "mt5"
    _ = it.next(); // "accounts"
    const seg_id = it.next();
    const seg_sub = it.next();
    const seg_sid = it.next();
    const seg_action = it.next();

    const sqlite = db.open() catch {
        req.setStatusNumeric(500);
        try req.sendJson("{\"error\":\"db\"}");
        return;
    };
    defer db.close(sqlite);

    // ── Collection: /api/march/mt5/accounts ──
    if (seg_id == null) {
        if (std.mem.eql(u8, method, "GET")) {
            var accounts: [64]db.Mt5Account = undefined;
            const count = db.listMt5Accounts(sqlite, &accounts);
            var json_buf: [8192]u8 = undefined;
            const json = writeAccountsJson(&json_buf, &accounts, count);
            try req.sendJson(json);
            return;
        }
        if (std.mem.eql(u8, method, "POST")) {
            var name_buf: [64]u8 = undefined;
            var login_buf: [32]u8 = undefined;
            var pass_buf: [128]u8 = undefined;
            var server_buf: [64]u8 = undefined;
            const name = name_buf[0 .. jsonStr(body, "name", &name_buf) orelse 0];
            const login = login_buf[0 .. jsonStr(body, "login", &login_buf) orelse 0];
            const password = pass_buf[0 .. jsonStr(body, "password", &pass_buf) orelse 0];
            const server = server_buf[0 .. jsonStr(body, "server", &server_buf) orelse 0];
            if (login.len == 0) {
                req.setStatusNumeric(400);
                try req.sendJson("{\"error\":\"missing login\"}");
                return;
            }
            const id = db.addMt5Account(sqlite, name, login, password, server);
            if (id < 0) {
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"insert\"}");
                return;
            }
            var resp_buf: [64]u8 = undefined;
            const resp = std.fmt.bufPrint(&resp_buf, "{{\"id\":{d}}}", .{id}) catch "{\"ok\":true}";
            try req.sendJson(resp);
            return;
        }
        req.setStatusNumeric(405);
        try req.sendJson("{\"error\":\"method\"}");
        return;
    }

    const account_id = std.fmt.parseInt(i64, seg_id.?, 10) catch {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"bad id\"}");
        return;
    };

    // ── Single account: /api/march/mt5/accounts/:id ──
    if (seg_sub == null) {
        if (std.mem.eql(u8, method, "DELETE")) {
            const ok = db.deleteMt5Account(sqlite, account_id);
            if (!ok) {
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"delete\"}");
                return;
            }
            try req.sendJson("{\"ok\":true}");
            return;
        }
        req.setStatusNumeric(405);
        try req.sendJson("{\"error\":\"method\"}");
        return;
    }

    if (!std.mem.eql(u8, seg_sub.?, "strategies")) {
        req.setStatusNumeric(404);
        try req.sendJson("{\"error\":\"not found\"}");
        return;
    }

    // ── Strategy collection: /api/march/mt5/accounts/:id/strategies ──
    if (seg_sid == null) {
        if (std.mem.eql(u8, method, "GET")) {
            var items: [64]db.AccountStrategy = undefined;
            const count = db.listAccountStrategies(sqlite, account_id, &items);
            var json_buf: [8192]u8 = undefined;
            const json = writeAccountStrategiesJson(&json_buf, &items, count);
            try req.sendJson(json);
            return;
        }
        if (std.mem.eql(u8, method, "POST")) {
            var strat_buf: [64]u8 = undefined;
            var symbol_buf: [32]u8 = undefined;
            const strategy = strat_buf[0 .. jsonStr(body, "strategy", &strat_buf) orelse 0];
            const symbol = symbol_buf[0 .. jsonStr(body, "symbol", &symbol_buf) orelse 0];
            if (strategy.len == 0) {
                req.setStatusNumeric(400);
                try req.sendJson("{\"error\":\"missing strategy\"}");
                return;
            }
            const id = db.addAccountStrategy(sqlite, account_id, strategy, symbol);
            if (id < 0) {
                req.setStatusNumeric(500);
                try req.sendJson("{\"error\":\"insert\"}");
                return;
            }
            var resp_buf: [64]u8 = undefined;
            const resp = std.fmt.bufPrint(&resp_buf, "{{\"id\":{d}}}", .{id}) catch "{\"ok\":true}";
            try req.sendJson(resp);
            return;
        }
        req.setStatusNumeric(405);
        try req.sendJson("{\"error\":\"method\"}");
        return;
    }

    const strat_id = std.fmt.parseInt(i64, seg_sid.?, 10) catch {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"bad id\"}");
        return;
    };

    // PUT /api/march/mt5/accounts/:id/strategies/:sid/on|off
    if (seg_action) |action| {
        if (!std.mem.eql(u8, method, "PUT")) {
            req.setStatusNumeric(405);
            try req.sendJson("{\"error\":\"method\"}");
            return;
        }
        const active = if (std.mem.eql(u8, action, "on")) true else if (std.mem.eql(u8, action, "off")) false else {
            req.setStatusNumeric(404);
            try req.sendJson("{\"error\":\"not found\"}");
            return;
        };

        var name_buf: [64]u8 = undefined;
        const nlen = db.accountStrategyName(sqlite, strat_id, &name_buf) orelse {
            req.setStatusNumeric(404);
            try req.sendJson("{\"error\":\"not found\"}");
            return;
        };
        const name = name_buf[0..nlen];

        if (!db.setAccountStrategyActive(sqlite, strat_id, active)) {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"update\"}");
            return;
        }

        if (active) {
            activateStrategy(io, name);
        } else if (!db.anyActiveForStrategy(sqlite, name)) {
            deactivateStrategy(io, name);
        }
        try req.sendJson("{\"ok\":true}");
        return;
    }

    // DELETE /api/march/mt5/accounts/:id/strategies/:sid
    if (std.mem.eql(u8, method, "DELETE")) {
        var name_buf: [64]u8 = undefined;
        const nlen = db.accountStrategyName(sqlite, strat_id, &name_buf);
        const ok = db.deleteAccountStrategy(sqlite, strat_id);
        if (!ok) {
            req.setStatusNumeric(500);
            try req.sendJson("{\"error\":\"delete\"}");
            return;
        }
        if (nlen) |n| {
            const name = name_buf[0..n];
            if (!db.anyActiveForStrategy(sqlite, name)) deactivateStrategy(io, name);
        }
        try req.sendJson("{\"ok\":true}");
        return;
    }
    req.setStatusNumeric(405);
    try req.sendJson("{\"error\":\"method\"}");
}
