const std = @import("std");
const builtin = @import("builtin");
const http = @import("http.zig");
const router = @import("router.zig");
const settings = @import("settings.zig");
const db = @import("db.zig");

const DEFAULT_PORT: u16 = 8080;

extern "c" fn getenv(name: [*:0]const u8) ?[*:0]const u8;

// Starts the march live-trading engine (strategy state + Bookmap WS thread).
// March routes are now served on the main web port via router.zig; there is no
// separate port-4000 server. Still spawned only on Windows because the Bookmap
// WebSocket (ws.zig/ws2_32) is Windows-only.
fn marchServe(io: std.Io) void {
    const march = @import("march_api.zig");
    march.init(io);
}

// Cross-platform HTTP server (Windows / macOS / Linux) on std.Io.net. The old
// zap/facil.io listener was POSIX-only and forced a WSL build on Windows.
pub fn main(init: std.process.Init) !void {
    const io = init.io;

    try settings.init();
    try db.initBacktestSchema();

    // Optional PORT override (defaults to 8080).
    var port: u16 = DEFAULT_PORT;
    if (getenv("PORT")) |p| {
        port = std.fmt.parseInt(u16, std.mem.sliceTo(p, 0), 10) catch DEFAULT_PORT;
    }

    const addr = try std.Io.net.IpAddress.parse("127.0.0.1", port);
    var srv = try addr.listen(io, .{ .reuse_address = true });
    defer srv.deinit(io);

    std.debug.print("Listening on http://127.0.0.1:{d}\n", .{port});

    // Integrated march live-trading server + engine, in this same process.
    // Windows-only (winhttp / MT5 / Bookmap); on macOS/Linux the web routes run
    // but the live engine does not (it can't — those integrations are Windows).
    if (builtin.os.tag == .windows) {
        _ = std.Thread.spawn(.{}, marchServe, .{io}) catch |err| {
            std.debug.print("failed to start march server: {any}\n", .{err});
        };
    }

    const alloc = std.heap.page_allocator;
    var read_buf: [65536]u8 = undefined;
    var write_buf: [65536]u8 = undefined;

    while (true) {
        const conn = srv.accept(io) catch |err| {
            std.debug.print("accept error: {any}\n", .{err});
            continue;
        };
        http.handleConnection(io, conn, &read_buf, &write_buf, alloc, router.onRequest) catch |err| {
            std.debug.print("connection error: {any}\n", .{err});
        };
    }
}
