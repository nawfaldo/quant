const std = @import("std");
const http = @import("http.zig");
const db = @import("db.zig");
const engine = @import("bt/engine.zig");
const montecarlo = @import("bt/montecarlo.zig");

const alloc = std.heap.page_allocator;

const MC_RENDER_PATHS: usize = 50;
const MC_SIMS: usize = 1000;
const MC_PATH_STEPS: usize = 50;
const MC_SEED: u64 = 1;

// POST /api/combine — merge N saved backtests into a combined portfolio and
// return the full report + trades + Monte Carlo as JSON (same shape as /api/run).
pub fn handle(req: *http.Ctx) !void {
    const body = req.body orelse return badBody(req);
    const p = (try parse(req, body)) orelse return;
    defer freeIds(p.ids);

    const result = compute(p) catch |err| return fail(req, err);
    defer alloc.free(result.trades);

    const json = buildJson(result, p.initial_balance) catch |err| return fail(req, err);
    defer alloc.free(json);

    try req.setContentType(.JSON);
    try req.sendBody(json);
}

// POST /api/combine/save — same as /api/combine but persists to app.db.
pub fn handleSave(req: *http.Ctx) !void {
    const body = req.body orelse return badBody(req);
    const p = (try parse(req, body)) orelse return;
    defer freeIds(p.ids);

    const result = compute(p) catch |err| return fail(req, err);
    defer alloc.free(result.trades);

    const id = persistCombine(result, p.initial_balance) catch |err| return fail(req, err);

    const resp = try std.fmt.allocPrint(alloc, "{{\"id\":{d}}}", .{id});
    defer alloc.free(resp);
    try req.setContentType(.JSON);
    try req.sendBody(resp);
}

// ── Parsed request ────────────────────────────────────────────────────────────

const Parsed = struct {
    ids: []i64,
    initial_balance: f64,
    from_date: ?[]const u8, // points into body slice
    to_date: ?[]const u8,
};

fn parse(req: *http.Ctx, body: []const u8) !?Parsed {
    const initial_balance = jsonNum(body, "initialBalance") orelse {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"missing initialBalance\"}");
        return null;
    };

    // Extract ids array: "ids":[1,2,3]
    const ids = parseIds(body) catch {
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"invalid ids\"}");
        return null;
    };
    if (ids.len < 2) {
        alloc.free(ids);
        req.setStatusNumeric(400);
        try req.sendJson("{\"error\":\"need at least 2 ids\"}");
        return null;
    }

    const from_raw = jsonStr(body, "fromDate");
    const to_raw   = jsonStr(body, "toDate");

    return .{
        .ids = ids,
        .initial_balance = initial_balance,
        .from_date = if (isIsoDate(from_raw)) from_raw else null,
        .to_date   = if (isIsoDate(to_raw))   to_raw   else null,
    };
}

fn freeIds(ids: []i64) void {
    alloc.free(ids);
}

// Parse "ids":[1,2,3] from a JSON body. Returns a heap-allocated slice.
fn parseIds(body: []const u8) ![]i64 {
    const needle = "\"ids\":[";
    const kpos = std.mem.indexOf(u8, body, needle) orelse return error.NotFound;
    var p = kpos + needle.len;
    var list: std.ArrayList(i64) = .empty;
    errdefer list.deinit(alloc);
    while (p < body.len and body[p] != ']') {
        while (p < body.len and (body[p] == ' ' or body[p] == ',')) : (p += 1) {}
        if (p >= body.len or body[p] == ']') break;
        const start = p;
        while (p < body.len and body[p] >= '0' and body[p] <= '9') : (p += 1) {}
        if (p == start) break;
        const id = std.fmt.parseInt(i64, body[start..p], 10) catch continue;
        try list.append(alloc, id);
    }
    return list.toOwnedSlice(alloc);
}

// ── Portfolio computation ────────────────────────────────────────────────────

const CombineResult = struct {
    trades: []engine.Trade, // heap-allocated, caller frees
    initial_balance: f64,
    strategies: []const u8, // comma-joined, stack buffer
    first_ts: [16]u8,
    last_ts: [16]u8,
    max_drawdown: f64,
    max_drawdown_dollars: f64,
    max_drawdown_peak_date: [10]u8,
    max_drawdown_trough_date: [10]u8,
    avg_drawdown: f64,
    avg_drawdown_dollars: f64,
    max_intraday_drawdown: f64,
    max_intraday_drawdown_dollars: f64,
    max_intraday_drawdown_date: [10]u8,
    avg_intraday_drawdown: f64,
    avg_intraday_drawdown_dollars: f64,
};

// Load all sources, scale PnLs to equal-weight allocation, merge, then compute
// realized drawdown at trade-close resolution.
fn compute(p: Parsed) !CombineResult {
    const n = p.ids.len;
    const alloc_per = p.initial_balance / @as(f64, @floatFromInt(n));

    // Load each source.
    const sources = try alloc.alloc(db.CombineSource, n);
    defer {
        for (sources) |s| {
            alloc.free(s.strategy);
            alloc.free(s.trades);
        }
        alloc.free(sources);
    }
    for (p.ids, 0..) |id, i| {
        sources[i] = try db.loadCombineSource(alloc, id);
    }

    // Build merged trade list (scaled + date-filtered).
    var merged: std.ArrayList(engine.Trade) = .empty;
    errdefer merged.deinit(alloc);

    for (sources) |src| {
        const scale = if (src.initial_bal > 0) alloc_per / src.initial_bal else 1.0;
        for (src.trades) |t| {
            // Date filter: keep if exit >= from_date AND entry <= to_date.
            if (p.from_date) |fd| {
                if (std.mem.order(u8, t.exit_ts[0..10], fd) == .lt) continue;
            }
            if (p.to_date) |td| {
                if (std.mem.order(u8, t.entry_ts[0..10], td) == .gt) continue;
            }
            var scaled = t;
            scaled.pnl *= scale;
            scaled.contracts *= scale;
            scaled.entry_price = t.entry_price; // prices don't scale
            scaled.exit_price  = t.exit_price;
            try merged.append(alloc, scaled);
        }
    }

    // Sort by exit_ts (realized equity order).
    std.mem.sort(engine.Trade, merged.items, {}, tradeLess);

    const trades = try merged.toOwnedSlice(alloc);

    // first_ts / last_ts from merged trade stream.
    var first_ts: [16]u8 = [_]u8{'9'} ** 16;
    var last_ts: [16]u8 = [_]u8{'0'} ** 16;
    for (trades) |t| {
        if (std.mem.order(u8, &t.entry_ts, &first_ts) == .lt) first_ts = t.entry_ts;
        if (std.mem.order(u8, &t.exit_ts,  &last_ts)  == .gt) last_ts  = t.exit_ts;
    }
    if (trades.len == 0) {
        first_ts = [_]u8{' '} ** 16;
        last_ts  = [_]u8{' '} ** 16;
    }

    // Realized drawdown (sampled at each trade close).
    const dd = realizedDrawdown(p.initial_balance, trades);

    return .{
        .trades = trades,
        .initial_balance = p.initial_balance,
        .strategies = "COMBINED",
        .first_ts = first_ts,
        .last_ts = last_ts,
        .max_drawdown = dd.max_drawdown,
        .max_drawdown_dollars = dd.max_drawdown_dollars,
        .max_drawdown_peak_date = dd.max_drawdown_peak_date,
        .max_drawdown_trough_date = dd.max_drawdown_trough_date,
        .avg_drawdown = dd.avg_drawdown,
        .avg_drawdown_dollars = dd.avg_drawdown_dollars,
        .max_intraday_drawdown = 0,
        .max_intraday_drawdown_dollars = 0,
        .max_intraday_drawdown_date = [_]u8{' '} ** 10,
        .avg_intraday_drawdown = 0,
        .avg_intraday_drawdown_dollars = 0,
    };
}

fn tradeLess(_: void, a: engine.Trade, b: engine.Trade) bool {
    return std.mem.order(u8, &a.exit_ts, &b.exit_ts) == .lt;
}

// Realized drawdown sampled at each trade close off the cumulative PnL curve.
const Drawdown = struct {
    max_drawdown: f64 = 0,
    avg_drawdown: f64 = 0,
    max_drawdown_peak_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_trough_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_dollars: f64 = 0,
    avg_drawdown_dollars: f64 = 0,
};

fn realizedDrawdown(initial: f64, trades: []const engine.Trade) Drawdown {
    var equity: f64 = initial;
    var peak: f64 = initial;
    var peak_date: [10]u8 = [_]u8{' '} ** 10;
    var max_dd: f64 = 0;
    var max_dd_dollars: f64 = 0;
    var dd_sum: f64 = 0;
    var dd_dollars_sum: f64 = 0;
    var dd_count: usize = 0;
    var max_dd_from: [10]u8 = [_]u8{' '} ** 10;
    var max_dd_to: [10]u8 = [_]u8{' '} ** 10;

    for (trades) |t| {
        equity += t.pnl;
        if (equity > peak) {
            peak = equity;
            @memcpy(peak_date[0..], t.exit_ts[0..10]);
        }
        const dd_dollars = peak - equity;
        const dd = if (peak > 0) dd_dollars / peak * 100.0 else 0.0;
        if (dd > max_dd) {
            max_dd = dd;
            max_dd_dollars = dd_dollars;
            @memcpy(max_dd_from[0..], peak_date[0..]);
            @memcpy(max_dd_to[0..], t.exit_ts[0..10]);
        }
        dd_sum += dd;
        dd_dollars_sum += dd_dollars;
        dd_count += 1;
    }
    const n: f64 = @floatFromInt(dd_count);
    return .{
        .max_drawdown = max_dd,
        .avg_drawdown = if (dd_count > 0) dd_sum / n else 0.0,
        .max_drawdown_peak_date = max_dd_from,
        .max_drawdown_trough_date = max_dd_to,
        .max_drawdown_dollars = max_dd_dollars,
        .avg_drawdown_dollars = if (dd_count > 0) dd_dollars_sum / n else 0.0,
    };
}

// ── Report + JSON ─────────────────────────────────────────────────────────────

const Report = struct {
    final_balance: f64,
    net_growth: f64,
    sharpe: f64,
    total_win: f64,
    total_loss: f64,
    win_rate: f64,
    win_count: usize,
    profit_factor: f64,
    expectancy: f64,
    max_lose_streak: usize,
    avg_size: f64,
    min_size: f64,
    max_size: f64,
    avg_weekly: f64,
    avg_monthly: f64,
    avg_weekly_pct: f64,
    avg_monthly_pct: f64,
    total_days: i64,
    max_daily_loss: f64,
    max_daily_loss_date: [10]u8,
    avg_daily_loss: f64,
};

fn computeReport(result: CombineResult) Report {
    const initial_balance = result.initial_balance;
    var balance = initial_balance;
    var total_win: f64 = 0;
    var total_loss: f64 = 0;
    var win_count: usize = 0;
    var contracts_sum: f64 = 0;
    var contracts_min: f64 = std.math.floatMax(f64);
    var contracts_max: f64 = 0;
    var cur_lose_streak: usize = 0;
    var max_lose_streak: usize = 0;
    var wf_n: f64 = 0;
    var wf_mean: f64 = 0;
    var wf_m2: f64 = 0;
    var current_day: [10]u8 = undefined;
    var day_initialized = false;
    var day_pnl: f64 = 0;
    var day_equity: f64 = initial_balance;
    var max_daily_loss: f64 = 0;
    var max_daily_loss_date: [10]u8 = [_]u8{' '} ** 10;
    var daily_loss_sum: f64 = 0;
    var losing_days: usize = 0;

    for (result.trades) |t| {
        const t_day = t.exit_ts[0..10];
        if (!day_initialized) {
            @memcpy(&current_day, t_day);
            day_initialized = true;
        } else if (!std.mem.eql(u8, &current_day, t_day)) {
            const daily_return = if (day_equity > 0) day_pnl / day_equity else 0.0;
            wf_n += 1;
            const delta = daily_return - wf_mean;
            wf_mean += delta / wf_n;
            wf_m2 += delta * (daily_return - wf_mean);
            if (day_pnl < 0) {
                daily_loss_sum += day_pnl;
                losing_days += 1;
                if (day_pnl < max_daily_loss) {
                    max_daily_loss = day_pnl;
                    @memcpy(max_daily_loss_date[0..], &current_day);
                }
            }
            day_equity = balance;
            day_pnl = 0;
            @memcpy(&current_day, t_day);
        }
        day_pnl += t.pnl;
        balance += t.pnl;
        if (t.pnl >= 0) {
            total_win += t.pnl;
            win_count += 1;
            cur_lose_streak = 0;
        } else {
            total_loss += t.pnl;
            cur_lose_streak += 1;
            if (cur_lose_streak > max_lose_streak) max_lose_streak = cur_lose_streak;
        }
        contracts_sum += t.contracts;
        if (t.contracts < contracts_min) contracts_min = t.contracts;
        if (t.contracts > contracts_max) contracts_max = t.contracts;
    }
    if (day_initialized) {
        const daily_return = if (day_equity > 0) day_pnl / day_equity else 0.0;
        wf_n += 1;
        const delta = daily_return - wf_mean;
        wf_mean += delta / wf_n;
        wf_m2 += delta * (daily_return - wf_mean);
        if (day_pnl < 0) {
            daily_loss_sum += day_pnl;
            losing_days += 1;
            if (day_pnl < max_daily_loss) {
                max_daily_loss = day_pnl;
                @memcpy(max_daily_loss_date[0..], &current_day);
            }
        }
    }

    const n_trades = result.trades.len;
    const avg_contracts = if (n_trades > 0) contracts_sum / @as(f64, @floatFromInt(n_trades)) else 0.0;
    if (n_trades == 0) contracts_min = 0;

    const net_pnl = balance - initial_balance;
    const growth = if (initial_balance != 0) net_pnl / initial_balance * 100.0 else 0.0;
    const total_days = daysBetween(result.first_ts, result.last_ts);
    const total_days_f: f64 = @floatFromInt(total_days);
    const avg_weekly = if (total_days > 0) net_pnl / (total_days_f / 7.0) else 0.0;
    const avg_monthly = if (total_days > 0) net_pnl / (total_days_f / 30.4375) else 0.0;
    const avg_weekly_pct = if (initial_balance != 0) avg_weekly / initial_balance * 100.0 else 0.0;
    const avg_monthly_pct = if (initial_balance != 0) avg_monthly / initial_balance * 100.0 else 0.0;

    const loss_count = n_trades - win_count;
    const win_rate = if (n_trades > 0) @as(f64, @floatFromInt(win_count)) / @as(f64, @floatFromInt(n_trades)) * 100.0 else 0.0;
    const avg_win = if (win_count > 0) total_win / @as(f64, @floatFromInt(win_count)) else 0.0;
    const avg_loss = if (loss_count > 0) total_loss / @as(f64, @floatFromInt(loss_count)) else 0.0;
    const profit_factor = if (total_loss < 0) total_win / @abs(total_loss) else 0.0;
    const expectancy = (win_rate / 100.0) * avg_win + (1.0 - win_rate / 100.0) * avg_loss;
    const avg_daily_loss = if (losing_days > 0) daily_loss_sum / @as(f64, @floatFromInt(losing_days)) else 0.0;

    const daily_std = if (wf_n > 1) @sqrt(wf_m2 / (wf_n - 1.0)) else 0.0;
    const sharpe = if (daily_std > 0) wf_mean / daily_std * @sqrt(252.0) else 0.0;

    return .{
        .final_balance   = balance,
        .net_growth      = growth,
        .sharpe          = sharpe,
        .total_win       = total_win,
        .total_loss      = total_loss,
        .win_rate        = win_rate,
        .win_count       = win_count,
        .profit_factor   = profit_factor,
        .expectancy      = expectancy,
        .max_lose_streak = max_lose_streak,
        .avg_size        = avg_contracts,
        .min_size        = contracts_min,
        .max_size        = contracts_max,
        .avg_weekly      = avg_weekly,
        .avg_monthly     = avg_monthly,
        .avg_weekly_pct  = avg_weekly_pct,
        .avg_monthly_pct = avg_monthly_pct,
        .total_days      = total_days,
        .max_daily_loss  = max_daily_loss,
        .max_daily_loss_date = max_daily_loss_date,
        .avg_daily_loss  = avg_daily_loss,
    };
}

fn buildJson(result: CombineResult, initial_balance: f64) ![]const u8 {
    _ = initial_balance;
    const r = computeReport(result);

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(alloc);

    const head = try std.fmt.allocPrint(alloc,
        \\{{"symbol":"COMBINED","instrument":"forex","first_ts":"{s}","last_ts":"{s}","total_days":{d},"num_trades":{d},"initial_bal":{d:.2},"final_bal":{d:.2},"net_growth":{d:.4},"sharpe":{d:.4},"total_win":{d:.2},"total_loss":{d:.2},"win_rate":{d:.4},"win_count":{d},"profit_factor":{d:.4},"expectancy":{d:.4},"max_lose_streak":{d},"avg_size":{d:.4},"min_size":{d:.4},"max_size":{d:.4},"avg_weekly":{d:.2},"avg_monthly":{d:.2},"avg_weekly_pct":{d:.4},"avg_monthly_pct":{d:.4}
    , .{
        result.first_ts,           result.last_ts,
        r.total_days,              result.trades.len,       fin(result.initial_balance),
        fin(r.final_balance),      fin(r.net_growth),       fin(r.sharpe),
        fin(r.total_win),          fin(r.total_loss),       fin(r.win_rate),
        r.win_count,               fin(r.profit_factor),    fin(r.expectancy),
        r.max_lose_streak,         fin(r.avg_size),         fin(r.min_size),
        fin(r.max_size),           fin(r.avg_weekly),       fin(r.avg_monthly),
        fin(r.avg_weekly_pct),     fin(r.avg_monthly_pct),
    });
    defer alloc.free(head);
    try out.appendSlice(alloc, head);

    const dd = try std.fmt.allocPrint(alloc,
        \\,"max_drawdown":{d:.4},"max_drawdown_dollars":{d:.2},"max_drawdown_peak_date":"{s}","max_drawdown_trough_date":"{s}","avg_drawdown":{d:.4},"avg_drawdown_dollars":{d:.2},"max_intraday_drawdown":{d:.4},"max_intraday_drawdown_dollars":{d:.2},"max_intraday_drawdown_date":"{s}","avg_intraday_drawdown":{d:.4},"avg_intraday_drawdown_dollars":{d:.2},"max_daily_loss":{d:.2},"max_daily_loss_date":"{s}","avg_daily_loss":{d:.2}
    , .{
        fin(result.max_drawdown),                   fin(result.max_drawdown_dollars),
        result.max_drawdown_peak_date,              result.max_drawdown_trough_date,
        fin(result.avg_drawdown),                   fin(result.avg_drawdown_dollars),
        fin(result.max_intraday_drawdown),          fin(result.max_intraday_drawdown_dollars),
        result.max_intraday_drawdown_date,          fin(result.avg_intraday_drawdown),
        fin(result.avg_intraday_drawdown_dollars),  fin(r.max_daily_loss),
        r.max_daily_loss_date,                      fin(r.avg_daily_loss),
    });
    defer alloc.free(dd);
    try out.appendSlice(alloc, dd);

    // Trades.
    try out.appendSlice(alloc, ",\"trades\":[");
    var tbuf: [192]u8 = undefined;
    for (result.trades, 0..) |t, i| {
        const side: []const u8 = if (t.side == .long) "long" else "short";
        const row = try std.fmt.bufPrint(&tbuf, "{s}{{\"side\":\"{s}\",\"et\":{d},\"xt\":{d},\"ep\":{d:.4},\"xp\":{d:.4},\"pnl\":{d:.4},\"qty\":{d:.4}}}", .{
            if (i == 0) "" else ",",
            side,
            tsToUnix(t.entry_ts),
            tsToUnix(t.exit_ts),
            fin(t.entry_price),
            fin(t.exit_price),
            fin(t.pnl),
            fin(t.contracts),
        });
        try out.appendSlice(alloc, row);
    }
    try out.appendSlice(alloc, "]");

    // Monte Carlo.
    try appendMonteCarlo(&out, result);

    try out.appendSlice(alloc, "}");
    return out.toOwnedSlice(alloc);
}

fn appendMonteCarlo(out: *std.ArrayList(u8), result: CombineResult) !void {
    if (result.trades.len == 0) {
        try out.appendSlice(alloc, ",\"montecarlo\":null");
        return;
    }
    const pnls = try alloc.alloc(f64, result.trades.len);
    defer alloc.free(pnls);
    for (result.trades, 0..) |t, i| pnls[i] = t.pnl;

    var paths: ?montecarlo.Paths = null;
    const mc = montecarlo.run(alloc, pnls, result.initial_balance, .{
        .sims = MC_SIMS,
        .path_steps = MC_PATH_STEPS,
        .seed = MC_SEED,
    }, &paths) catch {
        try out.appendSlice(alloc, ",\"montecarlo\":null");
        return;
    };
    defer if (paths) |*pp| pp.deinit(alloc);

    const p = paths orelse {
        try out.appendSlice(alloc, ",\"montecarlo\":null");
        return;
    };

    const render = @min(MC_RENDER_PATHS, p.n_paths);
    const head = try std.fmt.allocPrint(alloc,
        \\,"montecarlo":{{"initialBalance":{d:.2},"sims":{d},"steps":{d},"numPaths":{d},"p5":{d:.2},"p25":{d:.2},"p50":{d:.2},"p75":{d:.2},"p95":{d:.2},"pProfit":{d:.4},"pRuin":{d:.4},"ddP5":{d:.4},"ddP25":{d:.4},"ddP50":{d:.4},"ddP75":{d:.4},"ddP95":{d:.4}
    , .{
        fin(mc.initial_balance), mc.sims,             p.n_steps,
        render,                  fin(mc.final_balance[0]), fin(mc.final_balance[1]),
        fin(mc.final_balance[2]), fin(mc.final_balance[3]), fin(mc.final_balance[4]),
        fin(mc.p_profit),        fin(mc.p_ruin),
        fin(mc.max_drawdown[0]), fin(mc.max_drawdown[1]), fin(mc.max_drawdown[2]),
        fin(mc.max_drawdown[3]), fin(mc.max_drawdown[4]),
    });
    defer alloc.free(head);
    try out.appendSlice(alloc, head);

    try out.appendSlice(alloc, ",\"stepValues\":[");
    var nbuf: [32]u8 = undefined;
    for (p.steps, 0..) |sv, i| {
        const s = try std.fmt.bufPrint(&nbuf, "{s}{d}", .{ if (i == 0) "" else ",", sv });
        try out.appendSlice(alloc, s);
    }
    try out.appendSlice(alloc, "]");

    try out.appendSlice(alloc, ",\"paths\":[");
    var vbuf: [32]u8 = undefined;
    var pi: usize = 0;
    while (pi < render) : (pi += 1) {
        try out.appendSlice(alloc, if (pi == 0) "[" else ",[");
        var si: usize = 0;
        while (si < p.n_steps) : (si += 1) {
            const v = p.equity[pi * p.n_steps + si];
            const sv = try std.fmt.bufPrint(&vbuf, "{s}{d:.2}", .{ if (si == 0) "" else ",", fin(v) });
            try out.appendSlice(alloc, sv);
        }
        try out.appendSlice(alloc, "]");
    }
    try out.appendSlice(alloc, "]}");
}

// ── Persistence ───────────────────────────────────────────────────────────────

fn persistCombine(result: CombineResult, initial_balance: f64) !i64 {
    const r = computeReport(result);

    const meta = db.SaveMeta{
        .strategy = "COMBINED",
        .symbol   = "COMBINED",
        .instrument = "forex",
        .first_ts = result.first_ts[0..],
        .last_ts  = result.last_ts[0..],
        .total_days = r.total_days,
        .num_trades = @intCast(result.trades.len),
        .initial_bal = fin(initial_balance),
        .final_bal  = fin(r.final_balance),
        .net_growth = fin(r.net_growth),
        .max_drawdown = fin(result.max_drawdown),
        .avg_drawdown = fin(result.avg_drawdown),
        .sharpe = fin(r.sharpe),
        .total_win = fin(r.total_win),
        .total_loss = fin(r.total_loss),
        .win_rate = fin(r.win_rate),
        .win_count = @intCast(r.win_count),
        .profit_factor = fin(r.profit_factor),
        .expectancy = fin(r.expectancy),
        .max_lose_streak = @intCast(r.max_lose_streak),
        .avg_size = fin(r.avg_size),
        .min_size = fin(r.min_size),
        .max_size = fin(r.max_size),
        .avg_weekly = fin(r.avg_weekly),
        .avg_monthly = fin(r.avg_monthly),
        .avg_weekly_pct = fin(r.avg_weekly_pct),
        .avg_monthly_pct = fin(r.avg_monthly_pct),
        .max_drawdown_dollars = fin(result.max_drawdown_dollars),
        .max_drawdown_peak_date = result.max_drawdown_peak_date[0..],
        .max_drawdown_trough_date = result.max_drawdown_trough_date[0..],
        .avg_drawdown_dollars = fin(result.avg_drawdown_dollars),
        .max_intraday_drawdown = 0,
        .max_intraday_drawdown_dollars = 0,
        .max_intraday_drawdown_date = "          ",
        .avg_intraday_drawdown = 0,
        .avg_intraday_drawdown_dollars = 0,
        .max_daily_loss = fin(r.max_daily_loss),
        .max_daily_loss_date = r.max_daily_loss_date[0..],
        .avg_daily_loss = fin(r.avg_daily_loss),
    };

    const save_trades = try alloc.alloc(db.SaveTrade, result.trades.len);
    defer alloc.free(save_trades);
    for (result.trades, 0..) |t, i| {
        save_trades[i] = .{
            .side_long = t.side == .long,
            .entry_ts = result.trades[i].entry_ts[0..],
            .exit_ts  = result.trades[i].exit_ts[0..],
            .entry_price = fin(t.entry_price),
            .exit_price  = fin(t.exit_price),
            .pnl         = fin(t.pnl),
            .contracts   = fin(t.contracts),
        };
    }

    var mc_save: ?db.SaveMonteCarlo = null;
    var paths: ?montecarlo.Paths = null;
    defer if (paths) |*pp| pp.deinit(alloc);
    if (result.trades.len > 0) {
        const pnls = try alloc.alloc(f64, result.trades.len);
        defer alloc.free(pnls);
        for (result.trades, 0..) |t, i| pnls[i] = t.pnl;
        if (montecarlo.run(alloc, pnls, result.initial_balance, .{
            .sims = MC_SIMS, .path_steps = MC_PATH_STEPS, .seed = MC_SEED,
        }, &paths)) |mc| {
            if (paths) |pp| {
                mc_save = .{
                    .sims = @intCast(mc.sims),
                    .initial_balance = fin(mc.initial_balance),
                    .p5 = fin(mc.final_balance[0]), .p25 = fin(mc.final_balance[1]),
                    .p50 = fin(mc.final_balance[2]), .p75 = fin(mc.final_balance[3]),
                    .p95 = fin(mc.final_balance[4]),
                    .p_profit = fin(mc.p_profit), .p_ruin = fin(mc.p_ruin),
                    .dd_p5 = fin(mc.max_drawdown[0]), .dd_p25 = fin(mc.max_drawdown[1]),
                    .dd_p50 = fin(mc.max_drawdown[2]), .dd_p75 = fin(mc.max_drawdown[3]),
                    .dd_p95 = fin(mc.max_drawdown[4]),
                    .num_paths = @min(MC_RENDER_PATHS, pp.n_paths),
                    .num_steps = pp.n_steps,
                    .steps = pp.steps,
                    .equity = pp.equity,
                };
            }
        } else |_| {}
    }

    return db.saveBacktest(meta, save_trades, mc_save);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn fin(x: f64) f64 {
    return if (std.math.isFinite(x)) x else 0;
}

fn tsToUnix(ts: [16]u8) i64 {
    const y = std.fmt.parseInt(i64, ts[0..4], 10) catch return 0;
    const mo = std.fmt.parseInt(i64, ts[5..7], 10) catch return 0;
    const da = std.fmt.parseInt(i64, ts[8..10], 10) catch return 0;
    const hh = std.fmt.parseInt(i64, ts[11..13], 10) catch return 0;
    const mm = std.fmt.parseInt(i64, ts[14..16], 10) catch return 0;
    return daysFromCivil(y, mo, da) * 86400 + hh * 3600 + mm * 60;
}

fn daysFromCivil(y_in: i64, m: i64, d: i64) i64 {
    const y = if (m <= 2) y_in - 1 else y_in;
    const era = @divFloor(if (y >= 0) y else y - 399, @as(i64, 400));
    const yoe = y - era * 400;
    const mp = if (m > 2) m - 3 else m + 9;
    const doy = @divFloor(153 * mp + 2, @as(i64, 5)) + d - 1;
    const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy;
    return era * 146097 + doe - 719468;
}

fn jdn(ts: [16]u8) i64 {
    const y = std.fmt.parseInt(i64, ts[0..4], 10) catch return 0;
    const m = std.fmt.parseInt(i64, ts[5..7], 10) catch return 0;
    const d = std.fmt.parseInt(i64, ts[8..10], 10) catch return 0;
    const a = @divFloor(14 - m, 12);
    const yy = y + 4800 - a;
    const mm = m + 12 * a - 3;
    return d + @divFloor(153 * mm + 2, 5) + 365 * yy + @divFloor(yy, 4) - @divFloor(yy, 100) + @divFloor(yy, 400) - 32045;
}

fn daysBetween(first: [16]u8, last: [16]u8) i64 {
    return jdn(last) - jdn(first);
}

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

fn isIsoDate(s: []const u8) bool {
    if (s.len != 10) return false;
    for (s, 0..) |ch, i| {
        if (i == 4 or i == 7) {
            if (ch != '-') return false;
        } else if (ch < '0' or ch > '9') return false;
    }
    return true;
}

fn badBody(req: *http.Ctx) !void {
    req.setStatusNumeric(400);
    try req.sendJson("{\"error\":\"no body\"}");
}

fn fail(req: *http.Ctx, err: anyerror) !void {
    std.debug.print("combine error: {}\n", .{err});
    req.setStatusNumeric(503);
    try req.sendJson("{\"error\":\"combine failed\"}");
}
