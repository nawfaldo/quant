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
//   • Stop loss is the first candle's extreme:
//       long  → stop = LOW  of the first candle.
//       short → stop = HIGH of the first candle.
//     Fills INTRABAR the instant price touches the level (checked against the
//     bar's high/low), at the exact stop price — or the bar's open if it gapped
//     past the stop. No waiting for the candle to close, no 1-bar delay.
//   • Profit target: 10R from the ACTUAL entry (the 09:35 open):
//       long  → target = entry + 10 × (entry − stop)
//       short → target = entry − 10 × (stop − entry)
//     Also fills intrabar at the exact target (or the open if it gapped past).
//   • Exit: 10R or end of day, whichever comes first. Flatten at 15:55 →
//     fills at the 16:00 open (1-bar delay).
//   • One trade per day. No re-entry after a stop-out, target, or the close.
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
    tp_price: f64 = 0.0,
    exit_fill: ?f64 = null, // exact intrabar stop/TP fill price; consumed by the engine

    base_contracts: f64 = 0.0,
    base_set: bool = false,

    const TP_R_MULT: f64 = 10.0;
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
            @memcpy(&self.current_day, ts[0..10]);
            self.in_position = false;
            self.is_long = false;
            self.traded_today = false;
            self.have_entry = false;
            self.entry_price = 0.0;
            self.stop_price = 0.0;
            self.tp_price = 0.0;
            self.exit_fill = null;
        }

        if (mins < RTH_OPEN or mins >= RTH_CLOSE) return .flat;

        const close5 = bar.close;

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
                const risk = @abs(self.entry_price - self.stop_price);
                self.tp_price = if (self.is_long)
                    self.entry_price + TP_R_MULT * risk
                else
                    self.entry_price - TP_R_MULT * risk;
            }
            // Intrabar stop/TP: the level fills the instant price TOUCHES it
            // (using this bar's high/low), not after the bar closes. The fill is
            // the exact level — unless the bar gapped past it at the open, in
            // which case the realistic fill is that worse open price. `exit_fill`
            // is read by the engine to close on THIS bar (no 1-bar delay). Stop
            // is checked before target so a bar that spans both books the loss.
            if (self.is_long) {
                if (bar.low <= self.stop_price) {
                    self.exit_fill = @min(bar.open, self.stop_price);
                    self.in_position = false;
                    return .close;
                }
                if (bar.high >= self.tp_price) {
                    self.exit_fill = @max(bar.open, self.tp_price);
                    self.in_position = false;
                    return .close;
                }
            } else {
                if (bar.high >= self.stop_price) {
                    self.exit_fill = @max(bar.open, self.stop_price);
                    self.in_position = false;
                    return .close;
                }
                if (bar.low <= self.tp_price) {
                    self.exit_fill = @min(bar.open, self.tp_price);
                    self.in_position = false;
                    return .close;
                }
            }
            return .flat;
        }

        if (!self.traded_today and mins == RTH_OPEN) {
            self.traded_today = true;
            if (close5 == bar.open) return .flat;

            const mult = if (self.sizing_mode == .vol_target) self.vol.multiplier() else 1.0;
            self.contracts = self.base_contracts * mult;

            const go_long = close5 > bar.open;
            self.in_position = true;
            self.is_long = go_long;
            self.stop_price = if (go_long) bar.low else bar.high;
            return if (go_long) .long else .short;
        }

        return .flat;
    }
};
