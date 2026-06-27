const std = @import("std");
const engine = @import("../engine.zig");
const data = @import("../data.zig");
const sizing = @import("../sizings/vol_target.zig");

// All tunable parameters live in rth_vwap_config.zig — edit there, not here.
const cfg = @import("rth_vwap_config.zig").config;

// ── RTH VWAP Flip (Long/Short) ───────────────────────────────────────────────
//
// Rules:
//   • RTH only (09:30–16:00 ET). 1-min bars from {symbol}_1m.
//   • Session VWAP: a volume-weighted average of the typical price
//     ((high+low+close)/3) accumulated from the 09:30 open, reset every day.
//     The current bar is folded in BEFORE its close is compared to the VWAP.
//   • Entry: wait one minute from the RTH open — i.e. act on the first RTH bar
//     (09:30, which completes at 09:31). If it closes ABOVE the VWAP go long;
//     if it closes BELOW go short. The engine fills at the next bar's open
//     (09:31), so the entry lands one minute after the open.
//   • Flip: whenever a later RTH close lands on the OTHER side of the VWAP,
//     close the current side and enter the opposite one (close < VWAP while
//     long → flip to short; close > VWAP while short → flip to long). We emit
//     the desired side on every bar; the engine ignores a signal that matches
//     the open position and flips on one that differs, so repeats are free and
//     only genuine VWAP crosses trade.
//   • Exit: flatten the open position at 16:00. We emit .close on the 15:59 bar
//     → the engine fills at the 16:00 open (1-bar delay). One flat day, no
//     overnight carry; the next day rebuilds the VWAP from scratch.
//
// ── Position sizing ───────────────────────────────────────────────────────────
//   Identical model to OrbBuy: `sizing_mode` selects fixed lots (.none) or
//   Harvey et al. (2018) volatility targeting (.vol_target). The size is set on
//   `self.contracts` just before each entry/flip, because the engine reads
//   `strat.contracts` at signal time. See sizings/vol_target.zig.

pub const RthVwap = struct {
    pub const timeframe: []const u8 = "1m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        .volume = true,
    };

    contracts: f64 = cfg.contracts,
    leverage: f64 = cfg.leverage,

    // Position sizing. `vol` holds the volatility-target params/state and is
    // only consulted when sizing_mode == .vol_target.
    sizing_mode: sizing.Mode = cfg.sizing_mode,
    vol: sizing.VolTarget = cfg.vol,

    current_day: [10]u8 = .{0} ** 10,
    // Session VWAP accumulators (reset each day at the first RTH bar).
    cum_pv: f64 = 0.0, // Σ typical_price × volume
    cum_vol: f64 = 0.0, // Σ volume

    // Baseline lots (base_lots × leverage), snapshotted once on the first bar.
    base_contracts: f64 = 0.0,
    base_set: bool = false,

    const RTH_OPEN: u16 = cfg.rth_open; // first RTH bar timestamp (09:30)
    const EXIT_TIME: u16 = cfg.exit_time; // emit .close at 15:59 → fills at 16:00 open
    const RTH_CLOSE: u16 = cfg.rth_close; // 16:00, exclusive end of the trading window

    pub fn update(self: *RthVwap, bar: engine.Bar, ts: data.Ts) engine.Signal {
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
            self.cum_pv = 0.0;
            self.cum_vol = 0.0;
        }

        // Outside the RTH window: no VWAP, no trading.
        if (mins < RTH_OPEN or mins >= RTH_CLOSE) return .flat;

        // Fold this bar into the session VWAP before reading it (standard VWAP
        // includes the current bar). Typical price weighted by volume.
        const typical = (bar.high + bar.low + bar.close) / 3.0;
        const vol = @as(f64, @floatFromInt(bar.volume));
        self.cum_pv += typical * vol;
        self.cum_vol += vol;
        const vwap = if (self.cum_vol > 0.0) self.cum_pv / self.cum_vol else bar.close;

        // Time exit: flatten at 16:00. .close is a no-op when already flat, so
        // emitting it from 15:59 onward is safe.
        if (mins >= EXIT_TIME) return .close;

        // VWAP cross. Above → long, below → short. The engine flips on a
        // differing signal and ignores repeats, so this naturally enters on the
        // first RTH bar and flips on every subsequent close across the VWAP.
        if (bar.close > vwap) {
            const mult = if (self.sizing_mode == .vol_target) self.vol.multiplier() else 1.0;
            self.contracts = self.base_contracts * mult;
            return .long;
        } else if (bar.close < vwap) {
            const mult = if (self.sizing_mode == .vol_target) self.vol.multiplier() else 1.0;
            self.contracts = self.base_contracts * mult;
            return .short;
        }
        return .flat;
    }
};
