const std = @import("std");

// ── Configurable tune score ───────────────────────────────────────────────────
// Ranking score for a tuned combo. Isolated here on purpose: a new ranking
// formula is a one-function edit and never touches the engine, the worker pool,
// or the backtest math. The tuner calls `compute(metrics)`; everything else
// (CSV, summary, ranking endpoints) reads the resulting `score` field.
//
// Default formula (per the product spec):
//
//     score = sharpe * 100 + profitFactor * 50 - maxDrawdown * 2
//
// `max_drawdown` is a percentage of the running peak (e.g. 12.3 for 12.3%),
// exactly as the engine reports it — so the penalty term is "2 points of score
// per 1% of drawdown".

// All the metrics any formula might want. Extra fields are populated by the
// caller even though the default formula ignores them, so future formulas can
// use them without changing call sites.
pub const Metrics = struct {
    sharpe: f64,
    profit_factor: f64,
    max_drawdown: f64,
    return_pct: f64 = 0,
    expectancy: f64 = 0,
    win_rate: f64 = 0,
    avg_drawdown: f64 = 0,
};

// Named formulas. Add a variant + a switch arm below to introduce a new one.
pub const Formula = enum { default };

// The active formula. Swap this (or thread it through from a request param
// later) to change ranking globally.
pub const active: Formula = .default;

pub fn compute(m: Metrics) f64 {
    return computeWith(active, m);
}

pub fn computeWith(formula: Formula, m: Metrics) f64 {
    return switch (formula) {
        .default => san(m.sharpe) * 100.0 + san(m.profit_factor) * 50.0 - san(m.max_drawdown) * 2.0,
    };
}

// Guard against NaN/Inf leaking into the score (a zero-trade combo yields an
// infinite profit factor, for instance).
fn san(x: f64) f64 {
    return if (std.math.isFinite(x)) x else 0;
}
