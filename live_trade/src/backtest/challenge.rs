//! The FundedNext $6,000 two-step challenge, from every real start day: a port
//! of `sandbox/research/fundednext_challenge.py` (`challenge`, `judge`,
//! `worst_marks`, `summarise`) over `exness_combined_strategies.replay`.
//!
//! THE RULES. Phase 1 +$480, phase 2 +$300 on a fresh $6,000; the daily floor is
//! the CLOSED balance at the 17:00 New York rollover less $300, the static floor
//! $5,400; five trading days a phase. A pass needs the closed balance AND the
//! marked equity at the target. Loss limits are judged with every open position
//! at the worst price its 30-minute bar touched (`intrabar`).
//!
//! THE REPLAY IS PYTHON'S, QUIRKS INCLUDED, because the numbers are compared
//! with the research's to the start: one grid of every series' 30-minute bar
//! stamps and every trade stamp, each on its OWN series clock (an `off` or
//! window series runs +7h), settled then opened at each stamp and marked only
//! where the position's series printed. The server day is that raw stamp + 7h.
//! A day counts as traded from its first mark when ANY entry falls on it, and
//! the balance a mark reads is the largest `(stamp, balance)` of the sorted
//! close curve -- both exactly as `judge` reads them.
//!
//! Each start replays the whole remaining window, re-sizing every log trade off
//! that start's own $6,000 (`book_lots`, the book's rule), as `run_book` does.

use std::collections::{HashMap, HashSet};

use serde::Serialize;

use crate::strategies::cfd::{Sleeve, book_lots};

use super::types::{Side, Trade};

pub const ACCOUNT: f64 = 6_000.0;
const PHASE_TARGETS: [f64; 2] = [480.0, 300.0];
const DAILY_LOSS: f64 = 300.0;
const MAX_LOSS: f64 = 600.0;
const MIN_DAYS: usize = 5;
const SERVER_OFFSET: i64 = 7 * 3_600;
const DAY: i64 = 86_400;
const LOT_STEP: f64 = 0.01;

fn server_day(ts: i64) -> i64 {
    (ts + SERVER_OFFSET).div_euclid(DAY)
}

fn day_start(day: i64) -> i64 {
    day * DAY - SERVER_OFFSET
}

/// One log trade, on its series' clock, with what re-sizes it.
#[derive(Clone)]
pub struct LogTrade {
    pub sleeve: Sleeve,
    pub series: usize,
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub side: f64,
    pub entry: f64,
    /// Net points per unit: the trade's P&L over its lots and point value.
    pub points: f64,
    pub distance: f64,
    pub mult: f64,
}

/// One series' 30-minute bars on its own clock: `(close, high, low)`.
pub type SeriesBars = HashMap<i64, (f64, f64, f64)>;

pub struct Book {
    trades: Vec<LogTrade>,
    series: Vec<SeriesBars>,
    /// Every series' bar stamps and every trade stamp, ascending.
    grid: Vec<i64>,
    marks: HashSet<i64>,
}

impl Book {
    /// `trades` from a whole-book run (real New York stamps); `series` and
    /// `series_of` (sleeve -> series index, with its clock shift) from the run's
    /// bars. Trades whose exit is not before `hi` are dropped, as `load_state`
    /// drops them.
    pub fn new(trades: &[Trade], series: Vec<SeriesBars>, series_of: impl Fn(Sleeve) -> Option<usize>, hi: i64) -> Self {
        let mut logs = Vec::new();
        for trade in trades {
            let Some(sleeve) = Sleeve::from_display(trade.strategy_name()) else {
                continue;
            };
            let Some(index) = series_of(sleeve) else {
                continue;
            };
            let shift = sleeve.shift_hours() * 3_600;
            let exit_ts = trade.exit_timestamp + shift;
            if exit_ts >= hi || trade.quantity <= 0.0 || !trade.distance.is_finite() {
                continue;
            }
            logs.push(LogTrade {
                sleeve,
                series: index,
                entry_ts: trade.entry_timestamp + shift,
                exit_ts,
                side: if trade.side == Side::Long { 1.0 } else { -1.0 },
                entry: trade.entry_raw,
                points: trade.pnl / (trade.quantity * sleeve.point_value()),
                distance: trade.distance,
                mult: trade.vol_mult,
            });
        }
        // `pending.sort(key=lambda t: (t["entry_ts"], t["sleeve"]))`.
        logs.sort_by(|a, b| {
            (a.entry_ts, a.sleeve.python_key()).cmp(&(b.entry_ts, b.sleeve.python_key()))
        });
        let marks: HashSet<i64> = series.iter().flat_map(|bars| bars.keys().copied()).collect();
        let mut grid: Vec<i64> = marks
            .iter()
            .copied()
            .chain(logs.iter().flat_map(|t| [t.entry_ts, t.exit_ts]))
            .collect();
        grid.sort_unstable();
        grid.dedup();
        Self {
            trades: logs,
            series,
            grid,
            marks,
        }
    }

    /// One phase from `lo` to `hi`: `(outcome, at, worst daily, worst total)`.
    fn phase(&self, lo: i64, hi: i64, target: f64) -> (&'static str, Option<i64>, f64, f64) {
        struct Open {
            trade: usize,
            lots: f64,
            pnl: f64,
        }
        let pending: Vec<usize> = (0..self.trades.len())
            .filter(|&i| lo <= self.trades[i].entry_ts && self.trades[i].entry_ts < hi)
            .collect();
        let entry_days: Vec<i64> = {
            let mut days: Vec<i64> = pending.iter().map(|&i| server_day(self.trades[i].entry_ts)).collect();
            days.sort_unstable();
            days.dedup();
            days
        };
        let mut equity = ACCOUNT;
        let mut open: Vec<Open> = Vec::new();
        let mut next = 0usize;
        // The sorted close curve's maximum `(stamp, balance)` so far.
        let mut balance = ACCOUNT;
        let mut balance_ts = i64::MIN;
        let mut traded = 0usize;
        let mut ei = 0usize;
        let mut day = None;
        let mut reference = ACCOUNT;
        let (mut worst_daily, mut worst_total) = (0.0_f64, 0.0_f64);
        let floor = ACCOUNT - MAX_LOSS;
        let goal = ACCOUNT + target;
        let start = self.grid.partition_point(|stamp| *stamp < lo);
        for &stamp in &self.grid[start..] {
            if stamp >= hi {
                break;
            }
            // 1. settle
            let mut still = Vec::with_capacity(open.len());
            for position in open.drain(..) {
                let trade = &self.trades[position.trade];
                if trade.exit_ts <= stamp {
                    equity += position.pnl;
                    if trade.exit_ts > balance_ts || (trade.exit_ts == balance_ts && equity > balance) {
                        balance_ts = trade.exit_ts;
                        balance = equity;
                    }
                } else {
                    still.push(position);
                }
            }
            open = still;
            // 2. open, sized off the balance the closes left
            while next < pending.len() && self.trades[pending[next]].entry_ts <= stamp {
                let index = pending[next];
                next += 1;
                let trade = &self.trades[index];
                let contract = trade.sleeve.contract();
                let Some(lots) = book_lots(
                    trade.sleeve,
                    &contract,
                    LOT_STEP,
                    equity,
                    trade.entry,
                    trade.distance,
                    trade.mult,
                ) else {
                    continue;
                };
                if lots <= 0.0 {
                    continue;
                }
                open.push(Open {
                    trade: index,
                    lots,
                    pnl: trade.points * lots * contract.multiplier,
                });
            }
            // 3. mark, and judge the mark
            if !self.marks.contains(&stamp) {
                continue;
            }
            let (mut close_value, mut worst_value) = (0.0, 0.0);
            for position in &open {
                let trade = &self.trades[position.trade];
                let Some(&(close, high, low)) = self.series[trade.series].get(&stamp) else {
                    continue;
                };
                let money = position.lots * trade.sleeve.point_value();
                close_value += trade.side * (close - trade.entry) * money;
                let extreme = if trade.side > 0.0 { low } else { high };
                worst_value += trade.side * (extreme - trade.entry) * money;
            }
            let marked = equity + close_value;
            let low = marked.min(balance + worst_value);
            while ei < entry_days.len() && day_start(entry_days[ei]) <= stamp {
                traded += 1;
                ei += 1;
            }
            let today = server_day(stamp);
            if day != Some(today) {
                day = Some(today);
                reference = balance;
            }
            worst_daily = worst_daily.max(reference - low);
            worst_total = worst_total.max(ACCOUNT - low);
            if low <= floor {
                return ("max", Some(stamp), worst_daily, worst_total);
            }
            if low <= reference - DAILY_LOSS {
                return ("daily", Some(stamp), worst_daily, worst_total);
            }
            if balance >= goal && marked >= goal && traded >= MIN_DAYS {
                return ("pass", Some(stamp), worst_daily, worst_total);
            }
        }
        ("open", None, worst_daily, worst_total)
    }
}

/// One phase's result.
#[derive(Clone, Serialize)]
pub struct Phase {
    pub outcome: &'static str,
    pub calendar_days: Option<f64>,
    pub worst_daily: f64,
    pub worst_total: f64,
}

/// Python's `round(x, 1)` for the values this meets.
fn round1(value: f64) -> f64 {
    (value * 10.0).round_ties_even() / 10.0
}

/// `challenge`: phase 1 from `start`, phase 2 from the server day after it passes.
pub fn challenge(book: &Book, start: i64, end: i64) -> Vec<Phase> {
    let mut out = Vec::new();
    let mut lo = start;
    for target in PHASE_TARGETS {
        let (outcome, at, worst_daily, worst_total) = book.phase(lo, end, target);
        out.push(Phase {
            outcome,
            calendar_days: at.map(|at| round1((at - lo) as f64 / DAY as f64)),
            worst_daily,
            worst_total,
        });
        let Some(at) = at.filter(|_| outcome == "pass") else {
            break;
        };
        lo = day_start(server_day(at) + 1);
        if lo >= end && out.len() == 1 {
            out.push(Phase {
                outcome: "open",
                calendar_days: None,
                worst_daily: 0.0,
                worst_total: 0.0,
            });
            break;
        }
    }
    out
}

/// `starts_of(lo, hi, 1)`: every weekday server-day open in `(lo, hi)`.
pub fn starts_of(lo: i64, hi: i64) -> Vec<i64> {
    (server_day(lo) + 1..server_day(hi))
        .map(day_start)
        .filter(|stamp| ((stamp + SERVER_OFFSET).div_euclid(DAY) + 3).rem_euclid(7) < 5)
        .collect()
}

/// `summarise`, plus the deepest drawdown seen.
#[derive(Clone, Serialize)]
pub struct Summary {
    pub starts: usize,
    pub resolved: usize,
    pub pass_both_pct: Option<f64>,
    pub fail_daily: usize,
    pub fail_max: usize,
    pub open: usize,
    pub both_median_days: Option<f64>,
    pub worst_daily_seen: f64,
    pub deepest_seen: f64,
}

pub fn summarise(rolls: &[Vec<Phase>]) -> Summary {
    let resolved: Vec<&Vec<Phase>> = rolls
        .iter()
        .filter(|r| r[0].outcome != "open" && (r[0].outcome != "pass" || r.get(1).is_some_and(|p| p.outcome != "open")))
        .collect();
    let both: Vec<&&Vec<Phase>> = resolved
        .iter()
        .filter(|r| r[0].outcome == "pass" && r.get(1).is_some_and(|p| p.outcome == "pass"))
        .collect();
    let failed = |kind: &str| {
        resolved
            .iter()
            .filter(|r| !(r[0].outcome == "pass" && r.get(1).is_some_and(|p| p.outcome == "pass")))
            .filter(|r| r.iter().any(|p| p.outcome == kind))
            .count()
    };
    let mut days: Vec<f64> = both
        .iter()
        .filter_map(|r| Some(r[0].calendar_days? + r[1].calendar_days?))
        .collect();
    days.sort_by(f64::total_cmp);
    let median = (!days.is_empty()).then(|| {
        let n = days.len();
        round1(if n % 2 == 1 { days[n / 2] } else { (days[n / 2 - 1] + days[n / 2]) / 2.0 })
    });
    let phases = rolls.iter().flat_map(|r| r.iter());
    let worst_daily = phases.clone().map(|p| p.worst_daily).fold(0.0, f64::max);
    let deepest = phases.map(|p| p.worst_total).fold(0.0, f64::max);
    Summary {
        starts: rolls.len(),
        resolved: resolved.len(),
        pass_both_pct: (!resolved.is_empty()).then(|| round1(100.0 * both.len() as f64 / resolved.len() as f64)),
        fail_daily: failed("daily"),
        fail_max: failed("max"),
        open: rolls.len() - resolved.len(),
        both_median_days: median,
        worst_daily_seen: (worst_daily * 100.0).round() / 100.0,
        deepest_seen: (deepest * 100.0).round() / 100.0,
    }
}

/// Every real start day in `[lo, hi)` through both phases.
pub fn run(book: &Book, lo: i64, hi: i64) -> Summary {
    let rolls: Vec<Vec<Phase>> = starts_of(lo, hi).into_iter().map(|start| challenge(book, start, hi)).collect();
    summarise(&rolls)
}
