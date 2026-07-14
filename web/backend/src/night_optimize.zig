const std = @import("std");
const engine = @import("bt/engine.zig");
const NightDrift = @import("strategies/idk/night_drift.zig").NightDrift;
const Params = @import("strategies/idk/night_drift.zig").Params;

// Night Drift parameter search. The user-fixed run settings are pinned here:
// initial balance 1000, spread 0.2, slippage 0, nq, 2020 -> latest.
const INITIAL_BALANCE: f64 = 1000.0;
const OUT_DIR = "../artifacts/night_drift_optimization";

const Row = struct {
    id: usize,
    params: Params,
    final_balance: f64,
    growth: f64,
    max_drawdown: f64,
    max_intraday_drawdown: f64,
    trades: usize,
    profit_factor: f64,
};

const Rng = struct {
    state: u64 = 0x9e3779b97f4a7c15,

    fn next(self: *Rng) u64 {
        self.state = self.state *% 6364136223846793005 +% 1442695040888963407;
        return self.state;
    }

    fn pick(self: *Rng, comptime T: type, values: []const T) T {
        return values[@intCast(self.next() % values.len)];
    }
};

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const alloc = init.gpa;
    var requested: usize = 500;
    var args = std.process.Args.Iterator.init(init.minimal.args);
    _ = args.skip();
    if (args.next()) |arg| requested = std.fmt.parseInt(usize, arg, 10) catch requested;
    const mode = if (args.next()) |arg| arg else "broad";
    const local = std.mem.eql(u8, mode, "local");
    const refine = std.mem.eql(u8, mode, "refine");
    const refine2 = std.mem.eql(u8, mode, "refine2");
    const refine3 = std.mem.eql(u8, mode, "refine3");

    var path_buf: [128]u8 = undefined;
    const output_path = try std.fmt.bufPrint(&path_buf, "{s}/{s}.csv", .{ OUT_DIR, mode });

    const cfg = engine.Config{
        .symbol = "nq",
        .from = "2020-01-01",
        .to = null,
        .spread = 0.2,
        .slippage = 0.0,
        .warmup_days = 90,
    };

    std.debug.print("fetching nq_1m once (2020-latest plus warmup)...\n", .{});
    const dataset = try engine.fetchDatasetCfg(io, alloc, engine.columnsFor(NightDrift), "nq_1m", cfg);
    defer dataset.deinit();
    std.debug.print("loaded {d} bars; testing {d} {s} candidates + baseline anchor\n", .{ dataset.bars.len, requested, mode });

    var rows: std.ArrayList(Row) = .empty;
    defer rows.deinit(alloc);

    // Anchor 0: production defaults — must reproduce the user's reported
    // ~500% growth / ~12% max DD baseline.
    try runOne(alloc, dataset, cfg, &rows, 0, .{});
    printRow(rows.items[0]);

    var rng = Rng{};
    var i: usize = 0;
    while (i < requested) : (i += 1) {
        const p = if (refine3) randomRefine3Params(&rng) else if (refine2) randomRefine2Params(&rng) else if (refine) randomRefineParams(&rng) else if (local) randomLocalParams(&rng) else randomParams(&rng);
        try runOne(alloc, dataset, cfg, &rows, i + 1, p);
        if ((i + 1) % 100 == 0)
            std.debug.print("completed {d}/{d}\n", .{ i + 1, requested });
    }

    try writeCsv(io, alloc, rows.items, output_path);
    std.mem.sort(Row, rows.items, {}, growthDesc);
    printLeaders(rows.items, 15.0, 20);
    printLeaders(rows.items, 13.0, 15);
    std.debug.print("all {d} results: {s}\n", .{ rows.items.len, output_path });
}

fn randomParams(rng: *Rng) Params {
    const day_masks = [_]u8{ 0b11, 0b11, 0b1, 0b10, 0b111, 0b1011, 0b1111, 0b11111, 0b10011, 0b11011, 0b101, 0b1000011, 0b1011111 };
    const vix_floors = [_]f64{ 0, 12, 14, 15, 15, 16, 18, 20, 24 };
    const vix_caps = [_]f64{ 0, 0, 0, 25, 30, 35, 45 };
    const min_selloffs = [_]f64{ -999, -0.25, 0, 0, 0.15, 0.3, 0.5, 0.75, 1.0 };
    const entries = [_]u16{ 1110, 1140, 1170, 1200, 1230, 1260, 1290, 1320, 1380, 1410, 0, 30, 60, 90, 120, 150, 180, 240 };
    const exits = [_]u16{ 60, 120, 180, 210, 240, 270, 300, 330, 360, 420, 480, 540, 570 };
    const tps = [_]f64{ 0, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0 };
    const stops = [_]f64{ 0, 0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0 };
    const windows = [_]usize{ 7, 10, 14, 21, 30 };
    const boost_sigmas = [_]f64{ 0.4, 0.5, 0.75, 1.0, 1.25, 1.5 };
    const boost_mults = [_]f64{ 1.0, 1.25, 1.5, 1.75, 2.0, 2.5 };
    const size_scales = [_]f64{ 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0 };

    var p = Params{
        .day_mask = rng.pick(u8, &day_masks),
        .vix_floor = rng.pick(f64, &vix_floors),
        .vix_cap = rng.pick(f64, &vix_caps),
        .min_selloff_sigma = rng.pick(f64, &min_selloffs),
        .entry_minute = rng.pick(u16, &entries),
        .exit_minute = rng.pick(u16, &exits),
        .tp_sigma = rng.pick(f64, &tps),
        .stop_sigma = rng.pick(f64, &stops),
        .sigma_window = rng.pick(usize, &windows),
        .boost_sigma = rng.pick(f64, &boost_sigmas),
        .boost_mult = rng.pick(f64, &boost_mults),
        .size_scale = rng.pick(f64, &size_scales),
    };
    fixTimes(&p);
    return p;
}

// Local pass: mutate 1-5 dimensions off the current defaults.
fn randomLocalParams(rng: *Rng) Params {
    const day_masks = [_]u8{ 0b11, 0b111, 0b1011, 0b1111, 0b10011, 0b11011 };
    const vix_floors = [_]f64{ 12, 14, 15, 16, 17, 18 };
    const vix_caps = [_]f64{ 0, 0, 30, 35, 45 };
    const min_selloffs = [_]f64{ -0.25, 0, 0, 0.15, 0.3, 0.5 };
    const entries = [_]u16{ 1140, 1170, 1200, 1230, 1260, 1320 };
    const exits = [_]u16{ 240, 270, 300, 330, 360, 420, 480, 540 };
    const tps = [_]f64{ 1.0, 1.25, 1.5, 1.75, 2.0, 2.5 };
    const stops = [_]f64{ 0, 0, 0.75, 1.0, 1.5, 2.0 };
    const windows = [_]usize{ 10, 14, 21, 30 };
    const boost_sigmas = [_]f64{ 0.5, 0.75, 1.0, 1.25 };
    const boost_mults = [_]f64{ 1.0, 1.25, 1.5, 1.75, 2.0 };
    const size_scales = [_]f64{ 1.0, 1.25, 1.5, 1.75, 2.0, 2.25 };

    var p = Params{};
    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 5));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u4 = @intCast(rng.next() % 12);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.day_mask = rng.pick(u8, &day_masks),
            1 => p.vix_floor = rng.pick(f64, &vix_floors),
            2 => p.vix_cap = rng.pick(f64, &vix_caps),
            3 => p.min_selloff_sigma = rng.pick(f64, &min_selloffs),
            4 => p.entry_minute = rng.pick(u16, &entries),
            5 => p.exit_minute = rng.pick(u16, &exits),
            6 => p.tp_sigma = rng.pick(f64, &tps),
            7 => p.stop_sigma = rng.pick(f64, &stops),
            8 => p.sigma_window = rng.pick(usize, &windows),
            9 => p.boost_sigma = rng.pick(f64, &boost_sigmas),
            10 => p.boost_mult = rng.pick(f64, &boost_mults),
            11 => p.size_scale = rng.pick(f64, &size_scales),
            else => unreachable,
        }
    }
    fixTimes(&p);
    return p;
}

// Refine pass: mutate 1-4 dimensions off the local-pass winner
// (entry 19:30, boost_mult 1.5, rest baseline) with fine-grained grids.
fn randomRefineParams(rng: *Rng) Params {
    const vix_floors = [_]f64{ 13, 14, 15, 15, 16 };
    const vix_caps = [_]f64{ 0, 0, 35, 45 };
    const min_selloffs = [_]f64{ -0.1, 0, 0, 0.1, 0.15 };
    const entries = [_]u16{ 1130, 1140, 1150, 1160, 1170, 1170, 1180, 1190, 1200 };
    const exits = [_]u16{ 240, 270, 285, 300, 300, 315, 330, 360 };
    const tps = [_]f64{ 1.25, 1.4, 1.5, 1.5, 1.6, 1.75 };
    const stops = [_]f64{ 0, 0, 0, 1.5, 2.0, 2.5, 3.0 };
    const windows = [_]usize{ 10, 12, 14, 14, 16, 21 };
    const boost_sigmas = [_]f64{ 0.5, 0.6, 0.7, 0.75, 0.75, 0.9, 1.0 };
    const boost_mults = [_]f64{ 1.3, 1.4, 1.5, 1.5, 1.6, 1.75, 2.0 };
    const size_scales = [_]f64{ 0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1, 1.2, 1.3 };

    var p = Params{ .entry_minute = 1170, .boost_mult = 1.5 };
    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 4));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u4 = @intCast(rng.next() % 11);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.vix_floor = rng.pick(f64, &vix_floors),
            1 => p.vix_cap = rng.pick(f64, &vix_caps),
            2 => p.min_selloff_sigma = rng.pick(f64, &min_selloffs),
            3 => p.entry_minute = rng.pick(u16, &entries),
            4 => p.exit_minute = rng.pick(u16, &exits),
            5 => p.tp_sigma = rng.pick(f64, &tps),
            6 => p.stop_sigma = rng.pick(f64, &stops),
            7 => p.sigma_window = rng.pick(usize, &windows),
            8 => p.boost_sigma = rng.pick(f64, &boost_sigmas),
            9 => p.boost_mult = rng.pick(f64, &boost_mults),
            10 => p.size_scale = rng.pick(f64, &size_scales),
            else => unreachable,
        }
    }
    fixTimes(&p);
    return p;
}

// Second refine pass: mutate 1-3 dimensions off the refine winner
// (entry 19:30, boost 0.6 sigma -> 1.5x, tp 1.4), hunting size/stop/cap
// interactions that trim the worst drawdown episode.
fn randomRefine2Params(rng: *Rng) Params {
    const vix_floors = [_]f64{ 14, 14.5, 15, 15, 15.5, 16 };
    const vix_caps = [_]f64{ 0, 0, 30, 33, 35, 40, 50 };
    const min_selloffs = [_]f64{ -0.1, 0, 0, 0.05, 0.1 };
    const entries = [_]u16{ 1150, 1160, 1165, 1170, 1170, 1175, 1180 };
    const exits = [_]u16{ 270, 285, 300, 300, 315, 330 };
    const tps = [_]f64{ 1.3, 1.35, 1.4, 1.4, 1.45, 1.5 };
    const stops = [_]f64{ 0, 0, 0, 1.5, 2.0, 2.5, 3.0, 4.0 };
    const windows = [_]usize{ 13, 14, 14, 15, 16 };
    const boost_sigmas = [_]f64{ 0.5, 0.55, 0.6, 0.6, 0.65, 0.7 };
    const boost_mults = [_]f64{ 1.45, 1.5, 1.5, 1.55, 1.6, 1.7 };
    const size_scales = [_]f64{ 0.95, 1.0, 1.0, 1.02, 1.05, 1.08, 1.1, 1.15 };

    var p = Params{ .entry_minute = 1170, .boost_sigma = 0.6, .boost_mult = 1.5, .tp_sigma = 1.4 };
    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 3));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u4 = @intCast(rng.next() % 11);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.vix_floor = rng.pick(f64, &vix_floors),
            1 => p.vix_cap = rng.pick(f64, &vix_caps),
            2 => p.min_selloff_sigma = rng.pick(f64, &min_selloffs),
            3 => p.entry_minute = rng.pick(u16, &entries),
            4 => p.exit_minute = rng.pick(u16, &exits),
            5 => p.tp_sigma = rng.pick(f64, &tps),
            6 => p.stop_sigma = rng.pick(f64, &stops),
            7 => p.sigma_window = rng.pick(usize, &windows),
            8 => p.boost_sigma = rng.pick(f64, &boost_sigmas),
            9 => p.boost_mult = rng.pick(f64, &boost_mults),
            10 => p.size_scale = rng.pick(f64, &size_scales),
            else => unreachable,
        }
    }
    fixTimes(&p);
    return p;
}

// Final polish pass: 1-4 mutations off the refine2 winner (entry 19:30,
// min selloff 0.05, boost 0.6 sigma -> 1.55x, tp 1.4), hunting the last few
// percent of growth-per-drawdown efficiency.
fn randomRefine3Params(rng: *Rng) Params {
    const vix_floors = [_]f64{ 14.5, 15, 15, 15.5 };
    const vix_caps = [_]f64{ 0, 0, 0, 40, 50 };
    const min_selloffs = [_]f64{ 0, 0.05, 0.05, 0.1, 0.15, 0.2 };
    const entries = [_]u16{ 1155, 1160, 1165, 1170, 1170, 1175, 1180 };
    const exits = [_]u16{ 285, 290, 295, 300, 300, 305, 310, 315 };
    const tps = [_]f64{ 1.3, 1.35, 1.4, 1.4, 1.45, 1.5 };
    const stops = [_]f64{ 0, 0, 0, 2.5, 3.0, 4.0 };
    const windows = [_]usize{ 13, 14, 14, 15 };
    const boost_sigmas = [_]f64{ 0.5, 0.55, 0.6, 0.6, 0.65, 0.7 };
    const boost_mults = [_]f64{ 1.5, 1.55, 1.55, 1.6, 1.65, 1.7, 1.8 };
    const size_scales = [_]f64{ 0.97, 1.0, 1.0, 1.02, 1.04, 1.06, 1.08 };

    var p = Params{ .entry_minute = 1170, .min_selloff_sigma = 0.05, .boost_sigma = 0.6, .boost_mult = 1.55, .tp_sigma = 1.4 };
    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 4));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u4 = @intCast(rng.next() % 11);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.vix_floor = rng.pick(f64, &vix_floors),
            1 => p.vix_cap = rng.pick(f64, &vix_caps),
            2 => p.min_selloff_sigma = rng.pick(f64, &min_selloffs),
            3 => p.entry_minute = rng.pick(u16, &entries),
            4 => p.exit_minute = rng.pick(u16, &exits),
            5 => p.tp_sigma = rng.pick(f64, &tps),
            6 => p.stop_sigma = rng.pick(f64, &stops),
            7 => p.sigma_window = rng.pick(usize, &windows),
            8 => p.boost_sigma = rng.pick(f64, &boost_sigmas),
            9 => p.boost_mult = rng.pick(f64, &boost_mults),
            10 => p.size_scale = rng.pick(f64, &size_scales),
            else => unreachable,
        }
    }
    fixTimes(&p);
    return p;
}

// Keep the entry/exit pair valid: a post-midnight entry needs an exit at
// least an hour later the same morning; 18:00 exactly conflicts with the
// session-open bookkeeping bar.
fn fixTimes(p: *Params) void {
    if (p.entry_minute == 18 * 60) p.entry_minute = 18 * 60 + 30;
    if (p.entry_minute < 18 * 60 and p.exit_minute < p.entry_minute + 60)
        p.exit_minute = @min(570, p.entry_minute + 120);
}

fn runOne(
    alloc: std.mem.Allocator,
    dataset: @import("bt/data.zig").Dataset,
    cfg: engine.Config,
    rows: *std.ArrayList(Row),
    id: usize,
    params: Params,
) !void {
    var strat = NightDrift{
        .initial_balance = INITIAL_BALANCE,
        .account_equity = INITIAL_BALANCE,
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
    try rows.append(alloc, .{
        .id = id,
        .params = params,
        .final_balance = INITIAL_BALANCE + pnl,
        .growth = pnl / INITIAL_BALANCE * 100.0,
        .max_drawdown = result.max_drawdown,
        .max_intraday_drawdown = result.max_intraday_drawdown,
        .trades = result.trades.len,
        .profit_factor = if (losses > 0.0) wins / losses else 0.0,
    });
}

fn growthDesc(_: void, a: Row, b: Row) bool {
    return a.growth > b.growth;
}

fn printLeaders(rows: []const Row, dd_limit: f64, limit: usize) void {
    std.debug.print("\nTOP growth with max DD <= {d:.0}%\n", .{dd_limit});
    var shown: usize = 0;
    for (rows) |r| {
        if (r.max_drawdown > dd_limit or r.final_balance <= 0.0) continue;
        printRow(r);
        shown += 1;
        if (shown == limit) break;
    }
    if (shown == 0) std.debug.print("none\n", .{});
}

fn printRow(r: Row) void {
    const p = r.params;
    std.debug.print(
        "id={d} growth={d:.1}% dd={d:.2}% idd={d:.2}% pf={d:.2} trades={d} days={b} vixf={d:.0} vixc={d:.0} sell={d:.2} in={d} out={d} tp={d:.2} sl={d:.2} win={d} bs={d:.2} bm={d:.2} size={d:.2}\n",
        .{ r.id, r.growth, r.max_drawdown, r.max_intraday_drawdown, r.profit_factor, r.trades, p.day_mask, p.vix_floor, p.vix_cap, p.min_selloff_sigma, p.entry_minute, p.exit_minute, p.tp_sigma, p.stop_sigma, p.sigma_window, p.boost_sigma, p.boost_mult, p.size_scale },
    );
}

fn writeCsv(io: std.Io, alloc: std.mem.Allocator, rows: []const Row, output_path: []const u8) !void {
    var out: std.ArrayList(u8) = .empty;
    defer out.deinit(alloc);
    try out.appendSlice(alloc, "id,final_balance,growth,max_drawdown,max_intraday_drawdown,trades,profit_factor,day_mask,vix_floor,vix_cap,min_selloff_sigma,entry_minute,exit_minute,tp_sigma,stop_sigma,sigma_window,boost_sigma,boost_mult,size_scale\n");
    for (rows) |r| {
        const p = r.params;
        const line = try std.fmt.allocPrint(alloc, "{d},{d:.2},{d:.6},{d:.6},{d:.6},{d},{d:.6},{d},{d:.2},{d:.2},{d:.4},{d},{d},{d:.4},{d:.4},{d},{d:.4},{d:.4},{d:.4}\n", .{ r.id, r.final_balance, r.growth, r.max_drawdown, r.max_intraday_drawdown, r.trades, r.profit_factor, p.day_mask, p.vix_floor, p.vix_cap, p.min_selloff_sigma, p.entry_minute, p.exit_minute, p.tp_sigma, p.stop_sigma, p.sigma_window, p.boost_sigma, p.boost_mult, p.size_scale });
        defer alloc.free(line);
        try out.appendSlice(alloc, line);
    }
    const cwd = std.Io.Dir.cwd();
    try cwd.createDirPath(io, OUT_DIR);
    try cwd.writeFile(io, .{ .sub_path = output_path, .data = out.items });
}
