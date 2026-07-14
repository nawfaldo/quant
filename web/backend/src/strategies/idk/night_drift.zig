const std = @import("std");
const engine = @import("../../bt/engine.zig");
const data = @import("../../bt/data.zig");
const sizing = @import("../../sizings/vol_target.zig");

// Live-only sizing configuration. Backtests take sizing from the Test page
// request, while march applies these values when it constructs the strategy.
pub const Config = struct {
    contracts: f64,
    leverage: f64,
    sizing_mode: sizing.Mode,
    vol: sizing.VolTarget,
};

pub const config = Config{
    .contracts = 0.01,
    .leverage = 1.0,
    .sizing_mode = .none,
    .vol = .{},
};

// Night Drift
//
// Research basis: "The Overnight Drift," Federal Reserve Bank of New York
// Staff Report 917 (2020; revised 2022), by Nina Boyarchenko, Lars C. Larsen,
// and Paul Whelan. This implementation adapts its overnight-drift and
// sell-off-reversal findings to NQ futures; it is not a literal replication.
//
// Long-only overnight session strategy, parameterized via `Params`:
//   - decide at 18:00 from the last bar before 17:00; enter at the first bar
//     at/after `entry_minute` (same session for evening entries, the next
//     calendar day for post-midnight entries; default 19:30)
//   - only enter when the pre-17:00 selloff exceeds `min_selloff_sigma`
//     sigmas (0 = any red day; <= -100 disables the filter entirely)
//   - require latest VIX daily close in [vix_floor, vix_cap] (cap 0 = none)
//   - take profit at `tp_sigma` x the `sigma_window`-day close-diff sigma
//     (0 = off); optional intrabar stop at `stop_sigma` x sigma below entry
//   - close any remaining position at the first bar outside the holding
//     window (default: the 05:05 open)
//   - trade only sessions whose arming day is set in `day_mask`
//     (bit0=Monday ... bit6=Sunday)
//   - compound from a 1.41-lot anchor at the $6,000 calibration balance,
//     scaled by `size_scale`, preserving percentage exposure when a backtest
//     starts with another balance
//   - increase the compounded size by `boost_mult` when the pre-17:00
//     selloff is >= `boost_sigma` x sigma
//
// Example: a trade that fills at 20:00 on June 22 checks June 22's first bar
// open vs the last close before 17:00 and the latest VIX close from vix_1d.
// Defaults are the July 2026 optimization winner (see
// artifacts/night_drift_optimization/): on nq 2020->2026-06 with balance
// 1000 / spread 0.2 / slippage 0 it scores +1015.8% with 14.79% max
// mark-to-market drawdown (PF 2.92, 228 trades), beating the previous
// defaults (+510% / 12.6% DD) in every calendar year of the window.
// Previous defaults: min_selloff 0, entry 20:00, exit 05:00, tp 1.5,
// boost 0.75 sigma -> 1.25x.
pub const Params = struct {
    day_mask: u8 = 0b0000011, // bit0=Monday ... bit6=Sunday (arming day)
    vix_floor: f64 = 15.0,
    vix_cap: f64 = 0.0, // 0 = no cap
    min_selloff_sigma: f64 = 0.05, // arm when selloff > this; <= -100 disables
    entry_minute: u16 = 19 * 60 + 30, // > 18:00 = same session; < 18:00 = next day
    exit_minute: u16 = 5 * 60 + 5,
    tp_sigma: f64 = 1.4, // 0 = no take profit
    stop_sigma: f64 = 0.0, // 0 = no stop
    sigma_window: usize = 14, // trading days; 2..MAX_SIGMA_WINDOW
    boost_sigma: f64 = 0.6,
    boost_mult: f64 = 1.55,
    size_scale: f64 = 1.0,
};

pub const NightDrift = struct {
    pub const timeframe: []const u8 = "1m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        .volume = false,
        .vix = true,
    };

    initial_balance: f64 = 10_000.0,
    contracts: f64 = BASE_LOT,
    // Written by the backtest engine before every update. The live signal
    // runner leaves it at zero, in which case sizing falls back to 1.0x.
    account_equity: f64 = 0.0,
    leverage: f64 = 1.0,
    // Live March supplies the broker-facing configured lot here. Backtests
    // leave it null and continue using the calibrated equity-compounding rule.
    // Keeping the two sizing paths explicit prevents a live 0.01-lot setting
    // from being overwritten by the 1.41-lot backtest fallback.
    live_fixed_contracts: ?f64 = null,

    // Kept for runner/API compatibility. Night Drift owns its sizing internally.
    sizing_mode: sizing.Mode = .none,
    vol: sizing.VolTarget = .{},
    entry_fill: ?f64 = null,
    exit_fill: ?f64 = null,

    params: Params = .{},

    current_day: [10]u8 = .{0} ** 10,
    day_open: f64 = 0.0,
    day_close: f64 = 0.0,
    day_pre_entry_close: f64 = 0.0,
    day_pre_entry_close_ready: bool = false,
    last_completed_day_sigma_ready: bool = false,
    last_completed_day_sigma: f64 = 0.0,
    entry_selloff_mag: f64 = 0.0,
    session_day: [10]u8 = .{0} ** 10,
    session_open: f64 = 0.0,
    session_last_close: f64 = 0.0,
    session_ready: bool = false,
    armed: bool = false,
    in_position: bool = false,
    expected_entry_day: [10]u8 = .{0} ** 10,
    active_sigma: f64 = 0.0,
    active_target: f64 = 0.0,
    active_target_ready: bool = false,
    active_stop: f64 = 0.0,
    active_stop_ready: bool = false,

    previous_daily_close: f64 = 0.0,
    have_previous_daily_close: bool = false,
    diff_ring: [MAX_SIGMA_WINDOW]f64 = .{0.0} ** MAX_SIGMA_WINDOW,
    diff_pos: usize = 0,
    diff_count: usize = 0,
    diff_sum: f64 = 0.0,
    diff_sumsq: f64 = 0.0,

    const SESSION_OPEN: u16 = 18 * 60;
    const PRE_ENTRY_CUTOFF: u16 = 17 * 60;
    pub const MAX_SIGMA_WINDOW: usize = 64;
    const BASE_LOT: f64 = 1.41;
    const SIZING_ANCHOR_BALANCE: f64 = 6_000.0;

    pub fn update(self: *NightDrift, bar: engine.Bar, ts: data.Ts) engine.Signal {
        const mins = parseMinutes(ts) orelse return .flat;
        const day_changed = !std.mem.eql(u8, ts[0..10], self.current_day[0..]);

        if (day_changed) {
            if (hasDay(self.current_day)) self.completeDay();
            @memcpy(&self.current_day, ts[0..10]);
            self.day_open = bar.open;
            self.day_pre_entry_close = 0.0;
            self.day_pre_entry_close_ready = false;
        }
        self.day_close = bar.close;

        if (mins < PRE_ENTRY_CUTOFF) {
            self.day_pre_entry_close = bar.close;
            self.day_pre_entry_close_ready = true;
        }

        // Manage an open position before anything else so exits can never be
        // missed by session bookkeeping (stop first — conservative when both
        // the stop and the target are touched inside one bar).
        if (self.in_position) {
            if (self.stopHit(bar)) return self.closeAtStopOrWorse(bar);
            if (self.targetHit(bar)) return self.closeAtTargetOrBetter(bar);
            if (!self.inHoldingWindow(mins)) {
                self.exit_fill = bar.open;
                self.clearActiveSession();
                return .close;
            }
            if (self.session_ready) self.session_last_close = bar.close;
            return .long;
        }

        // Track the new futures session from 18:00 (or the first bar after it
        // on feeds that skip the exact timestamp) and arm an entry if the
        // pre-17:00 filter allows one.
        if (mins >= SESSION_OPEN and !std.mem.eql(u8, ts[0..10], self.session_day[0..])) {
            @memcpy(&self.session_day, ts[0..10]);
            self.session_open = bar.open;
            self.session_last_close = bar.close;
            self.session_ready = true;
            self.armEntryIfEligible(bar, ts);
            if (mins == SESSION_OPEN) return .flat;
        }

        if (self.armed) {
            switch (std.mem.order(u8, ts[0..10], self.expected_entry_day[0..])) {
                .gt => self.clearActiveSession(), // missed the window entirely
                .eq => switch (self.entryDecision(mins)) {
                    .enter => return self.enter(bar),
                    .missed => self.clearActiveSession(),
                    .wait => {},
                },
                .lt => {},
            }
        }

        if (self.session_ready) self.session_last_close = bar.close;
        return .flat;
    }

    pub fn onDiscardedSignal(self: *NightDrift, signal: engine.Signal) void {
        if (signal == .long) {
            self.entry_fill = null;
            self.clearActiveSession();
        }
    }

    // Live Bookmap path. Completed one-minute bars continue to drive all daily
    // statistics and session arming through update(); ticks are used only for
    // actions that must be executable immediately: scheduled entry/time exit
    // and protective stop/target touches. This avoids claiming a fill at a
    // minute's open after waiting for that minute to close.
    pub fn updateLiveTick(self: *NightDrift, price: f64, ts: data.Ts) engine.Signal {
        const mins = parseMinutes(ts) orelse return .flat;

        if (self.in_position) {
            if (self.active_stop_ready and price <= self.active_stop) {
                self.exit_fill = price;
                self.clearActiveSession();
                return .close;
            }
            if (self.active_target_ready and price >= self.active_target) {
                self.exit_fill = price;
                self.clearActiveSession();
                return .close;
            }
            if (!self.inHoldingWindow(mins)) {
                self.exit_fill = price;
                self.clearActiveSession();
                return .close;
            }
            return .long;
        }

        if (self.armed) {
            switch (std.mem.order(u8, ts[0..10], self.expected_entry_day[0..])) {
                .gt => self.clearActiveSession(),
                .eq => switch (self.entryDecision(mins)) {
                    .enter => return self.enter(.{
                        .open = price,
                        .high = price,
                        .low = price,
                        .close = price,
                    }),
                    .missed => self.clearActiveSession(),
                    .wait => {},
                },
                .lt => {},
            }
        }
        return .flat;
    }

    fn setEntrySize(self: *NightDrift) void {
        if (self.live_fixed_contracts) |fixed| {
            self.contracts = @max(0.0, fixed);
            return;
        }
        // BASE_LOT was calibrated on a $6,000 account. Scale from that fixed
        // dollar anchor rather than from each run's initial balance; otherwise
        // every account starts at 1.41 lots and a $1,000 run takes six times the
        // intended percentage risk. The engine supplies realized account equity
        // before every update, so this still compounds as the account changes.
        // Live callers currently do not supply equity and retain the calibrated
        // 1.41-lot fallback.
        const equity_mult = if (self.account_equity > 0.0)
            self.account_equity / SIZING_ANCHOR_BALANCE
        else
            1.0;
        const signal_mult: f64 = if (self.entry_selloff_mag >= self.params.boost_sigma)
            self.params.boost_mult
        else
            1.0;
        self.contracts = BASE_LOT * self.params.size_scale * equity_mult * signal_mult;
    }

    fn armEntryIfEligible(self: *NightDrift, bar: engine.Bar, ts: data.Ts) void {
        self.clearActiveSession();
        if (!self.last_completed_day_sigma_ready) return;
        if (!self.dayAllowed(ts)) return;
        const p = self.params;
        if (bar.vix_close < p.vix_floor) return;
        if (p.vix_cap > 0.0 and bar.vix_close > p.vix_cap) return;

        const sigma = self.last_completed_day_sigma;
        const selloff: f64 = if (self.day_pre_entry_close_ready)
            (self.day_open - self.day_pre_entry_close) / sigma
        else
            0.0;
        const filter_off = p.min_selloff_sigma <= -100.0;
        if (!filter_off and (!self.day_pre_entry_close_ready or selloff <= p.min_selloff_sigma)) return;

        self.armed = true;
        self.active_sigma = sigma;
        self.entry_selloff_mag = selloff;
        if (p.entry_minute > SESSION_OPEN) {
            @memcpy(&self.expected_entry_day, ts[0..10]);
        } else {
            self.expected_entry_day = nextDayString(ts[0..10].*);
        }
        self.setEntrySize();
    }

    const EntryDecision = enum { enter, wait, missed };

    fn entryDecision(self: *const NightDrift, mins: u16) EntryDecision {
        const p = self.params;
        if (p.entry_minute > SESSION_OPEN) {
            // Evening entry inside the arming session; window runs to midnight.
            return if (mins >= p.entry_minute) .enter else .wait;
        }
        // Post-midnight entry on the following calendar day.
        if (mins < p.entry_minute) return .wait;
        if (mins < p.exit_minute) return .enter;
        return .missed;
    }

    fn enter(self: *NightDrift, bar: engine.Bar) engine.Signal {
        self.armed = false;
        self.in_position = true;
        self.entry_fill = bar.open;
        if (self.params.tp_sigma > 0.0) {
            self.active_target = bar.open + self.params.tp_sigma * self.active_sigma;
            self.active_target_ready = true;
        }
        if (self.params.stop_sigma > 0.0) {
            self.active_stop = bar.open - self.params.stop_sigma * self.active_sigma;
            self.active_stop_ready = true;
        }
        return .long;
    }

    fn inHoldingWindow(self: *const NightDrift, mins: u16) bool {
        const p = self.params;
        if (p.entry_minute > SESSION_OPEN)
            return mins >= p.entry_minute or mins < p.exit_minute;
        return mins >= p.entry_minute and mins < p.exit_minute;
    }

    fn clearActiveSession(self: *NightDrift) void {
        self.armed = false;
        self.in_position = false;
        self.active_sigma = 0.0;
        self.active_target = 0.0;
        self.active_target_ready = false;
        self.active_stop = 0.0;
        self.active_stop_ready = false;
        self.entry_selloff_mag = 0.0;
        self.expected_entry_day = .{0} ** 10;
    }

    fn targetHit(self: *const NightDrift, bar: engine.Bar) bool {
        return self.active_target_ready and bar.high >= self.active_target;
    }

    fn stopHit(self: *const NightDrift, bar: engine.Bar) bool {
        return self.active_stop_ready and bar.low <= self.active_stop;
    }

    fn closeAtTargetOrBetter(self: *NightDrift, bar: engine.Bar) engine.Signal {
        self.exit_fill = @max(bar.open, self.active_target);
        self.clearActiveSession();
        return .close;
    }

    fn closeAtStopOrWorse(self: *NightDrift, bar: engine.Bar) engine.Signal {
        self.exit_fill = @min(bar.open, self.active_stop);
        self.clearActiveSession();
        return .close;
    }

    fn completeDay(self: *NightDrift) void {
        if (self.have_previous_daily_close) {
            self.addDailyDiff(self.day_close - self.previous_daily_close);
        }
        self.previous_daily_close = self.day_close;
        self.have_previous_daily_close = true;

        if (self.dailySigma()) |sigma| {
            self.last_completed_day_sigma_ready = sigma > 0.0;
            self.last_completed_day_sigma = sigma;
        } else {
            self.last_completed_day_sigma_ready = false;
            self.last_completed_day_sigma = 0.0;
        }
    }

    fn sigmaWindow(self: *const NightDrift) usize {
        return std.math.clamp(self.params.sigma_window, 2, MAX_SIGMA_WINDOW);
    }

    fn addDailyDiff(self: *NightDrift, diff: f64) void {
        const window = self.sigmaWindow();
        if (self.diff_count < window) {
            self.diff_count += 1;
        } else {
            const old = self.diff_ring[self.diff_pos];
            self.diff_sum -= old;
            self.diff_sumsq -= old * old;
        }
        self.diff_ring[self.diff_pos] = diff;
        self.diff_sum += diff;
        self.diff_sumsq += diff * diff;
        self.diff_pos = (self.diff_pos + 1) % window;
    }

    fn dailySigma(self: *const NightDrift) ?f64 {
        const window = self.sigmaWindow();
        if (self.diff_count < window) return null;
        const n: f64 = @floatFromInt(window);
        const variance = (self.diff_sumsq - (self.diff_sum * self.diff_sum) / n) / (n - 1.0);
        return std.math.sqrt(@max(0.0, variance));
    }

    fn parseMinutes(ts: data.Ts) ?u16 {
        const hour = std.fmt.parseInt(u8, ts[11..13], 10) catch return null;
        const minute = std.fmt.parseInt(u8, ts[14..16], 10) catch return null;
        return @as(u16, hour) * 60 + minute;
    }

    fn dayAllowed(self: *const NightDrift, ts: data.Ts) bool {
        const year = std.fmt.parseInt(i64, ts[0..4], 10) catch return false;
        const month = std.fmt.parseInt(i64, ts[5..7], 10) catch return false;
        const day = std.fmt.parseInt(i64, ts[8..10], 10) catch return false;
        // 1970-01-01 was Thursday. Monday=0, so Thursday=3.
        const weekday: u3 = @intCast(@mod(daysFromCivil(year, month, day) + 3, 7));
        return (self.params.day_mask >> weekday) & 1 == 1;
    }

    fn nextDayString(day: [10]u8) [10]u8 {
        const year = std.fmt.parseInt(i64, day[0..4], 10) catch return day;
        const month = std.fmt.parseInt(i64, day[5..7], 10) catch return day;
        const d = std.fmt.parseInt(i64, day[8..10], 10) catch return day;
        const c = civilFromDays(daysFromCivil(year, month, d) + 1);
        var out: [10]u8 = undefined;
        _ = std.fmt.bufPrint(&out, "{d:0>4}-{d:0>2}-{d:0>2}", .{
            @as(u32, @intCast(c.y)),
            @as(u32, @intCast(c.m)),
            @as(u32, @intCast(c.d)),
        }) catch return day;
        return out;
    }

    fn hasDay(day: [10]u8) bool {
        return day[0] != 0;
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
};

test "Night Drift enters on Monday or Tuesday after a red us day with high vix, compounds size, and closes at 05:05" {
    var strat = NightDrift{};
    try warmNightDrift(&strat, true);
    strat.initial_balance = 6_000.0;
    strat.account_equity = 12_000.0;

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    // 1.41 base x 2.0 equity mult x 1.55 selloff boost
    try std.testing.expectApproxEqAbs(@as(f64, 4.371), strat.contracts, 0.000001);
    try std.testing.expectEqual(engine.Signal.long, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 19:30".*));
    try std.testing.expectEqual(engine.Signal.long, strat.update(.{ .open = 101, .high = 102, .low = 100, .close = 101 }, "2025-01-15 05:04".*));
    try std.testing.expectEqual(engine.Signal.close, strat.update(.{ .open = 102, .high = 103, .low = 101, .close = 102 }, "2025-01-15 05:05".*));
    try std.testing.expectEqual(@as(?f64, 102.0), strat.exit_fill);
}

test "Night Drift scales its calibrated lot by account equity, not initial-balance ratio" {
    var small = NightDrift{ .initial_balance = 1_000.0, .account_equity = 1_000.0 };
    small.setEntrySize();
    try std.testing.expectApproxEqAbs(@as(f64, 0.235), small.contracts, 0.000001);

    var calibrated = NightDrift{ .initial_balance = 6_000.0, .account_equity = 6_000.0 };
    calibrated.setEntrySize();
    try std.testing.expectApproxEqAbs(@as(f64, 1.41), calibrated.contracts, 0.000001);

    // The live signal path does not currently inject account equity, so keep
    // its existing calibrated fallback rather than deriving it from the
    // strategy struct's default initial balance.
    var live = NightDrift{};
    live.setEntrySize();
    try std.testing.expectApproxEqAbs(@as(f64, 1.41), live.contracts, 0.000001);
}

test "Night Drift skips when us day is green or vix is below floor" {
    var strat = NightDrift{};
    try warmNightDrift(&strat, false);
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 102, .high = 103, .low = 101, .close = 102, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 20:00".*));

    var low_vix = NightDrift{};
    try warmNightDrift(&low_vix, false);
    try std.testing.expectEqual(engine.Signal.flat, low_vix.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 14.99 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, low_vix.update(.{ .open = 100, .high = 101, .low = 99, .close = 100, .vix_close = 14.99 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, low_vix.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 20:00".*));
}

test "Night Drift skips Wednesday through Sunday sessions" {
    var strat = NightDrift{};
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 120, .high = 120, .low = 120, .close = 120 }, "2025-01-15 00:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-15 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-15 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-15 20:00".*));
}

test "Night Drift exits at the 1.4 sigma take profit" {
    var strat = NightDrift{};
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.long, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 20:00".*));
    const target = strat.active_target;
    try std.testing.expect(target > 100.0);

    try std.testing.expectEqual(engine.Signal.close, strat.update(.{ .open = 101, .high = target + 0.25, .low = 100, .close = target }, "2025-01-14 20:01".*));
    try std.testing.expectEqual(@as(?f64, target), strat.exit_fill);
}

test "Night Drift stops out intrabar when a stop sigma is configured" {
    var strat = NightDrift{ .params = .{ .stop_sigma = 1.0 } };
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.long, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 20:00".*));
    const stop = strat.active_stop;
    try std.testing.expect(stop < 100.0);

    try std.testing.expectEqual(engine.Signal.close, strat.update(.{ .open = 99.5, .high = 99.6, .low = stop - 0.25, .close = stop }, "2025-01-14 20:01".*));
    try std.testing.expectEqual(@as(?f64, @min(99.5, stop)), strat.exit_fill);
}

test "Night Drift post-midnight entry fills on the following calendar day" {
    var strat = NightDrift{ .params = .{ .entry_minute = 2 * 60, .exit_minute = 9 * 60 } };
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-14 20:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-15 01:59".*));
    try std.testing.expectEqual(engine.Signal.long, strat.update(.{ .open = 101, .high = 102, .low = 100, .close = 101 }, "2025-01-15 02:00".*));
    try std.testing.expectEqual(engine.Signal.close, strat.update(.{ .open = 103, .high = 104, .low = 102, .close = 103 }, "2025-01-15 09:00".*));
    try std.testing.expectEqual(@as(?f64, 103.0), strat.exit_fill);
}

test "Night Drift does not carry a stale armed signal over the weekend" {
    var strat = NightDrift{};
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100, .vix_close = 20 }, "2025-01-18 18:00".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 100, .high = 101, .low = 99, .close = 100 }, "2025-01-18 20:00".*));
}

test "Night Drift live ticks enter and exit immediately at executable prices" {
    var strat = NightDrift{ .live_fixed_contracts = 0.01 };
    try warmNightDrift(&strat, true);

    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*));
    try std.testing.expectEqual(engine.Signal.flat, strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*));
    try std.testing.expectApproxEqAbs(@as(f64, 0.01), strat.contracts, 0.000001);

    try std.testing.expectEqual(engine.Signal.long, strat.updateLiveTick(100.25, "2025-01-14 19:30".*));
    try std.testing.expectApproxEqAbs(@as(f64, 100.25), strat.entry_fill.?, 0.000001);
    const target = strat.active_target;
    try std.testing.expectEqual(engine.Signal.close, strat.updateLiveTick(target + 0.5, "2025-01-14 19:31".*));
    try std.testing.expectApproxEqAbs(target + 0.5, strat.exit_fill.?, 0.000001);
}

test "Night Drift live tick closes at the first tick of the time-exit minute" {
    var strat = NightDrift{ .live_fixed_contracts = 0.01 };
    try warmNightDrift(&strat, true);

    _ = strat.update(.{ .open = 90, .high = 99, .low = 89, .close = 90, .vix_close = 20 }, "2025-01-14 16:59".*);
    _ = strat.update(.{ .open = 99, .high = 100, .low = 98, .close = 99, .vix_close = 20 }, "2025-01-14 18:00".*);
    try std.testing.expectEqual(engine.Signal.long, strat.updateLiveTick(100.0, "2025-01-14 19:30".*));
    try std.testing.expectEqual(engine.Signal.long, strat.updateLiveTick(101.0, "2025-01-15 05:04".*));
    try std.testing.expectEqual(engine.Signal.close, strat.updateLiveTick(100.75, "2025-01-15 05:05".*));
    try std.testing.expectApproxEqAbs(@as(f64, 100.75), strat.exit_fill.?, 0.000001);
}

fn warmNightDrift(strat: *NightDrift, previous_day_red: bool) !void {
    const closes = [_]f64{ 100, 101, 99, 102, 100, 103, 101, 104, 102, 105, 103, 106, 104, 107, 100 };
    for (closes, 0..) |close, i| {
        const date = dateFromOffset(i);
        const open: f64 = if (i == closes.len - 1)
            (if (previous_day_red) 120.0 else 90.0)
        else
            close;
        _ = strat.update(.{ .open = open, .high = @max(open, close), .low = @min(open, close), .close = open }, makeTs(&date, "00:00"));
        _ = strat.update(.{ .open = close, .high = @max(open, close), .low = @min(open, close), .close = close }, makeTs(&date, "23:59"));
    }
    const next = dateFromOffset(closes.len);
    _ = strat.update(.{ .open = 100, .high = 100, .low = 100, .close = 100 }, makeTs(&next, "00:00"));
}

fn dateFromOffset(offset: usize) [10]u8 {
    const c = NightDrift.civilFromDays(NightDrift.daysFromCivil(2024, 12, 30) + @as(i64, @intCast(offset)));
    var out: [10]u8 = undefined;
    _ = std.fmt.bufPrint(&out, "{d:0>4}-{d:0>2}-{d:0>2}", .{
        @as(u32, @intCast(c.y)),
        @as(u32, @intCast(c.m)),
        @as(u32, @intCast(c.d)),
    }) catch unreachable;
    return out;
}

fn makeTs(date: *const [10]u8, time: []const u8) data.Ts {
    var out: data.Ts = undefined;
    _ = std.fmt.bufPrint(&out, "{s} {s}", .{ date.*, time }) catch unreachable;
    return out;
}
