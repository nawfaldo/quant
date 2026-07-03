// strategies/zara_momentum_config.zig
//
// Tunable defaults for the Zara Momentum (Noise Area) strategy. Edit the
// values in `config` below, then run `zig build` to apply.
//
// zara_momentum.zig reads these as its struct-field defaults. The backtest
// (Test page) drives the strategy and OVERRIDES the sizing fields the request
// carries — initial_balance, contracts, leverage, sizing_mode, vol (see
// bt/run.zig's runStrategy). The signal params (vm, lookback), which the request
// does NOT send, are taken from here.

const sizing = @import("../sizings/vol_target.zig");

pub const Config = struct {
    // ── SIGNAL PARAMETERS (Zarattini/Aziz/Barbon paper defaults) ───────────────
    vm: f64, // Volatility Multiplier — noise-area band width scaler
    lookback: u32, // trading days averaged into each slot's σ (≤ 200)

    // ── SIZING DEFAULTS ─────────────────────────────────────────────────────────
    // In a backtest the Test-page request overrides initial_balance / contracts /
    // leverage / sizing_mode / vol.
    initial_balance: f64,
    contracts: f64,
    leverage: f64,
    sizing_mode: sizing.Mode,
    vol: sizing.VolTarget,
};

pub const config = Config{
    // ── Signal (paper defaults: VM = 1.0, lookback = 14 days) ──
    .vm = 1.0,
    .lookback = 14,

    // ── Sizing ──
    .initial_balance = 10_000.0,
    .contracts = 1.0,
    .leverage = 1.0,
    .sizing_mode = .none,
    .vol = .{},
};
