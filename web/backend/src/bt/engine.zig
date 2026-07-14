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
pub var symbol: []const u8 = "nq"; // table prefix, e.g. "nq" or "es"

var g_table_buf: [32]u8 = undefined;

// Warm-up buffer: when `from` is set, the fetch is widened backward by this
// many CALENDAR days so volatility/indicator state (e.g. the EWMA vol estimate
// used by vol-target sizing) is primed before the evaluation window. Bars in the warm-up region
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
    // Raw entry fill BEFORE this venue's spread/slippage was applied (i.e. the
    // next-bar open the signal filled at). The nq book prices off `entry_price`
    // (cost-adjusted); the fx book re-prices from its own feed and needs the
    // spread-free reference so it never inherits nq's spread — see fx.zig's
    // intrabar-stop reconstruction. Defaults to 0 for trades loaded from older
    // DB rows (fx falls back to `entry_price` then).
    entry_raw: f64 = 0,
};

// File-private (no `pub`). Used only inside this file.
const Position = struct {
    side: Side,
    entry_price: f64,
    entry_raw: f64,
    entry_ts: data.Ts,
    contracts: f64,
};

// Forex CFDs execute in 0.01-lot increments at every retail broker. Sizing
// (base_lot × leverage × vol-target multiplier) is otherwise a free-floating
// float, so without this a run can size a trade at 0.0001 or 0.8932 lots —
// numbers no broker would ever fill, which makes the backtest unrealistic and
// (for the live engine, see march_api.zig's liveVolume) would get the live
// order rejected outright.
const lot_step: f64 = 0.01;

fn roundToLotStep(raw: f64) f64 {
    return @max(lot_step, @round(raw / lot_step) * lot_step);
}

fn sizeFor(raw: f64) f64 {
    return roundToLotStep(raw);
}

pub fn instrumentName() []const u8 {
    return "forex";
}

// ── Transaction-cost model ──────────────────────────────────────────────────
// Every fill pays two fixed adverse costs, in price points, charged per side:
//
//   1. Half the bid/ask spread. A market order crosses the spread, paying the
//      ask when buying / hitting the bid when selling — i.e. half the quoted
//      spread away from mid on each leg. `spread` is the full quoted spread.
//      The application default is 0.2 point for every supported symbol.
//
//   2. Fixed slippage. A flat per-fill adverse price move that approximates
//      order-book friction at retail lot sizes (queue position, partial fills,
//      price movement in the 1-bar fill window). The default is zero.
//
// Per-side cost = spread/2 + slippage = 0.1 pt.
// Round-trip    = 2 × 0.1              = 0.2 pt.
//
// Set both to 0 to run frictionless.
pub var spread: f64 = 0.2; // full bid/ask spread in points
pub var slippage: f64 = 0.0; // fixed per-fill slippage in points

// Request fallbacks use the same defaults for every supported symbol.
pub fn defaultSpreadFor(_: []const u8) f64 {
    return spread;
}

pub fn defaultSlippageFor(_: []const u8) f64 {
    return slippage;
}

// Set to true by the CLI's stdin reader to abort a running backtest.
// Atomic so cli.zig (main thread) and engine (worker thread) can race safely.
pub var cancelled: std.atomic.Value(bool) = .init(false);

// Per-run configuration. The CLI's `/run` and `/tune` paths set the module-level
// globals above and call the global entry points, which snapshot them via
// `globalConfig()`. `/combine` instead runs several configs *concurrently*, so it
// builds an explicit Config per source and calls `runWith` — keeping each run's
// symbol/cost/date out of shared mutable state (no data races).
pub const Config = struct {
    symbol: []const u8,
    from: ?[]const u8,
    to: ?[]const u8,
    spread: f64,
    slippage: f64,
    warmup_days: u32,
};

// Snapshot the current global config (used by the global `run`/`fetchDataset`/
// `backtestOn` wrappers that the single-threaded `/run` and `/tune` flows call).
pub fn globalConfig() Config {
    return .{
        .symbol = symbol,
        .from = from,
        .to = to,
        .spread = spread,
        .slippage = slippage,
        .warmup_days = warmup_days,
    };
}

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
    return runWith(io, gpa, strat, globalConfig());
}

// Like `run`, but driven entirely by an explicit `cfg` instead of the module
// globals — so several runs can proceed *concurrently* on different threads
// (each with its own symbol/cost/date) without racing shared state.
// The table name is built into a stack buffer here, not the shared `g_table_buf`.
pub fn runWith(io: Io, gpa: std.mem.Allocator, strat: anytype, cfg: Config) !Result {
    const cols = columnsFor(@TypeOf(strat.*));
    var tbuf: [40]u8 = undefined;
    const table = std.fmt.bufPrint(&tbuf, "{s}_{s}", .{ cfg.symbol, @TypeOf(strat.*).timeframe }) catch return error.TableName;
    const dataset = try fetchDatasetCfg(io, gpa, cols, table, cfg);
    defer dataset.deinit();
    return try backtestOnCfg(gpa, strat, dataset, cfg);
}

// Which columns a strategy needs, derived from its compile-time `columns` decl.
pub fn columnsFor(comptime Strat: type) data.Columns {
    return .{
        .open = true,
        .high = Strat.columns.high,
        .low = Strat.columns.low,
        .close = Strat.columns.close,
        .volume = Strat.columns.volume,
        .vix = if (@hasField(@TypeOf(Strat.columns), "vix")) Strat.columns.vix else false,
    };
}

// Which QuestDB table a strategy reads: runtime symbol prefix + comptime timeframe.
pub fn tableFor(comptime Strat: type) []const u8 {
    return std.fmt.bufPrint(&g_table_buf, "{s}_{s}", .{ symbol, Strat.timeframe }) catch "unknown";
}

// Fetch the bar history once. The tuner calls this a single time and then reuses
// the dataset across every parameter combination via `backtestOn`.
pub fn fetchDataset(io: Io, gpa: std.mem.Allocator, cols: data.Columns, table: []const u8) !data.Dataset {
    return fetchDatasetCfg(io, gpa, cols, table, globalConfig());
}

pub fn fetchDatasetCfg(io: Io, gpa: std.mem.Allocator, cols: data.Columns, table: []const u8, cfg: Config) !data.Dataset {
    // Widen the fetch backward by `warmup_days` so the strategy can prime its
    // state before the real window. `cfg.from` is left untouched — it still marks
    // where trades begin (see realStartIndex / backtestOnCfg).
    var wbuf: [10]u8 = undefined;
    var fetch_from = cfg.from;
    if (cfg.from) |f| {
        if (cfg.warmup_days > 0 and f.len >= 10) {
            fetch_from = warmupFrom(&wbuf, f, cfg.warmup_days) catch f;
        }
    }
    const src = data.Source{ .table = table, .from = fetch_from, .to = cfg.to };
    return try data.fetch(io, gpa, cols, src);
}

// Run one backtest over an already-fetched dataset. Caller owns Result.trades.
pub fn backtestOn(gpa: std.mem.Allocator, strat: anytype, dataset: data.Dataset) !Result {
    return backtestOnCfg(gpa, strat, dataset, globalConfig());
}

pub fn backtestOnCfg(gpa: std.mem.Allocator, strat: anytype, dataset: data.Dataset, cfg: Config) !Result {
    const blank: data.Ts = [_]u8{' '} ** 16;
    // `start` is the first bar inside the real (post-warm-up) window. Stats and
    // trades begin here; bars before it only prime the strategy.
    const start = realStartIndex(dataset.timestamps, cfg.from);
    const first_ts = if (start < dataset.timestamps.len) dataset.timestamps[start] else blank;
    const last_ts = if (dataset.timestamps.len > 0) dataset.timestamps[dataset.timestamps.len - 1] else blank;

    const bt = try backtest(gpa, strat, dataset.bars, dataset.timestamps, start, cfg);
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
fn backtest(gpa: std.mem.Allocator, strat: anytype, bars: []const Bar, timestamps: []const data.Ts, start: usize, cfg: Config) !BacktestOut {
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
        setStrategyEquity(strat, equity);
        const signal = strat.update(bars[i], timestamps[i]);
        discardStrategyAction(strat, signal);
    }

    while (i < bars.len) : (i += 1) {
        if (cancelled.load(.acquire)) return error.Cancelled;

        setStrategyEquity(strat, equity);
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
                if (position) |*pos| {
                    const cf = closeFill(strat, bars, timestamps, i, pos.side, cfg);
                    const fraction = closeFraction(strat);
                    const closed_contracts = if (fraction >= 1.0)
                        pos.contracts
                    else
                        pos.contracts * fraction;
                    const pnl = calcPnl(pos.side, pos.entry_price, cf.price, closed_contracts);
                    try trades.append(gpa, .{
                        .entry_ts = pos.entry_ts,
                        .exit_ts = cf.ts,
                        .side = pos.side,
                        .entry_price = pos.entry_price,
                        .exit_price = cf.price,
                        .pnl = pnl,
                        .contracts = closed_contracts,
                        .entry_raw = pos.entry_raw,
                    });
                    equity += pnl;
                    pos.contracts -= closed_contracts;
                    if (pos.contracts <= 0.000000001) position = null;
                } else {
                    _ = closeFraction(strat);
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

        // By default we fill on the *next* bar's open (no lookahead cheating).
        // A strategy that exposes `entry_fill` may instead provide an exact raw
        // price touched inside the current bar (for stop-entry style signals).
        const exact_entry = takeEntryFill(strat);

        // Intrabar touch strategies normally stamp an exact fill at the bar's
        // end (i+1). A strategy that deliberately trades the current bar's open
        // can opt into the current timestamp instead.
        const current_exact_ts = exact_entry != null and exactFillTimestampCurrent(strat);
        if (!current_exact_ts and i + 1 >= bars.len) break;

        const raw_entry = exact_entry orelse bars[i + 1].open;
        const entry_ts = if (current_exact_ts) timestamps[i] else timestamps[i + 1];

        // Close any existing position at the next bar's open. Booking its PnL
        // into `equity` *here* (before entering the new position) means a flip
        // reallocates the freshly-updated account, matching the paper. The
        // closing leg carries its *own* size, so it gets its own impact cost.
        if (position) |pos| {
            const exit_fill = raw_entry;
            const pnl = calcPnl(pos.side, pos.entry_price, exit_fill, pos.contracts);
            try trades.append(gpa, .{
                .entry_ts = pos.entry_ts,
                .exit_ts = entry_ts,
                .side = pos.side,
                .entry_price = pos.entry_price,
                .exit_price = exit_fill,
                .pnl = pnl,
                .contracts = pos.contracts,
                .entry_raw = pos.entry_raw,
            });
            equity += pnl;
        }

        const open_contracts = sizeFor(strat.contracts);
        const entry_fill = applyFillCost(raw_entry, new_side == .long, cfg.spread, cfg.slippage);
        position = .{ .side = new_side, .entry_price = entry_fill, .entry_raw = raw_entry, .entry_ts = entry_ts, .contracts = open_contracts };
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
            .entry_raw = pos.entry_raw,
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

// Decide the exit fill for a `.close` signal. By DEFAULT the position is closed
// at the NEXT bar's open (the same 1-bar fill delay used for entries). A strategy
// may opt into an *exact same-bar* exit price — e.g. an intrabar stop/target that
// must fill the instant price touches the level, not after the candle closes — by
// exposing an optional `exit_fill: ?f64` field. When that field is non-null, the
// position is closed at exactly that price on the CURRENT bar and the field is
// cleared (so a stale value can't leak into a later close). Strategies without
// the field are unaffected: the `@hasField` check is comptime, so this branch is
// elided entirely for them and the next-open path is preserved.
fn closeFill(strat: anytype, bars: []const Bar, timestamps: []const data.Ts, i: usize, side: Side, cfg: Config) struct { price: f64, ts: data.Ts } {
    if (comptime @hasField(@TypeOf(strat.*), "exit_fill")) {
        if (strat.exit_fill) |p| {
            strat.exit_fill = null;
            // The exit_fill price is reached DURING bar i (e.g. noise momentum's 16:00
            // flatten quotes the bar's close), so stamp the exit at the bar's
            // end — the next bar's timestamp — not its start. Stamping at the
            // start puts a price on the chart up to a full bar before the
            // market trades there, which draws the exit arrow in empty space
            // on finer chart timeframes.
            const ts: data.Ts = if (exactFillTimestampCurrent(strat))
                timestamps[i]
            else if (i + 1 < timestamps.len)
                timestamps[i + 1]
            else
                timestamps[i];
            const priced = if (exactFillsPayCosts(strat))
                applyFillCost(p, side == .short, cfg.spread, cfg.slippage)
            else
                p;
            return .{ .price = priced, .ts = ts };
        }
    }
    const price: f64 = if (i + 1 < bars.len) bars[i + 1].open else bars[i].close;
    const ts: data.Ts = if (i + 1 < bars.len) timestamps[i + 1] else timestamps[i];
    return .{ .price = price, .ts = ts };
}

fn setStrategyEquity(strat: anytype, equity: f64) void {
    if (comptime @hasField(@TypeOf(strat.*), "account_equity"))
        strat.account_equity = equity;
}

fn takeEntryFill(strat: anytype) ?f64 {
    if (comptime @hasField(@TypeOf(strat.*), "entry_fill")) {
        const fill = strat.entry_fill;
        strat.entry_fill = null;
        return fill;
    }
    return null;
}

fn closeFraction(strat: anytype) f64 {
    if (comptime @hasField(@TypeOf(strat.*), "close_fraction")) {
        const fraction = std.math.clamp(strat.close_fraction, 0.0, 1.0);
        strat.close_fraction = 1.0;
        return fraction;
    }
    return 1.0;
}

fn exactFillsPayCosts(strat: anytype) bool {
    if (comptime @hasField(@TypeOf(strat.*), "cost_exact_fills"))
        return strat.cost_exact_fills;
    return false;
}

fn exactFillTimestampCurrent(strat: anytype) bool {
    if (comptime @hasField(@TypeOf(strat.*), "exact_fill_timestamp_current"))
        return strat.exact_fill_timestamp_current;
    return false;
}

fn discardStrategyAction(strat: anytype, signal: Signal) void {
    _ = takeEntryFill(strat);
    if (comptime @hasField(@TypeOf(strat.*), "exit_fill")) strat.exit_fill = null;
    _ = closeFraction(strat);
    if (comptime @hasDecl(@TypeOf(strat.*), "onDiscardedSignal"))
        strat.onDiscardedSignal(signal);
}

pub fn calcPnl(side: Side, entry: f64, exit: f64, lots: f64) f64 {
    return switch (side) {
        .long => (exit - entry) * lots,
        .short => (entry - exit) * lots,
    };
}

// Adjusts a raw fill price for half-spread + fixed slippage.
// buying=true : long entry or short exit — price is pushed higher (costs more).
// buying=false: short entry or long exit — price is pushed lower (gets less).
// The fixed model ignores size and bar range, so it takes only the cost params.
pub fn applyFillCost(raw: f64, buying: bool, sprd: f64, slip: f64) f64 {
    const adverse = sprd / 2.0 + slip;
    return if (buying) raw + adverse else raw - adverse;
}

test "optional exact and partial fill hooks realize separate legs" {
    const TestStrategy = struct {
        pub const timeframe = "1m";
        pub const columns = .{ .high = true, .low = true, .close = true, .volume = false };

        initial_balance: f64 = 1000.0,
        contracts: f64 = 2.0,
        account_equity: f64 = 0.0,
        entry_fill: ?f64 = null,
        exit_fill: ?f64 = null,
        close_fraction: f64 = 1.0,
        cost_exact_fills: bool = false,
        step: usize = 0,
        equities: [3]f64 = .{0.0} ** 3,

        pub fn update(self: *@This(), _: Bar, _: data.Ts) Signal {
            if (self.step < self.equities.len) self.equities[self.step] = self.account_equity;
            defer self.step += 1;
            return switch (self.step) {
                0 => blk: {
                    self.entry_fill = 100.0;
                    break :blk .long;
                },
                1 => blk: {
                    self.exit_fill = 110.0;
                    self.close_fraction = 0.5;
                    break :blk .close;
                },
                2 => blk: {
                    self.exit_fill = 120.0;
                    break :blk .close;
                },
                else => .flat,
            };
        }
    };

    var strat = TestStrategy{};
    const bars = [_]Bar{
        .{ .open = 99, .high = 101, .low = 98, .close = 100 },
        .{ .open = 101, .high = 111, .low = 100, .close = 110 },
        .{ .open = 111, .high = 121, .low = 110, .close = 120 },
        .{ .open = 120, .high = 120, .low = 120, .close = 120 },
    };
    const timestamps = [_]data.Ts{
        "2025-01-02 09:30".*,
        "2025-01-02 09:31".*,
        "2025-01-02 09:32".*,
        "2025-01-02 09:33".*,
    };
    const dataset = data.Dataset{
        .bars = @constCast(bars[0..]),
        .timestamps = @constCast(timestamps[0..]),
        .allocator = std.testing.allocator,
    };
    const cfg = Config{
        .symbol = "test",
        .from = null,
        .to = null,
        .spread = 0,
        .slippage = 0,
        .warmup_days = 0,
    };

    const result = try backtestOnCfg(std.testing.allocator, &strat, dataset, cfg);
    defer result.deinit(std.testing.allocator);

    try std.testing.expectEqual(@as(usize, 2), result.trades.len);
    try std.testing.expectApproxEqAbs(@as(f64, 1.0), result.trades[0].contracts, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 10.0), result.trades[0].pnl, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1.0), result.trades[1].contracts, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 20.0), result.trades[1].pnl, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1000.0), strat.equities[0], 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1000.0), strat.equities[1], 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1010.0), strat.equities[2], 0.000001);
}

test "strategies without exact-fill hooks keep next-open fills" {
    const TestStrategy = struct {
        pub const timeframe = "1m";
        pub const columns = .{ .high = false, .low = false, .close = true, .volume = false };

        initial_balance: f64 = 1000.0,
        contracts: f64 = 1.0,
        step: usize = 0,

        pub fn update(self: *@This(), _: Bar, _: data.Ts) Signal {
            defer self.step += 1;
            return switch (self.step) {
                0 => .long,
                1 => .close,
                else => .flat,
            };
        }
    };

    var strat = TestStrategy{};
    const bars = [_]Bar{
        .{ .open = 100, .close = 100 },
        .{ .open = 101, .close = 101 },
        .{ .open = 102, .close = 102 },
    };
    const timestamps = [_]data.Ts{
        "2025-01-02 09:30".*,
        "2025-01-02 09:31".*,
        "2025-01-02 09:32".*,
    };
    const dataset = data.Dataset{
        .bars = @constCast(bars[0..]),
        .timestamps = @constCast(timestamps[0..]),
        .allocator = std.testing.allocator,
    };
    const cfg = Config{
        .symbol = "test",
        .from = null,
        .to = null,
        .spread = 0,
        .slippage = 0,
        .warmup_days = 0,
    };

    const result = try backtestOnCfg(std.testing.allocator, &strat, dataset, cfg);
    defer result.deinit(std.testing.allocator);
    try std.testing.expectEqual(@as(usize, 1), result.trades.len);
    try std.testing.expectApproxEqAbs(@as(f64, 101.0), result.trades[0].entry_price, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 102.0), result.trades[0].exit_price, 0.000001);
}

test "exact current-bar fill hook preserves open-fill timestamps" {
    const TestStrategy = struct {
        pub const timeframe = "1d";
        pub const columns = .{ .high = false, .low = false, .close = true, .volume = false };

        initial_balance: f64 = 1000.0,
        contracts: f64 = 1.0,
        entry_fill: ?f64 = null,
        exit_fill: ?f64 = null,
        exact_fill_timestamp_current: bool = true,
        step: usize = 0,

        pub fn update(self: *@This(), bar: Bar, _: data.Ts) Signal {
            defer self.step += 1;
            return switch (self.step) {
                0 => blk: {
                    self.entry_fill = bar.open;
                    break :blk .long;
                },
                1 => blk: {
                    self.exit_fill = bar.open;
                    break :blk .close;
                },
                else => .flat,
            };
        }
    };

    var strat = TestStrategy{};
    const bars = [_]Bar{
        .{ .open = 100, .close = 101 },
        .{ .open = 110, .close = 111 },
    };
    const timestamps = [_]data.Ts{
        "2025-01-02 00:00".*,
        "2025-02-03 00:00".*,
    };
    const dataset = data.Dataset{
        .bars = @constCast(bars[0..]),
        .timestamps = @constCast(timestamps[0..]),
        .allocator = std.testing.allocator,
    };
    const cfg = Config{
        .symbol = "test",
        .from = null,
        .to = null,
        .spread = 0,
        .slippage = 0,
        .warmup_days = 0,
    };

    const result = try backtestOnCfg(std.testing.allocator, &strat, dataset, cfg);
    defer result.deinit(std.testing.allocator);
    try std.testing.expectEqual(@as(usize, 1), result.trades.len);
    try std.testing.expectEqual(timestamps[0], result.trades[0].entry_ts);
    try std.testing.expectEqual(timestamps[1], result.trades[0].exit_ts);
    try std.testing.expectApproxEqAbs(@as(f64, 10.0), result.trades[0].pnl, 0.000001);
}

// ── Warm-up window helpers ────────────────────────────────────────────────────

// Index of the first bar at or after the real `from` date. Bars before it are
// warm-up only. Returns 0 when `from` is null (no warm-up). Timestamps are
// "YYYY-MM-DD HH:MM" and `from` is "YYYY-MM-DD"; lexicographic order matches
// chronological order, and a same-day bar sorts after the 10-char date prefix.
pub fn realStartIndex(timestamps: []const data.Ts, from_opt: ?[]const u8) usize {
    const f = from_opt orelse return 0;
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
