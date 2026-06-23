const std = @import("std");
const engine = @import("src/engine.zig");
const data = @import("src/data.zig");
const OrbBuy = @import("src/strategies/30m_buy.zig").OrbBuy;

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const gpa = init.gpa;

    var buf: [512]u8 = undefined;
    var w = std.Io.File.stdout().writer(io, &buf);
    const out = &w.interface;

    var strat = OrbBuy{ .initial_balance = 10_000, .contracts = 1 };
    const result = try engine.run(io, gpa, &strat);
    defer result.deinit(gpa);

    try out.print("total trades: {d}\n", .{result.trades.len});
    const n = @min(result.trades.len, 12);
    for (result.trades[0..n]) |t| {
        try out.print("entry_ts='{s}'  exit_ts='{s}'  entry={d}  exit={d}  pnl={d}\n", .{
            t.entry_ts, t.exit_ts, t.entry_price, t.exit_price, t.pnl,
        });
    }
    try out.flush();
}
