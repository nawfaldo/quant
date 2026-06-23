const std = @import("std");
const zap = @import("zap");
const router = @import("router.zig");
const settings = @import("settings.zig");

pub fn main() !void {
    try settings.init();

    // Candle/VWAP blobs are built on demand per request (see cache.zig). The
    // server no longer scans every timeframe table at startup, so it comes up
    // instantly and never holds more than one blob in memory at a time.
    var listener = zap.HttpListener.init(.{
        .port = 8080,
        .on_request = router.onRequest,
        .log = false,
    });
    try listener.listen();
    std.debug.print("Listening on http://localhost:8080\n", .{});
    zap.start(.{ .threads = 2, .workers = 1 });
}
