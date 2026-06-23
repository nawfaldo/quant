const std = @import("std");
const engine = @import("engine.zig");
const data = @import("data.zig");

// Strategies — copy-pasted from backtest/src/strategies/ without modification.
const RthVwap = @import("strategies/rth_vwap.zig").RthVwap;
const OrbBuy = @import("strategies/30m_buy.zig").OrbBuy;
const BuyHold = @import("strategies/buy_hold.zig").BuyHold;

// ── Signal runner ─────────────────────────────────────────────────────────────
//
// Reads bars from stdin, feeds them to the selected strategy, writes signals
// to stdout. The Python side spawns this process and communicates over pipes.
//
// Protocol (line-based, UTF-8):
//   → STRATEGY rth_vwap           (or orb_buy, buy_hold)
//   → CONFIG key=value key=value  (contracts=0.1 leverage=1)
//   → BAR 2024-01-15 09:30,17500.50,17510.00,17498.00,17505.30,1234
//   ← LONG                        (or SHORT, FLAT, CLOSE)
//   → BAR ...
//   ← ...
//   → QUIT

const Strategy = union(enum) {
    rth_vwap: RthVwap,
    orb_buy: OrbBuy,
    buy_hold: BuyHold,

    fn update(self: *Strategy, bar: engine.Bar, ts: data.Ts) engine.Signal {
        return switch (self.*) {
            .rth_vwap => |*s| s.update(bar, ts),
            .orb_buy => |*s| s.update(bar, ts),
            .buy_hold => |*s| s.update(bar, ts),
        };
    }
};

pub fn main(init: std.process.Init) !void {
    const io = init.io;

    // Stdin — use streaming mode since pipes are not seekable.
    const stdin_file = std.Io.File.stdin();
    var read_buf: [4096]u8 = undefined;
    var stdin_reader = stdin_file.readerStreaming(io, &read_buf);
    const r = &stdin_reader.interface;

    // Stdout — use streaming mode for pipes.
    const stdout_file = std.Io.File.stdout();
    var write_buf: [4096]u8 = undefined;
    var stdout_writer = stdout_file.writerStreaming(io, &write_buf);
    const w = &stdout_writer.interface;

    var strat: Strategy = undefined;
    var strat_selected = false;

    // ── Phase 1: read STRATEGY line ───────────────────────────────────────
    var line_buf: [512]u8 = undefined;
    while (true) {
        const line = readLine(r, &line_buf) orelse break;
        if (line.len == 0) continue;

        if (std.mem.startsWith(u8, line, "STRATEGY ")) {
            const name = line["STRATEGY ".len..];
            if (std.mem.eql(u8, name, "rth_vwap")) {
                strat = .{ .rth_vwap = .{} };
            } else if (std.mem.eql(u8, name, "orb_buy")) {
                strat = .{ .orb_buy = .{} };
            } else if (std.mem.eql(u8, name, "buy_hold")) {
                strat = .{ .buy_hold = .{} };
            } else {
                try w.print("ERROR unknown strategy: {s}\n", .{name});
                try w.flush();
                return;
            }
            strat_selected = true;
            try w.print("OK strategy={s}\n", .{name});
            try w.flush();
            break;
        }
    }

    if (!strat_selected) return;

    // ── Phase 2: read CONFIG and BAR lines ────────────────────────────────
    while (true) {
        const line = readLine(r, &line_buf) orelse break;
        if (line.len == 0) continue;

        if (std.mem.startsWith(u8, line, "CONFIG ")) {
            parseConfig(&strat, line["CONFIG ".len..]);
            try w.print("OK config\n", .{});
            try w.flush();
            continue;
        }

        if (std.mem.eql(u8, line, "QUIT")) {
            try w.print("OK quit\n", .{});
            try w.flush();
            return;
        }

        if (std.mem.startsWith(u8, line, "BAR ")) {
            const bar_data = line["BAR ".len..];
            const parsed = parseBar(bar_data) orelse {
                try w.print("ERROR bad bar\n", .{});
                try w.flush();
                continue;
            };
            const signal = strat.update(parsed.bar, parsed.ts);
            const sig_str: []const u8 = switch (signal) {
                .long => "LONG",
                .short => "SHORT",
                .flat => "FLAT",
                .close => "CLOSE",
            };
            try w.print("{s}\n", .{sig_str});
            try w.flush();
            continue;
        }

        try w.print("ERROR unknown command\n", .{});
        try w.flush();
    }
}

// ── Line reader ───────────────────────────────────────────────────────────────
// Reads bytes one at a time until \n, trims \r, returns the line or null on EOF.
fn readLine(r: *std.Io.Reader, buf: []u8) ?[]const u8 {
    var pos: usize = 0;
    while (pos < buf.len) {
        const byte = r.takeByte() catch return null;
        if (byte == '\n') {
            // Trim trailing \r for Windows line endings.
            if (pos > 0 and buf[pos - 1] == '\r') {
                return buf[0 .. pos - 1];
            }
            return buf[0..pos];
        }
        buf[pos] = byte;
        pos += 1;
    }
    // Line too long — return what we have.
    return buf[0..pos];
}

// ── Bar parser ────────────────────────────────────────────────────────────────
// Format: "YYYY-MM-DD HH:MM,open,high,low,close,volume"
const ParsedBar = struct { bar: engine.Bar, ts: data.Ts };

fn parseBar(s: []const u8) ?ParsedBar {
    if (s.len < 17) return null;
    if (s[16] != ',') return null;

    var ts: data.Ts = undefined;
    @memcpy(&ts, s[0..16]);

    var rest = s[17..];
    const open = parseNextFloat(&rest) orelse return null;
    const high = parseNextFloat(&rest) orelse return null;
    const low = parseNextFloat(&rest) orelse return null;
    const close = parseNextFloat(&rest) orelse return null;
    const volume = parseNextInt(&rest) orelse return null;

    return .{
        .bar = .{ .open = open, .high = high, .low = low, .close = close, .volume = volume },
        .ts = ts,
    };
}

fn parseNextFloat(rest: *[]const u8) ?f64 {
    const s = rest.*;
    if (s.len == 0) return null;

    var end: usize = 0;
    while (end < s.len and s[end] != ',') : (end += 1) {}

    const val = std.fmt.parseFloat(f64, s[0..end]) catch return null;

    if (end < s.len) {
        rest.* = s[end + 1 ..];
    } else {
        rest.* = s[s.len..];
    }
    return val;
}

fn parseNextInt(rest: *[]const u8) ?i64 {
    const s = rest.*;
    if (s.len == 0) return null;

    var end: usize = 0;
    while (end < s.len and s[end] != ',') : (end += 1) {}

    const val = std.fmt.parseInt(i64, s[0..end], 10) catch return null;

    if (end < s.len) {
        rest.* = s[end + 1 ..];
    } else {
        rest.* = s[s.len..];
    }
    return val;
}

// ── Config parser ─────────────────────────────────────────────────────────────
fn parseConfig(strat: *Strategy, config_str: []const u8) void {
    var iter = std.mem.splitScalar(u8, config_str, ' ');
    while (iter.next()) |token| {
        if (token.len == 0) continue;
        if (std.mem.indexOfScalar(u8, token, '=')) |eq| {
            const key = token[0..eq];
            const val_str = token[eq + 1 ..];

            if (std.mem.eql(u8, key, "contracts")) {
                const v = std.fmt.parseFloat(f64, val_str) catch continue;
                switch (strat.*) {
                    .rth_vwap => |*s| s.contracts = v,
                    .orb_buy => |*s| s.contracts = v,
                    .buy_hold => |*s| s.contracts = v,
                }
            } else if (std.mem.eql(u8, key, "leverage")) {
                const v = std.fmt.parseFloat(f64, val_str) catch continue;
                switch (strat.*) {
                    .rth_vwap => |*s| {
                        s.contracts *= v;
                        s.leverage = v;
                    },
                    .orb_buy => |*s| {
                        s.contracts *= v;
                        s.leverage = v;
                    },
                    .buy_hold => {},
                }
            } else if (std.mem.eql(u8, key, "initial_balance")) {
                const v = std.fmt.parseFloat(f64, val_str) catch continue;
                switch (strat.*) {
                    .rth_vwap => |*s| s.initial_balance = v,
                    .orb_buy => |*s| s.initial_balance = v,
                    .buy_hold => |*s| s.initial_balance = v,
                }
            }
        }
    }
}
