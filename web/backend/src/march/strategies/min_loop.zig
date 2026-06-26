const std = @import("std");
const engine = @import("../engine.zig");
const data = @import("../data.zig");

// MinLoop strategy:
// On every completed 1-minute bar update, it returns .long.
// The engine/runner processes this and handles close-and-reopen execution.
pub const MinLoop = struct {
    pub const timeframe: []const u8 = "1m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        .volume = true,
    };

    contracts: f64 = 0.01, // default lot size of 0.1
    leverage: f64 = 1.0,
    bar_count: u32 = 0,

    pub fn update(self: *MinLoop, bar: engine.Bar, ts: data.Ts) engine.Signal {
        _ = bar;
        _ = ts;
        self.bar_count += 1;
        return .long;
    }
};
