// Monte Carlo resampling of a backtest's realized per-trade PnL series.
//
// A backtest is ONE realized ordering of trades. Its drawdown/return is a single
// draw from the distribution the edge could have produced. This module resamples
// the trade PnLs many times to recover that distribution, so the headline use is
// *sequence risk / position sizing* — not validating whether the edge is real
// (resampling realized trades can't tell you that).
//
// The default method is the **stationary block bootstrap** (Politis & Romano
// 1994): instead of drawing single trades IID (which destroys the autocorrelation
// of losing streaks and understates drawdowns), it draws contiguous *blocks* of
// trades of random (geometric) length, preserving local clustering. This is the
// credible version; plain IID is offered for comparison.
//
// PnL is additive in dollars (the engine's sizing is balance-independent), so a
// resampled equity curve is simply initial_balance + cumulative sum of the drawn
// trade PnLs — faithful to the engine's own accounting.

const std = @import("std");

pub const Mode = enum { stationary_block, iid };

pub const Config = struct {
    sims: usize = 1000,
    mode: Mode = .stationary_block,
    // Expected block length for the stationary bootstrap. 0 => auto = n^(1/3)
    // (the classic block-bootstrap optimal rate), floored at 2.
    block_mean: f64 = 0,
    // "Ruin" = equity ever dips to <= ruin_frac × initial_balance during a sim.
    ruin_frac: f64 = 0.5,
    seed: u64 = 0, // 0 => clock-seeded
    // When `run` is given a paths_out pointer, each sim's equity curve is captured
    // for charting, downsampled to this many checkpoints (step 0 = start balance).
    path_steps: usize = 200,
};

// Percentiles reported as {p5, p25, p50, p75, p95}.
pub const Pcts = [5]f64;

pub const Result = struct {
    sims: usize,
    mode: Mode,
    block_mean: f64,
    initial_balance: f64,
    num_trades: usize,
    // The actual realized outcome, for side-by-side reference.
    historical_final: f64,
    historical_max_drawdown: f64, // percent
    // Worst single realized trade. The bootstrap can never draw a loss deeper
    // than this, so it bounds the resampled tail (P(ruin)/p95 DD are optimistic).
    worst_trade: f64,
    final_balance: Pcts,
    max_drawdown: Pcts, // percent of running peak
    p_profit: f64,
    p_ruin: f64,
    ruin_frac: f64,
};

// Runs `cfg.sims` resampled simulations and returns the summary `Result`.
// If `paths_out` is non-null, every sim's equity curve is also captured
// (downsampled to `cfg.path_steps` checkpoints) and returned through it — so the
// saved spaghetti curves are the *same* simulations behind the summary stats.
// On capture, `paths_out.*` is set to an owned `Paths` the caller must `deinit`.
pub fn run(gpa: std.mem.Allocator, pnls: []const f64, initial_balance: f64, cfg: Config, paths_out: ?*?Paths) !Result {
    const n = pnls.len;
    if (n == 0) return error.NoTrades;

    var block_mean = cfg.block_mean;
    if (block_mean <= 0) {
        block_mean = std.math.cbrt(@as(f64, @floatFromInt(n)));
        if (block_mean < 2) block_mean = 2;
    }
    // Per-step probability of starting a fresh block (geometric block length,
    // mean = block_mean). 1.0 reproduces plain IID.
    const p_jump = if (cfg.mode == .iid) 1.0 else 1.0 / block_mean;

    var seed = cfg.seed;
    if (seed == 0) {
        // Portable entropy seed. The old code used std.c.clock_gettime / timespec,
        // which are POSIX-only (timespec resolves to `void` on Windows), and this
        // std build exposes no cross-platform wall clock. This app always passes a
        // fixed non-zero seed (MC_SEED=1) for reproducibility, so this branch is
        // effectively dead here; a stack-address (ASLR) seed compiles everywhere and
        // is fine for the non-reproducible case.
        var entropy: u8 = 0;
        seed = @intCast(@intFromPtr(&entropy));
    }
    var prng = std.Random.DefaultPrng.init(seed);
    const rnd = prng.random();

    var finals = try gpa.alloc(f64, cfg.sims);
    defer gpa.free(finals);
    var maxdds = try gpa.alloc(f64, cfg.sims);
    defer gpa.free(maxdds);

    // Optional per-sim equity-curve capture (for charting). Trade-count checkpoints
    // span 0..n; the equity buffer is row-major [sims][n_steps]. Caller owns these
    // on success (handed off via paths_out); freed by errdefer only on failure.
    const capture = paths_out != null;
    const n_steps: usize = if (capture) @min(cfg.path_steps, n + 1) else 0;
    var cap_steps: []u32 = &.{};
    var cap_equity: []f64 = &.{};
    if (capture) {
        cap_steps = try gpa.alloc(u32, n_steps);
        errdefer gpa.free(cap_steps);
        cap_equity = try gpa.alloc(f64, cfg.sims * n_steps);
        errdefer gpa.free(cap_equity);
        buildCheckpoints(cap_steps, n);
    }

    const ruin_level = initial_balance * cfg.ruin_frac;
    var profit_count: usize = 0;
    var ruin_count: usize = 0;

    for (0..cfg.sims) |s| {
        var idx = rnd.uintLessThan(usize, n);
        var equity = initial_balance;
        var peak = initial_balance;
        var maxdd: f64 = 0;
        var ruined = false;

        var cp: usize = 0;
        var applied: u32 = 0;
        if (capture) {
            while (cp < n_steps and cap_steps[cp] == 0) : (cp += 1) cap_equity[s * n_steps + cp] = equity;
        }

        for (0..n) |_| {
            equity += pnls[idx];
            if (equity > peak) peak = equity;
            if (peak > 0) {
                const dd = (peak - equity) / peak;
                if (dd > maxdd) maxdd = dd;
            }
            if (equity <= ruin_level) ruined = true;

            if (capture) {
                applied += 1;
                while (cp < n_steps and cap_steps[cp] == applied) : (cp += 1) cap_equity[s * n_steps + cp] = equity;
            }

            // Advance: start a new random block with prob p_jump, else continue
            // the current block (wrapping around the end of the series).
            if (rnd.float(f64) < p_jump) {
                idx = rnd.uintLessThan(usize, n);
            } else {
                idx = (idx + 1) % n;
            }
        }
        if (capture) {
            while (cp < n_steps) : (cp += 1) cap_equity[s * n_steps + cp] = equity;
        }

        finals[s] = equity;
        maxdds[s] = maxdd * 100.0;
        if (equity > initial_balance) profit_count += 1;
        if (ruined) ruin_count += 1;
    }

    if (paths_out) |po| {
        po.* = .{ .n_paths = cfg.sims, .n_steps = n_steps, .steps = cap_steps, .equity = cap_equity };
    }

    std.mem.sort(f64, finals, {}, std.sort.asc(f64));
    std.mem.sort(f64, maxdds, {}, std.sort.asc(f64));

    return .{
        .sims = cfg.sims,
        .mode = cfg.mode,
        .block_mean = block_mean,
        .initial_balance = initial_balance,
        .num_trades = n,
        .historical_final = historicalFinal(pnls, initial_balance),
        .historical_max_drawdown = historicalMaxDrawdown(pnls, initial_balance),
        .worst_trade = std.mem.min(f64, pnls),
        .final_balance = quantiles(finals),
        .max_drawdown = quantiles(maxdds),
        .p_profit = ratio(profit_count, cfg.sims),
        .p_ruin = ratio(ruin_count, cfg.sims),
        .ruin_frac = cfg.ruin_frac,
    };
}

// A set of sample equity curves captured during resampling, for plotting a
// "spaghetti" chart in an external tool. Each path is a full resampled equity
// curve, downsampled to `n_steps` checkpoints (trade counts) so the data stays
// compact even for very long trade series. `equity` is row-major [n_paths][n_steps].
pub const Paths = struct {
    n_paths: usize,
    n_steps: usize,
    steps: []u32, // trade count (x-axis) at each checkpoint, len n_steps
    equity: []f64, // n_paths * n_steps, row-major

    pub fn deinit(self: *Paths, gpa: std.mem.Allocator) void {
        gpa.free(self.steps);
        gpa.free(self.equity);
        self.* = undefined;
    }
};

// Fill `steps` with strictly-increasing trade-count checkpoints spanning 0..n
// (step 0 = the starting balance), so each maps to a distinct x value.
fn buildCheckpoints(steps: []u32, n: usize) void {
    const k = steps.len;
    if (k == 1) {
        steps[0] = @intCast(n);
        return;
    }
    var prev: u32 = 0;
    for (0..k) |i| {
        const frac = @as(f64, @floatFromInt(i)) / @as(f64, @floatFromInt(k - 1));
        var ct: u32 = @intFromFloat(@round(frac * @as(f64, @floatFromInt(n))));
        if (i > 0 and ct <= prev) ct = prev + 1;
        if (ct > n) ct = @intCast(n);
        steps[i] = ct;
        prev = ct;
    }
}

fn quantiles(sorted: []const f64) Pcts {
    return .{
        q(sorted, 0.05), q(sorted, 0.25), q(sorted, 0.50), q(sorted, 0.75), q(sorted, 0.95),
    };
}

// Nearest-rank quantile on an ascending slice.
fn q(sorted: []const f64, p: f64) f64 {
    if (sorted.len == 0) return 0;
    const last: f64 = @floatFromInt(sorted.len - 1);
    const i: usize = @intFromFloat(@round(p * last));
    return sorted[i];
}

fn ratio(num: usize, den: usize) f64 {
    if (den == 0) return 0;
    return @as(f64, @floatFromInt(num)) / @as(f64, @floatFromInt(den));
}

fn historicalFinal(pnls: []const f64, initial_balance: f64) f64 {
    var equity = initial_balance;
    for (pnls) |x| equity += x;
    return equity;
}

fn historicalMaxDrawdown(pnls: []const f64, initial_balance: f64) f64 {
    var equity = initial_balance;
    var peak = initial_balance;
    var maxdd: f64 = 0;
    for (pnls) |x| {
        equity += x;
        if (equity > peak) peak = equity;
        if (peak > 0) {
            const dd = (peak - equity) / peak;
            if (dd > maxdd) maxdd = dd;
        }
    }
    return maxdd * 100.0;
}
