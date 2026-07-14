const std = @import("std");
const engine = @import("bt/engine.zig");
const NightDrift = @import("strategies/idk/night_drift.zig").NightDrift;
const Params = @import("strategies/idk/night_drift.zig").Params;

// Subperiod validation for Night Drift finalists. Same pinned run settings as
// night_optimize.zig: balance 1000, spread 0.2, slippage 0, nq.
const INITIAL_BALANCE: f64 = 1000.0;
const OUTPUT_PATH = "../artifacts/night_drift_optimization/validation.csv";

const Case = struct { name: []const u8, params: Params };
const Period = struct { name: []const u8, from: []const u8, to: ?[]const u8 };

pub fn main(init: std.process.Init) !void {
    const alloc = init.gpa;
    const io = init.io;
    var cases: std.ArrayList(Case) = .empty;
    defer cases.deinit(alloc);
    try buildCases(alloc, &cases);

    const periods = [_]Period{
        .{ .name = "full", .from = "2020-01-01", .to = null },
        .{ .name = "y2020", .from = "2020-01-01", .to = "2021-01-01" },
        .{ .name = "y2021", .from = "2021-01-01", .to = "2022-01-01" },
        .{ .name = "y2022", .from = "2022-01-01", .to = "2023-01-01" },
        .{ .name = "y2023", .from = "2023-01-01", .to = "2024-01-01" },
        .{ .name = "y2024", .from = "2024-01-01", .to = "2025-01-01" },
        .{ .name = "y2025+", .from = "2025-01-01", .to = null },
    };

    var out: std.ArrayList(u8) = .empty;
    defer out.deinit(alloc);
    try out.appendSlice(alloc, "period,case,final_balance,growth,max_drawdown,max_intraday_drawdown,trades,profit_factor\n");

    for (periods) |period| {
        const cfg = engine.Config{
            .symbol = "nq",
            .from = period.from,
            .to = period.to,
            .spread = 0.2,
            .slippage = 0.0,
            .warmup_days = 90,
        };
        std.debug.print("fetching {s} ({s} to {s})...\n", .{ period.name, period.from, period.to orelse "latest" });
        const dataset = try engine.fetchDatasetCfg(io, alloc, engine.columnsFor(NightDrift), "nq_1m", cfg);
        defer dataset.deinit();
        for (cases.items) |case| {
            var strat = NightDrift{
                .initial_balance = INITIAL_BALANCE,
                .account_equity = INITIAL_BALANCE,
                .params = case.params,
            };
            const result = try engine.backtestOnCfg(alloc, &strat, dataset, cfg);
            defer result.deinit(alloc);

            var pnl: f64 = 0.0;
            var wins: f64 = 0.0;
            var losses: f64 = 0.0;
            for (result.trades) |trade| {
                pnl += trade.pnl;
                if (trade.pnl >= 0.0) wins += trade.pnl else losses -= trade.pnl;
            }
            const growth = pnl / INITIAL_BALANCE * 100.0;
            const pf = if (losses > 0.0) wins / losses else 0.0;
            std.debug.print("{s: <7} {s: <20} growth={d: >8.1}% dd={d: >5.2}% idd={d: >5.2}% pf={d:.2} trades={d}\n", .{ period.name, case.name, growth, result.max_drawdown, result.max_intraday_drawdown, pf, result.trades.len });
            const line = try std.fmt.allocPrint(alloc, "{s},{s},{d:.2},{d:.6},{d:.6},{d:.6},{d},{d:.6}\n", .{ period.name, case.name, INITIAL_BALANCE + pnl, growth, result.max_drawdown, result.max_intraday_drawdown, result.trades.len, pf });
            defer alloc.free(line);
            try out.appendSlice(alloc, line);
        }
    }

    const cwd = std.Io.Dir.cwd();
    try cwd.createDirPath(io, "../artifacts/night_drift_optimization");
    try cwd.writeFile(io, .{ .sub_path = OUTPUT_PATH, .data = out.items });
    std.debug.print("validation written to {s}\n", .{OUTPUT_PATH});
}

fn buildCases(alloc: std.mem.Allocator, cases: *std.ArrayList(Case)) !void {
    // The pre-optimization defaults (spelled out — `Params{}` is now the winner).
    try cases.append(alloc, .{ .name = "old_baseline", .params = .{
        .min_selloff_sigma = 0.0,
        .entry_minute = 20 * 60,
        .exit_minute = 5 * 60,
        .tp_sigma = 1.5,
        .boost_sigma = 0.75,
        .boost_mult = 1.25,
    } });

    // Shipped winner (current strategy defaults): 1015.8% @ 14.79% DD full window.
    const best = Params{};
    try cases.append(alloc, .{ .name = "final_1015", .params = best });

    // Neighbors — if the plateau is flat these should score close to the winner.
    var v = best;
    v.exit_minute = 300;
    try cases.append(alloc, .{ .name = "nb_out300", .params = v });

    v = best;
    v.vix_floor = 15.5;
    try cases.append(alloc, .{ .name = "nb_vixf155", .params = v });

    v = best;
    v.tp_sigma = 1.5;
    v.boost_mult = 1.5;
    try cases.append(alloc, .{ .name = "nb_tp15_bm15", .params = v });

    v = best;
    v.size_scale = 0.9;
    try cases.append(alloc, .{ .name = "final_size090", .params = v });
}
