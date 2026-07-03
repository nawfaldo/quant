const std = @import("std");
const score = @import("tune_score.zig");

// ── Tune reporting / serialization ────────────────────────────────────────────
// Everything that turns a finished grid of combos into something a human or a
// client can consume: ranked JSON, a summary, CSV, a Markdown report, and a
// heatmap surface. This module owns NO backtest math — it only reads the metric
// fields the worker already filled on each Combo (which come from the exact same
// computeReport() used by /api/run). Engine, strategies, dataset, and worker
// pool are untouched.

// One swept combination plus its realized metrics. Shared with bt_tune.zig
// (which aliases this as its `Combo`), so the worker writes straight into it.
pub const Combo = struct {
    // Swept parameters
    base_lot: f64,
    leverage: f64,
    vol_target: f64,
    vol_halflife: f64,
    vol_max_mult: f64,
    vol_min_days: u32,

    // Realized metrics (filled by the worker via bt_run.computeReport / Result).
    growth: f64 = 0, // net return %, kept under this name for back-compat
    drawdown: f64 = 0, // max drawdown %, kept under this name for back-compat
    final_balance: f64 = 0,
    return_pct: f64 = 0,
    profit_factor: f64 = 0,
    sharpe: f64 = 0,
    expectancy: f64 = 0,
    win_rate: f64 = 0,
    avg_drawdown: f64 = 0,
    score: f64 = 0,
};

// Run-level context shared by every combo (printed into CSV rows, the summary,
// and the Markdown header).
pub const Meta = struct {
    strategy: []const u8,
    symbol: []const u8,
    initial_balance: f64,
    total: usize,
    elapsed_ms: i64,
};

// ── Sorting ───────────────────────────────────────────────────────────────────

pub const SortKey = enum { score, growth, drawdown, profit_factor, sharpe, expectancy };

// Map a ?sort= query value to a key. Defaults to .score on anything unknown.
pub fn parseSort(s: []const u8) SortKey {
    if (std.mem.eql(u8, s, "growth")) return .growth;
    if (std.mem.eql(u8, s, "drawdown")) return .drawdown;
    if (std.mem.eql(u8, s, "profitFactor") or std.mem.eql(u8, s, "profit_factor")) return .profit_factor;
    if (std.mem.eql(u8, s, "sharpe")) return .sharpe;
    if (std.mem.eql(u8, s, "expectancy")) return .expectancy;
    return .score;
}

// Sort best-first: descending for "higher is better" metrics, ascending for
// drawdown (lower is better). Sorts in place.
pub fn sortRanked(combos: []Combo, key: SortKey) void {
    const Ctx = struct {
        key: SortKey,
        fn better(self: @This(), a: Combo, b: Combo) bool {
            return switch (self.key) {
                .score => a.score > b.score,
                .growth => a.growth > b.growth,
                .profit_factor => a.profit_factor > b.profit_factor,
                .sharpe => a.sharpe > b.sharpe,
                .expectancy => a.expectancy > b.expectancy,
                .drawdown => a.drawdown < b.drawdown,
            };
        }
    };
    std.mem.sort(Combo, combos, Ctx{ .key = key }, Ctx.better);
}

// ── JSON building blocks ──────────────────────────────────────────────────────

fn fin(x: f64) f64 {
    return if (std.math.isFinite(x)) x else 0;
}

// Emit one combo as a JSON object: swept params + every metric. Used by the
// ranked list and the full results payload.
fn appendComboObj(out: *std.ArrayList(u8), a: std.mem.Allocator, c: Combo) !void {
    const s = try std.fmt.allocPrint(a,
        \\{{"baseLot":{d:.4},"leverage":{d:.4},"volTarget":{d:.4},"volHalflife":{d:.4},"volMaxMult":{d:.4},"volMinDays":{d},"finalBalance":{d:.2},"returnPct":{d:.4},"profitFactor":{d:.4},"sharpe":{d:.4},"expectancy":{d:.4},"winRate":{d:.4},"maxDrawdown":{d:.4},"avgDrawdown":{d:.4},"score":{d:.4}}}
    , .{
        fin(c.base_lot),     fin(c.leverage),      fin(c.vol_target),
        fin(c.vol_halflife), fin(c.vol_max_mult),  c.vol_min_days,
        fin(c.final_balance), fin(c.return_pct),   fin(c.profit_factor),
        fin(c.sharpe),       fin(c.expectancy),    fin(c.win_rate),
        fin(c.drawdown),     fin(c.avg_drawdown),  fin(c.score),
    });
    defer a.free(s);
    try out.appendSlice(a, s);
}

// ── Ranked list — GET /api/tune/results?sort=&limit= ─────────────────────────
// Sorts a COPY so the shared combo order is never disturbed, then emits the top
// `limit` rows. Returns an owned slice.
pub fn buildRanked(a: std.mem.Allocator, combos: []const Combo, key: SortKey, limit: usize) ![]const u8 {
    const copy = try a.dupe(Combo, combos);
    defer a.free(copy);
    sortRanked(copy, key);

    const n = @min(limit, copy.len);
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);

    const head = try std.fmt.allocPrint(a, "{{\"sort\":\"{s}\",\"limit\":{d},\"total\":{d},\"results\":[", .{ @tagName(key), limit, combos.len });
    defer a.free(head);
    try out.appendSlice(a, head);

    for (copy[0..n], 0..) |c, i| {
        if (i != 0) try out.appendSlice(a, ",");
        try appendComboObj(&out, a, c);
    }
    try out.appendSlice(a, "]}");
    return out.toOwnedSlice(a);
}

// ── Summary ───────────────────────────────────────────────────────────────────
// bestByScore / bestGrowth / bestSharpe / bestProfitFactor / lowestDrawdown +
// averageMetrics across the whole grid. Returns an owned JSON object.
pub fn buildSummary(a: std.mem.Allocator, combos: []const Combo) ![]const u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendSlice(a, "{");

    if (combos.len == 0) {
        try out.appendSlice(a, "\"empty\":true}");
        return out.toOwnedSlice(a);
    }

    try appendBest(&out, a, combos, "bestByScore", .score);
    try out.appendSlice(a, ",");
    try appendBest(&out, a, combos, "bestGrowth", .growth);
    try out.appendSlice(a, ",");
    try appendBest(&out, a, combos, "bestSharpe", .sharpe);
    try out.appendSlice(a, ",");
    try appendBest(&out, a, combos, "bestProfitFactor", .profit_factor);
    try out.appendSlice(a, ",");
    try appendBest(&out, a, combos, "lowestDrawdown", .drawdown);

    // averageMetrics
    var sum_ret: f64 = 0;
    var sum_pf: f64 = 0;
    var sum_sh: f64 = 0;
    var sum_exp: f64 = 0;
    var sum_wr: f64 = 0;
    var sum_dd: f64 = 0;
    var sum_add: f64 = 0;
    var sum_sc: f64 = 0;
    for (combos) |c| {
        sum_ret += fin(c.return_pct);
        sum_pf += fin(c.profit_factor);
        sum_sh += fin(c.sharpe);
        sum_exp += fin(c.expectancy);
        sum_wr += fin(c.win_rate);
        sum_dd += fin(c.drawdown);
        sum_add += fin(c.avg_drawdown);
        sum_sc += fin(c.score);
    }
    const n: f64 = @floatFromInt(combos.len);
    const avg = try std.fmt.allocPrint(a,
        \\,"averageMetrics":{{"returnPct":{d:.4},"profitFactor":{d:.4},"sharpe":{d:.4},"expectancy":{d:.4},"winRate":{d:.4},"maxDrawdown":{d:.4},"avgDrawdown":{d:.4},"score":{d:.4}}}
    , .{ sum_ret / n, sum_pf / n, sum_sh / n, sum_exp / n, sum_wr / n, sum_dd / n, sum_add / n, sum_sc / n });
    defer a.free(avg);
    try out.appendSlice(a, avg);

    try out.appendSlice(a, "}");
    return out.toOwnedSlice(a);
}

fn bestIndex(combos: []const Combo, key: SortKey) usize {
    var best: usize = 0;
    for (combos, 0..) |c, i| {
        const b = combos[best];
        const win = switch (key) {
            .score => c.score > b.score,
            .growth => c.growth > b.growth,
            .profit_factor => c.profit_factor > b.profit_factor,
            .sharpe => c.sharpe > b.sharpe,
            .expectancy => c.expectancy > b.expectancy,
            .drawdown => c.drawdown < b.drawdown,
        };
        if (win) best = i;
    }
    return best;
}

fn appendBest(out: *std.ArrayList(u8), a: std.mem.Allocator, combos: []const Combo, label: []const u8, key: SortKey) !void {
    const kh = try std.fmt.allocPrint(a, "\"{s}\":", .{label});
    defer a.free(kh);
    try out.appendSlice(a, kh);
    try appendComboObj(out, a, combos[bestIndex(combos, key)]);
}

// ── Results payload — GET /api/tune/results.json[?full=true] ──────────────────
// Summary-only by default (JSON optimization); ?full=true appends every combo.
pub fn buildResultsJson(a: std.mem.Allocator, combos: []const Combo, meta: Meta, full: bool) ![]const u8 {
    const summary = try buildSummary(a, combos);
    defer a.free(summary);

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);

    const head = try std.fmt.allocPrint(a,
        \\{{"strategy":"{s}","symbol":"{s}","initialBalance":{d:.2},"total":{d},"elapsedMs":{d},"summary":
    , .{ meta.strategy, meta.symbol, fin(meta.initial_balance), meta.total, meta.elapsed_ms });
    defer a.free(head);
    try out.appendSlice(a, head);
    try out.appendSlice(a, summary);

    if (full) {
        try out.appendSlice(a, ",\"combos\":[");
        for (combos, 0..) |c, i| {
            if (i != 0) try out.appendSlice(a, ",");
            try appendComboObj(&out, a, c);
        }
        try out.appendSlice(a, "]");
    }

    try out.appendSlice(a, "}");
    return out.toOwnedSlice(a);
}

// ── CSV — GET /api/tune/results.csv ──────────────────────────────────────────
// Every combination, sorted descending by score.
pub fn buildCsv(a: std.mem.Allocator, combos: []const Combo, meta: Meta) ![]const u8 {
    const copy = try a.dupe(Combo, combos);
    defer a.free(copy);
    sortRanked(copy, .score);

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);

    try out.appendSlice(a, "strategy,symbol,baseLot,leverage,volTarget,volHalflife,volMaxMult,volMinDays,initialBalance,finalBalance,returnPct,profitFactor,sharpe,expectancy,winRate,maxDrawdown,avgDrawdown,score\n");

    for (copy) |c| {
        const row = try std.fmt.allocPrint(a, "{s},{s},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d},{d:.2},{d:.2},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4},{d:.4}\n", .{
            meta.strategy,       meta.symbol,         fin(c.base_lot),
            fin(c.leverage),     fin(c.vol_target),   fin(c.vol_halflife),
            fin(c.vol_max_mult), c.vol_min_days,      fin(meta.initial_balance),
            fin(c.final_balance), fin(c.return_pct),  fin(c.profit_factor),
            fin(c.sharpe),       fin(c.expectancy),   fin(c.win_rate),
            fin(c.drawdown),     fin(c.avg_drawdown), fin(c.score),
        });
        defer a.free(row);
        try out.appendSlice(a, row);
    }
    return out.toOwnedSlice(a);
}

// ── Markdown report — GET /api/tune/report.md ────────────────────────────────
pub fn buildMarkdown(a: std.mem.Allocator, combos: []const Combo, meta: Meta) ![]const u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);

    const secs: f64 = @as(f64, @floatFromInt(meta.elapsed_ms)) / 1000.0;
    const header = try std.fmt.allocPrint(a,
        \\# Hyperparameter Search
        \\
        \\- **Strategy:** {s}
        \\- **Symbol:** {s}
        \\- **Total combinations:** {d}
        \\- **Elapsed time:** {d:.1}s
        \\- **Initial balance:** {d:.2}
        \\
    , .{ meta.strategy, meta.symbol, meta.total, secs, fin(meta.initial_balance) });
    defer a.free(header);
    try out.appendSlice(a, header);

    if (combos.len == 0) {
        try out.appendSlice(a, "\n_No combinations._\n");
        return out.toOwnedSlice(a);
    }

    const b = combos[bestIndex(combos, .score)];
    const best = try std.fmt.allocPrint(a,
        \\
        \\## Best configuration
        \\
        \\| parameter | value |
        \\|---|---|
        \\| baseLot | {d:.4} |
        \\| leverage | {d:.4} |
        \\| volTarget | {d:.4} |
        \\| volHalflife | {d:.4} |
        \\| volMaxMult | {d:.4} |
        \\| volMinDays | {d} |
        \\
        \\### Performance
        \\
        \\| metric | value |
        \\|---|---|
        \\| Return | {d:.2}% |
        \\| Sharpe | {d:.2} |
        \\| Profit Factor | {d:.2} |
        \\| Drawdown | {d:.2}% |
        \\| Win Rate | {d:.2}% |
        \\| Score | {d:.2} |
        \\
        \\## Top 20 by score
        \\
        \\| # | baseLot | lev | volTarget | volHalflife | volMaxMult | volMinDays | return% | sharpe | PF | maxDD% | score |
        \\|---|---|---|---|---|---|---|---|---|---|---|---|
        \\
    , .{
        fin(b.base_lot),     fin(b.leverage),     fin(b.vol_target),
        fin(b.vol_halflife), fin(b.vol_max_mult), b.vol_min_days,
        fin(b.return_pct),   fin(b.sharpe),       fin(b.profit_factor),
        fin(b.drawdown),     fin(b.win_rate),     fin(b.score),
    });
    defer a.free(best);
    try out.appendSlice(a, best);

    const copy = try a.dupe(Combo, combos);
    defer a.free(copy);
    sortRanked(copy, .score);
    const n = @min(@as(usize, 20), copy.len);
    for (copy[0..n], 0..) |c, i| {
        const row = try std.fmt.allocPrint(a, "| {d} | {d:.2} | {d:.2} | {d:.3} | {d:.0} | {d:.0} | {d} | {d:.2} | {d:.2} | {d:.2} | {d:.2} | {d:.2} |\n", .{
            i + 1,            fin(c.base_lot),   fin(c.leverage),
            fin(c.vol_target), fin(c.vol_halflife), fin(c.vol_max_mult),
            c.vol_min_days,   fin(c.return_pct), fin(c.sharpe),
            fin(c.profit_factor), fin(c.drawdown), fin(c.score),
        });
        defer a.free(row);
        try out.appendSlice(a, row);
    }

    return out.toOwnedSlice(a);
}

// ── Heatmap — GET /api/tune/heatmap.json ─────────────────────────────────────
// Average score over the (volTarget × volHalflife) plane, collapsing the other
// swept dimensions. Drives a future 2-D sensitivity visualization.
pub fn buildHeatmap(a: std.mem.Allocator, combos: []const Combo) ![]const u8 {
    // Distinct axis values, in first-seen order.
    var xs: std.ArrayList(f64) = .empty;
    defer xs.deinit(a);
    var ys: std.ArrayList(f64) = .empty;
    defer ys.deinit(a);
    for (combos) |c| {
        try addUnique(&xs, a, c.vol_target);
        try addUnique(&ys, a, c.vol_halflife);
    }

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(a);
    try out.appendSlice(a, "{\"xParam\":\"volTarget\",\"yParam\":\"volHalflife\",\"xValues\":[");
    try appendFloatList(&out, a, xs.items);
    try out.appendSlice(a, "],\"yValues\":[");
    try appendFloatList(&out, a, ys.items);
    try out.appendSlice(a, "],\"cells\":[");

    var first = true;
    for (ys.items) |yv| {
        for (xs.items) |xv| {
            var sum: f64 = 0;
            var cnt: usize = 0;
            for (combos) |c| {
                if (c.vol_target == xv and c.vol_halflife == yv) {
                    sum += fin(c.score);
                    cnt += 1;
                }
            }
            const avg = if (cnt > 0) sum / @as(f64, @floatFromInt(cnt)) else 0;
            const cell = try std.fmt.allocPrint(a, "{s}{{\"volTarget\":{d:.4},\"volHalflife\":{d:.4},\"avgScore\":{d:.4},\"count\":{d}}}", .{
                if (first) "" else ",", xv, yv, avg, cnt,
            });
            defer a.free(cell);
            try out.appendSlice(a, cell);
            first = false;
        }
    }
    try out.appendSlice(a, "]}");
    return out.toOwnedSlice(a);
}

fn addUnique(list: *std.ArrayList(f64), a: std.mem.Allocator, v: f64) !void {
    for (list.items) |x| {
        if (x == v) return;
    }
    try list.append(a, v);
}

fn appendFloatList(out: *std.ArrayList(u8), a: std.mem.Allocator, vals: []const f64) !void {
    for (vals, 0..) |v, i| {
        const s = try std.fmt.allocPrint(a, "{s}{d:.4}", .{ if (i == 0) "" else ",", v });
        defer a.free(s);
        try out.appendSlice(a, s);
    }
}
