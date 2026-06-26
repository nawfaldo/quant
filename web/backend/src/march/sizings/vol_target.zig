const std = @import("std");

// ── Position sizing ───────────────────────────────────────────────────────────
// Sizing mode selector, shared by the CLI, the tuner, and strategies.
//   .none       — fixed lots (base_lots × leverage), no scaling.
//   .vol_target — Harvey et al. (2018) volatility targeting (VolTarget below).
pub const Mode = enum { none, vol_target };

// ── Volatility targeting (Harvey, Hoyle, Korgaonkar, Rattray, Sargaison &
//    Van Hemert, "The Impact of Volatility Targeting", 2018) ──────────────────
// Scales lots inversely to recent realized volatility so each trade contributes
// a roughly constant volatility to the equity curve:
//
//     lots = base_lots × min(target / σ̂, max_mult)
//
// where σ̂ is an annualized EWMA of squared daily (close-to-close) returns — the
// paper's headline daily estimator (Section 2, Table 3), 20-day half-life by
// default. The EWMA only folds in returns through the PRIOR day's close, so it
// is known one day ahead of the entry (the paper's "known a full 24-hours ahead"
// rule) and never peeks. High realized vol → fewer lots; calm markets → more
// (up to the cap). Risk assets like NQ are the case where the paper finds vol
// targeting lifts the Sharpe ratio (leverage effect) and thins the left tail.
pub const VolTarget = struct {
    // ── Params ──
    target: f64 = 0.20, // annualized vol the exposure is scaled toward
    halflife: f64 = 20.0, // EWMA half-life in trading days
    max_mult: f64 = 3.0, // cap on the size multiplier (calm-market leverage)
    min_days: u32 = 30, // warm-up: multiplier = 1 until this many returns seen

    // ── State (per strategy instance; starts zeroed) ──
    ewma_var: f64 = 0.0, // EWMA of squared daily returns (zero-mean variance)
    prev_close: f64 = 0.0, // reference close for the daily return
    last_close: f64 = 0.0, // most recent close seen (this day's running close)
    days_seen: u32 = 0, // count of daily returns folded into the EWMA

    // Feed one bar's close. `day_changed` must be true on the first bar of a new
    // day, computed by the caller BEFORE it updates its own day marker. On a
    // rollover the just-completed day's close-to-close return is folded into the
    // EWMA (zero mean, squared returns — the paper's footnote 9).
    pub fn onBar(self: *VolTarget, close: f64, day_changed: bool) void {
        if (day_changed) {
            if (self.prev_close > 0.0 and self.last_close > 0.0) {
                const r = self.last_close / self.prev_close - 1.0;
                if (self.days_seen == 0) {
                    self.ewma_var = r * r; // seed the EWMA with the first return
                } else {
                    const lambda = std.math.pow(f64, 0.5, 1.0 / self.halflife);
                    self.ewma_var = lambda * self.ewma_var + (1.0 - lambda) * r * r;
                }
                self.days_seen += 1;
                self.prev_close = self.last_close;
            } else if (self.last_close > 0.0) {
                self.prev_close = self.last_close; // first close → reference only
            }
        }
        self.last_close = close;
    }

    // Lot multiplier to apply to the base size. 1.0 (base size) until warmed up.
    pub fn multiplier(self: *const VolTarget) f64 {
        if (self.days_seen < self.min_days or self.ewma_var <= 0.0) return 1.0;
        const sigma_ann = @sqrt(self.ewma_var * 252.0);
        if (sigma_ann <= 0.0) return 1.0;
        return @min(self.target / sigma_ann, self.max_mult);
    }
};
