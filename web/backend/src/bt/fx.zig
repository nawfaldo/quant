const std = @import("std");
const engine = @import("engine.zig");
const data = @import("data.zig");

// ── FX execution re-pricing ──────────────────────────────────────────────────
// The strategy still generates its trade log on the `nq_*` bar tables (signal
// giver). This module takes that finished trade log and RE-PRICES every fill
// from the `fx_nq_ticks` tick table (signal receiver): for each entry/exit, the
// fill is the first tick at or after the native fill instant + a fixed latency,
// quoted with a fixed spread. Timing, side, and size are untouched — only the
// fill prices (and therefore PnL) change. Trades outside the tick table's
// coverage window are dropped ("don't trade outside the window").

// Latency between the nq-bar signal becoming actionable and the fx fill.
const LATENCY_MICROS: i64 = 100_000; // 100 ms

// Fixed full bid/ask spread applied around the tick mid (±half each side):
// a buy fills at mid+0.10, a sell at mid-0.10  → 25910.00 → 25910.10.
const SPREAD: f64 = 0.2;

// If the first tick at/after (fill_instant + latency) is more than this far
// away, the fill is treated as missing (e.g. the bar sits at the edge of a
// session gap) and the trade is dropped rather than filled across the gap.
const MAX_GAP_MICROS: i64 = 5 * 60 * 1_000_000; // 5 minutes

// Coverage window of fx_nq_ticks (inclusive day bounds). Trades whose fills fall
// outside this are not executed.
const COVERAGE_FROM = "2026-01-01";
const COVERAGE_LAST = "2026-06-26";

pub const Repriced = struct {
    trades: []engine.Trade, // heap-allocated; caller frees via deinit
    in_window: usize, // trades successfully re-priced
    total: usize, // trades in the native book

    pub fn deinit(self: Repriced, gpa: std.mem.Allocator) void {
        gpa.free(self.trades);
    }
};

// One fill lookup: the instant we want a price for, which output slot it feeds,
// and whether that leg is a buy (long entry / short exit) or a sell.
const Req = struct {
    target: i64, // unix micros (fake-UTC) of the desired fill
    slot: usize, // trade_index*2 + (0 = entry, 1 = exit)
    is_buy: bool,
    filled: bool = false,
    price: f64 = 0, // tick mid at the fill
    fill_ts: i64 = 0, // unix micros of the tick that filled it
};

// Streaming consumer: ticks arrive in ascending time order; requests are sorted
// ascending by target. We advance a single pointer, assigning each request the
// first tick whose timestamp reaches its target. O(ticks + reqs), O(reqs) memory.
const Consumer = struct {
    reqs: []Req,
    next: usize = 0,

    pub fn onTick(self: *Consumer, ts: i64, bid: f64, ask: f64) void {
        const mid = (bid + ask) * 0.5;
        while (self.next < self.reqs.len and self.reqs[self.next].target <= ts) : (self.next += 1) {
            self.reqs[self.next].filled = true;
            self.reqs[self.next].price = mid;
            self.reqs[self.next].fill_ts = ts;
        }
    }
};

// Re-price `native` against fx_nq_ticks. Returns null when no trade falls inside
// the coverage window (or there are no trades). Caller owns the result.
pub fn reprice(io: std.Io, gpa: std.mem.Allocator, native: []const engine.Trade, inst: engine.Instrument) !?Repriced {
    if (native.len == 0) return null;

    // Span of the native book, clamped to the fx coverage window.
    var min_entry: [10]u8 = "9999-99-99".*;
    var max_exit: [10]u8 = "0000-00-00".*;
    for (native) |t| {
        if (std.mem.order(u8, t.entry_ts[0..10], &min_entry) == .lt) @memcpy(&min_entry, t.entry_ts[0..10]);
        if (std.mem.order(u8, t.exit_ts[0..10], &max_exit) == .gt) @memcpy(&max_exit, t.exit_ts[0..10]);
    }
    const fetch_from: []const u8 = if (std.mem.order(u8, &min_entry, COVERAGE_FROM) == .lt) COVERAGE_FROM else min_entry[0..];
    const cap_exit: []const u8 = if (std.mem.order(u8, &max_exit, COVERAGE_LAST) == .gt) COVERAGE_LAST else max_exit[0..];
    if (std.mem.order(u8, fetch_from, cap_exit) == .gt) return null; // no overlap with coverage
    var to_buf: [10]u8 = undefined;
    const fetch_to = nextDay(&to_buf, cap_exit);

    // Build the fill requests (two per trade) and sort by target time.
    const reqs = try gpa.alloc(Req, native.len * 2);
    defer gpa.free(reqs);
    for (native, 0..) |t, i| {
        const is_long = t.side == .long;
        reqs[i * 2] = .{ .target = tsMicros(t.entry_ts) + LATENCY_MICROS, .slot = i * 2, .is_buy = is_long };
        reqs[i * 2 + 1] = .{ .target = tsMicros(t.exit_ts) + LATENCY_MICROS, .slot = i * 2 + 1, .is_buy = !is_long };
    }
    std.mem.sort(Req, reqs, {}, reqLess);

    var consumer = Consumer{ .reqs = reqs };
    try data.streamFxTicks(io, gpa, fetch_from, fetch_to, &consumer);

    // Scatter results back to per-leg slots, marking gaps too wide as unfilled.
    const ok = try gpa.alloc(bool, native.len * 2);
    defer gpa.free(ok);
    const px = try gpa.alloc(f64, native.len * 2);
    defer gpa.free(px);
    for (reqs) |req| {
        ok[req.slot] = req.filled and (req.fill_ts - req.target) <= MAX_GAP_MICROS;
        px[req.slot] = req.price;
    }

    var out: std.ArrayList(engine.Trade) = .empty;
    errdefer out.deinit(gpa);
    for (native, 0..) |t, i| {
        if (!ok[i * 2] or !ok[i * 2 + 1]) continue; // a leg fell outside coverage
        const is_long = t.side == .long;
        const entry = engine.applyFillCost(px[i * 2], is_long, SPREAD, 0);
        const exit = engine.applyFillCost(px[i * 2 + 1], !is_long, SPREAD, 0);
        try out.append(gpa, .{
            .entry_ts = t.entry_ts,
            .exit_ts = t.exit_ts,
            .side = t.side,
            .entry_price = entry,
            .exit_price = exit,
            .pnl = engine.calcPnl(t.side, entry, exit, t.contracts, inst),
            .contracts = t.contracts,
        });
    }

    if (out.items.len == 0) {
        out.deinit(gpa);
        return null;
    }
    const trades = try out.toOwnedSlice(gpa);
    return .{ .trades = trades, .in_window = trades.len, .total = native.len };
}

fn reqLess(_: void, a: Req, b: Req) bool {
    return a.target < b.target;
}

// ── Realized-equity drawdown ─────────────────────────────────────────────────
// The fx book is evaluated on its re-priced trade closes (not bar-by-bar
// mark-to-market like the native run), so drawdown is the realized-equity curve.
pub const Drawdown = struct {
    max_drawdown: f64 = 0,
    avg_drawdown: f64 = 0,
    max_drawdown_peak_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_trough_date: [10]u8 = [_]u8{' '} ** 10,
    max_intraday_drawdown: f64 = 0,
    avg_intraday_drawdown: f64 = 0,
    max_intraday_drawdown_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_dollars: f64 = 0,
    avg_drawdown_dollars: f64 = 0,
    max_intraday_drawdown_dollars: f64 = 0,
    avg_intraday_drawdown_dollars: f64 = 0,
};

pub fn realizedDrawdown(initial: f64, trades: []const engine.Trade) Drawdown {
    const blank = [_]u8{' '} ** 10;
    var equity: f64 = initial;
    var peak: f64 = initial;
    var peak_date: [10]u8 = blank;
    var max_dd: f64 = 0;
    var max_dd_dollars: f64 = 0;
    var dd_sum: f64 = 0;
    var dd_dollars_sum: f64 = 0;
    var dd_count: usize = 0;
    var max_dd_from: [10]u8 = blank;
    var max_dd_to: [10]u8 = blank;

    var day_peak: f64 = initial;
    var cur_day: [10]u8 = blank;
    var day_started = false;
    var day_max_idd: f64 = 0;
    var day_max_idd_dollars: f64 = 0;
    var max_idd: f64 = 0;
    var max_idd_dollars: f64 = 0;
    var max_idd_date: [10]u8 = blank;
    var idd_sum: f64 = 0;
    var idd_dollars_sum: f64 = 0;
    var idd_days: usize = 0;

    for (trades) |t| {
        const t_day = t.exit_ts[0..10];
        if (!day_started or !std.mem.eql(u8, t_day, cur_day[0..10])) {
            if (day_started) {
                idd_sum += day_max_idd;
                idd_dollars_sum += day_max_idd_dollars;
                idd_days += 1;
            }
            @memcpy(&cur_day, t_day);
            day_peak = equity;
            day_max_idd = 0;
            day_max_idd_dollars = 0;
            day_started = true;
        }

        equity += t.pnl;

        if (equity > peak) {
            peak = equity;
            @memcpy(peak_date[0..], t.exit_ts[0..10]);
        }
        const dd_dollars = peak - equity;
        const dd = if (peak > 0) dd_dollars / peak * 100.0 else 0.0;
        if (dd > max_dd) {
            max_dd = dd;
            max_dd_dollars = dd_dollars;
            @memcpy(max_dd_from[0..], peak_date[0..]);
            @memcpy(max_dd_to[0..], t.exit_ts[0..10]);
        }
        dd_sum += dd;
        dd_dollars_sum += dd_dollars;
        dd_count += 1;

        if (equity > day_peak) day_peak = equity;
        const idd_dollars = day_peak - equity;
        const idd = if (day_peak > 0) idd_dollars / day_peak * 100.0 else 0.0;
        if (idd > day_max_idd) {
            day_max_idd = idd;
            day_max_idd_dollars = idd_dollars;
        }
        if (idd > max_idd) {
            max_idd = idd;
            max_idd_dollars = idd_dollars;
            @memcpy(&max_idd_date, t_day);
        }
    }
    if (day_started) {
        idd_sum += day_max_idd;
        idd_dollars_sum += day_max_idd_dollars;
        idd_days += 1;
    }

    const n: f64 = @floatFromInt(dd_count);
    const iddc: f64 = @floatFromInt(idd_days);
    return .{
        .max_drawdown = max_dd,
        .avg_drawdown = if (dd_count > 0) dd_sum / n else 0.0,
        .max_drawdown_peak_date = max_dd_from,
        .max_drawdown_trough_date = max_dd_to,
        .max_drawdown_dollars = max_dd_dollars,
        .avg_drawdown_dollars = if (dd_count > 0) dd_dollars_sum / n else 0.0,
        .max_intraday_drawdown = max_idd,
        .max_intraday_drawdown_dollars = max_idd_dollars,
        .max_intraday_drawdown_date = max_idd_date,
        .avg_intraday_drawdown = if (idd_days > 0) idd_sum / iddc else 0.0,
        .avg_intraday_drawdown_dollars = if (idd_days > 0) idd_dollars_sum / iddc else 0.0,
    };
}

// Build an engine.Result from a re-priced trade slice so the existing report /
// JSON code can render the fx book exactly like a native run. `trades` is
// borrowed (not owned) by the returned Result.
pub fn resultFor(initial: f64, trades: []engine.Trade) engine.Result {
    const blank: data.Ts = [_]u8{' '} ** 16;
    var first: data.Ts = [_]u8{'9'} ** 16;
    var last: data.Ts = [_]u8{'0'} ** 16;
    for (trades) |t| {
        if (std.mem.order(u8, &t.entry_ts, &first) == .lt) first = t.entry_ts;
        if (std.mem.order(u8, &t.exit_ts, &last) == .gt) last = t.exit_ts;
    }
    if (trades.len == 0) {
        first = blank;
        last = blank;
    }
    const dd = realizedDrawdown(initial, trades);
    return .{
        .trades = trades,
        .first_ts = first,
        .last_ts = last,
        .initial_balance = initial,
        .max_drawdown = dd.max_drawdown,
        .avg_drawdown = dd.avg_drawdown,
        .max_drawdown_peak_date = dd.max_drawdown_peak_date,
        .max_drawdown_trough_date = dd.max_drawdown_trough_date,
        .max_intraday_drawdown = dd.max_intraday_drawdown,
        .avg_intraday_drawdown = dd.avg_intraday_drawdown,
        .max_intraday_drawdown_date = dd.max_intraday_drawdown_date,
        .max_drawdown_dollars = dd.max_drawdown_dollars,
        .avg_drawdown_dollars = dd.avg_drawdown_dollars,
        .max_intraday_drawdown_dollars = dd.max_intraday_drawdown_dollars,
        .avg_intraday_drawdown_dollars = dd.avg_intraday_drawdown_dollars,
    };
}

// ── Date helpers ─────────────────────────────────────────────────────────────

// "YYYY-MM-DD HH:MM" → unix microseconds (fake-UTC ET, same clock as the tick
// table after streamFxTicks' conversion).
fn tsMicros(ts: [16]u8) i64 {
    const y = std.fmt.parseInt(i64, ts[0..4], 10) catch return 0;
    const mo = std.fmt.parseInt(i64, ts[5..7], 10) catch return 0;
    const da = std.fmt.parseInt(i64, ts[8..10], 10) catch return 0;
    const hh = std.fmt.parseInt(i64, ts[11..13], 10) catch return 0;
    const mm = std.fmt.parseInt(i64, ts[14..16], 10) catch return 0;
    const secs = daysFromCivil(y, mo, da) * 86400 + hh * 3600 + mm * 60;
    return secs * 1_000_000;
}

fn nextDay(buf: *[10]u8, date: []const u8) []const u8 {
    const y = std.fmt.parseInt(i64, date[0..4], 10) catch return date;
    const mo = std.fmt.parseInt(i64, date[5..7], 10) catch return date;
    const da = std.fmt.parseInt(i64, date[8..10], 10) catch return date;
    const c = civilFromDays(daysFromCivil(y, mo, da) + 1);
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2}", .{
        @as(u32, @intCast(c.y)), @as(u32, @intCast(c.m)), @as(u32, @intCast(c.d)),
    }) catch date;
}

fn daysFromCivil(y_in: i64, m: i64, d: i64) i64 {
    const y = if (m <= 2) y_in - 1 else y_in;
    const era = @divFloor(if (y >= 0) y else y - 399, @as(i64, 400));
    const yoe = y - era * 400;
    const mp = if (m > 2) m - 3 else m + 9;
    const doy = @divFloor(153 * mp + 2, @as(i64, 5)) + d - 1;
    const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy;
    return era * 146097 + doe - 719468;
}

fn civilFromDays(z_in: i64) struct { y: i64, m: i64, d: i64 } {
    const z = z_in + 719468;
    const era = @divFloor(if (z >= 0) z else z - 146096, @as(i64, 146097));
    const doe = z - era * 146097;
    const yoe = @divFloor(doe - @divFloor(doe, 1460) + @divFloor(doe, 36524) - @divFloor(doe, 146096), @as(i64, 365));
    const y = yoe + era * 400;
    const doy = doe - (365 * yoe + @divFloor(yoe, 4) - @divFloor(yoe, 100));
    const mp = @divFloor(5 * doy + 2, @as(i64, 153));
    const d = doy - @divFloor(153 * mp + 2, @as(i64, 5)) + 1;
    const m = if (mp < 10) mp + 3 else mp - 9;
    return .{ .y = y + @as(i64, if (m <= 2) 1 else 0), .m = m, .d = d };
}
