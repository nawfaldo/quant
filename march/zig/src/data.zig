const std = @import("std");

pub const Bar = struct {
    open: f64 = 0,
    high: f64 = 0,
    low: f64 = 0,
    close: f64 = 0,
    volume: i64 = 0,
};

// "YYYY-MM-DD HH:MM" is exactly 16 ASCII bytes.
pub const Ts = [16]u8;
