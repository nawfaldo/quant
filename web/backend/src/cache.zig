const std = @import("std");
const questdb = @import("questdb.zig");

extern "c" fn usleep(usec: c_uint) c_int;

pub const ROW_BYTES: usize = 20;
pub const HEADER_BYTES: usize = 8;
pub const MAGIC: u32 = 0x45444C43;

pub const VWAP_MAGIC: u32 = 0x50415756;
pub const VWAP_ROW_BYTES: usize = 8;

pub const TF_NAMES = [_][]const u8{ "1m", "5m", "15m", "30m", "1h", "4h", "1d" };
pub const TF_COUNT = TF_NAMES.len;
pub const VALID_SYMBOLS = [_][]const u8{ "nq", "gbpusd", "eurusd" };

// On-demand only: nothing is cached. Each request builds the binary blob it
// needs straight from QuestDB and hands ownership to the caller, who frees it
// after sending. This keeps peak memory to a single blob at a time and means
// startup no longer scans every timeframe table (which thrashed an 8 GB Mac and
// raced QuestDB's post-restart partition hydration).

// Builds the binary candle blob for timeframe `name` (e.g. "5m") from QuestDB.
// Returns null if `name` is not a known timeframe; otherwise the caller owns
// the returned slice and must free it.
pub fn fetchTf(a: std.mem.Allocator, name: []const u8, symbol: []const u8, from: []const u8, to: []const u8) !?[]const u8 {
    for (TF_NAMES, 0..) |n, i| {
        if (std.mem.eql(u8, n, name)) return try withRetry(buildTf, .{ a, i, symbol, from, to });
    }
    return null;
}

// Builds the binary RTH VWAP blob (computed from nq_1m) from QuestDB. The
// caller owns the returned slice and must free it.
pub fn fetchVwap(a: std.mem.Allocator) ![]const u8 {
    return withRetry(buildVwap, .{a});
}

const RTH_OPEN_MIN: u32  = 9 * 60 + 30; // 09:30 ET
const RTH_CLOSE_MIN: u32 = 16 * 60;     // 16:00 ET

// QuestDB can transiently drop a connection mid-response when the cache fires
// several large queries back-to-back at startup. A truncated stream must never
// be cached, so each builder is retried with exponential backoff.
const MAX_ATTEMPTS: u32 = 6;

// Runs build() up to MAX_ATTEMPTS times, backing off between failures. build()
// returns error.IncompleteResponse on a truncated stream (the only retryable
// case); any partial output it allocated is freed by build()'s own errdefer.
fn withRetry(comptime build: anytype, args: anytype) ![]const u8 {
    var backoff_ns: u64 = 300 * std.time.ns_per_ms;
    var attempt: u32 = 1;
    while (true) : (attempt += 1) {
        return @call(.auto, build, args) catch |err| {
            if (attempt >= MAX_ATTEMPTS) return err;
            std.debug.print("cache build attempt {} failed ({}); retrying in {} ms\n", .{ attempt, err, backoff_ns / std.time.ns_per_ms });
            _ = usleep(@intCast(backoff_ns / std.time.ns_per_us));
            if (backoff_ns < 5 * std.time.ns_per_s) backoff_ns *= 2;
            continue;
        };
    }
}

fn buildTf(a: std.mem.Allocator, idx: usize, symbol: []const u8, from: []const u8, to: []const u8) ![]const u8 {
    // Bound the scan to [from, to] inclusive. `dateadd('d', 1, to)` makes the
    // upper bound the start of the day after `to`, so the whole `to` day is kept.
    const table = try std.fmt.allocPrint(a, "{s}_{s}", .{ symbol, TF_NAMES[idx] });
    defer a.free(table);
    const sql = try std.fmt.allocPrint(a,
        "SELECT cast(timestamp as long) ts, open, high, low, close FROM {s}" ++
        " WHERE timestamp >= '{s}' AND timestamp < dateadd('d', 1, '{s}')" ++
        " ORDER BY timestamp ASC",
        .{ table, from, to },
    );
    defer a.free(sql);

    var rd = try questdb.open(a, sql);
    defer rd.deinit();

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendNTimes(a, 0, HEADER_BYTES);

    _ = rd.nextLine(); // skip the CSV column-name header row
    var count: u32 = 0;

    while (rd.nextLine()) |line| {
        if (line.len == 0) continue;

        var c1: usize = 0;
        while (c1 < line.len and line[c1] != ',') : (c1 += 1) {}
        var c2: usize = c1 + 1;
        while (c2 < line.len and line[c2] != ',') : (c2 += 1) {}
        var c3: usize = c2 + 1;
        while (c3 < line.len and line[c3] != ',') : (c3 += 1) {}
        var c4: usize = c3 + 1;
        while (c4 < line.len and line[c4] != ',') : (c4 += 1) {}
        if (c4 >= line.len) continue;

        const ts_micros = std.fmt.parseInt(i64, line[0..c1], 10) catch continue;
        const open  = std.fmt.parseFloat(f32, line[c1 + 1 .. c2]) catch continue;
        const high  = std.fmt.parseFloat(f32, line[c2 + 1 .. c3]) catch continue;
        const low   = std.fmt.parseFloat(f32, line[c3 + 1 .. c4]) catch continue;
        const close = std.fmt.parseFloat(f32, line[c4 + 1 ..])    catch continue;

        const ts_secs: u32 = @intCast(@divFloor(ts_micros, 1_000_000));
        try out.ensureUnusedCapacity(a, ROW_BYTES);
        const dst = out.items.len;
        out.items.len += ROW_BYTES;
        std.mem.writeInt(u32, out.items[dst..][0..4],      ts_secs,        .little);
        std.mem.writeInt(u32, out.items[dst + 4 ..][0..4], @bitCast(open), .little);
        std.mem.writeInt(u32, out.items[dst + 8 ..][0..4], @bitCast(high), .little);
        std.mem.writeInt(u32, out.items[dst + 12..][0..4], @bitCast(low),  .little);
        std.mem.writeInt(u32, out.items[dst + 16..][0..4], @bitCast(close),.little);
        count += 1;
    }

    // A truncated stream must never be cached; discard the partial parse so the
    // retry path starts clean.
    if (!rd.complete) return error.IncompleteResponse;

    std.mem.writeInt(u32, out.items[0..4], MAGIC, .little);
    std.mem.writeInt(u32, out.items[4..8], count, .little);
    return out.toOwnedSlice(a);
}

// RTH VWAP is computed in code from nq_1m OHLCV (not stored in DB).
// typical = (high+low+close)/3, vwap = Σ(typical×volume)/Σ(volume),
// anchored per ET calendar day, reset each session open.
fn buildVwap(a: std.mem.Allocator) ![]const u8 {
    var rd = try questdb.open(a,
        "SELECT cast(timestamp as long) ts, high, low, close, volume FROM nq_1m ORDER BY timestamp ASC",
    );
    defer rd.deinit();

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendNTimes(a, 0, HEADER_BYTES);

    _ = rd.nextLine(); // skip the CSV column-name header row

    var cur_day: i64 = -1;
    var cum_pv: f64  = 0;
    var cum_vol: f64 = 0;
    var count: u32   = 0;

    while (rd.nextLine()) |line| {
        if (line.len == 0) continue;

        var c1: usize = 0;
        while (c1 < line.len and line[c1] != ',') : (c1 += 1) {}
        var c2: usize = c1 + 1;
        while (c2 < line.len and line[c2] != ',') : (c2 += 1) {}
        var c3: usize = c2 + 1;
        while (c3 < line.len and line[c3] != ',') : (c3 += 1) {}
        var c4: usize = c3 + 1;
        while (c4 < line.len and line[c4] != ',') : (c4 += 1) {}
        if (c4 >= line.len) continue;

        const ts_micros = std.fmt.parseInt(i64, line[0..c1], 10) catch continue;
        const high   = std.fmt.parseFloat(f64, line[c1 + 1 .. c2]) catch continue;
        const low    = std.fmt.parseFloat(f64, line[c2 + 1 .. c3]) catch continue;
        const close  = std.fmt.parseFloat(f64, line[c3 + 1 .. c4]) catch continue;
        const volume = std.fmt.parseFloat(f64, line[c4 + 1 ..])    catch continue;

        const ts_secs: u32 = @intCast(@divFloor(ts_micros, 1_000_000));

        // Timestamps are already ET wall-clock stored as fake-UTC by the importer
        // (see CLAUDE.md timezone model). Do NOT apply any timezone conversion —
        // derive the ET day and minute-of-day directly, the same way the frontend
        // candle/OpeningRange logic does.
        const et_day: i64  = @divFloor(@as(i64, ts_secs), 86_400);
        const min_of_day: u32 = @intCast(@divFloor(@mod(@as(i64, ts_secs), 86_400), 60));

        if (et_day != cur_day) {
            cur_day = et_day;
            cum_pv  = 0;
            cum_vol = 0;
        }

        var rth: f32 = 0;
        if (min_of_day >= RTH_OPEN_MIN and min_of_day < RTH_CLOSE_MIN) {
            const typical = (high + low + close) / 3.0;
            cum_pv  += typical * volume;
            cum_vol += volume;
            if (cum_vol > 0) rth = @floatCast(cum_pv / cum_vol);
        }

        try out.ensureUnusedCapacity(a, VWAP_ROW_BYTES);
        const dst = out.items.len;
        out.items.len += VWAP_ROW_BYTES;
        std.mem.writeInt(u32, out.items[dst..][0..4],      ts_secs,        .little);
        std.mem.writeInt(u32, out.items[dst + 4 ..][0..4], @bitCast(rth),  .little);
        count += 1;
    }

    if (!rd.complete) return error.IncompleteResponse;

    std.mem.writeInt(u32, out.items[0..4], VWAP_MAGIC, .little);
    std.mem.writeInt(u32, out.items[4..8], count,      .little);
    return out.toOwnedSlice(a);
}
