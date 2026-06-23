const std = @import("std");
const Io = std.Io;
const engine = @import("engine.zig");
const data = @import("data.zig");
const strategy = @import("strategy.zig");
const report = @import("report.zig");

// ── OrbBuy parameter tuning (parallel grid search) ───────────────────────────

pub const OrbCombo = struct {
    // Swept dimensions (one cartesian point per combo).
    base_contracts: f64,
    leverage: f64,
    vol_target: f64,
    vol_halflife: f64,
    vol_max_mult: f64,
    vol_min_days: u32,
    growth: f64 = 0,
    drawdown: f64 = 0,
    score: f64 = 0,
};

pub const OrbGrid = struct {
    initial_balance: f64,
    // Every position-sizing input is a swept dimension; the combos are the full
    // cartesian product of these lists. When sizing_mode == .none the vol lists
    // are single placeholder values (the CLI fills them with defaults), so they
    // contribute one combo each and have no effect.
    base_contracts: []const f64,
    leverage: []const f64,
    sizing_mode: strategy.sizing.Mode = .none,
    vol_target: []const f64,
    vol_halflife: []const f64,
    vol_max_mult: []const f64,
    vol_min_days: []const u32,
};

pub fn totalOrb(grid: OrbGrid) usize {
    return grid.base_contracts.len * grid.leverage.len *
        grid.vol_target.len * grid.vol_halflife.len *
        grid.vol_max_mult.len * grid.vol_min_days.len;
}

const OrbShared = struct {
    gpa: std.mem.Allocator,
    dataset: data.Dataset,
    initial_balance: f64,
    sizing_mode: strategy.sizing.Mode,
    combos: []OrbCombo,
    next: std.atomic.Value(usize) = .init(0),
    progress: *std.atomic.Value(usize),
    failed: std.atomic.Value(bool) = .init(false),
    worker_err: anyerror = error.Unexpected,
};

// Generic over the strategy type. OrbBuy and RthVwap share the tunable surface
// (base_contracts sweep + fixed leverage/sizing), so one worker drives both.
fn Worker(comptime S: type) type {
    return struct {
        fn run(sh: *OrbShared) void {
            while (true) {
                const i = sh.next.fetchAdd(1, .monotonic);
                if (i >= sh.combos.len) break;
                if (engine.cancelled.load(.acquire)) break;

                const c = &sh.combos[i];
                var strat = S{
                    .initial_balance = sh.initial_balance,
                    .contracts = c.base_contracts * c.leverage,
                    .leverage = c.leverage,
                    .sizing_mode = sh.sizing_mode,
                    .vol = .{
                        .target = c.vol_target,
                        .halflife = c.vol_halflife,
                        .max_mult = c.vol_max_mult,
                        .min_days = c.vol_min_days,
                    },
                };
                const result = engine.backtestOn(sh.gpa, &strat, sh.dataset) catch |err| {
                    if (err != error.Cancelled) {
                        sh.worker_err = err;
                        sh.failed.store(true, .release);
                    }
                    engine.cancelled.store(true, .release);
                    break;
                };
                defer result.deinit(sh.gpa);

                const s = report.summarize(result);
                c.growth = s.net_growth;
                c.drawdown = s.max_drawdown;
                _ = sh.progress.fetchAdd(1, .release);
            }
        }
    };
}

pub fn runOrb(comptime S: type, io: Io, gpa: std.mem.Allocator, grid: OrbGrid, progress: *std.atomic.Value(usize)) ![]OrbCombo {
    const cols = engine.columnsFor(S);
    const table = engine.tableFor(S);
    const dataset = try engine.fetchDataset(io, gpa, cols, table);
    defer dataset.deinit();

    const n = totalOrb(grid);
    const combos = try gpa.alloc(OrbCombo, n);
    errdefer gpa.free(combos);

    var k: usize = 0;
    for (grid.base_contracts) |bl| {
        for (grid.leverage) |lev| {
            for (grid.vol_target) |vt| {
                for (grid.vol_halflife) |vh| {
                    for (grid.vol_max_mult) |vm| {
                        for (grid.vol_min_days) |vd| {
                            combos[k] = .{
                                .base_contracts = bl,
                                .leverage = lev,
                                .vol_target = vt,
                                .vol_halflife = vh,
                                .vol_max_mult = vm,
                                .vol_min_days = vd,
                            };
                            k += 1;
                        }
                    }
                }
            }
        }
    }
    if (n == 0) return combos;

    var shared = OrbShared{
        .gpa = gpa,
        .dataset = dataset,
        .initial_balance = grid.initial_balance,
        .sizing_mode = grid.sizing_mode,
        .combos = combos,
        .progress = progress,
    };

    const cpu = std.Thread.getCpuCount() catch 1;
    const n_threads = @min(@max(cpu, 1), n);

    const threads = try gpa.alloc(std.Thread, n_threads);
    defer gpa.free(threads);

    const worker = Worker(S).run;
    var spawned: usize = 0;
    for (0..n_threads) |ti| {
        threads[ti] = std.Thread.spawn(.{}, worker, .{&shared}) catch break;
        spawned += 1;
    }
    if (spawned == 0) worker(&shared);
    for (threads[0..spawned]) |t| t.join();

    if (shared.failed.load(.acquire)) return shared.worker_err;
    if (engine.cancelled.load(.acquire)) return error.Cancelled;
    return combos;
}

// ── Reporting ──────────────────────────────────────────────────────────────────

pub fn printReportOrb(io: Io, gpa: std.mem.Allocator, combos: []const OrbCombo, mode: strategy.sizing.Mode) !void {
    var buf: [8192]u8 = undefined;
    var writer = Io.File.stdout().writer(io, &buf);
    const w = &writer.interface;

    if (combos.len == 0) {
        try w.print("\n  No combinations evaluated.\n\n", .{});
        try w.flush();
        return;
    }

    const work = try gpa.alloc(OrbCombo, combos.len);
    defer gpa.free(work);
    @memcpy(work, combos);

    try computeScores(gpa, work);

    try w.print("\n  Tuned {d} combination(s).\n", .{combos.len});

    std.mem.sort(OrbCombo, work, {}, byGrowthDesc);
    try printList(w, "Top 10 — Best Growth", work, mode);

    std.mem.sort(OrbCombo, work, {}, byDrawdownAsc);
    try printList(w, "Top 10 — Smallest Drawdown", work, mode);

    std.mem.sort(OrbCombo, work, {}, byScoreDesc);
    try printList(w, "Top 10 — Best of Two (balanced growth + drawdown)", work, mode);

    try w.print("\n", .{});
    try w.flush();
}

fn computeScores(gpa: std.mem.Allocator, work: []OrbCombo) !void {
    const n = work.len;
    if (n <= 1) {
        if (n == 1) work[0].score = 1;
        return;
    }

    const gi = try gpa.alloc(usize, n);
    defer gpa.free(gi);
    const di = try gpa.alloc(usize, n);
    defer gpa.free(di);
    const grank = try gpa.alloc(usize, n);
    defer gpa.free(grank);

    for (0..n) |i| {
        gi[i] = i;
        di[i] = i;
    }
    std.mem.sort(usize, gi, @as([]const OrbCombo, work), growthIdxDesc);
    std.mem.sort(usize, di, @as([]const OrbCombo, work), drawdownIdxAsc);

    for (gi, 0..) |ci, r| grank[ci] = r;

    const denom: f64 = @floatFromInt(2 * (n - 1));
    for (di, 0..) |ci, r| {
        const rank_sum: f64 = @floatFromInt(grank[ci] + r);
        work[ci].score = 1.0 - rank_sum / denom;
    }
}

fn growthIdxDesc(ctx: []const OrbCombo, a: usize, b: usize) bool {
    return ctx[a].growth > ctx[b].growth;
}
fn drawdownIdxAsc(ctx: []const OrbCombo, a: usize, b: usize) bool {
    return ctx[a].drawdown < ctx[b].drawdown;
}
fn byGrowthDesc(_: void, a: OrbCombo, b: OrbCombo) bool {
    return a.growth > b.growth;
}
fn byDrawdownAsc(_: void, a: OrbCombo, b: OrbCombo) bool {
    return a.drawdown < b.drawdown;
}
fn byScoreDesc(_: void, a: OrbCombo, b: OrbCombo) bool {
    return a.score > b.score;
}

fn printList(w: *Io.Writer, title: []const u8, sorted: []const OrbCombo, mode: strategy.sizing.Mode) !void {
    try w.print("\n  {s}\n", .{title});
    const n = @min(sorted.len, 10);
    if (mode == .vol_target) {
        try w.print("    {s:>8}  {s:>7}  {s:>6}  {s:>7}  {s:>5}  {s:>6}  {s:>5}  {s:>5}  {s:>5}\n", .{
            "Growth", "MaxDD", "Score", "baseCon", "Lev", "target", "half", "maxM", "minD",
        });
        for (sorted[0..n]) |c| {
            try w.print("    {d:>7.2}% {d:>6.2}%  {d:>6.3}  {d:>7.2}  {d:>5.2}  {d:>6.3}  {d:>5.1}  {d:>5.2}  {d:>5}\n", .{
                c.growth, c.drawdown, c.score, c.base_contracts, c.leverage,
                c.vol_target, c.vol_halflife, c.vol_max_mult, c.vol_min_days,
            });
        }
    } else {
        try w.print("    {s:>8}  {s:>7}  {s:>6}  {s:>7}  {s:>5}\n", .{
            "Growth", "MaxDD", "Score", "baseCon", "Lev",
        });
        for (sorted[0..n]) |c| {
            try w.print("    {d:>7.2}% {d:>6.2}%  {d:>6.3}  {d:>7.2}  {d:>5.2}\n", .{
                c.growth, c.drawdown, c.score, c.base_contracts, c.leverage,
            });
        }
    }
}
