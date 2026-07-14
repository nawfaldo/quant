const std = @import("std");
const engine = @import("bt/engine.zig");
const NoiseMomentum = @import("strategies/idk/noise_momentum.zig").NoiseMomentum;
const Params = @import("strategies/idk/noise_momentum.zig").Params;

const INITIAL_BALANCE: f64 = 1000.0;
const BROAD_OUTPUT_PATH = "../artifacts/noise_momentum_optimization/broad.csv";
const LOCAL_OUTPUT_PATH = "../artifacts/noise_momentum_optimization/local.csv";
const REFINE_OUTPUT_PATH = "../artifacts/noise_momentum_optimization/refine.csv";
const EXIT_OUTPUT_PATH = "../artifacts/noise_momentum_optimization/exit.csv";
const XREFINE_OUTPUT_PATH = "../artifacts/noise_momentum_optimization/xrefine.csv";

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
    state: u64 = 0x4d595df4d0f33173,

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
    const exit_mode = std.mem.eql(u8, mode, "exit");
    const xrefine = std.mem.eql(u8, mode, "xrefine");
    const output_path = if (xrefine)
        XREFINE_OUTPUT_PATH
    else if (exit_mode)
        EXIT_OUTPUT_PATH
    else if (refine)
        REFINE_OUTPUT_PATH
    else if (local)
        LOCAL_OUTPUT_PATH
    else
        BROAD_OUTPUT_PATH;

    const cfg = engine.Config{
        .symbol = "nq",
        .from = "2020-01-01",
        .to = null,
        // Noise Momentum uses exact raw exits.  A doubled engine spread makes
        // the one entry fill pay the requested full 0.2 point and exits pay 0.
        .spread = 0.4,
        .slippage = 0.0,
        .warmup_days = 90,
    };

    std.debug.print("fetching nq_1m once (2020-latest plus warmup)...\n", .{});
    const dataset = try engine.fetchDatasetCfg(io, alloc, engine.columnsFor(NoiseMomentum), "nq_1m", cfg);
    defer dataset.deinit();
    std.debug.print("loaded {d} bars; testing {d} {s} candidates + 3 anchors\n", .{ dataset.bars.len, requested, mode });

    var rows: std.ArrayList(Row) = .empty;
    defer rows.deinit(alloc);

    // Production default and the paper's two published Ladder parameter sets.
    try runOne(alloc, dataset, cfg, &rows, 0, .{});
    try runOne(alloc, dataset, cfg, &rows, 1, paperParams(0.064, 0.16, 2.55, 3.68));
    try runOne(alloc, dataset, cfg, &rows, 2, paperParams(0.052, -0.27, 2.12, 20.32));
    if (exit_mode) {
        // The paper's published VWAP / VWAP&Ladder / exit-boundary parameter
        // sets (Tables 9-11), as structural anchors for the search.
        try runOne(alloc, dataset, cfg, &rows, 3, paperVwap(2, 1.03, 0.013, 12, 45, 13));
        try runOne(alloc, dataset, cfg, &rows, 4, paperVwap(2, 0.85, 0.014, 12, 45, 13));
        try runOne(alloc, dataset, cfg, &rows, 5, paperVwapLadder(4, 1.33, 0.025, 42, 46, -0.41, -0.28, 2.2, 37.08));
        try runOne(alloc, dataset, cfg, &rows, 6, paperVwapLadder(4, 1.33, 0.020, 13, 15, -0.65, 2.87, 11.72, 21.64));
        try runOne(alloc, dataset, cfg, &rows, 7, paperExitBoundary(5, 1.29, 0.78, 0.016, 7, 35, 16, false));
        try runOne(alloc, dataset, cfg, &rows, 8, paperExitBoundary(4, 1.14, 0.35, 0.030, 10, 50, 31, true));
    }

    var rng = Rng{};
    var i: usize = 0;
    while (i < requested) : (i += 1) {
        const p = if (xrefine)
            randomExitRefineParams(&rng)
        else if (exit_mode)
            randomExitParams(&rng)
        else if (refine)
            randomRefineParams(&rng)
        else if (local)
            randomLocalParams(&rng)
        else
            randomParams(&rng);
        try runOne(alloc, dataset, cfg, &rows, i + 9, p);
        if ((i + 1) % 25 == 0)
            std.debug.print("completed {d}/{d}\n", .{ i + 1, requested });
    }

    try writeCsv(io, alloc, rows.items, output_path);
    std.mem.sort(Row, rows.items, {}, growthDesc);
    printLeaders(rows.items, 15.0, 20);
    printLeaders(rows.items, 10.0, 20);
    std.debug.print("all {d} results: {s}\n", .{ rows.items.len, output_path });
}

fn paperParams(target_vol: f64, stop1: f64, tp0: f64, tp1: f64) Params {
    var p = Params{};
    p.lookback_days = 4;
    p.volatility_multiplier_enter = 1.28;
    p.target_daily_volatility = target_vol;
    p.start_trade_after_open_minutes = 42;
    p.trade_frequency_minutes = 23;
    p.exit_trades_before_close_minutes = 30;
    p.stop_loss_ladder_step_0 = -0.40;
    p.stop_loss_ladder_step_1 = stop1;
    p.take_profit_ladder_step_0 = tp0;
    p.take_profit_ladder_step_1 = tp1;
    return p;
}

fn paperVwap(lookback: u8, vm: f64, target_vol: f64, start: u16, freq: u16, exit_before: u16) Params {
    var p = Params{};
    p.lookback_days = lookback;
    p.volatility_multiplier_enter = vm;
    p.target_daily_volatility = target_vol;
    p.start_trade_after_open_minutes = start;
    p.trade_frequency_minutes = freq;
    p.exit_trades_before_close_minutes = exit_before;
    p.exit_ladder = false;
    p.exit_vwap = true;
    return p;
}

fn paperVwapLadder(lookback: u8, vm: f64, target_vol: f64, start: u16, freq: u16, sl0: f64, sl1: f64, tp0: f64, tp1: f64) Params {
    var p = Params{};
    p.lookback_days = lookback;
    p.volatility_multiplier_enter = vm;
    p.target_daily_volatility = target_vol;
    p.start_trade_after_open_minutes = start;
    p.trade_frequency_minutes = freq;
    p.exit_trades_before_close_minutes = 30;
    p.stop_loss_ladder_step_0 = sl0;
    p.stop_loss_ladder_step_1 = sl1;
    p.take_profit_ladder_step_0 = tp0;
    p.take_profit_ladder_step_1 = tp1;
    p.exit_vwap = true;
    return p;
}

fn paperExitBoundary(lookback: u8, vm_enter: f64, vm_exit: f64, target_vol: f64, start: u16, freq: u16, exit_before: u16, with_vwap: bool) Params {
    var p = Params{};
    p.lookback_days = lookback;
    p.volatility_multiplier_enter = vm_enter;
    p.volatility_multiplier_exit = vm_exit;
    p.target_daily_volatility = target_vol;
    p.start_trade_after_open_minutes = start;
    p.trade_frequency_minutes = freq;
    p.exit_trades_before_close_minutes = exit_before;
    p.exit_ladder = false;
    p.exit_boundary = true;
    p.exit_vwap = with_vwap;
    return p;
}

fn randomExitParams(rng: *Rng) Params {
    const lookbacks = [_]u8{ 2, 3, 4, 5, 7, 10, 14, 14 };
    const multipliers = [_]f64{ 0.85, 0.95, 1.03, 1.14, 1.28, 1.35, 1.45 };
    const exit_fracs = [_]f64{ 0.25, 0.40, 0.55, 0.70, 0.85 };
    const target_vols = [_]f64{ 0.013, 0.016, 0.020, 0.024, 0.030 };
    const starts = [_]u16{ 5, 10, 12, 20, 30, 42, 60 };
    const frequencies = [_]u16{ 12, 15, 23, 30, 45, 60 };
    const exits = [_]u16{ 15, 20, 30, 45 };
    const last_entries = [_]u16{ 240, 300, 360, 390 };
    const risk_sigmas = [_]f64{ 0.28, 0.36, 0.44, 0.52 };
    const stop0s = [_]f64{ -0.30, -0.40, -0.50, -0.65 };
    const stop1s = [_]f64{ -0.35, -0.25, -0.10, 0.0, 0.15 };
    const tp0s = [_]f64{ 1.75, 2.12, 2.55, 3.20 };
    const tp1s = [_]f64{ 5.0, 8.0, 12.0, 20.0, 37.0 };
    const sizing_scales = [_]f64{ 0.85, 1.0, 1.10, 1.20, 1.35, 1.50 };
    // (ladder, vwap, boundary) weighted toward the paper's best structures.
    const structures = [_]u8{ 0b010, 0b010, 0b010, 0b110, 0b110, 0b110, 0b001, 0b011, 0b101, 0b100 };

    const structure = rng.pick(u8, &structures);
    var p = Params{};
    p.lookback_days = rng.pick(u8, &lookbacks);
    p.volatility_multiplier_enter = rng.pick(f64, &multipliers);
    p.volatility_multiplier_exit = p.volatility_multiplier_enter * rng.pick(f64, &exit_fracs);
    p.target_daily_volatility = rng.pick(f64, &target_vols);
    p.start_trade_after_open_minutes = rng.pick(u16, &starts);
    p.trade_frequency_minutes = rng.pick(u16, &frequencies);
    p.exit_trades_before_close_minutes = rng.pick(u16, &exits);
    p.last_entry_after_open_minutes = rng.pick(u16, &last_entries);
    p.risk_unit_sigma = rng.pick(f64, &risk_sigmas);
    p.stop_loss_ladder_step_0 = rng.pick(f64, &stop0s);
    p.stop_loss_ladder_step_1 = rng.pick(f64, &stop1s);
    p.take_profit_ladder_step_0 = rng.pick(f64, &tp0s);
    p.take_profit_ladder_step_1 = rng.pick(f64, &tp1s);
    p.sizing_scale = rng.pick(f64, &sizing_scales);
    p.exit_ladder = (structure & 0b100) != 0;
    p.exit_vwap = (structure & 0b010) != 0;
    p.exit_boundary = (structure & 0b001) != 0;
    return p;
}

// Fine search around the broad sensitivity plateau.
fn randomExitRefineParams(rng: *Rng) Params {
    const lookbacks = [_]u8{ 12, 13, 14, 15, 16 };
    const multipliers = [_]f64{ 1.30, 1.325, 1.35, 1.375, 1.40 };
    const target_vols = [_]f64{ 0.018, 0.019, 0.020, 0.021, 0.022, 0.024 };
    const starts = [_]u16{ 25, 27, 30, 33, 35 };
    const frequencies = [_]u16{ 27, 28, 30, 32, 33 };
    const exits = [_]u16{ 25, 27, 30, 33, 35 };
    const last_entries = [_]u16{ 330, 360, 390 };
    const risk_sigmas = [_]f64{ 0.34, 0.35, 0.36, 0.37, 0.38, 0.39 };
    const stop0s = [_]f64{ -0.40, -0.4125, -0.425, -0.4375, -0.45 };
    const stop1s = [_]f64{ -0.30, -0.275, -0.25, -0.225, -0.20 };
    const tp0s = [_]f64{ 2.40, 2.475, 2.55, 2.625, 2.70 };
    const tp1s = [_]f64{ 8.0, 10.0, 12.0, 14.0, 16.0 };
    const sizing_scales = [_]f64{ 1.14, 1.15, 1.16, 1.17, 1.18, 1.19 };
    const max_entries = [_]u8{ 8, 12, 20, 255, 255, 255 };

    var p = Params{};
    p.volatility_multiplier_enter = 1.35;
    p.target_daily_volatility = 0.020;
    p.risk_unit_sigma = 0.36;
    p.stop_loss_ladder_step_0 = -0.425;
    p.stop_loss_ladder_step_1 = -0.25;
    p.take_profit_ladder_step_0 = 2.55;
    p.take_profit_ladder_step_1 = 12.0;
    p.sizing_scale = 1.15;

    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 6));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u5 = @intCast(rng.next() % 14);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.lookback_days = rng.pick(u8, &lookbacks),
            1 => p.volatility_multiplier_enter = rng.pick(f64, &multipliers),
            2 => p.target_daily_volatility = rng.pick(f64, &target_vols),
            3 => p.start_trade_after_open_minutes = rng.pick(u16, &starts),
            4 => p.trade_frequency_minutes = rng.pick(u16, &frequencies),
            5 => p.exit_trades_before_close_minutes = rng.pick(u16, &exits),
            6 => p.last_entry_after_open_minutes = rng.pick(u16, &last_entries),
            7 => p.risk_unit_sigma = rng.pick(f64, &risk_sigmas),
            8 => p.stop_loss_ladder_step_0 = rng.pick(f64, &stop0s),
            9 => p.stop_loss_ladder_step_1 = rng.pick(f64, &stop1s),
            10 => p.take_profit_ladder_step_0 = rng.pick(f64, &tp0s),
            11 => p.take_profit_ladder_step_1 = rng.pick(f64, &tp1s),
            12 => p.sizing_scale = rng.pick(f64, &sizing_scales),
            13 => p.max_entries_per_day = rng.pick(u8, &max_entries),
            else => unreachable,
        }
    }
    return p;
}

fn randomParams(rng: *Rng) Params {
    const lookbacks = [_]u8{ 2, 3, 4, 5, 7, 10, 14, 20, 30, 45, 60 };
    const multipliers = [_]f64{ 0.75, 0.85, 0.95, 1.05, 1.15, 1.28, 1.40, 1.55, 1.75 };
    const target_vols = [_]f64{ 0.012, 0.016, 0.020, 0.024, 0.030, 0.040, 0.052 };
    const starts = [_]u16{ 5, 15, 25, 35, 42, 50, 60, 75, 90 };
    const frequencies = [_]u16{ 10, 15, 20, 23, 30, 40, 45, 50, 60 };
    const exits = [_]u16{ 15, 20, 30, 45, 60, 75, 90 };
    const last_entries = [_]u16{ 120, 180, 240, 300, 360, 390 };
    const risk_sigmas = [_]f64{ 0.20, 0.28, 0.36, 0.40, 0.48, 0.60, 0.75 };
    const stop0s = [_]f64{ -0.25, -0.35, -0.40, -0.50, -0.65, -0.80 };
    const stop1s = [_]f64{ -0.40, -0.27, -0.15, 0.0, 0.15, 0.30, 0.50 };
    const tp0s = [_]f64{ 1.25, 1.60, 2.0, 2.12, 2.55, 3.0, 4.0 };
    const tp1s = [_]f64{ 3.5, 5.0, 8.0, 12.0, 20.0, 30.0 };
    const sizing_scales = [_]f64{ 0.55, 0.70, 0.85, 1.0, 1.15, 1.30 };
    const day_masks = [_]u8{ 0b11111, 0b11110, 0b01111, 0b11101, 0b10111, 0b11011, 0b01110, 0b10101 };
    const max_entries = [_]u8{ 1, 2, 3, 5, 8, 255 };
    const directions = [_]u8{ 3, 3, 3, 1, 2 };

    const direction = rng.pick(u8, &directions);
    return .{
        .lookback_days = rng.pick(u8, &lookbacks),
        .volatility_multiplier_enter = rng.pick(f64, &multipliers),
        .target_daily_volatility = rng.pick(f64, &target_vols),
        .start_trade_after_open_minutes = rng.pick(u16, &starts),
        .trade_frequency_minutes = rng.pick(u16, &frequencies),
        .exit_trades_before_close_minutes = rng.pick(u16, &exits),
        .last_entry_after_open_minutes = rng.pick(u16, &last_entries),
        .risk_unit_sigma = rng.pick(f64, &risk_sigmas),
        .stop_loss_ladder_step_0 = rng.pick(f64, &stop0s),
        .stop_loss_ladder_step_1 = rng.pick(f64, &stop1s),
        .take_profit_ladder_step_0 = rng.pick(f64, &tp0s),
        .take_profit_ladder_step_1 = rng.pick(f64, &tp1s),
        .sizing_scale = rng.pick(f64, &sizing_scales),
        .day_mask = rng.pick(u8, &day_masks),
        .max_entries_per_day = rng.pick(u8, &max_entries),
        .allow_long = (direction & 1) != 0,
        .allow_short = (direction & 2) != 0,
    };
}

fn randomLocalParams(rng: *Rng) Params {
    const lookbacks = [_]u8{ 7, 10, 12, 14, 16, 20, 25 };
    const multipliers = [_]f64{ 1.10, 1.20, 1.25, 1.30, 1.35, 1.40, 1.50 };
    const target_vols = [_]f64{ 0.014, 0.016, 0.018, 0.020, 0.022, 0.024, 0.028 };
    const starts = [_]u16{ 20, 25, 30, 35, 40, 42, 45, 50, 60 };
    const frequencies = [_]u16{ 20, 23, 25, 30, 35, 40, 45 };
    const exits = [_]u16{ 20, 25, 30, 35, 40, 45, 60 };
    const last_entries = [_]u16{ 240, 270, 300, 330, 360, 390 };
    const risk_sigmas = [_]f64{ 0.28, 0.32, 0.36, 0.40, 0.44, 0.48, 0.55 };
    const stop0s = [_]f64{ -0.30, -0.35, -0.40, -0.45, -0.50, -0.60 };
    const stop1s = [_]f64{ -0.40, -0.30, -0.25, -0.20, -0.10, 0.0, 0.10, 0.20 };
    const tp0s = [_]f64{ 1.50, 1.75, 2.0, 2.12, 2.25, 2.50, 3.0 };
    const tp1s = [_]f64{ 8.0, 12.0, 16.0, 20.0, 24.0, 30.0 };
    const sizing_scales = [_]f64{ 0.70, 0.80, 0.90, 1.0, 1.05, 1.10, 1.15, 1.20 };
    const day_masks = [_]u8{ 0b11111, 0b11111, 0b11111, 0b11110, 0b01111, 0b11101, 0b10111, 0b11011 };
    const max_entries = [_]u8{ 3, 5, 8, 12, 255, 255, 255 };
    const directions = [_]u8{ 3, 3, 3, 3, 3, 1, 2 };

    var p = Params{};
    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 6));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u5 = @intCast(rng.next() % 16);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.lookback_days = rng.pick(u8, &lookbacks),
            1 => p.volatility_multiplier_enter = rng.pick(f64, &multipliers),
            2 => p.target_daily_volatility = rng.pick(f64, &target_vols),
            3 => p.start_trade_after_open_minutes = rng.pick(u16, &starts),
            4 => p.trade_frequency_minutes = rng.pick(u16, &frequencies),
            5 => p.exit_trades_before_close_minutes = rng.pick(u16, &exits),
            6 => p.last_entry_after_open_minutes = rng.pick(u16, &last_entries),
            7 => p.risk_unit_sigma = rng.pick(f64, &risk_sigmas),
            8 => p.stop_loss_ladder_step_0 = rng.pick(f64, &stop0s),
            9 => p.stop_loss_ladder_step_1 = rng.pick(f64, &stop1s),
            10 => p.take_profit_ladder_step_0 = rng.pick(f64, &tp0s),
            11 => p.take_profit_ladder_step_1 = rng.pick(f64, &tp1s),
            12 => p.sizing_scale = rng.pick(f64, &sizing_scales),
            13 => p.day_mask = rng.pick(u8, &day_masks),
            14 => p.max_entries_per_day = rng.pick(u8, &max_entries),
            15 => {
                const direction = rng.pick(u8, &directions);
                p.allow_long = (direction & 1) != 0;
                p.allow_short = (direction & 2) != 0;
            },
            else => unreachable,
        }
    }
    return p;
}

fn randomRefineParams(rng: *Rng) Params {
    const lookbacks = [_]u8{ 10, 12, 14, 16, 18 };
    const multipliers = [_]f64{ 1.25, 1.30, 1.325, 1.35, 1.375, 1.40, 1.45 };
    const target_vols = [_]f64{ 0.017, 0.018, 0.019, 0.020, 0.021, 0.022, 0.024 };
    const starts = [_]u16{ 20, 25, 30, 35, 40 };
    const frequencies = [_]u16{ 23, 25, 27, 30, 33, 35, 40 };
    const exits = [_]u16{ 30, 35, 40, 45, 50, 55, 60 };
    const last_entries = [_]u16{ 270, 300, 330, 360, 390 };
    const risk_sigmas = [_]f64{ 0.32, 0.34, 0.36, 0.38, 0.40, 0.44, 0.48, 0.52 };
    const stop0s = [_]f64{ -0.30, -0.35, -0.375, -0.40, -0.425, -0.45, -0.50 };
    const stop1s = [_]f64{ -0.35, -0.30, -0.275, -0.25, -0.225, -0.20, -0.15, -0.10 };
    const tp0s = [_]f64{ 1.75, 1.90, 2.0, 2.12, 2.25, 2.40, 2.55 };
    const tp1s = [_]f64{ 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0 };
    const sizing_scales = [_]f64{ 0.90, 0.95, 1.0, 1.025, 1.05, 1.075, 1.10, 1.125, 1.15 };
    const day_masks = [_]u8{ 0b11111, 0b11111, 0b11111, 0b11111, 0b11110, 0b01111, 0b11101, 0b10111, 0b11011 };
    const max_entries = [_]u8{ 5, 8, 12, 255, 255, 255, 255 };

    var p = Params{};
    // Alternate between the best <=15% candidate and the highest-quality
    // lower-drawdown candidate from the local pass.
    if ((rng.next() & 1) == 0) {
        p.exit_trades_before_close_minutes = 45;
        p.risk_unit_sigma = 0.48;
        p.take_profit_ladder_step_1 = 8.0;
    } else {
        p.volatility_multiplier_enter = 1.35;
        p.risk_unit_sigma = 0.36;
    }

    const mutation_count: usize = 1 + @as(usize, @intCast(rng.next() % 6));
    var mutated: u32 = 0;
    var n: usize = 0;
    while (n < mutation_count) {
        const dim: u5 = @intCast(rng.next() % 15);
        const bit = @as(u32, 1) << dim;
        if ((mutated & bit) != 0) continue;
        mutated |= bit;
        n += 1;
        switch (dim) {
            0 => p.lookback_days = rng.pick(u8, &lookbacks),
            1 => p.volatility_multiplier_enter = rng.pick(f64, &multipliers),
            2 => p.target_daily_volatility = rng.pick(f64, &target_vols),
            3 => p.start_trade_after_open_minutes = rng.pick(u16, &starts),
            4 => p.trade_frequency_minutes = rng.pick(u16, &frequencies),
            5 => p.exit_trades_before_close_minutes = rng.pick(u16, &exits),
            6 => p.last_entry_after_open_minutes = rng.pick(u16, &last_entries),
            7 => p.risk_unit_sigma = rng.pick(f64, &risk_sigmas),
            8 => p.stop_loss_ladder_step_0 = rng.pick(f64, &stop0s),
            9 => p.stop_loss_ladder_step_1 = rng.pick(f64, &stop1s),
            10 => p.take_profit_ladder_step_0 = rng.pick(f64, &tp0s),
            11 => p.take_profit_ladder_step_1 = rng.pick(f64, &tp1s),
            12 => p.sizing_scale = rng.pick(f64, &sizing_scales),
            13 => p.day_mask = rng.pick(u8, &day_masks),
            14 => p.max_entries_per_day = rng.pick(u8, &max_entries),
            else => unreachable,
        }
    }
    return p;
}

fn runOne(
    alloc: std.mem.Allocator,
    dataset: @import("bt/data.zig").Dataset,
    cfg: engine.Config,
    rows: *std.ArrayList(Row),
    id: usize,
    params: Params,
) !void {
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
        "id={d} growth={d:.1}% dd={d:.2}% idd={d:.2}% pf={d:.2} trades={d} lb={d} vm={d:.2} vx={d:.2} tv={d:.3} start={d} freq={d} exit={d} last={d} risk={d:.2} sl0={d:.2} sl1={d:.2} tp0={d:.2} tp1={d:.2} size={d:.2} days={d} maxE={d} L={} S={} xl={} xv={} xb={}\n",
        .{ r.id, r.growth, r.max_drawdown, r.max_intraday_drawdown, r.profit_factor, r.trades, p.lookback_days, p.volatility_multiplier_enter, p.volatility_multiplier_exit, p.target_daily_volatility, p.start_trade_after_open_minutes, p.trade_frequency_minutes, p.exit_trades_before_close_minutes, p.last_entry_after_open_minutes, p.risk_unit_sigma, p.stop_loss_ladder_step_0, p.stop_loss_ladder_step_1, p.take_profit_ladder_step_0, p.take_profit_ladder_step_1, p.sizing_scale, p.day_mask, p.max_entries_per_day, p.allow_long, p.allow_short, p.exit_ladder, p.exit_vwap, p.exit_boundary },
    );
}

fn writeCsv(io: std.Io, alloc: std.mem.Allocator, rows: []const Row, output_path: []const u8) !void {
    var out: std.ArrayList(u8) = .empty;
    defer out.deinit(alloc);
    try out.appendSlice(alloc, "id,final_balance,growth,max_drawdown,max_intraday_drawdown,trades,profit_factor,lookback,vol_multiplier,vol_multiplier_exit,target_vol,start_min,frequency_min,exit_before_close,last_entry_after_open,risk_sigma,stop0,stop1,tp0,tp1,sizing_scale,day_mask,max_entries,allow_long,allow_short,exit_ladder,exit_vwap,exit_boundary\n");
    for (rows) |r| {
        const p = r.params;
        const line = try std.fmt.allocPrint(alloc, "{d},{d:.2},{d:.6},{d:.6},{d:.6},{d},{d:.6},{d},{d:.4},{d:.4},{d:.4},{d},{d},{d},{d},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d},{d},{},{},{},{},{}\n", .{ r.id, r.final_balance, r.growth, r.max_drawdown, r.max_intraday_drawdown, r.trades, r.profit_factor, p.lookback_days, p.volatility_multiplier_enter, p.volatility_multiplier_exit, p.target_daily_volatility, p.start_trade_after_open_minutes, p.trade_frequency_minutes, p.exit_trades_before_close_minutes, p.last_entry_after_open_minutes, p.risk_unit_sigma, p.stop_loss_ladder_step_0, p.stop_loss_ladder_step_1, p.take_profit_ladder_step_0, p.take_profit_ladder_step_1, p.sizing_scale, p.day_mask, p.max_entries_per_day, p.allow_long, p.allow_short, p.exit_ladder, p.exit_vwap, p.exit_boundary });
        defer alloc.free(line);
        try out.appendSlice(alloc, line);
    }
    const cwd = std.Io.Dir.cwd();
    try cwd.createDirPath(io, "../artifacts/noise_momentum_optimization");
    try cwd.writeFile(io, .{ .sub_path = output_path, .data = out.items });
}
