const std = @import("std");
const Io = std.Io;
const engine = @import("engine.zig");
const db = @import("db.zig");

pub fn print(io: Io, result: engine.Result) !db.Summary {
    var buf: [512]u8 = undefined;
    var dollar_buf: [32]u8 = undefined;
    var writer = Io.File.stdout().writer(io, &buf);
    const w = &writer.interface;

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

    // Welford online algorithm for daily returns — drives the Sharpe ratio.
    // Trades are sorted by exit_ts; we bucket by date (exit_ts[0..10]).
    // day_equity = equity at the start of the current calendar day (before any
    // of that day's trades close), so daily_return = day_pnl / day_equity.
    var wf_n: f64 = 0;
    var wf_mean: f64 = 0;
    var wf_m2: f64 = 0;
    var current_day: [10]u8 = undefined;
    var day_initialized = false;
    var day_pnl: f64 = 0;
    var day_equity: f64 = initial_balance;

    // Daily loss: net realized PnL per calendar day, counting only losing days.
    // Stored as signed (negative) dollars. `max_daily_loss` is the single worst
    // day; `avg_daily_loss` averages over losing days only.
    var max_daily_loss: f64 = 0;
    var max_daily_loss_date: [10]u8 = [_]u8{' '} ** 10;
    var daily_loss_sum: f64 = 0;
    var losing_days: usize = 0;

    for (result.trades) |t| {
        // Check day boundary BEFORE updating balance so day_equity is correct.
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

    // Finalize the last day.
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
    // Drawdown is computed by the engine on the mark-to-market equity curve
    // (captures dips during a held position, not just at trade closes).
    const max_dd = result.max_drawdown;
    const avg_dd = result.avg_drawdown;
    const net_pnl = balance - initial_balance;
    const growth = if (initial_balance != 0) net_pnl / initial_balance * 100.0 else 0.0;
    const growth_sign: u8 = if (growth >= 0) '+' else '-';
    const abs_growth = @abs(growth);
    const total_days = daysBetween(result.first_ts, result.last_ts);

    const total_days_f: f64 = @floatFromInt(total_days);
    const avg_weekly = if (total_days > 0) net_pnl / (total_days_f / 7.0) else 0.0;
    const avg_monthly = if (total_days > 0) net_pnl / (total_days_f / 30.4375) else 0.0;
    const avg_weekly_pct = if (initial_balance != 0) avg_weekly / initial_balance * 100.0 else 0.0;
    const avg_monthly_pct = if (initial_balance != 0) avg_monthly / initial_balance * 100.0 else 0.0;
    const weekly_sign: u8 = if (avg_weekly >= 0) '+' else '-';
    const monthly_sign: u8 = if (avg_monthly >= 0) '+' else '-';

    // Win rate, profit factor, expectancy.
    const loss_count = n_trades - win_count;
    const win_rate = if (n_trades > 0) @as(f64, @floatFromInt(win_count)) / @as(f64, @floatFromInt(n_trades)) * 100.0 else 0.0;
    const avg_win = if (win_count > 0) total_win / @as(f64, @floatFromInt(win_count)) else 0.0;
    const avg_loss = if (loss_count > 0) total_loss / @as(f64, @floatFromInt(loss_count)) else 0.0;
    const profit_factor = if (total_loss < 0) total_win / @abs(total_loss) else 0.0;
    const expectancy = (win_rate / 100.0) * avg_win + (1.0 - win_rate / 100.0) * avg_loss;
    const expectancy_sign: u8 = if (expectancy >= 0) '+' else '-';

    const avg_daily_loss = if (losing_days > 0) daily_loss_sum / @as(f64, @floatFromInt(losing_days)) else 0.0;

    // Sharpe ratio: annualized with 252 trading days, Rf = 0.
    const daily_std = if (wf_n > 1) @sqrt(wf_m2 / (wf_n - 1.0)) else 0.0;
    const sharpe = if (daily_std > 0) wf_mean / daily_std * @sqrt(252.0) else 0.0;

    try w.print("\n", .{});
    try w.print("  Instrument       {s}\n", .{engine.instrumentName()});
    try w.print("  Initial Balance  ${s}\n", .{fmtDollars(&dollar_buf, initial_balance)});
    try w.print("  Final Balance    ${s}\n", .{fmtDollars(&dollar_buf, balance)});
    try w.print("  Net Growth       {c}${s} ({c}{d:.2}%)\n", .{ growth_sign, fmtDollars(&dollar_buf, @abs(net_pnl)), growth_sign, abs_growth });
    try w.print("  Sharpe Ratio     {d:.2}\n", .{sharpe});
    try w.print("  Max Drawdown     -${s} ({d:.2}%)  ({s} → {s})\n", .{ fmtDollars(&dollar_buf, result.max_drawdown_dollars), max_dd, result.max_drawdown_peak_date, result.max_drawdown_trough_date });
    try w.print("  Avg Drawdown     -${s} ({d:.2}%)\n", .{ fmtDollars(&dollar_buf, result.avg_drawdown_dollars), avg_dd });
    try w.print("  Max Intraday DD  -${s} ({d:.2}%)  ({s})\n", .{ fmtDollars(&dollar_buf, result.max_intraday_drawdown_dollars), result.max_intraday_drawdown, result.max_intraday_drawdown_date });
    try w.print("  Avg Intraday DD  -${s} ({d:.2}%)\n", .{ fmtDollars(&dollar_buf, result.avg_intraday_drawdown_dollars), result.avg_intraday_drawdown });
    try w.print("  Total Win        ${s}\n", .{fmtDollars(&dollar_buf, total_win)});
    try w.print("  Total Loss       ${s}\n", .{fmtDollars(&dollar_buf, total_loss)});
    try w.print("  Max Daily Loss   ${s}  ({s})\n", .{ fmtDollars(&dollar_buf, max_daily_loss), max_daily_loss_date });
    try w.print("  Avg Daily Loss   ${s}\n", .{fmtDollars(&dollar_buf, avg_daily_loss)});
    try w.print("  Win Rate         {d:.1}%  ({d}/{d} trades)\n", .{ win_rate, win_count, n_trades });
    try w.print("  Profit Factor    {d:.2}\n", .{profit_factor});
    try w.print("  Expectancy       {c}${s} per trade\n", .{ expectancy_sign, fmtDollars(&dollar_buf, @abs(expectancy)) });
    try w.print("  Max Lose Streak  {d}\n", .{max_lose_streak});
    // Futures instruments trade whole contracts; forex trades fractional lots.
    if (engine.usesContracts()) {
        try w.print("  Avg Contracts    {d:.2}  (min {d:.0}, max {d:.0})\n", .{ avg_contracts, contracts_min, contracts_max });
    } else {
        try w.print("  Avg Lots         {d:.2}  (min {d:.2}, max {d:.2})\n", .{ avg_contracts, contracts_min, contracts_max });
    }
    try w.print("  Total Days       {d}\n", .{total_days});
    try w.print("  Avg Weekly Gain  {c}${s} ({c}{d:.2}%)\n", .{ weekly_sign, fmtDollars(&dollar_buf, @abs(avg_weekly)), weekly_sign, @abs(avg_weekly_pct) });
    try w.print("  Avg Monthly Gain {c}${s} ({c}{d:.2}%)\n\n", .{ monthly_sign, fmtDollars(&dollar_buf, @abs(avg_monthly)), monthly_sign, @abs(avg_monthly_pct) });
    try w.flush();

    return .{
        .final_balance = balance,
        .net_growth = growth,
        .avg_drawdown = avg_dd,
        .max_drawdown = max_dd,
        .total_days = total_days,
        .sharpe = sharpe,
        .total_win = total_win,
        .total_loss = total_loss,
        .win_rate = win_rate,
        .win_count = win_count,
        .profit_factor = profit_factor,
        .expectancy = expectancy,
        .max_lose_streak = max_lose_streak,
        .avg_size = avg_contracts,
        .min_size = contracts_min,
        .max_size = contracts_max,
        .avg_weekly = avg_weekly,
        .avg_monthly = avg_monthly,
        .avg_weekly_pct = avg_weekly_pct,
        .avg_monthly_pct = avg_monthly_pct,
        .max_drawdown_dollars = result.max_drawdown_dollars,
        .max_drawdown_peak_date = result.max_drawdown_peak_date,
        .max_drawdown_trough_date = result.max_drawdown_trough_date,
        .avg_drawdown_dollars = result.avg_drawdown_dollars,
        .max_intraday_drawdown = result.max_intraday_drawdown,
        .max_intraday_drawdown_dollars = result.max_intraday_drawdown_dollars,
        .max_intraday_drawdown_date = result.max_intraday_drawdown_date,
        .avg_intraday_drawdown = result.avg_intraday_drawdown,
        .avg_intraday_drawdown_dollars = result.avg_intraday_drawdown_dollars,
        .max_daily_loss = max_daily_loss,
        .max_daily_loss_date = max_daily_loss_date,
        .avg_daily_loss = avg_daily_loss,
    };
}

// Compute the core metrics without printing — used by the tuner, which runs
// many backtests and only needs growth / drawdown to rank them.
pub fn summarize(result: engine.Result) db.Summary {
    const initial_balance = result.initial_balance;
    var balance = initial_balance;
    for (result.trades) |t| balance += t.pnl;

    const net_pnl = balance - initial_balance;
    const growth = if (initial_balance != 0) net_pnl / initial_balance * 100.0 else 0.0;
    return .{
        .final_balance = balance,
        .net_growth = growth,
        .avg_drawdown = result.avg_drawdown,
        .max_drawdown = result.max_drawdown,
        .total_days = daysBetween(result.first_ts, result.last_ts),
        .sharpe = 0,
        .total_win = 0,
        .total_loss = 0,
        .win_rate = 0,
        .win_count = 0,
        .profit_factor = 0,
        .expectancy = 0,
        .max_lose_streak = 0,
        .avg_size = 0,
        .min_size = 0,
        .max_size = 0,
        .avg_weekly = 0,
        .avg_monthly = 0,
        .avg_weekly_pct = 0,
        .avg_monthly_pct = 0,
        .max_drawdown_dollars = result.max_drawdown_dollars,
        .max_drawdown_peak_date = result.max_drawdown_peak_date,
        .max_drawdown_trough_date = result.max_drawdown_trough_date,
        .avg_drawdown_dollars = result.avg_drawdown_dollars,
        .max_intraday_drawdown = result.max_intraday_drawdown,
        .max_intraday_drawdown_dollars = result.max_intraday_drawdown_dollars,
        .max_intraday_drawdown_date = result.max_intraday_drawdown_date,
        .avg_intraday_drawdown = result.avg_intraday_drawdown,
        .avg_intraday_drawdown_dollars = result.avg_intraday_drawdown_dollars,
        .max_daily_loss = 0,
        .max_daily_loss_date = [_]u8{' '} ** 10,
        .avg_daily_loss = 0,
    };
}

// Julian Day Number for a "YYYY-MM-DD HH:MM" timestamp string.
// Subtracting two JDNs gives the calendar-day span between them.
fn jdn(ts: [16]u8) i64 {
    const y = @as(i64, parseInt4(ts[0..4]));
    const m = @as(i64, parseInt2(ts[5..7]));
    const d = @as(i64, parseInt2(ts[8..10]));
    const a = @divFloor(14 - m, 12);
    const yy = y + 4800 - a;
    const mm = m + 12 * a - 3;
    return d + @divFloor(153 * mm + 2, 5) + 365 * yy + @divFloor(yy, 4) - @divFloor(yy, 100) + @divFloor(yy, 400) - 32045;
}

fn daysBetween(first: [16]u8, last: [16]u8) i64 {
    return jdn(last) - jdn(first);
}

// Format a dollar amount with thousands separators, e.g. 5284.40 → "5,284.40".
// Negative values produce "-5,284.40". buf must be at least 32 bytes.
fn fmtDollars(buf: []u8, value: f64) []u8 {
    var tmp: [64]u8 = undefined;
    const s = std.fmt.bufPrint(&tmp, "{d:.2}", .{@abs(value)}) catch return buf[0..0];
    const dot_pos = std.mem.indexOfScalar(u8, s, '.') orelse s.len;
    const int_part = s[0..dot_pos];
    const frac_part = if (dot_pos < s.len) s[dot_pos..] else "";
    var out_len: usize = 0;
    if (value < 0) {
        buf[out_len] = '-';
        out_len += 1;
    }
    const int_len = int_part.len;
    for (int_part, 0..) |c, i| {
        const digits_remaining = int_len - i;
        if (i > 0 and digits_remaining % 3 == 0) {
            buf[out_len] = ',';
            out_len += 1;
        }
        buf[out_len] = c;
        out_len += 1;
    }
    @memcpy(buf[out_len..][0..frac_part.len], frac_part);
    out_len += frac_part.len;
    return buf[0..out_len];
}

fn parseInt4(s: *const [4]u8) u16 {
    return (@as(u16, s[0] - '0') * 1000 + @as(u16, s[1] - '0') * 100 +
        @as(u16, s[2] - '0') * 10 + @as(u16, s[3] - '0'));
}

fn parseInt2(s: *const [2]u8) u8 {
    return (s[0] - '0') * 10 + (s[1] - '0');
}
