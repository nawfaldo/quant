const std = @import("std");
const engine = @import("bt/engine.zig");
const data = @import("bt/data.zig");
const NoiseMomentum = @import("strategies/idk/noise_momentum.zig").NoiseMomentum;
const Params = @import("strategies/idk/noise_momentum.zig").Params;

const INITIAL_BALANCE: f64 = 1000.0;
const OUTPUT_PATH = "../artifacts/noise_momentum_optimization/validation.csv";

const Case = struct { name: []const u8, params: Params };
const Period = struct { name: []const u8, from: []const u8, to: ?[]const u8 };

pub fn main(init: std.process.Init) !void {
    const alloc = init.gpa;
    const io = init.io;
    var cases: std.ArrayList(Case) = .empty;
    defer cases.deinit(alloc);
    try buildCases(alloc, &cases);
    var args = std.process.Args.Iterator.init(init.minimal.args);
    _ = args.skip();
    const full_only = if (args.next()) |arg| std.mem.eql(u8, arg, "full") else false;

    const periods = [_]Period{
        .{ .name = "full", .from = "2020-01-01", .to = null },
        .{ .name = "early", .from = "2020-01-01", .to = "2023-01-01" },
        .{ .name = "recent", .from = "2023-01-01", .to = null },
    };

    var out: std.ArrayList(u8) = .empty;
    defer out.deinit(alloc);
    try out.appendSlice(alloc, "period,case,final_balance,growth,max_drawdown,max_intraday_drawdown,trades,profit_factor\n");

    for (periods, 0..) |period, period_i| {
        if (full_only and period_i > 0) break;
        const cfg = runConfig(period.from, period.to);
        std.debug.print("fetching {s} ({s} to {s})...\n", .{ period.name, period.from, period.to orelse "latest" });
        const dataset = try engine.fetchDatasetCfg(io, alloc, engine.columnsFor(NoiseMomentum), "nq_1m", cfg);
        defer dataset.deinit();
        for (cases.items) |case| {
            const m = try measure(alloc, dataset, cfg, case.params);
            std.debug.print("{s: <6} {s: <20} growth={d: >8.1}% dd={d: >5.2}% idd={d: >5.2}% pf={d:.2} trades={d}\n", .{ period.name, case.name, m.growth, m.dd, m.idd, m.pf, m.trades });
            const line = try std.fmt.allocPrint(alloc, "{s},{s},{d:.2},{d:.6},{d:.6},{d:.6},{d},{d:.6}\n", .{ period.name, case.name, m.final, m.growth, m.dd, m.idd, m.trades, m.pf });
            defer alloc.free(line);
            try out.appendSlice(alloc, line);
        }
    }

    const cwd = std.Io.Dir.cwd();
    try cwd.createDirPath(io, "../artifacts/noise_momentum_optimization");
    try cwd.writeFile(io, .{ .sub_path = OUTPUT_PATH, .data = out.items });
    std.debug.print("validation written to {s}\n", .{OUTPUT_PATH});
}

fn buildCases(alloc: std.mem.Allocator, cases: *std.ArrayList(Case)) !void {
    try cases.append(alloc, .{ .name = "baseline", .params = .{} });

    var robust = Params{};
    robust.volatility_multiplier_enter = 1.35;
    robust.risk_unit_sigma = 0.36;
    robust.stop_loss_ladder_step_1 = -0.275;
    robust.take_profit_ladder_step_0 = 2.55;
    robust.take_profit_ladder_step_1 = 12.0;
    robust.sizing_scale = 1.10;
    try cases.append(alloc, .{ .name = "robust_1100", .params = robust });
    robust.sizing_scale = 1.105;
    try cases.append(alloc, .{ .name = "robust_1105", .params = robust });
    robust.sizing_scale = 1.11;
    try cases.append(alloc, .{ .name = "robust_1110", .params = robust });
    robust.sizing_scale = 1.115;
    try cases.append(alloc, .{ .name = "robust_1115", .params = robust });
    robust.sizing_scale = 0.70;
    try cases.append(alloc, .{ .name = "robust_0700", .params = robust });
    robust.sizing_scale = 0.75;
    try cases.append(alloc, .{ .name = "robust_0750", .params = robust });
    robust.sizing_scale = 0.80;
    try cases.append(alloc, .{ .name = "robust_0800", .params = robust });

    robust.sizing_scale = 1.105;
    robust.volatility_multiplier_enter = 1.325;
    try cases.append(alloc, .{ .name = "robust_vm1325", .params = robust });
    robust.volatility_multiplier_enter = 1.375;
    try cases.append(alloc, .{ .name = "robust_vm1375", .params = robust });
    robust.volatility_multiplier_enter = 1.35;
    robust.risk_unit_sigma = 0.34;
    try cases.append(alloc, .{ .name = "robust_risk034", .params = robust });
    robust.risk_unit_sigma = 0.38;
    try cases.append(alloc, .{ .name = "robust_risk038", .params = robust });
    robust.risk_unit_sigma = 0.36;
    robust.stop_loss_ladder_step_1 = -0.25;
    try cases.append(alloc, .{ .name = "robust_sl1_025", .params = robust });
    robust.stop_loss_ladder_step_1 = -0.30;
    try cases.append(alloc, .{ .name = "robust_sl1_030", .params = robust });
    robust.stop_loss_ladder_step_1 = -0.275;
    robust.take_profit_ladder_step_0 = 2.40;
    try cases.append(alloc, .{ .name = "robust_tp0_240", .params = robust });
    robust.take_profit_ladder_step_0 = 2.70;
    try cases.append(alloc, .{ .name = "robust_tp0_270", .params = robust });
    robust.take_profit_ladder_step_0 = 2.55;
    robust.take_profit_ladder_step_1 = 8.0;
    try cases.append(alloc, .{ .name = "robust_tp1_8", .params = robust });
    robust.take_profit_ladder_step_1 = 16.0;
    try cases.append(alloc, .{ .name = "robust_tp1_16", .params = robust });

    var winner = Params{};
    winner.volatility_multiplier_enter = 1.35;
    winner.target_daily_volatility = 0.017;
    winner.risk_unit_sigma = 0.36;
    winner.stop_loss_ladder_step_1 = -0.35;
    winner.take_profit_ladder_step_0 = 2.40;
    winner.sizing_scale = 1.15;
    winner.max_entries_per_day = 12;
    try cases.append(alloc, .{ .name = "winner", .params = winner });

    var p = winner;
    p.target_daily_volatility = 0.016;
    try cases.append(alloc, .{ .name = "winner_tv016", .params = p });
    p = winner;
    p.target_daily_volatility = 0.018;
    try cases.append(alloc, .{ .name = "winner_tv018", .params = p });
    p = winner;
    p.volatility_multiplier_enter = 1.325;
    try cases.append(alloc, .{ .name = "winner_vm1325", .params = p });
    p = winner;
    p.volatility_multiplier_enter = 1.375;
    try cases.append(alloc, .{ .name = "winner_vm1375", .params = p });
    p = winner;
    p.risk_unit_sigma = 0.34;
    try cases.append(alloc, .{ .name = "winner_risk034", .params = p });
    p = winner;
    p.risk_unit_sigma = 0.38;
    try cases.append(alloc, .{ .name = "winner_risk038", .params = p });
    p = winner;
    p.stop_loss_ladder_step_1 = -0.30;
    try cases.append(alloc, .{ .name = "winner_sl1_030", .params = p });
    p = winner;
    p.stop_loss_ladder_step_1 = -0.40;
    try cases.append(alloc, .{ .name = "winner_sl1_040", .params = p });
    p = winner;
    p.take_profit_ladder_step_0 = 2.25;
    try cases.append(alloc, .{ .name = "winner_tp0_225", .params = p });
    p = winner;
    p.take_profit_ladder_step_0 = 2.55;
    try cases.append(alloc, .{ .name = "winner_tp0_255", .params = p });
    p = winner;
    p.sizing_scale = 1.10;
    try cases.append(alloc, .{ .name = "winner_size110", .params = p });
    p = winner;
    p.max_entries_per_day = 255;
    try cases.append(alloc, .{ .name = "winner_no_cap", .params = p });

    // Fine-sweep plateau around the best <=15% full-period candidates.
    var fine = Params{};
    fine.volatility_multiplier_enter = 1.35;
    fine.target_daily_volatility = 0.024;
    fine.risk_unit_sigma = 0.36;
    fine.stop_loss_ladder_step_0 = -0.425;
    fine.stop_loss_ladder_step_1 = -0.25;
    fine.take_profit_ladder_step_0 = 2.55;
    fine.take_profit_ladder_step_1 = 8.0;
    fine.sizing_scale = 1.15;
    try cases.append(alloc, .{ .name = "fine_115", .params = fine });
    fine.sizing_scale = 1.14;
    try cases.append(alloc, .{ .name = "fine_114", .params = fine });
    fine.sizing_scale = 1.16;
    try cases.append(alloc, .{ .name = "fine_116", .params = fine });
    fine.sizing_scale = 1.17;
    try cases.append(alloc, .{ .name = "fine_117", .params = fine });

    fine.sizing_scale = 1.15;
    fine.target_daily_volatility = 0.020;
    try cases.append(alloc, .{ .name = "fine_tv020", .params = fine });
    fine.target_daily_volatility = 0.021;
    try cases.append(alloc, .{ .name = "fine_tv021", .params = fine });
    fine.target_daily_volatility = 0.022;
    try cases.append(alloc, .{ .name = "fine_tv022", .params = fine });

    fine.target_daily_volatility = 0.024;
    fine.take_profit_ladder_step_1 = 10.0;
    try cases.append(alloc, .{ .name = "fine_tp1_10", .params = fine });
    fine.take_profit_ladder_step_1 = 12.0;
    try cases.append(alloc, .{ .name = "fine_tp1_12", .params = fine });
    fine.take_profit_ladder_step_1 = 8.0;
    fine.stop_loss_ladder_step_0 = -0.4125;
    try cases.append(alloc, .{ .name = "fine_stop04125", .params = fine });
    fine.stop_loss_ladder_step_0 = -0.4375;
    try cases.append(alloc, .{ .name = "fine_stop04375", .params = fine });

    fine.stop_loss_ladder_step_0 = -0.425;
    fine.risk_unit_sigma = 0.35;
    try cases.append(alloc, .{ .name = "fine_risk035", .params = fine });
    fine.risk_unit_sigma = 0.37;
    try cases.append(alloc, .{ .name = "fine_risk037", .params = fine });

    fine.risk_unit_sigma = 0.36;
    fine.sizing_scale = 1.17;
    fine.max_entries_per_day = 8;
    fine.target_daily_volatility = 0.020;
    try cases.append(alloc, .{ .name = "fine_cap8", .params = fine });
}

fn runConfig(from: []const u8, to: ?[]const u8) engine.Config {
    return .{
        .symbol = "nq",
        .from = from,
        .to = to,
        .spread = 0.4,
        .slippage = 0.0,
        .warmup_days = 90,
    };
}

const Metrics = struct { final: f64, growth: f64, dd: f64, idd: f64, trades: usize, pf: f64 };

fn measure(alloc: std.mem.Allocator, dataset: data.Dataset, cfg: engine.Config, params: Params) !Metrics {
    var strat = NoiseMomentum{
        .initial_balance = INITIAL_BALANCE,
        .account_equity = INITIAL_BALANCE,
        .cost_exact_fills = false,
        .params = params,
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
    return .{
        .final = INITIAL_BALANCE + pnl,
        .growth = pnl / INITIAL_BALANCE * 100.0,
        .dd = result.max_drawdown,
        .idd = result.max_intraday_drawdown,
        .trades = result.trades.len,
        .pf = if (losses > 0.0) wins / losses else 0.0,
    };
}
