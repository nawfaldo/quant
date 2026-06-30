const std = @import("std");
const http = @import("http.zig");
const engine = @import("bt/engine.zig");
const data = @import("bt/data.zig");
const sizing = @import("bt/sizings/vol_target.zig");
const bt_run = @import("bt_run.zig");
const report = @import("tune_report.zig");
const scoring = @import("tune_score.zig");

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

// ── Experiment-platform state (progress timing + post-run artifacts) ─────────
// All written once at completion (or read live for progress); nothing here feeds
// the engine. Held until the next /api/tune overwrites them.
pub var g_start_ms: i64 = 0; // monotonic ms at job start (for elapsed/throughput/ETA)
pub var g_end_ms: i64 = 0; // monotonic ms at completion
pub var g_io: ?std.Io = null; // io handle, used to read the monotonic clock on status polls
pub var g_combos: ?[]Combo = null; // full result grid, kept alive for ranking/export endpoints
pub var g_meta: report.Meta = .{ .strategy = "", .symbol = "", .initial_balance = 0, .total = 0, .elapsed_ms = 0 };
pub var g_summary_json: ?[]const u8 = null;
pub var g_csv: ?[]const u8 = null;
pub var g_markdown: ?[]const u8 = null;
pub var g_heatmap_json: ?[]const u8 = null;

fn nowMs(io: std.Io) i64 {
    return std.Io.Clock.awake.now(io).toMilliseconds();
}

// Free any artifacts from a previous run before starting a new one.
fn resetArtifacts() void {
    if (g_combos) |c| {
        alloc.free(c);
        g_combos = null;
    }
    if (g_summary_json) |s| {
        alloc.free(s);
        g_summary_json = null;
    }
    if (g_csv) |s| {
        alloc.free(s);
        g_csv = null;
    }
    if (g_markdown) |s| {
        alloc.free(s);
        g_markdown = null;
    }
    if (g_heatmap_json) |s| {
        alloc.free(s);
        g_heatmap_json = null;
    }
    if (g_meta.strategy.len > 0) alloc.free(g_meta.strategy);
    if (g_meta.symbol.len > 0) alloc.free(g_meta.symbol);
    g_meta = .{ .strategy = "", .symbol = "", .initial_balance = 0, .total = 0, .elapsed_ms = 0 };
}

const ThreadCtx = struct {
    io: std.Io,
    strategy: []const u8,
    symbol: []const u8,
    balance: f64,
    sizing_mode: sizing.Mode,
    base_lots: []const f64,
    leverages: []const f64,
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
        alloc.free(self.leverages);
        alloc.free(self.vol_targets);
        alloc.free(self.vol_halflifes);
        alloc.free(self.vol_max_mults);
        alloc.free(self.vol_min_days);
        alloc.free(self.fromDate);
        alloc.free(self.toDate);
        alloc.destroy(self);
    }
};

// The combo type (swept params + realized metrics) now lives in tune_report.zig
// so the reporting/serialization code and the worker share one definition. The
// worker fills the metric fields; the report module reads them. Engine and
// worker-pool mechanics are unchanged.
const Combo = report.Combo;

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

    // Parse comma-separated leverage list (empty → [1.0], like the CLI default).
    const leverage_str = jsonStr(body, "leverage");
    var leverages: [MAX_GRID]f64 = undefined;
    const leverages_n = parseFloatListOrDefault(leverage_str, &leverages, 1.0) orelse {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"invalid leverage list\"}");
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
    const total = base_lots_n * leverages_n * vol_targets_n * vol_halflifes_n * vol_max_mults_n * vol_min_days_n;
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
        .leverages = try alloc.dupe(f64, leverages[0..leverages_n]),
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
    resetArtifacts();
    g_io = req.io;
    g_start_ms = nowMs(req.io);
    g_end_ms = 0;
    g_meta = .{
        .strategy = alloc.dupe(u8, strategy) catch "",
        .symbol = alloc.dupe(u8, prefix) catch "",
        .initial_balance = balance,
        .total = total,
        .elapsed_ms = 0,
    };
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
    const total = ctx.base_lots.len * ctx.leverages.len * ctx.vol_targets.len * ctx.vol_halflifes.len * ctx.vol_max_mults.len * ctx.vol_min_days.len;
    const combos = alloc.alloc(Combo, total) catch {
        g_error_msg = alloc.dupe(u8, "Alloc failed") catch null;
        g_status = .failed;
        ctx.deinit();
        return;
    };
    defer alloc.free(combos);

    var k: usize = 0;
    for (ctx.base_lots) |bl| {
        for (ctx.leverages) |lev| {
            for (ctx.vol_targets) |vt| {
                for (ctx.vol_halflifes) |vh| {
                    for (ctx.vol_max_mults) |vm| {
                        for (ctx.vol_min_days) |vd| {
                            combos[k] = .{
                                .base_lot = bl,
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

    // Configurable score (see tune_score.zig) — replaces the old growth-only
    // rank composite. Each combo's score is now an explicit formula over its
    // metrics, so every ranking/summary/export agrees on one number.
    applyScores(combos);

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

    // ── Experiment artifacts ──────────────────────────────────────────────────
    // Keep the full grid alive for the ranking/export endpoints, stamp elapsed
    // time, then pre-build the summary/CSV/Markdown/heatmap once. All best-effort:
    // a failure here still yields a completed job with the core result JSON.
    g_end_ms = nowMs(ctx.io);
    g_meta.elapsed_ms = g_end_ms - g_start_ms;

    g_combos = alloc.dupe(Combo, combos) catch null;
    if (g_combos) |gc| {
        g_summary_json = report.buildSummary(alloc, gc) catch null;
        g_csv = report.buildCsv(alloc, gc, g_meta) catch null;
        g_markdown = report.buildMarkdown(alloc, gc, g_meta) catch null;
        g_heatmap_json = report.buildHeatmap(alloc, gc) catch null;
    }

    g_result_json = json;
    g_status = .completed;
    ctx.deinit();
}

// Apply the configurable score to every combo. Isolated from the formula itself
// (tune_score.zig) so the ranking rule can change without touching the grid.
fn applyScores(combos: []Combo) void {
    for (combos) |*c| {
        c.score = scoring.compute(.{
            .sharpe = c.sharpe,
            .profit_factor = c.profit_factor,
            .max_drawdown = c.drawdown,
            .return_pct = c.return_pct,
            .expectancy = c.expectancy,
            .win_rate = c.win_rate,
            .avg_drawdown = c.avg_drawdown,
        });
    }
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
                    .contracts = c.base_lot * c.leverage,
                    .leverage = c.leverage,
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

                // Reporting only: pull the full metric set from this combo's
                // Result using the SAME computeReport() that /api/run uses, so
                // ranking/CSV/summary stay numerically identical to a single run.
                // (growth/drawdown keep their old meaning for back-compat.)
                const rep = bt_run.computeReport(result);
                c.final_balance = rep.final_balance;
                c.return_pct = rep.net_growth;
                c.growth = rep.net_growth;
                c.profit_factor = rep.profit_factor;
                c.sharpe = rep.sharpe;
                c.expectancy = rep.expectancy;
                c.win_rate = rep.win_rate;
                c.drawdown = result.max_drawdown;
                c.avg_drawdown = result.avg_drawdown;

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

    // Full grid (every combo, not just the top 10) → drives the Sensitivity
    // heatmap on the frontend. Order is irrelevant; each combo carries its own
    // swept params, so the client pivots them into an X/Y surface.
    try appendFullGrid(&out, work, mode);

    try out.appendSlice(alloc, "}");
    return out.toOwnedSlice(alloc);
}

// Emit the entire combo array under "grid". Same per-combo shape as
// appendComboList but with no top-N cap — the frontend needs every cell to draw
// the parameter-sensitivity surface.
fn appendFullGrid(out: *std.ArrayList(u8), all: []const Combo, mode: sizing.Mode) !void {
    try out.appendSlice(alloc, ",\"grid\":[");
    var buf: [512]u8 = undefined;
    for (all, 0..) |c, i| {
        const comma: []const u8 = if (i == 0) "" else ",";
        if (mode == .vol_target) {
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4},\"leverage\":{d:.4},\"volTarget\":{d:.4},\"volHalflife\":{d:.4},\"volMaxMult\":{d:.4},\"volMinDays\":{d}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
                fin(c.leverage),
                fin(c.vol_target),
                fin(c.vol_halflife),
                fin(c.vol_max_mult),
                c.vol_min_days,
            });
            try out.appendSlice(alloc, s);
        } else {
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4},\"leverage\":{d:.4}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
                fin(c.leverage),
            });
            try out.appendSlice(alloc, s);
        }
    }
    try out.appendSlice(alloc, "]");
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
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4},\"leverage\":{d:.4},\"volTarget\":{d:.4},\"volHalflife\":{d:.4},\"volMaxMult\":{d:.4},\"volMinDays\":{d}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
                fin(c.leverage),
                fin(c.vol_target),
                fin(c.vol_halflife),
                fin(c.vol_max_mult),
                c.vol_min_days,
            });
            try out.appendSlice(alloc, s);
        } else {
            const s = try std.fmt.bufPrint(&buf, "{s}{{\"growth\":{d:.4},\"drawdown\":{d:.4},\"score\":{d:.4},\"baseLot\":{d:.4},\"leverage\":{d:.4}}}", .{
                comma,
                fin(c.growth),
                fin(c.drawdown),
                fin(c.score),
                fin(c.base_lot),
                fin(c.leverage),
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
            const done = g_progress.load(.acquire);
            const total = g_total;
            const remaining = if (total > done) total - done else 0;
            const pct: f64 = if (total > 0) @as(f64, @floatFromInt(done)) / @as(f64, @floatFromInt(total)) * 100.0 else 0.0;

            // Elapsed + rolling throughput drive a live ETA. Uses the monotonic
            // clock captured at job start.
            const elapsed_ms: i64 = if (g_io) |io| @max(@as(i64, 0), nowMs(io) - g_start_ms) else 0;
            const elapsed_s: f64 = @as(f64, @floatFromInt(elapsed_ms)) / 1000.0;
            const throughput: f64 = if (elapsed_s > 0) @as(f64, @floatFromInt(done)) / elapsed_s else 0.0;
            const eta_ms: i64 = if (throughput > 0)
                @intFromFloat(@as(f64, @floatFromInt(remaining)) / throughput * 1000.0)
            else
                0;

            // progress/total kept for backward compatibility with the existing UI.
            const json = try std.fmt.allocPrint(alloc,
                \\{{"status":"running","progress":{d},"total":{d},"completed":{d},"remaining":{d},"percentage":{d:.2},"elapsed":{d},"estimatedRemaining":{d},"throughput":{d:.2}}}
            , .{ done, total, done, remaining, pct, elapsed_ms, eta_ms, throughput });
            defer alloc.free(json);
            try req.setContentType(.JSON);
            try req.sendBody(json);
        },
        .completed => {
            if (g_result_json) |json| {
                const summary = g_summary_json orelse "null";
                const resp = try std.fmt.allocPrint(alloc,
                    \\{{"status":"completed","elapsed":{d},"total":{d},"summary":{s},"result":{s}}}
                , .{ g_meta.elapsed_ms, g_meta.total, summary, json });
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

// ── Result / export endpoints ─────────────────────────────────────────────────
// All read the artifacts built at completion. If no completed run is in memory
// they return 404 so the client can distinguish "not run yet" from an empty grid.

fn noResults(req: *http.Ctx) !void {
    req.setStatusNumeric(404);
    try req.sendJson("{\"error\":\"no completed tune in memory\"}");
}

// GET /api/tune/results?sort=score|growth|drawdown|profitFactor|sharpe|expectancy&limit=N
pub fn handleResults(req: *http.Ctx) !void {
    const combos = g_combos orelse return noResults(req);
    const query = req.query orelse "";
    const sort_key = report.parseSort(queryParam(query, "sort") orelse "score");
    const limit: usize = blk: {
        const l = queryParam(query, "limit") orelse break :blk 20;
        break :blk std.fmt.parseInt(usize, l, 10) catch 20;
    };
    const body = report.buildRanked(alloc, combos, sort_key, limit) catch return fail(req, error.Build);
    defer alloc.free(body);
    try req.setContentType(.JSON);
    try req.sendBody(body);
}

// GET /api/tune/results.json[?full=true] — summary by default; full grid on demand.
pub fn handleResultsJson(req: *http.Ctx) !void {
    const combos = g_combos orelse return noResults(req);
    const query = req.query orelse "";
    const full = std.mem.eql(u8, queryParam(query, "full") orelse "", "true");
    const body = report.buildResultsJson(alloc, combos, g_meta, full) catch return fail(req, error.Build);
    defer alloc.free(body);
    try req.setContentType(.JSON);
    try req.sendBody(body);
}

// GET /api/tune/results.csv — every combo, sorted by score desc.
pub fn handleResultsCsv(req: *http.Ctx) !void {
    const csv = g_csv orelse return noResults(req);
    req.setHeader("Content-Type", "text/csv") catch {};
    try req.sendBody(csv);
}

// GET /api/tune/report.md — Markdown experiment report.
pub fn handleReportMd(req: *http.Ctx) !void {
    const md = g_markdown orelse return noResults(req);
    req.setHeader("Content-Type", "text/markdown") catch {};
    try req.sendBody(md);
}

// GET /api/tune/heatmap.json — volTarget × volHalflife average-score surface.
pub fn handleHeatmap(req: *http.Ctx) !void {
    const hm = g_heatmap_json orelse return noResults(req);
    try req.setContentType(.JSON);
    try req.sendBody(hm);
}

// Local query-string param reader (mirrors router.zig's helper) so these
// handlers don't depend on router internals.
fn queryParam(query: []const u8, key: []const u8) ?[]const u8 {
    var pos: usize = 0;
    while (pos < query.len) {
        const eq = std.mem.indexOfScalarPos(u8, query, pos, '=') orelse break;
        const amp = std.mem.indexOfScalarPos(u8, query, eq + 1, '&') orelse query.len;
        if (std.mem.eql(u8, query[pos..eq], key)) return query[eq + 1 .. amp];
        pos = amp + 1;
    }
    return null;
}
