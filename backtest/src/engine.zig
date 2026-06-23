const std = @import("std");
const Io = std.Io;
const data = @import("data.zig");

// Re-export `data.Bar` so strategy files only need `@import("engine.zig")`.
// In Zig, `pub const X = Y` just makes `Y` accessible as `X` from outside.
pub const Bar = data.Bar;
pub const Ts = data.Ts;

// ── Source config ─────────────────────────────────────────────────────────────
// `?[]const u8` is an *optional* slice of bytes (string). The `?` means it can
// be `null`. `[]const u8` is Zig's standard string type — a pointer + length
// to immutable bytes. Setting these to `null` means "no date filter" — fetch
// the whole table.
pub var from: ?[]const u8 = null; // e.g. "2023-01-01"
pub var to: ?[]const u8 = null; // e.g. "2025-01-01"
pub var symbol: []const u8 = "nq"; // table prefix, e.g. "nq" or "gbpusd"

// How the selected price series is traded. This is decoupled from `symbol`:
// NQ price data can be modeled three different ways (the CLI prompts for it when
// NQ is selected), while every other symbol is always a forex CFD.
//   .forex    — CFD: $1/point per 1.0 lot, fractional lots, "lot" terminology.
//   .nq_mini  — E-mini Nasdaq-100 future (NQ): $20/point per contract, whole
//               contracts, "contract" terminology.
//   .nq_micro — Micro E-mini Nasdaq-100 future (MNQ): $2/point per contract,
//               whole contracts, "contract" terminology.
// Set by the CLI before each run (always `.forex` for non-NQ symbols).
pub const Instrument = enum { forex, nq_mini, nq_micro };
pub var instrument: Instrument = .nq_mini;

var g_table_buf: [32]u8 = undefined;

// Warm-up buffer: when `from` is set, the fetch is widened backward by this
// many CALENDAR days so volatility/indicator state (e.g. the EWMA vol estimate
// in OrbBuy) is primed before the evaluation window. Bars in the warm-up region
// are fed to `strat.update()` but produce no trades and are excluded from the
// reported stats. Set to 0 to disable. Has no effect when `from` is null
// (full-history runs warm up naturally from the first bar).
pub var warmup_days: u32 = 90;

// ── Types ─────────────────────────────────────────────────────────────────────
// `enum` declares a tagged set of named values. By default Zig picks the
// integer representation. You refer to values as `Signal.long` or — when the
// type is known from context — just `.long`.
pub const Signal = enum { long, short, flat, close };
pub const Side = enum { long, short };

// A plain data struct. No methods needed — just a record of one round-trip.
pub const Trade = struct {
    entry_ts: data.Ts,
    exit_ts: data.Ts,
    side: Side,
    entry_price: f64,
    exit_price: f64,
    pnl: f64,
    contracts: f64,
};

// File-private (no `pub`). Used only inside this file.
const Position = struct {
    side: Side,
    entry_price: f64,
    entry_ts: data.Ts,
    contracts: f64,
};

// Point value per unit of position, per instrument.
//   .nq_mini  — E-mini Nasdaq-100 future (NQ): $20 per index point per contract.
//              A 10-point move on 1 contract = $200.
//   .nq_micro — Micro E-mini (MNQ): $2 per index point per contract (1/10 of NQ).
//   .forex    — CFD at $1 per point per 1 standard lot. A 10-point move on
//              0.1 lot = $1.
fn lotMult() f64 {
    return switch (instrument) {
        .forex => 1.0,
        .nq_mini => 20.0,
        .nq_micro => 2.0,
    };
}

// Dollar value of one index point per contract/lot for the active instrument.
// Exposed so the CLI can visualize transaction costs ($ = points × pointValue).
pub fn pointValue() f64 {
    return lotMult();
}

// Futures instruments (nq mini/micro) trade in whole contracts; the requested
// size is rounded to the nearest integer (min 1). Forex CFDs keep fractional
// lots untouched. Also drives the "contract" vs "lot" terminology in the CLI
// prompts and the report.
pub fn usesContracts() bool {
    return instrument != .forex;
}

fn sizeFor(raw: f64) f64 {
    if (usesContracts()) return @max(1.0, @round(raw));
    return raw;
}

// Human-readable instrument label — stored in the DB and shown in the report.
pub fn instrumentName() []const u8 {
    return switch (instrument) {
        .forex => "forex",
        .nq_mini => "nq mini",
        .nq_micro => "nq micro",
    };
}

// ── Transaction-cost model ──────────────────────────────────────────────────
// Every fill pays two fixed adverse costs, in price points, charged per side:
//
//   1. Half the bid/ask spread. A market order crosses the spread, paying the
//      ask when buying / hitting the bid when selling — i.e. half the quoted
//      spread away from mid on each leg. `spread` is the full quoted spread.
//      Exness NAS100: typically 0.3–0.6 pt; using 0.4 as the midpoint default.
//
//   2. Fixed slippage. A flat per-fill adverse price move that approximates
//      order-book friction at retail lot sizes (queue position, partial fills,
//      price movement in the 1-bar fill window). At 0.2 pt this represents a
//      modest but realistic retail execution cost on a liquid CFD.
//
// Per-side cost = spread/2 + slippage = 2.0 + 0.2 = 2.2 pt  (NQ default).
// Round-trip    = 2 × 2.2              = 4.4 pt.
//
// Set both to 0 to run frictionless.
pub var spread: f64 = 4.0; // full bid/ask spread in points
pub var slippage: f64 = 0.2; // fixed per-fill slippage in points

// Set to true by the CLI's stdin reader to abort a running backtest.
// Atomic so cli.zig (main thread) and engine (worker thread) can race safely.
pub var cancelled: std.atomic.Value(bool) = .init(false);

// Returned by run(). Caller owns `trades` and must free it.
pub const Result = struct {
    trades: []Trade,
    first_ts: data.Ts,
    last_ts: data.Ts,
    initial_balance: f64,
    // Drawdown is measured on the bar-by-bar MARK-TO-MARKET equity curve
    // (realized balance + the open position's unrealized PnL at each bar's
    // close), so it captures dips *during* a held position — not just at trade
    // closes. Percentages of the running peak. Computed in `backtest`.
    max_drawdown: f64,
    avg_drawdown: f64,
    max_drawdown_peak_date: [10]u8, // "YYYY-MM-DD" — date the peak was set
    max_drawdown_trough_date: [10]u8, // "YYYY-MM-DD" — date of the max DD trough
    // Intraday trailing drawdown: same mark-to-market curve, but the peak resets
    // at the start of each calendar day, so it measures the worst dip from each
    // day's own high. `max` is the deepest such dip across all days; `avg` is the
    // mean of each day's worst dip (one value per trading day). Computed in
    // `backtest`.
    max_intraday_drawdown: f64,
    avg_intraday_drawdown: f64,
    max_intraday_drawdown_date: [10]u8, // "YYYY-MM-DD" — day of the worst intraday DD
    // Dollar equivalents of the drawdown percentages (peak - trough in $).
    max_drawdown_dollars: f64,
    avg_drawdown_dollars: f64,
    max_intraday_drawdown_dollars: f64,
    avg_intraday_drawdown_dollars: f64,

    pub fn deinit(self: Result, gpa: std.mem.Allocator) void {
        gpa.free(self.trades);
    }
};

// ── Entry point ───────────────────────────────────────────────────────────────
// `strat: anytype` means the parameter is generic — Zig will compile a fresh
// version of `run` for whatever concrete type you pass. This is *compile-time
// duck typing*: the function works with any type that has the right shape.
//
// The strategy must be a *pointer* to a struct exposing:
//   pub const columns = .{ .open=bool, .high=bool, .low=bool, .close=bool, .volume=bool };
//   pub fn update(self: *@This(), bar: engine.Bar) engine.Signal
//
// Returns `![]Trade` — an error union of "slice of Trade". The caller owns
// the returned memory and must `free` it (see main.zig).
pub fn run(io: Io, gpa: std.mem.Allocator, strat: anytype) !Result {
    const cols = columnsFor(@TypeOf(strat.*));
    const table = tableFor(@TypeOf(strat.*));
    const dataset = try fetchDataset(io, gpa, cols, table);
    defer dataset.deinit();
    return try backtestOn(gpa, strat, dataset);
}

// Which columns a strategy needs, derived from its compile-time `columns` decl.
pub fn columnsFor(comptime Strat: type) data.Columns {
    return .{
        .open = true,
        .high = Strat.columns.high,
        .low = Strat.columns.low,
        .close = Strat.columns.close,
        .volume = Strat.columns.volume,
    };
}

// Which QuestDB table a strategy reads: runtime symbol prefix + comptime timeframe.
pub fn tableFor(comptime Strat: type) []const u8 {
    return std.fmt.bufPrint(&g_table_buf, "{s}_{s}", .{ symbol, Strat.timeframe }) catch "unknown";
}

// Fetch the bar history once. The tuner calls this a single time and then reuses
// the dataset across every parameter combination via `backtestOn`.
pub fn fetchDataset(io: Io, gpa: std.mem.Allocator, cols: data.Columns, table: []const u8) !data.Dataset {
    // Widen the fetch backward by `warmup_days` so the strategy can prime its
    // state before the real window. The module-level `from` is left untouched —
    // it still marks where trades begin (see realStartIndex / backtestOn).
    var wbuf: [10]u8 = undefined;
    var fetch_from = from;
    if (from) |f| {
        if (warmup_days > 0 and f.len >= 10) {
            fetch_from = warmupFrom(&wbuf, f, warmup_days) catch f;
        }
    }
    const src = data.Source{ .table = table, .from = fetch_from, .to = to };
    return try data.fetch(io, gpa, cols, src);
}

// Run one backtest over an already-fetched dataset. Caller owns Result.trades.
pub fn backtestOn(gpa: std.mem.Allocator, strat: anytype, dataset: data.Dataset) !Result {
    const blank: data.Ts = [_]u8{' '} ** 16;
    // `start` is the first bar inside the real (post-warm-up) window. Stats and
    // trades begin here; bars before it only prime the strategy.
    const start = realStartIndex(dataset.timestamps);
    const first_ts = if (start < dataset.timestamps.len) dataset.timestamps[start] else blank;
    const last_ts = if (dataset.timestamps.len > 0) dataset.timestamps[dataset.timestamps.len - 1] else blank;

    const bt = try backtest(gpa, strat, dataset.bars, dataset.timestamps, start);
    return .{
        .trades = bt.trades,
        .first_ts = first_ts,
        .last_ts = last_ts,
        .initial_balance = strat.initial_balance,
        .max_drawdown = bt.max_drawdown,
        .avg_drawdown = bt.avg_drawdown,
        .max_drawdown_peak_date = bt.max_drawdown_peak_date,
        .max_drawdown_trough_date = bt.max_drawdown_trough_date,
        .max_intraday_drawdown = bt.max_intraday_drawdown,
        .avg_intraday_drawdown = bt.avg_intraday_drawdown,
        .max_intraday_drawdown_date = bt.max_intraday_drawdown_date,
        .max_drawdown_dollars = bt.max_drawdown_dollars,
        .avg_drawdown_dollars = bt.avg_drawdown_dollars,
        .max_intraday_drawdown_dollars = bt.max_intraday_drawdown_dollars,
        .avg_intraday_drawdown_dollars = bt.avg_intraday_drawdown_dollars,
    };
}

// What `backtest` returns: the trade log plus mark-to-market drawdown stats.
const BacktestOut = struct {
    trades: []Trade,
    max_drawdown: f64,
    avg_drawdown: f64,
    max_drawdown_peak_date: [10]u8,
    max_drawdown_trough_date: [10]u8,
    max_intraday_drawdown: f64,
    avg_intraday_drawdown: f64,
    max_intraday_drawdown_date: [10]u8,
    max_drawdown_dollars: f64,
    avg_drawdown_dollars: f64,
    max_intraday_drawdown_dollars: f64,
    avg_intraday_drawdown_dollars: f64,
};

// File-private helper. `[]const Bar` means "read-only slice of Bar".
fn backtest(gpa: std.mem.Allocator, strat: anytype, bars: []const Bar, timestamps: []const data.Ts, start: usize) !BacktestOut {
    var trades: std.ArrayList(Trade) = .empty;
    var position: ?Position = null;

    // Mark-to-market equity tracking for drawdown. `equity` is the realized
    // balance (booked at each trade close); each bar we add the open position's
    // unrealized PnL (marked at that bar's close) to get the live equity, then
    // update the running peak and drawdown series.
    var equity: f64 = strat.initial_balance;
    var peak: f64 = equity;
    var max_dd: f64 = 0;
    var dd_sum: f64 = 0;
    var dd_count: usize = 0;
    const blank_date = [_]u8{' '} ** 10;
    var peak_date: [10]u8 = blank_date; // date the current all-time peak was set
    var max_dd_from: [10]u8 = blank_date; // date of the peak before the worst DD
    var max_dd_to: [10]u8 = blank_date; // date of the worst DD trough

    // Intraday trailing drawdown: a peak that resets each calendar day. We track
    // the worst dip within the current day (`day_max_idd`) and roll it into the
    // running average (one sample per trading day) when the day changes.
    var day_peak: f64 = equity;
    var cur_day: [10]u8 = undefined;
    var day_started = false;
    var day_max_idd: f64 = 0;
    var max_idd: f64 = 0;
    var max_idd_date: [10]u8 = blank_date; // day of the worst intraday DD
    var idd_sum: f64 = 0;
    var idd_days: usize = 0;
    var max_dd_dollars: f64 = 0;
    var dd_dollars_sum: f64 = 0;
    var day_max_idd_dollars: f64 = 0;
    var max_idd_dollars: f64 = 0;
    var idd_dollars_sum: f64 = 0;

    var i: usize = 0;

    // Warm-up region [0, start): feed bars to the strategy so it can prime its
    // internal state (vol estimate, day tracking, …). Signals are discarded and
    // no positions are opened. Because `from` is a midnight boundary, the first
    // real bar triggers the strategy's day-rollover reset, clearing any intraday
    // state the warm-up left set.
    while (i < start) : (i += 1) {
        if (cancelled.load(.acquire)) return error.Cancelled;
        _ = strat.update(bars[i], timestamps[i]);
    }

    while (i < bars.len) : (i += 1) {
        if (cancelled.load(.acquire)) return error.Cancelled;

        const signal = strat.update(bars[i], timestamps[i]);

        // Sample the mark-to-market equity for this bar BEFORE acting on the
        // signal (the position held *into* this bar, marked at its close). This
        // runs every bar — including the `.flat` / already-in-position cases
        // below that `continue` — so drawdown reflects the whole holding period.
        {
            var mtm = equity;
            if (position) |pos| mtm += calcPnl(pos.side, pos.entry_price, bars[i].close, pos.contracts);

            // All-time trailing drawdown (peak never resets).
            if (mtm > peak) {
                peak = mtm;
                @memcpy(peak_date[0..], timestamps[i][0..10]);
            }
            const dd_dollars = peak - mtm;
            const dd = if (peak > 0) dd_dollars / peak * 100.0 else 0.0;
            if (dd > max_dd) {
                max_dd = dd;
                max_dd_dollars = dd_dollars;
                @memcpy(max_dd_from[0..], peak_date[0..]);
                @memcpy(max_dd_to[0..], timestamps[i][0..10]);
            }
            dd_sum += dd;
            dd_dollars_sum += dd_dollars;
            dd_count += 1;

            // Intraday trailing drawdown (peak resets each calendar day). On a
            // day rollover, bank the just-finished day's worst dip as one sample.
            const day = timestamps[i][0..10];
            if (!day_started or !std.mem.eql(u8, day, cur_day[0..])) {
                if (day_started) {
                    idd_sum += day_max_idd;
                    idd_dollars_sum += day_max_idd_dollars;
                    idd_days += 1;
                }
                @memcpy(cur_day[0..], day);
                day_peak = mtm;
                day_max_idd = 0;
                day_max_idd_dollars = 0;
                day_started = true;
            }
            if (mtm > day_peak) day_peak = mtm;
            const idd_dollars = day_peak - mtm;
            const idd = if (day_peak > 0) idd_dollars / day_peak * 100.0 else 0.0;
            if (idd > day_max_idd) {
                day_max_idd = idd;
                day_max_idd_dollars = idd_dollars;
            }
            if (idd > max_idd) {
                max_idd = idd;
                max_idd_dollars = idd_dollars;
                @memcpy(max_idd_date[0..], cur_day[0..]);
            }
        }

        switch (signal) {
            .flat => continue,
            .close => {
                if (position) |pos| {
                    const exit_price: f64 = if (i + 1 < bars.len) bars[i + 1].open else bars[i].close;
                    const exit_ts: data.Ts = if (i + 1 < bars.len) timestamps[i + 1] else timestamps[i];
                    const pnl = calcPnl(pos.side, pos.entry_price, exit_price, pos.contracts);
                    try trades.append(gpa, .{
                        .entry_ts = pos.entry_ts,
                        .exit_ts = exit_ts,
                        .side = pos.side,
                        .entry_price = pos.entry_price,
                        .exit_price = exit_price,
                        .pnl = pnl,
                        .contracts = pos.contracts,
                    });
                    equity += pnl;
                    position = null;
                }
                continue;
            },
            .long, .short => {},
        }

        const new_side: Side = switch (signal) {
            .long => .long,
            .short => .short,
            else => unreachable,
        };

        // `if (position) |pos|` is Zig's *optional unwrap* syntax. The body
        // only runs when `position` is non-null; inside, `pos` is the unwrapped
        // value. Here we skip if we're already in the same direction.
        if (position) |pos| if (pos.side == new_side) continue;

        // We fill on the *next* bar's open (no lookahead cheating). If there
        // is no next bar, we can't fill — bail out of the loop.
        if (i + 1 >= bars.len) break;

        const next_bar = bars[i + 1];
        const next_open = next_bar.open;
        const next_ts = timestamps[i + 1];

        // Close any existing position at the next bar's open. Booking its PnL
        // into `equity` *here* (before entering the new position) means a flip
        // reallocates the freshly-updated account, matching the paper. The
        // closing leg carries its *own* size, so it gets its own impact cost.
        if (position) |pos| {
            const exit_fill = next_open;
            const pnl = calcPnl(pos.side, pos.entry_price, exit_fill, pos.contracts);
            try trades.append(gpa, .{
                .entry_ts = pos.entry_ts,
                .exit_ts = next_ts,
                .side = pos.side,
                .entry_price = pos.entry_price,
                .exit_price = exit_fill,
                .pnl = pnl,
                .contracts = pos.contracts,
            });
            equity += pnl;
        }

        const open_contracts = sizeFor(strat.contracts);
        const entry_fill = applyFillCost(next_open, open_contracts, next_bar, new_side == .long);
        position = .{ .side = new_side, .entry_price = entry_fill, .entry_ts = next_ts, .contracts = open_contracts };
    }

    // If we ended the loop still holding a position, close it at the final
    // bar's close so the report includes that PnL.
    if (position) |pos| {
        const last = bars[bars.len - 1];
        const exit_price = last.close;
        const pnl = calcPnl(pos.side, pos.entry_price, exit_price, pos.contracts);
        try trades.append(gpa, .{
            .entry_ts = pos.entry_ts,
            .exit_ts = timestamps[bars.len - 1],
            .side = pos.side,
            .entry_price = pos.entry_price,
            .exit_price = exit_price,
            .pnl = pnl,
            .contracts = pos.contracts,
        });
        equity += pnl;
    }

    // Bank the final (unfinished) day's worst intraday dip as its sample.
    if (day_started) {
        idd_sum += day_max_idd;
        idd_dollars_sum += day_max_idd_dollars;
        idd_days += 1;
    }

    // Hand ownership of the underlying buffer to the caller as a slice.
    // After this call, `trades` is empty — the memory belongs to the slice.
    return .{
        .trades = try trades.toOwnedSlice(gpa),
        .max_drawdown = max_dd,
        .avg_drawdown = if (dd_count > 0) dd_sum / @as(f64, @floatFromInt(dd_count)) else 0.0,
        .max_drawdown_peak_date = max_dd_from,
        .max_drawdown_trough_date = max_dd_to,
        .max_intraday_drawdown = max_idd,
        .avg_intraday_drawdown = if (idd_days > 0) idd_sum / @as(f64, @floatFromInt(idd_days)) else 0.0,
        .max_intraday_drawdown_date = max_idd_date,
        .max_drawdown_dollars = max_dd_dollars,
        .avg_drawdown_dollars = if (dd_count > 0) dd_dollars_sum / @as(f64, @floatFromInt(dd_count)) else 0.0,
        .max_intraday_drawdown_dollars = max_idd_dollars,
        .avg_intraday_drawdown_dollars = if (idd_days > 0) idd_dollars_sum / @as(f64, @floatFromInt(idd_days)) else 0.0,
    };
}

fn calcPnl(side: Side, entry: f64, exit: f64, lots: f64) f64 {
    const mult = lotMult();
    return switch (side) {
        .long => (exit - entry) * mult * lots,
        .short => (entry - exit) * mult * lots,
    };
}

// Adjusts a raw fill price for half-spread + fixed slippage.
// buying=true : long entry or short exit — price is pushed higher (costs more).
// buying=false: short entry or long exit — price is pushed lower (gets less).
fn applyFillCost(raw: f64, lots: f64, bar: Bar, buying: bool) f64 {
    _ = lots; // fixed model — cost does not scale with size
    _ = bar; // fixed model — cost does not depend on bar range/volume
    const adverse = spread / 2.0 + slippage;
    return if (buying) raw + adverse else raw - adverse;
}

// ── Warm-up window helpers ────────────────────────────────────────────────────

// Index of the first bar at or after the real `from` date. Bars before it are
// warm-up only. Returns 0 when `from` is null (no warm-up). Timestamps are
// "YYYY-MM-DD HH:MM" and `from` is "YYYY-MM-DD"; lexicographic order matches
// chronological order, and a same-day bar sorts after the 10-char date prefix.
fn realStartIndex(timestamps: []const data.Ts) usize {
    const f = from orelse return 0;
    for (timestamps, 0..) |ts, idx| {
        if (std.mem.order(u8, ts[0..], f) != .lt) return idx;
    }
    return timestamps.len;
}

// "YYYY-MM-DD" minus `days` calendar days, written into `buf`. Uses Howard
// Hinnant's civil-date algorithms (days_from_civil / civil_from_days).
fn warmupFrom(buf: []u8, from_str: []const u8, days: u32) ![]const u8 {
    const y = try std.fmt.parseInt(i64, from_str[0..4], 10);
    const mo = try std.fmt.parseInt(i64, from_str[5..7], 10);
    const da = try std.fmt.parseInt(i64, from_str[8..10], 10);
    const c = civilFromDays(daysFromCivil(y, mo, da) - @as(i64, days));
    // Cast to unsigned: Zig 0.16's "{d:0>N}" emits a '+' sign and skips padding
    // for signed ints. Dates here are always positive.
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2}", .{
        @as(u32, @intCast(c.y)), @as(u32, @intCast(c.m)), @as(u32, @intCast(c.d)),
    });
}

// Days since 1970-01-01 for a proleptic-Gregorian civil date.
fn daysFromCivil(y_in: i64, m: i64, d: i64) i64 {
    const y = if (m <= 2) y_in - 1 else y_in;
    const era = @divFloor(if (y >= 0) y else y - 399, @as(i64, 400));
    const yoe = y - era * 400; // [0, 399]
    const mp = if (m > 2) m - 3 else m + 9; // [0, 11]
    const doy = @divFloor(153 * mp + 2, @as(i64, 5)) + d - 1; // [0, 365]
    const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy; // [0, 146096]
    return era * 146097 + doe - 719468;
}

// Inverse of daysFromCivil.
fn civilFromDays(z_in: i64) struct { y: i64, m: i64, d: i64 } {
    const z = z_in + 719468;
    const era = @divFloor(if (z >= 0) z else z - 146096, @as(i64, 146097));
    const doe = z - era * 146097; // [0, 146096]
    const yoe = @divFloor(doe - @divFloor(doe, 1460) + @divFloor(doe, 36524) - @divFloor(doe, 146096), @as(i64, 365)); // [0, 399]
    const y = yoe + era * 400;
    const doy = doe - (365 * yoe + @divFloor(yoe, 4) - @divFloor(yoe, 100)); // [0, 365]
    const mp = @divFloor(5 * doy + 2, @as(i64, 153)); // [0, 11]
    const d = doy - @divFloor(153 * mp + 2, @as(i64, 5)) + 1; // [1, 31]
    const m = if (mp < 10) mp + 3 else mp - 9; // [1, 12]
    return .{ .y = y + @as(i64, if (m <= 2) 1 else 0), .m = m, .d = d };
}
