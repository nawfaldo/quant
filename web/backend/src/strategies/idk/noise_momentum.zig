const std = @import("std");
const engine = @import("../../bt/engine.zig");
const data = @import("../../bt/data.zig");

// Parameters for Akos Maroy's "Ladder" strategy, adapted to NQ one-minute
// data. The paper's QQQ values were optimized in-sample, so the NQ search
// retained the existing 14-day and 30-minute timing structure and promoted a
// sensitivity plateau rather than the highest isolated result. On 2020-01-01
// through the latest 2026 data, neighboring size/stop/second-target values
// remained near the same 15% drawdown frontier; both 2020-2022 and 2023-latest
// were profitable. The key structural difference from the paper remains a
// volatility-scaled price risk unit rather than a fixed fraction of AUM.
pub const Config = struct {
    lookback_days: u32,
    volatility_multiplier_enter: f64,
    target_daily_volatility: f64,
    start_trade_after_open_minutes: u16,
    trade_frequency_minutes: u16,
    exit_trades_before_close_minutes: u16,
    // Risk unit r as a multiple of realized daily sigma (price distance =
    // entry * risk_unit_sigma * sigma), so it widens and narrows with regime.
    risk_unit_sigma: f64,
    stop_loss_ladder_step_0: f64,
    stop_loss_ladder_step_1: f64,
    take_profit_ladder_step_0: f64,
    take_profit_ladder_step_1: f64,
    long_margin_requirement: f64,
    short_margin_requirement: f64,
    // Final multiplier applied after margin and volatility scaling. This is a
    // deliberately small adjustment; 1.14-1.17 all remained below 15% MDD in
    // the 2020-2026 sensitivity run. 1.15 retains more margin than the peak.
    sizing_scale: f64,
    initial_balance: f64,
};

pub const config = Config{
    .lookback_days = 14,
    .volatility_multiplier_enter = 1.35,
    // A 2.4% target remained on the same plateau as 2.0-2.2%, while the paper's
    // 5.2% sat above NQ realized vol and therefore rarely de-levered from 4x.
    .target_daily_volatility = 0.024,
    .start_trade_after_open_minutes = 30,
    .trade_frequency_minutes = 30,
    .exit_trades_before_close_minutes = 30,
    .risk_unit_sigma = 0.36,
    .stop_loss_ladder_step_0 = -0.425,
    .stop_loss_ladder_step_1 = -0.25,
    .take_profit_ladder_step_0 = 2.55,
    // Eight risk units was the start of a stable 8-12 plateau and performed
    // better than the prior 12-unit second target on the full history.
    .take_profit_ladder_step_1 = 8.0,
    .long_margin_requirement = 0.25,
    .short_margin_requirement = 0.30,
    .sizing_scale = 1.15,
    .initial_balance = 10_000.0,
};

const cfg = config;

// Runtime parameter surface used by the optimizer and by robustness tests.
// Defaults preserve the production configuration exactly.  Keeping the state
// arrays at the paper's maximum 60-day lookback lets a single fetched dataset
// be reused for a large sweep instead of recompiling once per combination.
pub const Params = struct {
    lookback_days: u8 = @intCast(cfg.lookback_days),
    volatility_multiplier_enter: f64 = cfg.volatility_multiplier_enter,
    target_daily_volatility: f64 = cfg.target_daily_volatility,
    start_trade_after_open_minutes: u16 = cfg.start_trade_after_open_minutes,
    trade_frequency_minutes: u16 = cfg.trade_frequency_minutes,
    exit_trades_before_close_minutes: u16 = cfg.exit_trades_before_close_minutes,
    // Do not initiate a new position later than this many minutes after 09:30.
    // 390 means the force-exit time remains the effective limit.
    last_entry_after_open_minutes: u16 = 390,
    risk_unit_sigma: f64 = cfg.risk_unit_sigma,
    stop_loss_ladder_step_0: f64 = cfg.stop_loss_ladder_step_0,
    stop_loss_ladder_step_1: f64 = cfg.stop_loss_ladder_step_1,
    take_profit_ladder_step_0: f64 = cfg.take_profit_ladder_step_0,
    take_profit_ladder_step_1: f64 = cfg.take_profit_ladder_step_1,
    long_margin_requirement: f64 = cfg.long_margin_requirement,
    short_margin_requirement: f64 = cfg.short_margin_requirement,
    sizing_scale: f64 = cfg.sizing_scale,
    // Monday is bit 0, Friday bit 4. Weekend bits are accepted for completeness.
    day_mask: u8 = 0b0001_1111,
    max_entries_per_day: u8 = 255,
    allow_long: bool = true,
    allow_short: bool = true,
    // Exit structure (Maróy §4). The ladder is the production default; the
    // intraday-VWAP stop and the narrower exit-boundary stop are the paper's
    // other top-performing exits. Enabling several combines them into one
    // protective stop at the tightest enabled level; disabling the ladder
    // removes the take-profit legs (position runs until a stop or force exit).
    exit_ladder: bool = true,
    exit_vwap: bool = false,
    exit_boundary: bool = false,
    // Only used when exit_boundary is set; must stay below
    // volatility_multiplier_enter or the stop sits beyond the entry level.
    volatility_multiplier_exit: f64 = 0.80,
};

// Research basis: Ákos Maróy, "Improvements to Intraday Momentum Strategies
// Using Parameter Optimization and Different Exit Strategies" (2025), Ladder
// strategy. Maróy's work extends the noise-boundary intraday-momentum strategy
// of Carlo Zarattini, Andrew Aziz, and Andrea Barbon, "Beat the Market: An
// Effective Intraday Momentum Strategy for S&P500 ETF (SPY)" (2024).
// This port uses de-overfit parameters and a volatility-scaled risk unit
// instead of Maróy's in-sample-optimized "Ladder #2" values (see the config
// above for the rationale).
// The paper evaluates QQQ on one-second bars. This port uses one-minute OHLC:
// boundary entries and ladder exits are treated as intrabar touch orders, a
// stop wins if a stop and target share a bar, and a dual-boundary bar is skipped.
pub const NoiseMomentum = struct {
    pub const timeframe: []const u8 = "1m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        // Volume feeds the session-anchored VWAP used by the exit_vwap stop.
        .volume = true,
    };

    initial_balance: f64 = cfg.initial_balance,
    contracts: f64 = 0.0,
    params: Params = .{},

    // Optional engine hooks. They leave every existing strategy's behavior
    // unchanged and provide current-AUM sizing plus touched/partial fills here.
    account_equity: f64 = cfg.initial_balance,
    entry_fill: ?f64 = null,
    exit_fill: ?f64 = null,
    close_fraction: f64 = 1.0,
    // Entry-only transaction cost: runNoise doubles the engine's quoted spread
    // so the entry pays the requested 0.2 point; exact exits remain raw.
    cost_exact_fills: bool = false,

    // Current noise-area boundaries, exposed for the chart indicator.
    ub: f64 = 0.0,
    lb: f64 = 0.0,
    // Narrower exit boundaries (volatility_multiplier_exit), when enabled.
    exit_ub: f64 = 0.0,
    exit_lb: f64 = 0.0,
    // Session-anchored VWAP accumulators (reset each day). Accumulated AFTER
    // the bar's exit/entry decision, so a stop check never sees the current
    // bar's own close inside its VWAP.
    vwap_pv: f64 = 0.0,
    vwap_volume: f64 = 0.0,

    current_day: [10]u8 = .{0} ** 10,
    day_open: f64 = 0.0,
    prev_close: f64 = 0.0,
    day_last_rth_close: f64 = 0.0,

    moves: [MAX_SLOTS][MAX_LOOKBACK]f64 = .{.{0.0} ** MAX_LOOKBACK} ** MAX_SLOTS,
    move_counts: [MAX_SLOTS]u8 = .{0} ** MAX_SLOTS,
    move_heads: [MAX_SLOTS]u8 = .{0} ** MAX_SLOTS,

    daily_returns: [MAX_LOOKBACK]f64 = .{0.0} ** MAX_LOOKBACK,
    return_count: u8 = 0,
    return_head: u8 = 0,
    entries_today: u8 = 0,

    position: enum { flat, long, short } = .flat,
    ladder_step: u8 = 0,
    entry_raw: f64 = 0.0,
    entry_contracts: f64 = 0.0,
    // Risk unit r in PRICE points, fixed at entry: entry × risk_unit_sigma ×
    // 14-day daily σ. Ladder stops/targets are multiples of this, so their
    // distance tracks the volatility regime (the old AUM-fraction unit was a
    // constant ~0.2% of price and got machine-gunned in high-vol chop).
    risk_unit: f64 = 0.0,

    const MAX_LOOKBACK: usize = 60;
    const RTH_OPEN: u16 = 9 * 60 + 30;
    const RTH_CLOSE: u16 = 16 * 60;
    const MAX_SLOTS: usize = RTH_CLOSE - RTH_OPEN;

    pub fn update(self: *NoiseMomentum, bar: engine.Bar, ts: data.Ts) engine.Signal {
        const mins = parseMinutes(ts) orelse return .flat;
        self.rollDay(ts);
        self.ub = 0.0;
        self.lb = 0.0;
        self.exit_ub = 0.0;
        self.exit_lb = 0.0;
        const force_exit = RTH_CLOSE - self.params.exit_trades_before_close_minutes;

        if (mins >= RTH_OPEN and mins < RTH_CLOSE) {
            if (mins == RTH_OPEN) self.day_open = bar.open;
            self.day_last_rth_close = bar.close;
        }

        // The paper exits 30 minutes before the close. Continue consuming the
        // later bars only to capture the true 16:00 close for tomorrow's state.
        if (mins == force_exit) {
            if (self.position != .flat) {
                self.position = .flat;
                self.exit_fill = bar.open;
                self.close_fraction = 1.0;
                return .close;
            }
            return .flat;
        }
        if (mins < RTH_OPEN or mins >= force_exit or self.day_open <= 0.0)
            return .flat;

        const slot: usize = mins - RTH_OPEN;
        var signal: engine.Signal = .flat;

        if (self.move_counts[slot] == self.params.lookback_days) {
            const bounds = self.boundaries(slot, self.params.volatility_multiplier_enter);
            self.ub = bounds.upper;
            self.lb = bounds.lower;
            if (self.params.exit_boundary) {
                const exit_bounds = self.boundaries(slot, self.params.volatility_multiplier_exit);
                self.exit_ub = exit_bounds.upper;
                self.exit_lb = exit_bounds.lower;
            }
        }

        if (self.position != .flat) {
            signal = self.exitSignal(bar);
        } else if (self.isEntryMinute(mins) and
            self.dayAllowed(ts) and
            self.entries_today < self.params.max_entries_per_day and
            self.ub > 0.0 and
            self.return_count == self.params.lookback_days)
        {
            const hit_long = self.params.allow_long and bar.high >= self.ub;
            const hit_short = self.params.allow_short and bar.low <= self.lb;

            // One-minute data cannot establish which boundary traded first.
            if (hit_long != hit_short) {
                if (hit_long) {
                    const raw = if (bar.open >= self.ub) bar.open else self.ub;
                    signal = self.enter(.long, raw);
                } else {
                    const raw = if (bar.open <= self.lb) bar.open else self.lb;
                    signal = self.enter(.short, raw);
                }
            }
        }

        // Session VWAP, stored after the decision (same no-lookahead rule as
        // the moves below): the stop check on this bar used prior bars only.
        if (bar.volume > 0) {
            const typical = (bar.high + bar.low + bar.close) / 3.0;
            const vol: f64 = @floatFromInt(bar.volume);
            self.vwap_pv += typical * vol;
            self.vwap_volume += vol;
        }

        // Store the current day's close-to-open move after the decision so the
        // current bar can never influence its own boundary.
        const move = @abs(bar.close / self.day_open - 1.0);
        const head: usize = self.move_heads[slot];
        self.moves[slot][head] = move;
        const lookback: usize = self.params.lookback_days;
        self.move_heads[slot] = @intCast((head + 1) % lookback);
        if (self.move_counts[slot] < self.params.lookback_days) self.move_counts[slot] += 1;

        return signal;
    }

    fn rollDay(self: *NoiseMomentum, ts: data.Ts) void {
        if (std.mem.eql(u8, ts[0..10], self.current_day[0..])) return;

        if (self.day_last_rth_close > 0.0) {
            if (self.prev_close > 0.0)
                self.pushReturn(self.day_last_rth_close / self.prev_close - 1.0);
            self.prev_close = self.day_last_rth_close;
        }

        @memcpy(&self.current_day, ts[0..10]);
        self.day_open = 0.0;
        self.day_last_rth_close = 0.0;
        self.position = .flat;
        self.ladder_step = 0;
        self.entries_today = 0;
        self.vwap_pv = 0.0;
        self.vwap_volume = 0.0;
    }

    fn pushReturn(self: *NoiseMomentum, value: f64) void {
        const head: usize = self.return_head;
        self.daily_returns[head] = value;
        const lookback: usize = self.params.lookback_days;
        self.return_head = @intCast((head + 1) % lookback);
        if (self.return_count < self.params.lookback_days) self.return_count += 1;
    }

    fn dailyVolatility(self: *const NoiseMomentum) f64 {
        var mean: f64 = 0.0;
        const lookback: usize = self.params.lookback_days;
        for (self.daily_returns[0..lookback]) |value| mean += value;
        mean /= @as(f64, @floatFromInt(lookback));

        var squared: f64 = 0.0;
        for (self.daily_returns[0..lookback]) |value| squared += (value - mean) * (value - mean);
        return @sqrt(squared / @as(f64, @floatFromInt(lookback)));
    }

    fn boundaries(self: *const NoiseMomentum, slot: usize, multiplier: f64) struct { upper: f64, lower: f64 } {
        var sum: f64 = 0.0;
        const lookback: usize = self.params.lookback_days;
        for (self.moves[slot][0..lookback]) |move| sum += move;
        const sigma = sum / @as(f64, @floatFromInt(lookback));
        const reference = if (self.prev_close > 0.0) self.prev_close else self.day_open;
        return .{
            .upper = @max(self.day_open, reference) * (1.0 + multiplier * sigma),
            .lower = @min(self.day_open, reference) * (1.0 - multiplier * sigma),
        };
    }

    fn isEntryMinute(self: *const NoiseMomentum, mins: u16) bool {
        const since_open = mins - RTH_OPEN;
        return since_open >= self.params.start_trade_after_open_minutes and
            since_open <= self.params.last_entry_after_open_minutes and
            (since_open - self.params.start_trade_after_open_minutes) % self.params.trade_frequency_minutes == 0;
    }

    fn dayAllowed(self: *const NoiseMomentum, ts: data.Ts) bool {
        const y = std.fmt.parseInt(i64, ts[0..4], 10) catch return true;
        const m = std.fmt.parseInt(i64, ts[5..7], 10) catch return true;
        const d = std.fmt.parseInt(i64, ts[8..10], 10) catch return true;
        const yy = if (m <= 2) y - 1 else y;
        const era = @divFloor(yy, 400);
        const yoe = yy - era * 400;
        const doy = @divFloor(153 * @mod(m + 9, 12) + 2, 5) + d - 1;
        const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy;
        const epoch_days = era * 146097 + doe - 719468;
        // 1970-01-01 was Thursday. Convert to Monday=0 ... Sunday=6.
        const weekday: u3 = @intCast(@mod(epoch_days + 3, 7));
        return (self.params.day_mask & (@as(u8, 1) << weekday)) != 0;
    }

    fn enter(self: *NoiseMomentum, side: enum { long, short }, raw_price: f64) engine.Signal {
        const volatility = self.dailyVolatility();
        const scale = if (volatility > 0.0)
            @min(1.0, self.params.target_daily_volatility / volatility)
        else
            1.0;
        const margin_requirement = if (side == .long)
            self.params.long_margin_requirement
        else
            self.params.short_margin_requirement;
        const aum = @max(0.0, self.account_equity);
        const raw_contracts = (aum / margin_requirement) * scale * self.params.sizing_scale / raw_price;

        // Match the engine's 0.01-lot entry rounding so ladder risk levels use
        // the actual backtested position size.
        self.contracts = @max(0.01, @round(raw_contracts / 0.01) * 0.01);
        self.entry_contracts = self.contracts;
        self.entry_raw = raw_price;
        // Entries require a full 14-day return window, so volatility is real
        // here in live use; the 1% floor only guards direct enter() calls.
        self.risk_unit = raw_price * self.params.risk_unit_sigma *
            (if (volatility > 0.0) volatility else 0.01);
        self.ladder_step = 0;
        self.entries_today +|= 1;
        self.entry_fill = raw_price;
        self.position = if (side == .long) .long else .short;
        return if (side == .long) .long else .short;
    }

    fn exitSignal(self: *NoiseMomentum, bar: engine.Bar) engine.Signal {
        if (self.entry_contracts <= 0.0) return .flat;

        const long = self.position == .long;
        const direction: f64 = if (long) 1.0 else -1.0;

        // Protective stop: the tightest enabled level among the ladder stop,
        // the session VWAP, and the narrower exit noise boundary.
        var stop: ?f64 = null;
        if (self.params.exit_ladder) {
            const stop_multiple = if (self.ladder_step == 0)
                self.params.stop_loss_ladder_step_0
            else
                self.params.stop_loss_ladder_step_1;
            stop = self.entry_raw + direction * stop_multiple * self.risk_unit;
        }
        if (self.params.exit_vwap and self.vwap_volume > 0.0)
            stop = tighter(long, stop, self.vwap_pv / self.vwap_volume);
        if (self.params.exit_boundary and self.exit_ub > 0.0)
            stop = tighter(long, stop, if (long) self.exit_ub else self.exit_lb);

        // Conservative OHLC ordering: if both a stop and a target occur in one
        // minute, the stop is assumed to have occurred first.
        if (stop) |level| {
            const stop_hit = if (long) bar.low <= level else bar.high >= level;
            if (stop_hit) {
                self.exit_fill = stopFill(self.position, bar.open, level);
                self.close_fraction = 1.0;
                self.position = .flat;
                return .close;
            }
        }

        if (!self.params.exit_ladder) return .flat;

        const target_multiple = if (self.ladder_step == 0)
            self.params.take_profit_ladder_step_0
        else
            self.params.take_profit_ladder_step_1;
        const target = self.entry_raw + direction * target_multiple * self.risk_unit;
        const target_hit = if (long) bar.high >= target else bar.low <= target;
        if (!target_hit) return .flat;

        self.exit_fill = targetFill(self.position, bar.open, target);
        if (self.ladder_step == 0) {
            self.close_fraction = 0.5;
            self.ladder_step = 1;
        } else {
            self.close_fraction = 1.0;
            self.position = .flat;
        }
        return .close;
    }

    fn tighter(long: bool, current: ?f64, candidate: f64) ?f64 {
        const cur = current orelse return candidate;
        return if (long) @max(cur, candidate) else @min(cur, candidate);
    }

    fn stopFill(side: @TypeOf(@as(NoiseMomentum, undefined).position), open: f64, stop: f64) f64 {
        return if (side == .long) @min(open, stop) else @max(open, stop);
    }

    fn targetFill(side: @TypeOf(@as(NoiseMomentum, undefined).position), open: f64, target: f64) f64 {
        return if (side == .long) @max(open, target) else @min(open, target);
    }

    fn parseMinutes(ts: data.Ts) ?u16 {
        const hour = std.fmt.parseInt(u8, ts[11..13], 10) catch return null;
        const minute = std.fmt.parseInt(u8, ts[14..16], 10) catch return null;
        return @as(u16, hour) * 60 + minute;
    }
};

test "entry schedule starts after 30 minutes and repeats every 30" {
    const strat = NoiseMomentum{};
    try std.testing.expect(!strat.isEntryMinute(9 * 60 + 59));
    try std.testing.expect(strat.isEntryMinute(10 * 60));
    try std.testing.expect(strat.isEntryMinute(10 * 60 + 30));
    try std.testing.expect(!strat.isEntryMinute(10 * 60 + 15));
}

test "paper margin sizing differs for long and short" {
    var long = NoiseMomentum{ .account_equity = 10_000.0, .params = .{ .sizing_scale = 1.0 } };
    _ = long.enter(.long, 100.0);
    try std.testing.expectApproxEqAbs(@as(f64, 400.0), long.contracts, 0.000001);

    var short = NoiseMomentum{ .account_equity = 10_000.0, .params = .{ .sizing_scale = 1.0 } };
    _ = short.enter(.short, 100.0);
    try std.testing.expectApproxEqAbs(@as(f64, 333.33), short.contracts, 0.000001);
}

test "vwap stop exits the full position and wins over the ladder target" {
    var strat = NoiseMomentum{
        .position = .long,
        .entry_raw = 100.0,
        .entry_contracts = 10.0,
        .risk_unit = 20.0,
        .vwap_pv = 98.0 * 1000.0,
        .vwap_volume = 1000.0,
        .params = .{
            .exit_ladder = false,
            .exit_vwap = true,
        },
    };

    // Bar stays above VWAP (98): no ladder means no target, so hold.
    try std.testing.expectEqual(engine.Signal.flat, strat.exitSignal(.{
        .open = 101.0,
        .high = 150.0,
        .low = 99.0,
        .close = 120.0,
    }));

    // Bar touches VWAP: full exit at the level.
    try std.testing.expectEqual(engine.Signal.close, strat.exitSignal(.{
        .open = 99.0,
        .high = 99.5,
        .low = 97.5,
        .close = 98.5,
    }));
    try std.testing.expectApproxEqAbs(@as(f64, 98.0), strat.exit_fill.?, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1.0), strat.close_fraction, 0.000001);
    try std.testing.expectEqual(@TypeOf(strat.position).flat, strat.position);
}

test "combined stops use the tightest level for longs" {
    var strat = NoiseMomentum{
        .position = .long,
        .entry_raw = 100.0,
        .entry_contracts = 10.0,
        .risk_unit = 10.0,
        .exit_ub = 97.0,
        .exit_lb = 90.0,
        .vwap_pv = 95.0 * 1000.0,
        .vwap_volume = 1000.0,
        .params = .{
            .exit_vwap = true,
            .exit_boundary = true,
            .stop_loss_ladder_step_0 = -0.40, // ladder stop at 96
        },
    };

    // Tightest of ladder stop 96, VWAP 95, exit boundary 97 is 97.
    try std.testing.expectEqual(engine.Signal.close, strat.exitSignal(.{
        .open = 98.0,
        .high = 98.5,
        .low = 96.8,
        .close = 98.2,
    }));
    try std.testing.expectApproxEqAbs(@as(f64, 97.0), strat.exit_fill.?, 0.000001);
}

test "ladder closes half then stop-closes the remainder with stop priority" {
    var strat = NoiseMomentum{
        .position = .long,
        .entry_raw = 100.0,
        .entry_contracts = 10.0,
        .risk_unit = 20.0,
        .params = .{
            .take_profit_ladder_step_0 = 2.0,
            .take_profit_ladder_step_1 = 20.0,
        },
    };

    // Step 0 target = 100 + 2.0 * 20 = 140.
    try std.testing.expectEqual(engine.Signal.close, strat.exitSignal(.{
        .open = 120.0,
        .high = 143.0,
        .low = 100.0,
        .close = 140.0,
    }));
    try std.testing.expectApproxEqAbs(@as(f64, 140.0), strat.exit_fill.?, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 0.5), strat.close_fraction, 0.000001);
    try std.testing.expectEqual(@as(u8, 1), strat.ladder_step);
    try std.testing.expectEqual(@TypeOf(strat.position).long, strat.position);

    // Step 1 stop = 100 - 0.25 * 20 = 95. The step-1 target (500) is also
    // touched, but the stop must win.
    try std.testing.expectEqual(engine.Signal.close, strat.exitSignal(.{
        .open = 100.0,
        .high = 510.0,
        .low = 94.0,
        .close = 200.0,
    }));
    try std.testing.expectApproxEqAbs(@as(f64, 95.0), strat.exit_fill.?, 0.000001);
    try std.testing.expectApproxEqAbs(@as(f64, 1.0), strat.close_fraction, 0.000001);
    try std.testing.expectEqual(@TypeOf(strat.position).flat, strat.position);
}
