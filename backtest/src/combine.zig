const std = @import("std");
const Io = std.Io;
const engine = @import("engine.zig");
const data = @import("data.zig");

// ── /combine drawdown ─────────────────────────────────────────────────────────
// The combined report pools trades from several saved backtests. To get a REAL
// drawdown — one that captures dips *while positions are open*, not just at trade
// closes — we re-fetch each source's price bars from QuestDB and mark the whole
// combined book to market bar-by-bar, exactly like the engine does for a single
// run. Equity at any instant is:
//
//   initial + Σ(realized PnL of closed trades) + Σ(unrealized PnL of open trades)
//
// where a long's unrealized = (close − entry) × pointValue × contracts. Encoding
// each position as a = ±pointValue×contracts lets a dataset's whole open book be
// valued as `close × ΣA − Σ(A×entry)`, updated incrementally as trades open/close.

// One group of trades sharing an instrument point-value and (optionally) a
// QuestDB price table. `table_len == 0` marks a source whose price series can't
// be resolved (e.g. a previously-saved COMBINED run); its trades still book
// realized PnL but are never marked to market.
pub const TradeSrc = struct {
    trades: []const engine.Trade,
    mult: f64,
    table_buf: [32]u8 = undefined,
    table_len: usize = 0,
};

pub const Drawdown = struct {
    max_drawdown: f64 = 0,
    avg_drawdown: f64 = 0,
    max_drawdown_peak_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_trough_date: [10]u8 = [_]u8{' '} ** 10,
    max_intraday_drawdown: f64 = 0,
    avg_intraday_drawdown: f64 = 0,
    max_intraday_drawdown_date: [10]u8 = [_]u8{' '} ** 10,
    max_drawdown_dollars: f64 = 0,
    avg_drawdown_dollars: f64 = 0,
    max_intraday_drawdown_dollars: f64 = 0,
    avg_intraday_drawdown_dollars: f64 = 0,
};

const blank_date = [_]u8{' '} ** 10;

// A position-open or position-close event on the global timeline.
const Event = struct {
    ts: engine.Ts,
    ds: i64, // dataset index, -1 = unresolved (no mark-to-market)
    a: f64, // signed pointValue×contracts (+long / −short)
    ae: f64, // a × entry_price
    pnl: f64, // realized PnL (close events only)
};

fn eventLess(_: void, a: Event, b: Event) bool {
    return std.mem.order(u8, &a.ts, &b.ts) == .lt;
}

// Mark the combined book to market over real bars and return its drawdown stats.
// Returns error.NoData if no resolved source yields bars (caller falls back to a
// realized-equity drawdown).
pub fn markToMarket(io: Io, gpa: std.mem.Allocator, initial: f64, srcs: []const TradeSrc) !Drawdown {
    // ── 1. distinct price tables, and per-table date window ──────────────────
    var tables: std.ArrayList([]const u8) = .empty;
    defer tables.deinit(gpa);
    var win_from: std.ArrayList([10]u8) = .empty;
    defer win_from.deinit(gpa);
    var win_to: std.ArrayList([10]u8) = .empty;
    defer win_to.deinit(gpa);

    const ds_of_src = try gpa.alloc(i64, srcs.len);
    defer gpa.free(ds_of_src);

    for (srcs, 0..) |src, si| {
        if (src.table_len == 0) {
            ds_of_src[si] = -1;
            continue;
        }
        const tbl = src.table_buf[0..src.table_len];
        var idx: i64 = -1;
        for (tables.items, 0..) |t, ti| {
            if (std.mem.eql(u8, t, tbl)) {
                idx = @intCast(ti);
                break;
            }
        }
        if (idx < 0) {
            idx = @intCast(tables.items.len);
            try tables.append(gpa, tbl);
            try win_from.append(gpa, "9999-99-99".*);
            try win_to.append(gpa, "0000-00-00".*);
        }
        ds_of_src[si] = idx;
        // Widen this table's fetch window to span the source's trades.
        const di: usize = @intCast(idx);
        for (src.trades) |t| {
            if (std.mem.order(u8, t.entry_ts[0..10], &win_from.items[di]) == .lt)
                @memcpy(win_from.items[di][0..], t.entry_ts[0..10]);
            if (std.mem.order(u8, t.exit_ts[0..10], &win_to.items[di]) == .gt)
                @memcpy(win_to.items[di][0..], t.exit_ts[0..10]);
        }
    }

    if (tables.items.len == 0) return error.NoData;

    // ── 2. fetch each table's bars over its window ───────────────────────────
    const cols = data.Columns{ .open = true, .high = false, .low = false, .close = true, .volume = false };
    var datasets = try gpa.alloc(data.Dataset, tables.items.len);
    var n_ds: usize = 0;
    defer {
        for (datasets[0..n_ds]) |d| d.deinit();
        gpa.free(datasets);
    }
    for (tables.items, 0..) |tbl, ti| {
        var to_buf: [10]u8 = undefined;
        const to_slice = nextDay(&to_buf, win_to.items[ti]); // `to` is exclusive
        const src = data.Source{ .table = tbl, .from = win_from.items[ti][0..], .to = to_slice };
        datasets[ti] = try data.fetch(io, gpa, cols, src);
        n_ds += 1;
    }

    // ── 3. build open/close events for every trade ───────────────────────────
    var opens: std.ArrayList(Event) = .empty;
    defer opens.deinit(gpa);
    var closes: std.ArrayList(Event) = .empty;
    defer closes.deinit(gpa);
    for (srcs, 0..) |src, si| {
        const ds = ds_of_src[si];
        for (src.trades) |t| {
            const sign: f64 = if (t.side == .long) 1.0 else -1.0;
            const a = sign * src.mult * t.contracts;
            const ae = a * t.entry_price;
            try opens.append(gpa, .{ .ts = t.entry_ts, .ds = ds, .a = a, .ae = ae, .pnl = 0 });
            try closes.append(gpa, .{ .ts = t.exit_ts, .ds = ds, .a = a, .ae = ae, .pnl = t.pnl });
        }
    }
    std.mem.sort(Event, opens.items, {}, eventLess);
    std.mem.sort(Event, closes.items, {}, eventLess);

    // ── 4. per-dataset running open book (ΣA, Σ A×entry) + bar cursor ────────
    const nd = n_ds;
    const sumA = try gpa.alloc(f64, nd);
    defer gpa.free(sumA);
    const sumAE = try gpa.alloc(f64, nd);
    defer gpa.free(sumAE);
    const pbar = try gpa.alloc(usize, nd); // next-bar cursor per dataset
    defer gpa.free(pbar);
    const last_close = try gpa.alloc(f64, nd);
    defer gpa.free(last_close);
    const seen = try gpa.alloc(bool, nd); // has this dataset produced a bar yet?
    defer gpa.free(seen);
    for (0..nd) |d| {
        sumA[d] = 0;
        sumAE[d] = 0;
        pbar[d] = 0;
        last_close[d] = 0;
        seen[d] = false;
    }

    var realized: f64 = 0;
    var op: usize = 0;
    var cl: usize = 0;

    // ── 5. k-way merge of the sorted bar + event streams ─────────────────────
    // The datasets' timestamps and the open/close events are each already in
    // ascending order, so we advance them in lockstep instead of materializing
    // and sorting their union — far less memory, no giant sort. At each distinct
    // timestamp T we apply every open/close at T, roll each dataset's last close
    // forward, then mark the whole book to market.
    var peak: f64 = initial;
    var peak_date: [10]u8 = blank_date;
    var max_dd: f64 = 0;
    var max_dd_dollars: f64 = 0;
    var dd_sum: f64 = 0;
    var dd_dollars_sum: f64 = 0;
    var dd_count: usize = 0;
    var max_dd_from: [10]u8 = blank_date;
    var max_dd_to: [10]u8 = blank_date;

    var day_peak: f64 = initial;
    var cur_day: [10]u8 = undefined;
    var day_started = false;
    var day_max_idd: f64 = 0;
    var day_max_idd_dollars: f64 = 0;
    var max_idd: f64 = 0;
    var max_idd_dollars: f64 = 0;
    var max_idd_date: [10]u8 = blank_date;
    var idd_sum: f64 = 0;
    var idd_dollars_sum: f64 = 0;
    var idd_days: usize = 0;

    while (true) {
        // Smallest next timestamp across all bar cursors and pending events.
        var have = false;
        var t: engine.Ts = undefined;
        for (0..nd) |d| {
            if (pbar[d] < datasets[d].timestamps.len) {
                const c = datasets[d].timestamps[pbar[d]];
                if (!have or std.mem.order(u8, &c, &t) == .lt) {
                    t = c;
                    have = true;
                }
            }
        }
        if (op < opens.items.len) {
            const c = opens.items[op].ts;
            if (!have or std.mem.order(u8, &c, &t) == .lt) {
                t = c;
                have = true;
            }
        }
        if (cl < closes.items.len) {
            const c = closes.items[cl].ts;
            if (!have or std.mem.order(u8, &c, &t) == .lt) {
                t = c;
                have = true;
            }
        }
        if (!have) break;

        // Open trades at T (skip unresolved, ds < 0).
        while (op < opens.items.len and std.mem.eql(u8, &opens.items[op].ts, &t)) : (op += 1) {
            const e = opens.items[op];
            if (e.ds >= 0) {
                const d: usize = @intCast(e.ds);
                sumA[d] += e.a;
                sumAE[d] += e.ae;
            }
        }
        // Close trades at T: book realized, drop from the open book.
        while (cl < closes.items.len and std.mem.eql(u8, &closes.items[cl].ts, &t)) : (cl += 1) {
            const e = closes.items[cl];
            realized += e.pnl;
            if (e.ds >= 0) {
                const d: usize = @intCast(e.ds);
                sumA[d] -= e.a;
                sumAE[d] -= e.ae;
            }
        }
        // Roll each dataset's last close forward to any bar at T.
        for (0..nd) |d| {
            while (pbar[d] < datasets[d].timestamps.len and
                std.mem.eql(u8, &datasets[d].timestamps[pbar[d]], &t)) : (pbar[d] += 1)
            {
                last_close[d] = datasets[d].bars[pbar[d]].close;
                seen[d] = true;
            }
        }

        // Mark-to-market equity across the whole book.
        var mtm = initial + realized;
        for (0..nd) |d| {
            if (seen[d]) mtm += last_close[d] * sumA[d] - sumAE[d];
        }

        // All-time trailing drawdown (peak never resets).
        if (mtm > peak) {
            peak = mtm;
            @memcpy(peak_date[0..], t[0..10]);
        }
        const dd_dollars = peak - mtm;
        const dd = if (peak > 0) dd_dollars / peak * 100.0 else 0.0;
        if (dd > max_dd) {
            max_dd = dd;
            max_dd_dollars = dd_dollars;
            @memcpy(max_dd_from[0..], peak_date[0..]);
            @memcpy(max_dd_to[0..], t[0..10]);
        }
        dd_sum += dd;
        dd_dollars_sum += dd_dollars;
        dd_count += 1;

        // Intraday trailing drawdown (peak resets each calendar day).
        const day = t[0..10];
        if (!day_started or !std.mem.eql(u8, day, cur_day[0..])) {
            if (day_started) {
                idd_sum += day_max_idd;
                idd_dollars_sum += day_max_idd_dollars;
                idd_days += 1;
            }
            @memcpy(cur_day[0..], day);
            day_peak = mtm;
            day_max_idd = 0;
            day_max_idd_dollars = 0;
            day_started = true;
        }
        if (mtm > day_peak) day_peak = mtm;
        const idd_dollars = day_peak - mtm;
        const idd = if (day_peak > 0) idd_dollars / day_peak * 100.0 else 0.0;
        if (idd > day_max_idd) {
            day_max_idd = idd;
            day_max_idd_dollars = idd_dollars;
        }
        if (idd > max_idd) {
            max_idd = idd;
            max_idd_dollars = idd_dollars;
            @memcpy(max_idd_date[0..], cur_day[0..]);
        }
    }
    if (day_started) {
        idd_sum += day_max_idd;
        idd_dollars_sum += day_max_idd_dollars;
        idd_days += 1;
    }

    const ddc: f64 = @floatFromInt(dd_count);
    const iddc: f64 = @floatFromInt(idd_days);
    return .{
        .max_drawdown = max_dd,
        .avg_drawdown = if (dd_count > 0) dd_sum / ddc else 0.0,
        .max_drawdown_peak_date = max_dd_from,
        .max_drawdown_trough_date = max_dd_to,
        .max_intraday_drawdown = max_idd,
        .avg_intraday_drawdown = if (idd_days > 0) idd_sum / iddc else 0.0,
        .max_intraday_drawdown_date = max_idd_date,
        .max_drawdown_dollars = max_dd_dollars,
        .avg_drawdown_dollars = if (dd_count > 0) dd_dollars_sum / ddc else 0.0,
        .max_intraday_drawdown_dollars = max_idd_dollars,
        .avg_intraday_drawdown_dollars = if (idd_days > 0) idd_dollars_sum / iddc else 0.0,
    };
}

// Fallback drawdown sampled only at trade closes off the realized-equity curve.
// Used when no price series can be fetched (intraday fields stay zero). Trades
// must be sorted by exit_ts.
pub fn realizedDrawdown(initial: f64, trades: []const engine.Trade) Drawdown {
    var equity: f64 = initial;
    var peak: f64 = initial;
    var peak_date: [10]u8 = blank_date;
    var max_dd: f64 = 0;
    var max_dd_dollars: f64 = 0;
    var dd_sum: f64 = 0;
    var dd_dollars_sum: f64 = 0;
    var dd_count: usize = 0;
    var max_dd_from: [10]u8 = blank_date;
    var max_dd_to: [10]u8 = blank_date;

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

// ── date helper ────────────────────────────────────────────────────────────
// "YYYY-MM-DD" + 1 calendar day (Hinnant civil-date math), for the exclusive
// upper bound of the bar fetch so the final exit day is included.
fn nextDay(buf: *[10]u8, date: [10]u8) []const u8 {
    const y = std.fmt.parseInt(i64, date[0..4], 10) catch return date[0..];
    const mo = std.fmt.parseInt(i64, date[5..7], 10) catch return date[0..];
    const da = std.fmt.parseInt(i64, date[8..10], 10) catch return date[0..];
    const c = civilFromDays(daysFromCivil(y, mo, da) + 1);
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2}", .{
        @as(u32, @intCast(c.y)), @as(u32, @intCast(c.m)), @as(u32, @intCast(c.d)),
    }) catch date[0..];
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

fn civilFromDays(z_in: i64) struct { y: i64, m: i64, d: i64 } {
    const z = z_in + 719468;
    const era = @divFloor(if (z >= 0) z else z - 146096, @as(i64, 146097));
    const doe = z - era * 146097;
    const yoe = @divFloor(doe - @divFloor(doe, 1460) + @divFloor(doe, 36524) - @divFloor(doe, 146096), @as(i64, 365));
    const y = yoe + era * 400;
    const doy = doe - (365 * yoe + @divFloor(yoe, 4) - @divFloor(yoe, 100));
    const mp = @divFloor(5 * doy + 2, @as(i64, 153));
    const d = doy - @divFloor(153 * mp + 2, @as(i64, 5)) + 1;
    const m = if (mp < 10) mp + 3 else mp - 9;
    return .{ .y = y + @as(i64, if (m <= 2) 1 else 0), .m = m, .d = d };
}
