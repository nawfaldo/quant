// march/zig/src/strategies/30m_buy_config.zig
//
// Manual configuration for the Opening-Range Breakout (orb_buy) strategy in march.
// Edit the values in `config` below, then run `zig build` to apply.
//
// march-native — NOT shared with the backtester. 30m_buy.zig reads its defaults,
// time windows, and the take-profit reward:risk straight from `config`.

const sizing = @import("../sizings/vol_target.zig");

pub const Config = struct {
    // ── SIGNAL PARAMETERS — these change what march actually trades, live ──────
    // Times are minutes since local midnight (ET; the feed is NY-wall-clock).
    rth_open: u16, // 09:30 — start of RTH
    range_def_end: u16, // 09:55 — opening range built from bars with ts < this (09:30–09:50)
    or_end: u16, // 10:00 — breakout is checked on the bar at 09:55 (ts < this)
    exit_time: u16, // 13:55 — time-stop: emit .close (fills next bar / 14:00)
    rth_close: u16, // 16:00 — window end (exclusive)
    rr_multiple: f64, // take-profit reward:risk. 1.0 == 1:1 (tp = entry + 1×risk)

    // ── SIZING — drives the live MT5 order volume ─────────────────────────────
    // Lots per entry = contracts × leverage × (vol-target multiplier if enabled).
    // Overrides the Python VOLUME env. See rth_vwap_config.zig for detail.
    contracts: f64,
    leverage: f64,
    sizing_mode: sizing.Mode,
    vol: sizing.VolTarget,
};

pub const config = Config{
    // ── Signal ──
    .rth_open = 9 * 60 + 30, // 09:30 ET
    .range_def_end = 9 * 60 + 55, // 09:55 ET
    .or_end = 10 * 60, // 10:00 ET
    .exit_time = 13 * 60 + 55, // 13:55 ET → fills at 14:00 open
    .rth_close = 16 * 60, // 16:00 ET
    .rr_multiple = 1.0, // 1:1 take-profit (original behavior)

    // ── Sizing (drives the live order volume) ──
    .contracts = 0.1,
    .leverage = 1.0,
    .sizing_mode = .vol_target,
    .vol = .{
        .target = 0.20,
        .halflife = 20.0,
        .max_mult = 3.0,
        .min_days = 30,
    },
};
