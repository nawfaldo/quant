// strategies/rth_vwap_config.zig
//
// LIVE-ONLY sizing config for the RTH-VWAP strategy in march. Edit the values
// in `config` below, then `zig build` to apply.
//
// The strategy LOGIC (and its RTH time windows 09:30 / 15:59 / 16:00) lives in
// the shared strategies/rth_vwap.zig, which the backtester and the live
// engine both run. This file carries only the parameters that differ between a
// backtest (driven by the Test-page request) and live trading — the order size.
// march_api.zig reads it in makeRthVwap() and applies it at construction; the
// backtester ignores it entirely.

const sizing = @import("../sizings/vol_target.zig");

pub const Config = struct {
    // ── SIZING — drives the live MT5 order volume ─────────────────────────────
    // Lots march sends per entry = contracts × leverage × (vol-target multiplier
    // if sizing_mode == .vol_target). This is the actual order size submitted to
    // MetaTrader 5; it overrides the Python VOLUME env.
    contracts: f64, // base lots
    leverage: f64, // multiplier on contracts (1.0 = off)
    sizing_mode: sizing.Mode, // .none (fixed lots) or .vol_target
    vol: sizing.VolTarget, // volatility-target params (consulted only if .vol_target)
};

pub const config = Config{
    .contracts = 0.01,
    .leverage = 5.0,
    .sizing_mode = .vol_target,
    .vol = .{
        .target = 0.20, // annualized vol the exposure is scaled toward
        .halflife = 20.0, // EWMA half-life in trading days
        .max_mult = 3.0, // cap on the size multiplier
        .min_days = 30, // warm-up: multiplier = 1 until this many daily returns
    },
};
