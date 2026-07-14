const std = @import("std");
const noise_momentum = @import("strategies/idk/noise_momentum.zig");
const night_drift = @import("strategies/idk/night_drift.zig");

test {
    std.testing.refAllDecls(noise_momentum);
    std.testing.refAllDecls(night_drift);
}
