const std = @import("std");
const http = @import("http.zig");
const engine = @import("bt/engine.zig");
const data = @import("bt/data.zig");
const sizing = @import("bt/sizings/vol_target.zig");

const RthVwap = @import("bt/strategies/rth_vwap.zig").RthVwap;
const ThirtyMinBuy = @import("bt/strategies/30m_buy.zig").ThirtyMinBuy;
const Orb = @import("bt/strategies/5m_orb.zig").Orb;

const alloc = std.heap.page_allocator;

// ── /api/tune ───────────────────────────────────────────────────────────────
// Runs a grid search over position-sizing parameters and returns three ranked
// lists: best growth, min drawdown, and best-of-two (balanced score). This is
// the web equivalent of the CLI's `/tune` command.

const MAX_GRID = 16;

pub const TuneState = enum {
    idle,
    running,
    completed,
    failed,
};

pub var g_status: TuneState = .idle;
pub var g_progress: std.atomic.Value(usize) = .init(0);
pub var g_total: usize = 0;
pub var g_result_json: ?[]const u8 = null;
pub var g_error_msg: ?[]const u8 = null;

const ThreadCtx = struct {
    io: std.Io,
    strategy: []const u8,
    symbol: []const u8,
    balance: f64,
    sizing_mode: sizing.Mode,
    base_lots: []const f64,
    vol_targets: []const f64,
    vol_halflifes: []const f64,
    vol_max_mults: []const f64,
    vol_min_days: []const u32,
    fromDate: []const u8,
    toDate: []const u8,
    spread: f64,
    slippage: f64,

    fn deinit(self: *ThreadCtx) void {
        alloc.free(self.strategy);
        alloc.free(self.symbol);
        alloc.free(self.base_lots);
        alloc.free(self.vol_targets);
        alloc.free(self.vol_halflifes);
        alloc.free(self.vol_max_mults);
        alloc.free(self.vol_min_days);
        alloc.free(self.fromDate);
        alloc.free(self.toDate);
        alloc.destroy(self);
    }
};

const Combo = struct {
    base_lot: f64,
    vol_target: f64,
    vol_halflife: f64,
    vol_max_mult: f64,
    vol_min_days: u32,
    growth: f64 = 0,
    drawdown: f64 = 0,
    score: f64 = 0,
};

pub fn handle(req: *http.Ctx) !void {
    const body = req.body orelse {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"no body\"}");
        return;
    };
    std.debug.print("TUNE REQUEST BODY: {s}\n", .{body});

    const strategy = jsonStr(body, "strategy");
    const prefix = symbolPrefix(jsonStr(body, "symbol")) orelse {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"unknown symbol\"}");
        return;
    };

    const balance = jsonNum(body, "initialBalance") orelse 0;

    // Parse comma-separated base lot list.
    const base_lot_str = jsonStr(body, "baseLot");
    var base_lots: [MAX_GRID]f64 = undefined;
    const base_lots_n = parseFloatList(base_lot_str, &base_lots) orelse {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"invalid baseLot list\"}");
        return;
    };

    // Sizing mode.
    const sizing_str = jsonStr(body, "sizing");
    const sizing_mode: sizing.Mode = if (std.mem.eql(u8, sizing_str, "Vol Target")) .vol_target else .none;

    // Vol params — comma-separated lists for grid sweep.
    var vol_targets: [MAX_GRID]f64 = undefined;
    var vol_halflifes: [MAX_GRID]f64 = undefined;
    var vol_max_mults: [MAX_GRID]f64 = undefined;
    var vol_min_days: [MAX_GRID]u32 = undefined;
    var vol_targets_n: usize = 1;
    var vol_halflifes_n: usize = 1;
    var vol_max_mults_n: usize = 1;
    var vol_min_days_n: usize = 1;

    if (sizing_mode == .vol_target) {
        vol_targets_n = parseFloatListOrDefault(jsonStr(body, "volTarget"), &vol_targets, 0.20) orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid volTarget list\"}");
            return;
        };
        vol_halflifes_n = parseFloatListOrDefault(jsonStr(body, "volHalflife"), &vol_halflifes, 20.0) orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid volHalflife list\"}");
            return;
        };
        vol_max_mults_n = parseFloatListOrDefault(jsonStr(body, "volMaxMult"), &vol_max_mults, 3.0) orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid volMaxMult list\"}");
            return;
        };
        vol_min_days_n = parseUintListOrDefault(jsonStr(body, "volMinDays"), &vol_min_days, 30) orelse {
            req.setStatusNumeric(400);
            try req.sendJson("{\"error\":\"invalid volMinDays list\"}");
            return;
        };
    } else {
        vol_targets[0] = 0.20;
        vol_halflifes[0] = 20.0;
        vol_max_mults[0] = 3.0;
        vol_min_days[0] = 30;
    }

    // Date range.
    const from_raw = jsonStr(body, "fromDate");
    const to_raw = jsonStr(body, "toDate");
    const spread_val = jsonNum(body, "spread") orelse engine.spread;
    const slippage_val = jsonNum(body, "slippage") orelse engine.slippage;

    // Build the cartesian product of all swept dimensions.
    const total = base_lots_n * vol_targets_n * vol_halflifes_n * vol_max_mults_n * vol_min_days_n;
    if (total == 0 or total > 10000) {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"invalid grid size\"}");
        return;
    }

    const ctx = try alloc.create(ThreadCtx);
    ctx.* = .{
        .io = req.io,
        .strategy = try alloc.dupe(u8, strategy),
        .symbol = try alloc.dupe(u8, prefix),
        .balance = balance,
        .sizing_mode = sizing_mode,
        .base_lots = try alloc.dupe(f64, base_lots[0..base_lots_n]),
        .vol_targets = try alloc.dupe(f64, vol_targets[0..vol_targets_n]),
        .vol_halflifes = try alloc.dupe(f64, vol_halflifes[0..vol_halflifes_n]),
        .vol_max_mults = try alloc.dupe(f64, vol_max_mults[0..vol_max_mults_n]),
        .vol_min_days = try alloc.dupe(u32, vol_min_days[0..vol_min_days_n]),
        .fromDate = try alloc.dupe(u8, from_raw),
        .toDate = try alloc.dupe(u8, to_raw),
        .spread = spread_val,
        .slippage = slippage_val,
    };

    // Reset globals
    if (g_result_json) |rj| {
        alloc.free(rj);
        g_result_json = null;
    }
    if (g_error_msg) |em| {
        alloc.free(em);
        g_error_msg = null;
    }
    g_progress.store(0, .release);
    g_total = total;
    g_status = .running;

    const t = try std.Thread.spawn(.{}, runTuneAsync, .{ctx});
    t.detach();

    try req.sendJson("{\"ok\":true}");
}

fn setAsyncError(err: anyerror) void {
    var buf: [128]u8 = undefined;
    const msg = std.fmt.bufPrint(&buf, "Tune failed: {s}", .{@errorName(err)}) catch "Tune failed";
    g_error_msg = alloc.dupe(u8, msg) catch null;
    g_status = .failed;
}

fn runTuneAsync(ctx: *ThreadCtx) void {
    const total = ctx.base_lots.len * ctx.vol_targets.len * ctx.vol_halflifes.len * ctx.vol_max_mults.len * ctx.vol_min_days.len;
    const combos = alloc.alloc(Combo, total) catch {
        g_error_msg = alloc.dupe(u8, "Alloc failed") catch null;
        g_status = .failed;
        ctx.deinit();
        return;
    };
    defer alloc.free(combos);

    var k: usize = 0;
    for (ctx.base_lots) |bl| {
        for (ctx.vol_targets) |vt| {
            for (ctx.vol_halflifes) |vh| {
                for (ctx.vol_max_mults) |vm| {
                    for (ctx.vol_min_days) |vd| {
                        combos[k] = .{
                            .base_lot = bl,
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

    const cfg = engine.Config{
        .symbol = ctx.symbol,
        .instrument = .forex,
        .from = if (ctx.fromDate.len > 0) ctx.fromDate else null,
        .to = if (ctx.toDate.len > 0) ctx.toDate else null,
        .spread = ctx.spread,
        .slippage = ctx.slippage,
        .warmup_days = 90,
    };

    if (std.mem.eql(u8, ctx.strategy, "RTH VWAP")) {
        runGrid(RthVwap, ctx.io, combos, ctx.balance, ctx.sizing_mode, cfg) catch |err| {
            setAsyncError(err);
            ctx.deinit();
            return;
        };
    } else if (std.mem.eql(u8, ctx.strategy, "30m Buy")) {
        runGrid(ThirtyMinBuy, ctx.io, combos, ctx.balance, ctx.sizing_mode, cfg) catch |err| {
            setAsyncError(err);
            ctx.deinit();
            return;
        };
    } else if (std.mem.eql(u8, ctx.strategy, "5m ORB")) {
        runGrid(Orb, ctx.io, combos, ctx.balance, ctx.sizing_mode, cfg) catch |err| {
            setAsyncError(err);
            ctx.deinit();
            return;
        };
    } else {
        g_error_msg = alloc.dupe(u8, "Unknown strategy") catch null;
        g_status = .failed;
        ctx.deinit();
        return;
    }

    computeScores(combos);

    const work = alloc.alloc(Combo, total) catch {
        g_error_msg = alloc.dupe(u8, "Alloc failed") catch null;
        g_status = .failed;
        ctx.deinit();
        return;
    };
    defer alloc.free(work);
    @memcpy(work, combos);

    const json = buildTuneJson(work, ctx.sizing_mode) catch {
        g_error_msg = alloc.dupe(u8, "JSON build failed") catch null;
        g_status = .failed;
        ctx.deinit();
        return;
    };

    g_result_json = json;
    g_status = .completed;
    ctx.deinit();
}

const Shared = struct {
    gpa: std.mem.Allocator,
    dataset: data.Dataset,
    balance: f64,
    sizing_mode: sizing.Mode,
    cfg: engine.Config,
    combos: []Combo,
    next: std.atomic.Value(usize) = .init(0),
    failed: std.atomic.Value(bool) = .init(false),
    worker_err: anyerror = error.Unexpected,
};

fn Worker(comptime S: type) type {
    return struct {
        fn run(sh: *Shared) void {
            while (true) {
                const i = sh.next.fetchAdd(1, .monotonic);
                if (i >= sh.combos.len) break;
                if (sh.failed.load(.acquire)) break;

                const c = &sh.combos[i];
                var strat = S{
                    .initial_balance = sh.balance,
                    .contracts = c.base_lot,
                    .leverage = 1.0,
                    .sizing_mode = sh.sizing_mode,
                    .vol = .{
                        .target = c.vol_target,
                        .halflife = c.vol_halflife,
                        .max_mult = c.vol_max_mult,
                        .min_days = c.vol_min_days,
                    },
                };
                const result = engine.backtestOnCfg(sh.gpa, &strat, sh.dataset, sh.cfg) catch |err| {
                    sh.worker_err = err;
                    sh.failed.store(true, .release);
                    break;
                };
                defer result.deinit(sh.gpa);

                // Compute growth and max drawdown.
                var final_balance = sh.balance;
                for (result.trades) |t| {
                    final_balance += t.pnl;
                }
                c.growth = if (sh.balance > 0) (final_balance - sh.balance) / sh.balance * 100.0 else 0.0;
                c.drawdown = result.max_drawdown;

                _ = g_progress.fetchAdd(1, .release);
            }
        }
    };
}

fn runGrid(comptime S: type, io: std.Io, combos: []Combo, balance: f64, sizing_mode: sizing.Mode, cfg: engine.Config) !void {
    // Fetch the dataset once and reuse for all combos.
    const cols = engine.columnsFor(S);
    var tbuf: [40]u8 = undefined;
    const table = std.fmt.bufPrint(&tbuf, "{s}_{s}", .{ cfg.symbol, S.timeframe }) catch return error.TableName;
    const dataset = try engine.fetchDatasetCfg(io, alloc, cols, table, cfg);
    defer dataset.deinit();

    var shared = Shared{
        .gpa = alloc,
        .dataset = dataset,
        .balance = balance,
        .sizing_mode = sizing_mode,
        .cfg = cfg,
        .combos = combos,
    };

    const cpu = std.Thread.getCpuCount() catch 1;
    const n_threads = @min(@max(cpu, 1), combos.len);

    const threads = try alloc.alloc(std.Thread, n_threads);
    defer alloc.free(threads);

    const worker = Worker(S).run;
    var spawned: usize = 0;
    for (0..n_threads) |ti| {
        threads[ti] = std.Thread.spawn(.{}, worker, .{&shared}) catch break;
        spawned += 1;
    }
    if (spawned == 0) worker(&shared);
    for (threads[0..spawned]) |t| t.join();

    if (shared.failed.load(.acquire)) return shared.worker_err;
}

fn computeScores(combos: []Combo) void {
    const n = combos.len;
    if (n <= 1) {
        if (n == 1) combos[0].score = 1;
        return;
    }

    // Build index arrays for sorting.
    const gi = alloc.alloc(usize, n) catch return;
    defer alloc.free(gi);
    const di = alloc.alloc(usize, n) catch return;
    defer alloc.free(di);
    const grank = alloc.alloc(usize, n) catch return;
    defer alloc.free(grank);

    for (0..n) |i| {
        gi[i] = i;
        di[i] = i;
    }

    // Sort growth indices descending.
    const GrowthCtx = struct {
        combos: []const Combo,
        fn cmp(ctx: @This(), a: usize, b: usize) bool {
            return ctx.combos[a].growth > ctx.combos[b].growth;
        }
    };
    std.mem.sort(usize, gi, GrowthCtx{ .combos = combos }, GrowthCtx.cmp);

    // Sort drawdown indices ascending.
    const DdCtx = struct {
        combos: []const Combo,
        fn cmp(ctx: @This(), a: usize, b: usize) bool {
            return ctx.combos[a].drawdown < ctx.combos[b].drawdown;
        }
    };
    std.mem.sort(usize, di, DdCtx{ .combos = combos }, DdCtx.cmp);

    // Growth rank for each combo.
    for (gi, 0..) |ci, r| grank[ci] = r;

    // Composite score: 1.0 = best in both, 0.0 = worst in both.
    const denom: f64 = @floatFromInt(2 * (n - 1));
    for (di, 0..) |ci, r| {
        const rank_sum: f64 = @floatFromInt(grank[ci] + r);
        combos[ci].score = 1.0 - rank_sum / denom;
    }
}

fn buildTuneJson(work: []Combo, mode: sizing.Mode) ![]const u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(alloc);

    try out.appendSlice(alloc, "{\"totalCombos\":");
    var nbuf: [32]u8 = undefined;
    const ns = try std.fmt.bufPrint(&nbuf, "{d}", .{work.len});
    try out.appendSlice(alloc, ns);

    // Sort by growth desc → bestGrowth.
    std.mem.sort(Combo, work, {}, byGrowthDesc);
    try appendComboList(&out, "bestGrowth", work, mode);

    // Sort by drawdown asc → minDrawdown.
    std.mem.sort(Combo, work, {}, byDrawdownAsc);
    try appendComboList(&out, "minDrawdown", work, mode);

    // Sort by score desc → bestOfTwo.
    std.mem.sort(Combo, work, {}, byScoreDesc);
    try appendComboList(&out, "bestOfTwo", work, mode);

    try out.appendSlice(alloc, "}");
    return out.toOwnedSlice(alloc);
}

fn appendComboList(out: *std.ArrayList(u8), key: []const u8, sorted: []const Combo, mode: sizing.Mode) !void {
    var kbuf: [64]u8 = undefined;
    const kh = try std.fmt.bufPrint(&kbuf, ",\"{s}\":[", .{key});
    try out.appendSlice(alloc, kh);

    const n = @min(sorted.len, 10);
    var buf: [512]u8 = undefined;
    for (sorted[0..n], 0..) |c, i| {
        const comma: []const u8 = if (i == 0) "" else ",";
        if (mode == .vol_target) {
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4},\"volTarget\":{d:.4},\"volHalflife\":{d:.4},\"volMaxMult\":{d:.4},\"volMinDays\":{d}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
                fin(c.vol_target),
                fin(c.vol_halflife),
                fin(c.vol_max_mult),
                c.vol_min_days,
            });
            try out.appendSlice(alloc, s);
        } else {
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
            });
            try out.appendSlice(alloc, s);
        }
    }
    try out.appendSlice(alloc, "]");
}

fn byGrowthDesc(_: void, a: Combo, b: Combo) bool {
    return a.growth > b.growth;
}
fn byDrawdownAsc(_: void, a: Combo, b: Combo) bool {
    return a.drawdown < b.drawdown;
}
fn byScoreDesc(_: void, a: Combo, b: Combo) bool {
    return a.score > b.score;
}

fn fail(req: *http.Ctx, err: anyerror) !void {
    std.debug.print("tune error: {}\n", .{err});
    req.setStatusNumeric(503);
    try req.sendJson("{\"error\":\"tune failed\"}");
}

fn fin(x: f64) f64 {
    return if (std.math.isFinite(x)) x else 0;
}

// ── JSON helpers (mirrors bt_run.zig) ────────────────────────────────────────

fn jsonStr(body: []const u8, key: []const u8) []const u8 {
    const needle = std.fmt.allocPrint(alloc, "\"{s}\":", .{key}) catch return "";
    defer alloc.free(needle);
    const kpos = std.mem.indexOf(u8, body, needle) orelse return "";
    var p = kpos + needle.len;
    while (p < body.len and (body[p] == ' ' or body[p] == '\t')) : (p += 1) {}
    if (p >= body.len or body[p] != '"') return "";
    p += 1;
    const end = std.mem.indexOfScalarPos(u8, body, p, '"') orelse return "";
    return body[p..end];
}

fn jsonNum(body: []const u8, key: []const u8) ?f64 {
    const needle = std.fmt.allocPrint(alloc, "\"{s}\":", .{key}) catch return null;
    defer alloc.free(needle);
    const kpos = std.mem.indexOf(u8, body, needle) orelse return null;
    var p = kpos + needle.len;
    while (p < body.len and (body[p] == ' ' or body[p] == '\t')) : (p += 1) {}
    if (p < body.len and body[p] == '"') p += 1;
    const start = p;
    while (p < body.len) : (p += 1) {
        const ch = body[p];
        const numeric = (ch >= '0' and ch <= '9') or ch == '.' or ch == '-' or ch == '+' or ch == 'e' or ch == 'E';
        if (!numeric) break;
    }
    if (p == start) return null;
    return std.fmt.parseFloat(f64, body[start..p]) catch null;
}

fn symbolPrefix(label: []const u8) ?[]const u8 {
    if (std.mem.eql(u8, label, "NQ") or std.mem.eql(u8, label, "nq")) return "nq";
    if (std.mem.eql(u8, label, "GBPUSD") or std.mem.eql(u8, label, "gbpusd")) return "gbpusd";
    if (std.mem.eql(u8, label, "EURUSD") or std.mem.eql(u8, label, "eurusd")) return "eurusd";
    return null;
}

fn isIsoDate(s: []const u8) bool {
    if (s.len != 10) return false;
    for (s, 0..) |ch, i| {
        if (i == 4 or i == 7) {
            if (ch != '-') return false;
        } else if (ch < '0' or ch > '9') return false;
    }
    return true;
}

// ── Comma-separated list parsers (mirrors CLI tune flow) ─────────────────────

fn parseFloatList(s: []const u8, dst: []f64) ?usize {
    const t = std.mem.trim(u8, s, " ");
    if (t.len == 0) return null;
    var n: usize = 0;
    var it = std.mem.tokenizeAny(u8, t, ", ");
    while (it.next()) |tok| {
        if (n >= dst.len) break;
        dst[n] = std.fmt.parseFloat(f64, tok) catch return null;
        n += 1;
    }
    if (n == 0) return null;
    return n;
}

fn parseFloatListOrDefault(s: []const u8, dst: []f64, def: f64) ?usize {
    const t = std.mem.trim(u8, s, " ");
    if (t.len == 0) {
        dst[0] = def;
        return 1;
    }
    return parseFloatList(t, dst);
}

fn parseUintListOrDefault(s: []const u8, dst: []u32, def: u32) ?usize {
    const t = std.mem.trim(u8, s, " ");
    if (t.len == 0) {
        dst[0] = def;
        return 1;
    }
    var n: usize = 0;
    var it = std.mem.tokenizeAny(u8, t, ", ");
    while (it.next()) |tok| {
        if (n >= dst.len) break;
        const v = std.fmt.parseFloat(f64, tok) catch return null;
        if (v < 0) return null;
        dst[n] = @intFromFloat(v);
        n += 1;
    }
    if (n == 0) return null;
    return n;
}

pub fn handleStatus(req: *http.Ctx) !void {
    switch (g_status) {
        .idle, .running => {
            var buf: [128]u8 = undefined;
            const json = try std.fmt.bufPrint(&buf, "{{\"status\":\"running\",\"progress\":{d},\"total\":{d}}}", .{
                g_progress.load(.acquire),
                g_total,
            });
            try req.setContentType(.JSON);
            try req.sendBody(json);
        },
        .completed => {
            if (g_result_json) |json| {
                const resp = try std.fmt.allocPrint(alloc, "{{\"status\":\"completed\",\"result\":{s}}}", .{json});
                defer alloc.free(resp);
                try req.setContentType(.JSON);
                try req.sendBody(resp);
            } else {
                try req.sendJson("{\"status\":\"failed\",\"error\":\"Missing result\"}");
            }
        },
        .failed => {
            const err_msg = g_error_msg orelse "Unknown error";
            const resp = try std.fmt.allocPrint(alloc, "{{\"status\":\"failed\",\"error\":\"{s}\"}}", .{err_msg});
            defer alloc.free(resp);
            try req.setContentType(.JSON);
            try req.sendBody(resp);
        },
    }
}
