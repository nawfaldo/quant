// march/zig/src/strategies/rth_vwap_config.zig
//
// Manual configuration for the RTH-VWAP strategy in march.
// Edit the values in `config` below, then run `zig build` to apply.
//
// This file is march-native — it is NOT shared with the backtester, so tuning
// it here changes only the live system. Every tunable parameter lives here;
// rth_vwap.zig reads its defaults and time windows straight from `config`.

const sizing = @import("../sizings/vol_target.zig");

pub const Config = struct {
    // ── SIGNAL PARAMETERS — these change what march actually trades, live ──────
    // Session window, in minutes since local midnight. The tick feed is
    // timestamped NY-wall-clock, so these are ET. `9 * 60 + 30` == 09:30.
    rth_open: u16, // first RTH bar — the entry side is decided here
    exit_time: u16, // emit .close from this minute onward (flatten into the close)
    rth_close: u16, // hard end of the trading window (exclusive)

    // ── SIZING — drives the live MT5 order volume ─────────────────────────────
    // The lots march sends per entry = contracts × leverage × (vol-target
    // multiplier, if sizing_mode == .vol_target). This is the actual order size
    // submitted to MetaTrader 5; it overrides the Python VOLUME env.
    contracts: f64, // base lots
    leverage: f64, // multiplier on contracts (1.0 = off)
    sizing_mode: sizing.Mode, // .none (fixed lots) or .vol_target
    vol: sizing.VolTarget, // volatility-target params (consulted only if .vol_target)
};

pub const config = Config{
    // ── Signal ──
    .rth_open = 9 * 60 + 30, // 09:30 ET
    .exit_time = 15 * 60 + 59, // 15:59 ET → flatten into the 16:00 close
    .rth_close = 16 * 60, // 16:00 ET

    // ── Sizing (drives the live order volume) ──
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
