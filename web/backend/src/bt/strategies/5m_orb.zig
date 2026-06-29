const std = @import("std");
const engine = @import("../engine.zig");
const data = @import("../data.zig");
const sizing = @import("../sizings/vol_target.zig");

// ── Opening-Range Breakout (Long / Short) ────────────────────────────────────
//
// Port of Zarattini & Aziz, "Can Day Trading Really Be Profitable?" (SSRN
// 4416622) — the simplified 5-minute ORB. RTH only (09:30–16:00 ET), 5-min bars.
//
// Rules:
//   • Direction is set by the FIRST 5-min candle (09:30, completes 09:35):
//       close > open → the first move was up → go LONG.
//       close < open → the first move was down → go SHORT.
//       close == open → doji → no trade that day.
//     The signal is emitted on that first bar, so the engine's 1-bar fill delay
//     opens the position at the OPEN of the SECOND candle (09:35).
//   • Stop loss: 5% of the 14-day ATR (Wilder's smoothing) from the actual
//     entry price (09:35 open). Fills INTRABAR the instant price touches the
//     level (checked against the bar's high/low), at the exact stop price — or
//     the bar's open if it gapped past. No 1-bar delay.
//   • No take-profit target. Hold until stop or end of day.
//   • Time exit: flatten at 15:55 → fills at 16:00 open (1-bar delay).
//   • One trade per day. No re-entry after a stop-out or the close.
//   • No trade if ATR has fewer than 14 complete days of history.
//
// ── Position sizing ───────────────────────────────────────────────────────────
//   `sizing_mode` selects how each entry is sized:
//     .none       → fixed lots (base_lots × leverage).
//     .vol_target → Harvey et al. (2018) volatility targeting; lots scale by
//                   vol_target/σ̂. See sizings/vol_target.zig for the method.
//   The CLI/tuner set the mode and (for vol targeting) the params on the struct.

pub const Orb = struct {
    pub const timeframe: []const u8 = "5m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        .volume = false,
    };

    initial_balance: f64 = 10_000.0,
    contracts: f64 = 1.0,
    leverage: f64 = 1.0,

    sizing_mode: sizing.Mode = .none,
    vol: sizing.VolTarget = .{},

    current_day: [10]u8 = .{0} ** 10,
    in_position: bool = false,
    is_long: bool = false,
    traded_today: bool = false,
    have_entry: bool = false,
    entry_price: f64 = 0.0,
    stop_price: f64 = 0.0,
    stop_dist: f64 = 0.0, // set at signal time, applied to entry price at fill
    exit_fill: ?f64 = null, // exact intrabar stop fill price; consumed by the engine

    base_contracts: f64 = 0.0,
    base_set: bool = false,

    // 14-day ATR state (Wilder's smoothing over daily OHLC synthesised from 5m bars)
    prev_close: f64 = 0.0,
    day_high: f64 = 0.0,
    day_low: f64 = 0.0,
    day_close: f64 = 0.0,
    day_started: bool = false,
    has_prev_close: bool = false,
    atr14: f64 = 0.0,
    atr_days: u8 = 0,
    atr_init_sum: f64 = 0.0,

    const ATR_PERIOD: u8 = 14;
    const ATR_STOP_PCT: f64 = 0.05;
    const RTH_OPEN: u16 = 9 * 60 + 30;
    const EXIT_TIME: u16 = 15 * 60 + 55;
    const RTH_CLOSE: u16 = 16 * 60;

    pub fn update(self: *Orb, bar: engine.Bar, ts: data.Ts) engine.Signal {
        if (!self.base_set) {
            self.base_contracts = self.contracts;
            self.base_set = true;
        }

        const hh = std.fmt.parseInt(u8, ts[11..13], 10) catch return .flat;
        const mm = std.fmt.parseInt(u8, ts[14..16], 10) catch return .flat;
        const mins: u16 = @as(u16, hh) * 60 + mm;

        const day_changed = !std.mem.eql(u8, ts[0..10], self.current_day[0..]);

        if (self.sizing_mode == .vol_target) self.vol.onBar(bar.close, day_changed);

        if (day_changed) {
            // Finalise the just-ended day into the 14-day ATR (Wilder's smoothing)
            if (self.day_started and self.has_prev_close) {
                const tr = @max(
                    self.day_high - self.day_low,
                    @max(@abs(self.day_high - self.prev_close), @abs(self.day_low - self.prev_close)),
                );
                if (self.atr_days < ATR_PERIOD) {
                    self.atr_init_sum += tr;
                    self.atr_days += 1;
                    if (self.atr_days == ATR_PERIOD) {
                        self.atr14 = self.atr_init_sum / @as(f64, @floatFromInt(ATR_PERIOD));
                    }
                } else {
                    // Wilder's EMA: atr = (atr * 13 + tr) / 14
                    self.atr14 = (self.atr14 * 13.0 + tr) / 14.0;
                }
            }
            if (self.day_started) {
                self.prev_close = self.day_close;
                self.has_prev_close = true;
            }

            @memcpy(&self.current_day, ts[0..10]);
            self.in_position = false;
            self.is_long = false;
            self.traded_today = false;
            self.have_entry = false;
            self.entry_price = 0.0;
            self.stop_price = 0.0;
            self.stop_dist = 0.0;
            self.exit_fill = null;

            self.day_high = bar.high;
            self.day_low = bar.low;
            self.day_close = bar.close;
            self.day_started = true;
        } else {
            self.day_high = @max(self.day_high, bar.high);
            self.day_low = @min(self.day_low, bar.low);
            self.day_close = bar.close;
        }

        if (mins < RTH_OPEN or mins >= RTH_CLOSE) return .flat;

        if (mins >= EXIT_TIME) {
            if (self.in_position) {
                self.in_position = false;
                return .close;
            }
            return .flat;
        }

        if (self.in_position) {
            if (!self.have_entry) {
                self.have_entry = true;
                self.entry_price = bar.open;
                self.stop_price = if (self.is_long)
                    self.entry_price - self.stop_dist
                else
                    self.entry_price + self.stop_dist;
            }
            // Intrabar stop: fills the instant price touches the level
            if (self.is_long) {
                if (bar.low <= self.stop_price) {
                    self.exit_fill = @min(bar.open, self.stop_price);
                    self.in_position = false;
                    return .close;
                }
            } else {
                if (bar.high >= self.stop_price) {
                    self.exit_fill = @max(bar.open, self.stop_price);
                    self.in_position = false;
                    return .close;
                }
            }
            return .flat;
        }

        if (!self.traded_today and mins == RTH_OPEN) {
            self.traded_today = true;
            if (bar.close == bar.open) return .flat;
            if (self.atr_days < ATR_PERIOD) return .flat;

            const mult = if (self.sizing_mode == .vol_target) self.vol.multiplier() else 1.0;
            self.contracts = self.base_contracts * mult;

            const go_long = bar.close > bar.open;
            self.in_position = true;
            self.is_long = go_long;
            self.stop_dist = ATR_STOP_PCT * self.atr14;
            return if (go_long) .long else .short;
        }

        return .flat;
    }
};
