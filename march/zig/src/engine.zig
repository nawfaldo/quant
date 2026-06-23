const data = @import("data.zig");

// Re-export types so strategy files only need `@import("engine.zig")`.
pub const Bar = data.Bar;
pub const Ts = data.Ts;

pub const Signal = enum { long, short, flat, close };
