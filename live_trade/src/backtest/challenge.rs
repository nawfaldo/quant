//! The FundedNext $6,000 two-step challenge MONTE CARLO -- the research's
//! default report (`fundednext_combined_strategies._grid_table`), ported from
//! `fundednext_challenge.daily_series` / `mc_paths` / `mc_stats` and
//! `fundednext_combined_strategies.stressed_paths` / `_grid_job` / `_dud`.
//!
//! THE GRID. SYMBOLS ALIVE (the share of symbols that keep their full edge;
//! every strategy on the others has none, `GRID_DRAWS` draws) x EDGE KEPT (the
//! share of its edge every surviving strategy keeps), `REPORT_PATHS` challenges
//! a cell, each a chain of 10-day blocks resampled from the book's server days.
//!
//! THE RULES. Phase 1 +$480, phase 2 +$300 on a fresh $6,000; the daily floor is
//! the CLOSED balance at the 17:00 New York rollover less $300, the static floor
//! $5,400; five trading days a phase. Loss limits are judged with every open
//! position at the worst price its 30-minute bar touched.
//!
//! BIT FOR BIT WITH THE RESEARCH, which is why its quirks are kept: one grid of
//! every series' 30-minute bar stamps and every trade stamp, each on its OWN
//! series clock (an `off` or window series runs +7h); the server day is that
//! raw stamp + 7h; the balance a day opens on is the last entry of the SORTED
//! `(stamp, balance)` close curve before the rollover; and the dice are
//! CPython's (`pyrandom`), seeded as the research seeds them.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::Serialize;
use serde_json::{Value, json};

use crate::strategies::cfd::{Sleeve, book_lots};

use super::pyrandom::PyRandom;
use super::types::{Side, Trade};

pub const ACCOUNT: f64 = 6_000.0;
const PHASE_TARGETS: [f64; 2] = [480.0, 300.0];
const DAILY_LOSS: f64 = 300.0;
const MAX_LOSS: f64 = 600.0;
const MIN_DAYS: usize = 5;
const SERVER_OFFSET: i64 = 7 * 3_600;
const DAY: i64 = 86_400;
const LOT_STEP: f64 = 0.01;
const BLOCK_DAYS: usize = 10;
const MAX_DAYS: usize = 500;
const WITHIN: [usize; 3] = [20, 40, 60];
pub const SYMBOLS_ALIVE: [f64; 4] = [1.0, 0.75, 0.5, 0.25];
pub const EDGE_KEPT: [f64; 4] = [1.0, 0.75, 0.5, 0.25];
pub const GRID_DRAWS: usize = 20;
pub const REPORT_PATHS: usize = 10_000;
/// `test`'s seed for the full-symbol rows; the dud draws use `7000 + draw`.
pub const SEED: u64 = 2026;

fn server_day(ts: i64) -> i64 {
    (ts + SERVER_OFFSET).div_euclid(DAY)
}

fn day_start(day: i64) -> i64 {
    day * DAY - SERVER_OFFSET
}

/// One log trade, on its series' clock, with what re-sizes it.
#[derive(Clone)]
struct LogTrade {
    sleeve: Sleeve,
    series: usize,
    entry_ts: i64,
    exit_ts: i64,
    side: f64,
    entry: f64,
    points: f64,
    distance: f64,
    mult: f64,
}

/// One series' 30-minute bars on its own clock: `(close, high, low)`.
pub type SeriesBars = HashMap<i64, (f64, f64, f64)>;

/// The book's log and marks over one window `[lo, hi)`.
pub struct Book {
    trades: Vec<LogTrade>,
    series: Vec<SeriesBars>,
    grid: Vec<i64>,
    marks: HashSet<i64>,
    lo: i64,
    hi: i64,
}

impl Book {
    /// `trades` from a whole-book run (real New York stamps); `series_of` maps a
    /// sleeve to its marking series. The log is `load_state`'s: every trade
    /// closed before `hi`, however early it entered -- each sleeve's mean R (the
    /// edge a cut takes away) is read over all of it -- and the replay opens only
    /// those entered from `lo`.
    pub fn new(
        trades: &[Trade],
        series: Vec<(SeriesBars, BTreeMap<i64, (f64, f64, f64)>)>,
        series_of: impl Fn(Sleeve) -> Option<usize>,
        lo: i64,
        hi: i64,
    ) -> Self {
        // `load_state` keeps marking bars from `WARMUP_DAYS` (180) before the
        // window's own start, so each window's throttle warms from there.
        let multipliers: Vec<HashMap<i64, f64>> = series
            .iter()
            .map(|(_, marks)| daily_multipliers(marks, lo - 180 * DAY))
            .collect();
        let series: Vec<SeriesBars> = series.into_iter().map(|(bars, _)| bars).collect();
        let mut logs = Vec::new();
        for trade in trades {
            let Some(sleeve) = Sleeve::from_display(trade.strategy_name()) else {
                continue;
            };
            let Some(index) = series_of(sleeve) else {
                continue;
            };
            let shift = sleeve.shift_hours() * 3_600;
            let (entry_ts, exit_ts) = (trade.entry_timestamp + shift, trade.exit_timestamp + shift);
            if exit_ts >= hi || trade.quantity <= 0.0 || !trade.distance.is_finite() {
                continue;
            }
            logs.push(LogTrade {
                sleeve,
                series: index,
                entry_ts,
                exit_ts,
                side: if trade.side == Side::Long { 1.0 } else { -1.0 },
                entry: trade.entry_raw,
                points: if trade.points.is_finite() {
                    trade.points
                } else {
                    trade.pnl / (trade.quantity * sleeve.point_value())
                },
                distance: trade.distance,
                mult: multipliers[index].get(&entry_ts.div_euclid(DAY)).copied().unwrap_or(1.0),
            });
        }
        logs.sort_by(|a, b| (a.entry_ts, a.sleeve.python_key()).cmp(&(b.entry_ts, b.sleeve.python_key())));
        let marks: HashSet<i64> = series
            .iter()
            .flat_map(|bars| bars.keys().copied())
            .filter(|stamp| lo <= *stamp && *stamp < hi)
            .collect();
        let mut grid: Vec<i64> = marks
            .iter()
            .copied()
            .chain(logs.iter().filter(|t| t.entry_ts >= lo).flat_map(|t| [t.entry_ts, t.exit_ts]))
            .filter(|stamp| lo <= *stamp && *stamp < hi)
            .collect();
        grid.sort_unstable();
        grid.dedup();
        Self { trades: logs, series, grid, marks, lo, hi }
    }

    /// `_dud(log, keep)` for every sleeve: each trade's points lose
    /// `(1 - keep)` of its sleeve's mean R times its own stop distance.
    fn points_with(&self, cut: &dyn Fn(Sleeve) -> f64) -> Vec<f64> {
        // Each sleeve's R multiples in log order, summed as CPython's `sum()`
        // sums floats (compensated, 3.12+), so the cut is Python's to the bit.
        let mut by_sleeve: HashMap<&str, Vec<f64>> = HashMap::new();
        for trade in &self.trades {
            if trade.distance > 0.0 {
                by_sleeve
                    .entry(trade.sleeve.python_key())
                    .or_default()
                    .push(trade.points / trade.distance);
            }
        }
        let mean_r: HashMap<&str, f64> = by_sleeve
            .into_iter()
            .map(|(key, r)| (key, crate::strategies::cfd::python_fsum_like(r.iter().copied()) / r.len() as f64))
            .collect();
        self.trades
            .iter()
            .map(|trade| {
                let share = cut(trade.sleeve);
                if share == 0.0 || trade.distance <= 0.0 {
                    return trade.points;
                }
                trade.points - share * mean_r[trade.sleeve.python_key()] * trade.distance
            })
            .collect()
    }

    /// `daily_series(state, risk, fixed=True)`: per server day, `(return, worst
    /// dip, traded, reference gap)` as fractions of $6,000.
    fn daily_series(&self, points: &[f64], risk: f64) -> Vec<(f64, f64, bool, f64)> {
        let _ = risk; // the dial is each sleeve's own `Book::risk_scale`
        struct Open {
            trade: usize,
            lots: f64,
            pnl: f64,
        }
        let mut equity = ACCOUNT;
        let mut open: Vec<Open> = Vec::new();
        let mut next = self.trades.partition_point(|t| t.entry_ts < self.lo);
        // The close curve, kept sorted as Python sorts `(stamp, balance)`.
        let mut curve: Vec<(i64, f64)> = Vec::new();
        let mut traded: HashSet<i64> = HashSet::new();
        let mut marked: Vec<(i64, f64, f64)> = Vec::new();
        for &stamp in &self.grid {
            let mut still = Vec::with_capacity(open.len());
            for position in open.drain(..) {
                if self.trades[position.trade].exit_ts <= stamp {
                    equity += position.pnl;
                    curve.push((self.trades[position.trade].exit_ts, equity));
                } else {
                    still.push(position);
                }
            }
            open = still;
            while next < self.trades.len() && self.trades[next].entry_ts <= stamp {
                let trade = &self.trades[next];
                let index = next;
                next += 1;
                let contract = trade.sleeve.contract();
                let Some(lots) = book_lots(
                    trade.sleeve,
                    &contract,
                    LOT_STEP,
                    equity,
                    trade.entry,
                    trade.distance,
                    trade.mult,
                    Some(ACCOUNT),
                )
                .filter(|lots| *lots > 0.0) else {
                    continue;
                };
                traded.insert(server_day(trade.entry_ts));
                open.push(Open {
                    trade: index,
                    lots,
                    pnl: points[index] * lots * contract.multiplier,
                });
            }
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
            marked.push((stamp, equity + close_value, worst_value));
        }
        curve.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
        // `worst_marks`: the balance is the sorted curve's last entry AT OR
        // before the mark.
        let mut ci = 0usize;
        let mut balance = ACCOUNT;
        let lows: Vec<f64> = marked
            .iter()
            .map(|&(stamp, value, worst)| {
                while ci < curve.len() && curve[ci].0 <= stamp {
                    balance = curve[ci].1;
                    ci += 1;
                }
                value.min(balance + worst)
            })
            .collect();
        let frac = |value: f64, opened: f64| (value - opened) / ACCOUNT;
        let mut days = Vec::new();
        let (mut prev, mut balance, mut ci) = (ACCOUNT, ACCOUNT, 0usize);
        let mut current: Option<i64> = None;
        let (mut opened, mut low, mut close, mut gap) = (0.0_f64, 0.0_f64, 0.0_f64, 0.0_f64);
        for (&(stamp, value, _), &worst) in marked.iter().zip(&lows) {
            let day = server_day(stamp);
            if current != Some(day) {
                if let Some(day) = current {
                    days.push((frac(close, opened), frac(low.min(opened), opened), traded.contains(&day), gap));
                    prev = close;
                }
                while ci < curve.len() && curve[ci].0 < day_start(day) {
                    balance = curve[ci].1;
                    ci += 1;
                }
                current = Some(day);
                opened = prev;
                low = worst;
                gap = frac(balance, opened);
            }
            low = low.min(worst);
            close = value;
        }
        if let Some(day) = current {
            days.push((frac(close, opened), frac(low.min(opened), opened), traded.contains(&day), gap));
        }
        days
    }
}

/// One path: `(outcome, trading days, worst day $, deepest $)`.
type Row = (&'static str, usize, f64, f64);

fn phase(days: &[(f64, f64, bool, f64)], rng: &mut PyRandom, target: f64) -> Row {
    let (mut equity, mut used, mut traded) = (ACCOUNT, 0usize, 0usize);
    let (mut worst_day, mut deepest) = (0.0_f64, 0.0_f64);
    let (goal, floor) = (ACCOUNT + target, ACCOUNT - MAX_LOSS);
    while used < MAX_DAYS {
        let start = rng.randrange(days.len() - BLOCK_DAYS);
        for &(ret, dip, did, gap) in &days[start..start + BLOCK_DAYS] {
            used += 1;
            let low = equity * (1.0 + dip);
            let reference = equity * (1.0 + gap);
            worst_day = worst_day.max(reference - low);
            deepest = deepest.max(ACCOUNT - low);
            if low <= floor {
                return ("max", used, worst_day, deepest);
            }
            if low <= reference - DAILY_LOSS {
                return ("daily", used, worst_day, deepest);
            }
            equity *= 1.0 + ret;
            traded += usize::from(did);
            if equity <= floor {
                return ("max", used, worst_day, deepest);
            }
            if equity >= goal && traded >= MIN_DAYS {
                return ("pass", used, worst_day, deepest);
            }
        }
    }
    ("stuck", used, worst_day, deepest)
}

/// `mc_paths`.
fn paths(days: &[(f64, f64, bool, f64)], count: usize, seed: u64) -> Vec<Row> {
    // A window shorter than one resampled block has no path to draw (Python's
    // `randrange` raises); report nothing rather than panic.
    if days.len() <= BLOCK_DAYS {
        return Vec::new();
    }
    let mut rng = PyRandom::from_int(seed);
    (0..count)
        .map(|_| {
            let (mut total, mut worst, mut deep) = (0usize, 0.0_f64, 0.0_f64);
            let mut outcome = "pass";
            for target in PHASE_TARGETS {
                let (o, used, day, deepest) = phase(days, &mut rng, target);
                total += used;
                worst = worst.max(day);
                deep = deep.max(deepest);
                outcome = o;
                if o != "pass" {
                    break;
                }
            }
            (outcome, total, worst, deep)
        })
        .collect()
}

/// Python's `round(x, places)`: correctly rounded on the EXACT binary value
/// (so 0.05, stored just above it, rounds up), which is what Rust's formatter
/// does too -- scaling first and rounding ties would not be.
fn round_to(value: f64, places: i32) -> f64 {
    format!("{value:.*}", places.max(0) as usize).parse().unwrap_or(value)
}

/// `mc_stats`.
#[derive(Clone, Serialize)]
pub struct Stats {
    pub pass: f64,
    pub daily: f64,
    pub max: f64,
    pub stuck: f64,
    pub paths: usize,
    pub pass_se: f64,
    pub median_trading_days: Option<usize>,
    pub p90_trading_days: Option<usize>,
    pub p75_trading_days: Option<usize>,
    pub p95_trading_days: Option<usize>,
    pub within: BTreeMap<usize, f64>,
    pub expected_days: Option<f64>,
    pub worst_day_50: Option<f64>,
    pub worst_day_75: Option<f64>,
    pub worst_day_95: Option<f64>,
    pub deepest_50: Option<f64>,
    pub deepest_75: Option<f64>,
    pub deepest_95: Option<f64>,
}

fn stats(rows: &[Row]) -> Stats {
    fn pct<T: Copy + PartialOrd>(mut values: Vec<T>, q: f64) -> Option<T> {
        values.sort_by(|a, b| a.partial_cmp(b).expect("finite"));
        (!values.is_empty()).then(|| values[(values.len() - 1).min((q * values.len() as f64) as usize)])
    }
    let n = rows.len();
    let won: Vec<&Row> = rows.iter().filter(|r| r.0 == "pass").collect();
    let p = won.len() as f64 / n as f64;
    let share = |kind: &str| round_to(100.0 * rows.iter().filter(|r| r.0 == kind).count() as f64 / n as f64, 1);
    let days: Vec<usize> = won.iter().map(|r| r.1).collect();
    Stats {
        pass: share("pass"),
        daily: share("daily"),
        max: share("max"),
        stuck: share("stuck"),
        paths: n,
        pass_se: round_to(100.0 * (p * (1.0 - p) / n as f64).sqrt(), 2),
        median_trading_days: pct(days.clone(), 0.5),
        p90_trading_days: pct(days.clone(), 0.9),
        p75_trading_days: pct(days.clone(), 0.75),
        p95_trading_days: pct(days.clone(), 0.95),
        within: WITHIN
            .iter()
            .map(|&d| (d, round_to(100.0 * days.iter().filter(|x| **x <= d).count() as f64 / n as f64, 1)))
            .collect(),
        expected_days: (p > 0.0).then(|| round_to(rows.iter().map(|r| r.1).sum::<usize>() as f64 / n as f64 / p, 1)),
        worst_day_50: pct(won.iter().map(|r| r.2).collect(), 0.5).map(|v| round_to(v, 0)),
        worst_day_75: pct(won.iter().map(|r| r.2).collect(), 0.75).map(|v| round_to(v, 0)),
        worst_day_95: pct(won.iter().map(|r| r.2).collect(), 0.95).map(|v| round_to(v, 0)),
        deepest_50: pct(won.iter().map(|r| r.3).collect(), 0.5).map(|v| round_to(v, 0)),
        deepest_75: pct(won.iter().map(|r| r.3).collect(), 0.75).map(|v| round_to(v, 0)),
        deepest_95: pct(won.iter().map(|r| r.3).collect(), 0.95).map(|v| round_to(v, 0)),
    }
}

/// `stressed_paths(state, risk, paths, share, 0, draws, keep)`: in each draw the
/// sleeves on a dud SYMBOL lose all their edge, the rest keep `keep` of theirs.
fn stressed(book: &Book, risk: f64, count: usize, share: f64, draws: usize, keep: f64) -> Vec<Row> {
    let symbol = |s: Sleeve| s.python_key().split(':').next().unwrap_or_default().to_owned();
    let symbols: HashSet<String> = book.trades.iter().map(|t| symbol(t.sleeve)).collect();
    let duds = (share * draws as f64).round_ties_even() as usize;
    let dud_in: HashMap<String, HashSet<usize>> = symbols
        .into_iter()
        .map(|s| {
            let picked = PyRandom::from_str(&s).sample_range(draws, duds).into_iter().collect();
            (s, picked)
        })
        .collect();
    let per = (count / draws).max(1);
    let mut rows = Vec::new();
    for draw in 0..draws {
        let points = book.points_with(&|sleeve| {
            if dud_in[&symbol(sleeve)].contains(&draw) {
                1.0
            } else {
                1.0 - keep
            }
        });
        let days = book.daily_series(&points, risk);
        rows.extend(paths(&days, per, 7_000 + draw as u64));
    }
    rows
}

/// One grid cell: `_grid_job`.
pub fn cell(book: &Book, risk: f64, alive: f64, keep: f64, count: usize) -> Stats {
    let rows = if alive == 1.0 {
        let points = book.points_with(&|_| 1.0 - keep);
        paths(&book.daily_series(&points, risk), count, SEED)
    } else {
        stressed(book, risk, count, 1.0 - alive, GRID_DRAWS, keep)
    };
    stats(&rows)
}

/// The whole default report: every `(symbols alive, edge kept)` cell, keyed
/// `"alive/keep"` as the research's test log keys them.
pub fn grid(book: &Book, risk: f64, count: usize) -> Value {
    let mut out = serde_json::Map::new();
    for alive in SYMBOLS_ALIVE {
        for keep in EDGE_KEPT {
            let stats = cell(book, risk, alive, keep, count);
            out.insert(format!("{alive}/{keep}"), json!(stats));
        }
    }
    json!({"from": book.lo, "to": book.hi, "paths": count, "cells": out})
}

/// The report a FundedNext whole-book run carries: one grid per window, its
/// marks built from the run's own bars as `load_state` builds `bars_by` --
/// 30-minute buckets on each series' clock, broker-quoted at 30 minutes
/// (`CFD_BROKER_HOURS`), in-session for a cash-session series, every bar of
/// the broker day for an `off` or window one.
pub(crate) fn report(
    prepared: &super::prepare::PreparedRun,
    names: &[String],
    trades: &[Trade],
    windows: &[(i64, i64)],
    paths: usize,
) -> Value {
    let mut series_of: HashMap<&'static str, usize> = HashMap::new();
    let mut series: Vec<(SeriesBars, BTreeMap<i64, (f64, f64, f64)>)> = Vec::new();
    let mut built: HashMap<usize, usize> = HashMap::new();
    for (index, name) in names.iter().enumerate() {
        let Some(sleeve) = Sleeve::from_display(name) else {
            continue;
        };
        let source = prepared.strategy_symbols.get(index).copied().unwrap_or(0);
        let at = *built.entry(source).or_insert_with(|| {
            let marks = series_marks(prepared, sleeve, source);
            let kept: (SeriesBars, BTreeMap<i64, (f64, f64, f64)>) = (marks.clone().into_iter().collect(), marks);
            series.push(kept);
            series.len() - 1
        });
        series_of.insert(sleeve.python_key(), at);
    }
    let risk = crate::strategies::cfd::Book::FundedNext.risk_scale();
    let grids: Vec<Value> = windows
        .iter()
        .map(|&(lo, hi)| {
            let book = Book::new(trades, series.clone(), |sleeve| series_of.get(sleeve.python_key()).copied(), lo, hi);
            grid(&book, risk, paths)
        })
        .collect();
    json!(grids)
}

/// One series' 30-minute bars as the research's `bars_by` holds them: buckets
/// on the series' clock, broker-quoted at 30 minutes, in-session for a
/// cash-session series, every bar for an `off` or window one.
pub(crate) fn series_marks(
    prepared: &super::prepare::PreparedRun,
    sleeve: Sleeve,
    source: usize,
) -> BTreeMap<i64, (f64, f64, f64)> {
    let bars = prepared.symbol_bars.get(source).unwrap_or(&prepared.bars);
    let shift = sleeve.shift_hours() * 3_600;
    let fills = prepared
        .fills
        .get(&super::prepare::fill_key(sleeve.market(), sleeve.shift_hours()));
    let mut buckets: BTreeMap<i64, (f64, f64, f64)> = BTreeMap::new();
    for bar in bars {
        let bucket = (bar.ts + shift).div_euclid(1_800) * 1_800;
        buckets
            .entry(bucket)
            .and_modify(|(close, high, low)| {
                *close = bar.close;
                *high = high.max(bar.high);
                *low = low.min(bar.low);
            })
            .or_insert((bar.close, bar.high, bar.low));
    }
    buckets.retain(|bucket, _| {
        fills.is_none_or(|fills| fills.quoted(*bucket, 1_800))
            && (sleeve.broker_day() || sleeve.in_session(bucket - shift))
    });
    buckets
}

/// A live-rules run's sizing throttle for ONE sleeve: `daily_multipliers` over
/// its market's 30-minute closes from `THROTTLE_FROM` -- the primed history,
/// then the run's own unfiltered bars -- kept where the broker quoted the
/// bucket and the bucket is in THIS sleeve's session (or every bucket of the
/// broker day). Exactly what the live sleeve's streaming throttle folds
/// (`FamilyEngine::prime_throttle` then `fold_throttle` / `on_candle`).
pub(crate) fn sleeve_throttle(
    prepared: &super::prepare::PreparedRun,
    sleeve: Sleeve,
    source: usize,
) -> HashMap<i64, f64> {
    let bars = prepared.symbol_bars.get(source).unwrap_or(&prepared.bars);
    let shift = sleeve.shift_hours() * 3_600;
    let fills = prepared
        .fills
        .get(&super::prepare::fill_key(sleeve.market(), sleeve.shift_hours()));
    let mut buckets: BTreeMap<i64, (f64, f64, f64)> = BTreeMap::new();
    let primed = prepared.prime_marks.get(&source).map(|m| m.as_slice()).unwrap_or(&[]);
    for (ts, close) in primed.iter().copied().chain(bars.iter().map(|bar| (bar.ts, bar.close))) {
        let bucket = (ts + shift).div_euclid(1_800) * 1_800;
        buckets.insert(bucket, (close, close, close));
    }
    buckets.retain(|bucket, _| {
        fills.is_none_or(|fills| fills.quoted(*bucket, 1_800))
            && (sleeve.broker_day() || sleeve.in_session(bucket - shift))
    });
    daily_multipliers(&buckets, crate::strategies::fundednext::oct_21_2026::THROTTLE_FROM)
}

/// `exness_combined_strategies.daily_multipliers` over a series' 30-minute
/// closes from `from` on: each day's multiplier from the days strictly before it.
pub(crate) fn daily_multipliers(marks: &BTreeMap<i64, (f64, f64, f64)>, from: i64) -> HashMap<i64, f64> {
    use crate::strategies::cfd::{VOL_HALFLIFE, VOL_MAX_MULTIPLIER, VOL_MIN_DAYS, VOL_TARGET};
    let mut by_day: BTreeMap<i64, f64> = BTreeMap::new();
    for (&ts, &(close, _, _)) in marks.range(from..) {
        by_day.insert(ts.div_euclid(DAY), close);
    }
    let lam = 0.5f64.powf(1.0 / VOL_HALFLIFE);
    let (mut variance, mut seen, mut previous) = (0.0_f64, 0u32, None::<f64>);
    let mut out = HashMap::new();
    for (day, close) in by_day {
        let value = if seen < VOL_MIN_DAYS || variance <= 0.0 {
            1.0
        } else {
            let annual = (variance * 252.0).sqrt();
            if annual > 0.0 { (VOL_TARGET / annual).min(VOL_MAX_MULTIPLIER) } else { 1.0 }
        };
        out.insert(day, value);
        if let Some(previous) = previous
            && previous > 0.0
            && close > 0.0
        {
            let change = close / previous - 1.0;
            variance = if seen == 0 { change * change } else { lam * variance + (1.0 - lam) * change * change };
            seen += 1;
        }
        previous = Some(close);
    }
    out
}
