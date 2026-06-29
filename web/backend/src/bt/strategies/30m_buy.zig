const std = @import("std");
const engine = @import("../engine.zig");
const data = @import("../data.zig");
const sizing = @import("../sizings/vol_target.zig");

// ── Opening-Range Breakout (Long Only) ───────────────────────────────────────
//
// Rules:
//   • RTH only (09:30–16:00 ET). Long only. 5-min bars from nq_5m.
//   • No wicks — breakout uses 5-min close prices; the range box uses bodies.
//   • Opening range = 9:30–10:00 (six 5-min candles).
//     OR_high = max close of the FIRST FIVE candles (9:30…9:50) — the breakout
//     reference. OR_low = the lowest candle BODY (min of open/close, no wick)
//     across ALL SIX candles (9:30…9:55) — the stop. This matches the red
//     opening-range box drawn in the frontend.
//   • Breakout is checked at ONE moment only: the bar at 9:55 (covers
//     9:55–10:00). If that close > OR_high, enter at the OPEN of the 10:00
//     bar (engine 1-bar fill delay). No trade that day otherwise.
//   • Stop loss: if a 5-min bar closes at or below OR_low, emit .close.
//     Engine fills at the next bar's open.
//   • Take profit (1:1 RR): risk = entry − OR_low, where entry is the ACTUAL
//     fill price (the open of the 10:00 bar). If a 5-min bar closes at or above
//     (entry + risk), emit .close. Same close-based mechanic as the stop;
//     engine fills at the next bar's open.
//   • Exit: if neither stop nor TP hits, signal emitted at 13:55 bar → fills at
//     14:00 open (1-bar delay).
//   • One trade per day. No re-entry after a stop-out or the 14:00 exit.
//
// ── Position sizing ───────────────────────────────────────────────────────────
//   `sizing_mode` selects how each entry is sized:
//     .none       → fixed lots (base_lots × leverage).
//     .vol_target → Harvey et al. (2018) volatility targeting; lots scale by
//                   vol_target/σ̂. See sizings/vol_target.zig for the method.
//   The CLI/tuner set the mode and (for vol targeting) the params on the struct.

pub const ThirtyMinBuy = struct {
    pub const timeframe: []const u8 = "5m";
    pub const columns = .{
        .open = true,
        .high = false,
        .low = false,
        .close = true,
        .volume = false,
    };

    initial_balance: f64 = 10_000.0,
    contracts: f64 = 1.0,
    leverage: f64 = 1.0,

    // Position sizing. `vol` holds the volatility-target params/state and is
    // only consulted when sizing_mode == .vol_target.
    sizing_mode: sizing.Mode = .none,
    vol: sizing.VolTarget = .{},

    current_day: [10]u8 = .{0} ** 10,
    or_high: f64 = 0.0,
    or_low: f64 = std.math.inf(f64),
    in_position: bool = false,
    traded_today: bool = false,
    stop_price: f64 = 0.0,
    tp_price: f64 = 0.0,
    entry_price: f64 = 0.0,
    have_entry: bool = false,

    // Baseline lots (base_lots × leverage), snapshotted once on the first bar.
    base_contracts: f64 = 0.0,
    base_set: bool = false,

    const RTH_OPEN: u16 = 9 * 60 + 30;
    const RANGE_DEF_END: u16 = 9 * 60 + 55; // OR built from bars 9:30…9:50 (timestamps < 9:55)
    const OR_END: u16 = 10 * 60; // breakout bar timestamp = 9:55 (< 10:00)
    const EXIT_TIME: u16 = 13 * 60 + 55; // emit .close at 13:55 → fills at 14:00 open (1-bar delay)
    const RTH_CLOSE: u16 = 16 * 60;

    pub fn update(self: *ThirtyMinBuy, bar: engine.Bar, ts: data.Ts) engine.Signal {
        // Snapshot the configured size once. The CLI/tuner set `contracts` to
        // base_lots × leverage before the run; every entry rescales this
        // baseline by the sizing multiplier.
        if (!self.base_set) {
            self.base_contracts = self.contracts;
            self.base_set = true;
        }

        const hh = std.fmt.parseInt(u8, ts[11..13], 10) catch return .flat;
        const mm = std.fmt.parseInt(u8, ts[14..16], 10) catch return .flat;
        const mins: u16 = @as(u16, hh) * 60 + mm;

        const day_changed = !std.mem.eql(u8, ts[0..10], self.current_day[0..]);

        // Feed every bar (incl. pre/post-market) to the vol sizer so it tracks
        // the running daily close and folds completed-day returns into σ̂. Done
        // before the day marker is updated so the rollover is detected here too.
        if (self.sizing_mode == .vol_target) self.vol.onBar(bar.close, day_changed);

        if (day_changed) {
            @memcpy(&self.current_day, ts[0..10]);
            self.or_high = 0.0;
            self.or_low = std.math.inf(f64);
            self.in_position = false;
            self.traded_today = false;
            self.stop_price = 0.0;
            self.tp_price = 0.0;
            self.entry_price = 0.0;
            self.have_entry = false;
        }

        if (mins < RTH_OPEN or mins >= RTH_CLOSE) return .flat;

        const close5 = bar.close;

        // Build the opening range from the first five bars (timestamps 9:30–9:50).
        // OR_high (breakout reference) is the max close; OR_low (stop) is the
        // lowest candle BODY (min of open/close, no wick). The 9:55 bar is the
        // breakout trigger but its body still counts toward OR_low (folded in
        // below), so OR_low spans all six candles like the frontend box.
        if (mins < RANGE_DEF_END) {
            if (close5 > self.or_high) self.or_high = close5;
            const body_low = @min(bar.open, close5);
            if (body_low < self.or_low) self.or_low = body_low;
            return .flat;
        }

        // Exit unconditionally on the first 5-min bar closing at or after 14:00.
        if (mins >= EXIT_TIME) {
            if (self.in_position) {
                self.in_position = false;
                return .close;
            }
            return .flat;
        }

        // Manage an open position: close-based stop loss at OR_low and a 1:1
        // take profit anchored to the actual entry, both on closes (no wicks).
        if (self.in_position) {
            // The engine fills the entry at the open of the first bar after the
            // breakout signal (the 10:00 bar). That bar is the first one we see
            // here while in_position, so its open is the real entry price. Set
            // the 1:1 TP from it: risk = entry − OR_low, TP = entry + risk.
            if (!self.have_entry) {
                self.have_entry = true;
                self.entry_price = bar.open;
                self.tp_price = self.entry_price + (self.entry_price - self.stop_price);
            }
            if (close5 <= self.stop_price or close5 >= self.tp_price) {
                self.in_position = false;
                return .close;
            }
            return .flat;
        }

        // SINGLE breakout check: only the bar at 9:55 (covers 9:55–10:00).
        // If it closes above OR_high, emit .long → engine fills at the open
        // of the 10:00 bar. No entries at any other time of day.
        if (mins >= RANGE_DEF_END and mins < OR_END) {
            // The 9:55 bar's body also counts toward OR_low (matches the box).
            const body_low = @min(bar.open, close5);
            if (body_low < self.or_low) self.or_low = body_low;
            if (!self.traded_today and self.or_high > 0 and close5 > self.or_high) {
                self.traded_today = true;
                self.in_position = true;
                self.stop_price = self.or_low;
                // Size this trade. The engine reads `self.contracts` for the
                // fill it opens on the next bar. .none → multiplier 1.0.
                const mult = if (self.sizing_mode == .vol_target) self.vol.multiplier() else 1.0;
                self.contracts = self.base_contracts * mult;
                // tp_price is set on the next bar once the actual entry is known.
                return .long;
            }
            return .flat;
        }

        return .flat;
    }
};
