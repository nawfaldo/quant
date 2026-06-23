const std = @import("std");
const engine = @import("../engine.zig");
const data = @import("../data.zig");

// ── Buy & Hold ────────────────────────────────────────────────────────────────
// The simplest possible benchmark: go long once at the start of the date range
// and hold until the end.
//
//   • Daily bars (nq_1d). Long only, a single position for the whole period.
//   • Emits .long on every bar. The engine opens at the first in-window bar's
//     next open (1-bar fill delay), ignores subsequent .long signals while
//     already long, and closes the position at the final bar's close.
//   • Emitting every bar (not just once) means the entry still lands on the
//     first REAL bar when the engine feeds a warm-up buffer ahead of the
//     window: warm-up .long signals are discarded, and the first post-warm-up
//     .long opens the position.
//
// Sizing: fixed `contracts` lots — no volatility targeting. Produces one trade,
// so it serves as a benchmark equity curve rather than a per-trade study.

pub const BuyHold = struct {
    pub const timeframe: []const u8 = "1d";
    pub const columns = .{
        .open = true,
        .high = false,
        .low = false,
        .close = true,
        .volume = false,
    };

    initial_balance: f64 = 1000.0,
    contracts: f64 = 0.1,

    pub fn update(self: *BuyHold, bar: engine.Bar, ts: data.Ts) engine.Signal {
        _ = self;
        _ = bar;
        _ = ts;
        return .long;
    }
};
