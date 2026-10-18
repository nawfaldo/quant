use super::{
    drawdown::DrawdownTracker,
    exposure,
    prepare::PreparedRun,
    report::{montecarlo, report},
    request::RunRequest,
    types::{Action, Bar, LOT_STEP, Side, Strategy, Trade, lot_size},
};
use crate::{
    error::ApiError,
    strategies::cfd::{CfdSleeve, Sleeve},
};
use serde_json::Value;

use super::data::format_day;
#[cfg(test)]
use super::data::parse_iso_days;

pub fn execute(prepared: &PreparedRun, request: &RunRequest) -> Result<RunResult, ApiError> {
    let engine = prepared.engine.clone();
    let strategy = build_strategy(&request.strategy, prepared)?;
    Ok(run_engines(&prepared.bars, vec![strategy], engine))
}

/// Runs several strategies side by side against one shared account: they see
/// the same bars and the same running equity, hold their positions
/// independently, and their realized PnL lands in a single balance.
pub fn execute_combined(prepared: &PreparedRun, names: &[String]) -> Result<RunResult, ApiError> {
    let engine = prepared.engine.clone();
    let strategies = names
        .iter()
        .map(|name| build_strategy(name, prepared))
        .collect::<Result<Vec<_>, _>>()?;
    if prepared.symbol_bars.len() > 1 {
        // ONE STREAM PER (MARKET, CANDLE WIDTH), NOT PER MARKET.
        //
        // An aggregating sleeve emits candle S's decisions when the first bar
        // of slot S + width arrives, so its stream is offered `width` early
        // (see the lag below) to land them at S, where Python acts. Since the
        // TT27 book a market can carry sleeves of several widths -- GBPJPY has
        // 15-, 30- and 120-minute cells -- and one lag cannot be right for all
        // of them. So each width on a market gets its OWN copy of that market's
        // bars, offered at its own lag, and every sleeve is stepped on its
        // width's copy. A market with one width keeps exactly one stream, so a
        // 30-minute book runs precisely as before.
        let mut series: Vec<(usize, i64)> = Vec::new();
        let mut assignment: Vec<usize> = Vec::with_capacity(names.len());
        for (strategy, name) in names.iter().enumerate() {
            let symbol = prepared
                .strategy_symbols
                .get(strategy)
                .copied()
                .unwrap_or(0);
            let width = crate::strategies::cfd::Sleeve::from_display(name)
                .map_or(0, |sleeve| sleeve.candle_seconds());
            let index = match series.iter().position(|entry| *entry == (symbol, width)) {
                Some(index) => index,
                None => {
                    series.push((symbol, width));
                    series.len() - 1
                }
            };
            assignment.push(index);
        }
        // Merge every series' bars into one timestamp-ordered stream. A stable
        // sort keeps same-timestamp bars in series order, so a run is
        // reproducible rather than depending on which market happened to print
        // first.
        let mut stream: Vec<(usize, Bar)> = Vec::new();
        for (index, (symbol, width)) in series.iter().enumerate() {
            // `CFD_BROKER_HOURS`: a series whose sleeves were sealed under it
            // loses every vendor candle the broker did not quote, at THIS
            // series' own candle width, exactly as `cfd_families.all_bars`
            // drops it before anything is built.
            let quotes = names
                .iter()
                .zip(&assignment)
                .filter(|(_, at)| **at == index)
                .find_map(|(name, _)| Sleeve::from_display(name))
                .filter(|sleeve| sleeve.broker_hours())
                .and_then(|sleeve| {
                    let shift = sleeve.shift_hours() * 3_600;
                    prepared
                        .fills
                        .get(&super::prepare::fill_key(sleeve.market(), sleeve.shift_hours()))
                        .map(|fills| (std::sync::Arc::clone(fills), shift))
                });
            stream.extend(
                prepared.symbol_bars[*symbol]
                    .iter()
                    .filter(|bar| {
                        quotes.as_ref().is_none_or(|(fills, shift)| {
                            let bucket = (bar.ts + shift).div_euclid(*width) * *width;
                            fills.quoted(bucket, *width)
                        })
                    })
                    .map(|bar| (index, *bar)),
            );
        }
        // THE ORDER MARKETS ARE VISITED AT ONE TIMESTAMP DECIDES WHO GETS THE
        // GROSS-CAP BUDGET, so it is Python's order and not the request's.
        //
        // `exness_combined_strategies.replay` sorts every pending trade in the
        // whole book by `(entry_ts, sleeve)` and offers them in that order, so
        // at a shared timestamp `audusd:zscore` is asked before
        // `jp225:swing_break`. Sorting by the request's symbol index instead
        // asked whole markets in registration order, and on 2025-05-09 01:30 --
        // headroom 939.33 against audusd at 640.14 and two JP225 entries at
        // 703.32 each -- that reversed the outcome: Python funds audusd and
        // refuses both JP225 sleeves, the port did the opposite.
        //
        // Ranking a market by the LOWEST `python_key` among its sleeves
        // reproduces the global order exactly, because a sleeve key is
        // `"<market>:<family>"` and so every market's sleeves are already
        // contiguous in that global sort. It also orders the three NQ series --
        // `nq:drift_vwap`, `nq:ofi`, `nq:volatility_breakout` -- among
        // themselves, which a symbol index cannot do because they share a
        // market.
        let mut market_rank: Vec<&str> = vec![""; series.len()];
        // AND THE SHIFTED CLOCK, which changes WHEN a market's bars are offered
        // relative to every other market's.
        //
        // `cfd_families.all_bars` adds JP225's six hours as it loads, so a
        // JP225 trade carries a SHIFTED `entry_ts` -- and `replay` then sorts it
        // against unshifted AUDUSD and NQ stamps and marks it on a grid built
        // from those same shifted stamps. Its positions therefore open and close
        // six hours later than real time RELATIVE TO THE REST OF THE BOOK, and
        // they overlap a different set of other-market positions than they
        // really would. That is what the gross-cap budget is shared against.
        //
        // Ordering here is the only place it is reproduced: the recorded trade
        // timestamps stay real, because a shifted stamp in the trade log would
        // be wrong for anything that is not this comparison.
        let mut market_shift: Vec<i64> = vec![0; series.len()];
        // AND THE ONE-BAR FLUSH LATENCY, which is the other reason a stream is
        // not offered where its decisions happen.
        //
        // A `FamilyEngine` sleeve aggregates thirty-minute candles out of the
        // bars it is fed, so it cannot know candle S is finished until the FIRST
        // BAR OF SLOT S+1 arrives -- and it emits candle S's exit and fill on
        // that bar. Python has the whole array and acts at stamp S.
        //
        // Half an hour of the rest of the book therefore lands in between, and
        // the shared balance is what those sleeves size against. It cost exactly
        // one trade to notice: `jp225:swing_donchian` entering 2025-07-23 sized
        // 6.18 lots against $637.97 where Python sized 6.16 against $635.40,
        // because the book had gained $2.57 in the thirty minutes the port was
        // late. Every later divergence in the run was downstream of that one.
        //
        // ONLY THE AGGREGATING STREAMS. `nq:ofi` and `nq:drift_vwap` act on the
        // minute bar they are given and are already offered where they act;
        // moving them would introduce the error this removes.
        //
        // THE LAG IS THE SLEEVE'S OWN CANDLE: thirty minutes for a
        // `cfd_families` cell, five for a 5-minute TikTok cell.
        let mut market_lag: Vec<i64> = vec![0; series.len()];
        let mut market_known: Vec<bool> = vec![false; series.len()];
        for (strategy, name) in names.iter().enumerate() {
            let symbol = assignment[strategy];
            let sleeve = crate::strategies::cfd::Sleeve::from_display(name);
            let key = sleeve.map_or("", |sleeve| sleeve.python_key());
            if market_rank[symbol].is_empty() || key < market_rank[symbol] {
                market_rank[symbol] = key;
            }
            market_shift[symbol] = sleeve.map_or(0, |sleeve| sleeve.shift_hours()) * 3_600;
            // A stream is only lagged when EVERY sleeve on it aggregates. A
            // stream is keyed by `(market, source)`, so the four NQ sleeves sit
            // on four separate streams and the two that aggregate can be lagged
            // without touching the two that do not.
            let aggregates = sleeve.is_some_and(Sleeve::aggregates_candles);
            let width = sleeve.map_or(0, Sleeve::candle_seconds);
            market_lag[symbol] = if market_known[symbol] {
                market_lag[symbol].min(if aggregates { width } else { 0 })
            } else if aggregates {
                width
            } else {
                0
            };
            market_known[symbol] = true;
        }
        // A stable sort, so two series that rank identically -- which is every
        // run the registry does not know -- keep their registration order and a
        // run stays reproducible.
        let moment = |index: usize, bar: &Bar| bar.ts + market_shift[index] - market_lag[index];
        // CANON REPLAY OF THE FUNDEDNEXT BOOK: the bar that CLOSES a candle acts
        // at the candle's own stamp. A candle closes on the last bar its slot
        // has, which is the slot's final minute only when that minute printed;
        // `fundednext_us2000_1m` has no 09:29 on 2025-03-17, so its 09:00
        // candle closed a minute ahead of every other market's and opened an
        // entry against a balance not yet paid gbpjpy's exit at the same
        // stamp. Python's grid settles every exit at a stamp before opening
        // anything there; keying each closing bar to its candle restores that.
        let canon_fn = engine.canon_replay
            && names
                .iter()
                .all(|name| Sleeve::from_display(name).is_some_and(|s| s.book() == crate::strategies::cfd::Book::FundedNext));
        // The closing bars are MARKED in place (`depth_events`, which an OHLCV
        // bar never carries) rather than keyed in a side table: the merged
        // stream is tens of millions of minutes and a second copy does not fit.
        if canon_fn {
            let mut seen: std::collections::HashMap<usize, i64> = std::collections::HashMap::new();
            for (index, bar) in stream.iter_mut().rev() {
                let width = series[*index].1.max(60);
                let slot = (bar.ts + market_shift[*index]).div_euclid(width);
                if seen.get(index) != Some(&slot) {
                    bar.depth_events = 1;
                }
                seen.insert(*index, slot);
            }
        }
        let key = |index: usize, bar: &Bar| {
            if canon_fn && bar.depth_events == 1 {
                let width = series[index].1.max(60);
                (bar.ts + market_shift[index]).div_euclid(width) * width
            } else if canon_fn {
                // One second later, so the NEXT candle's first bar -- whose
                // moment equals this candle's stamp -- is never stepped inside
                // the instant: `update_all` clears `action_timestamp` on entry.
                moment(index, bar) + 1
            } else {
                moment(index, bar)
            }
        };
        stream.sort_by_key(|(index, bar)| (key(*index, bar), market_rank[*index], *index, bar.ts));
        let keyed: Vec<i64> = stream.iter().map(|(index, bar)| key(*index, bar)).collect();
        if canon_fn {
            for (_, bar) in stream.iter_mut() {
                bar.depth_events = 0;
            }
        }
        let apply_keys: Vec<i64> = keyed;
        return Ok(run_engines_streams_at(
            &stream,
            &apply_keys,
            series.len(),
            strategies,
            names,
            &assignment,
            engine,
        ));
    }
    Ok(run_engines_named(&prepared.bars, strategies, names, engine))
}

/// Builds one sleeve of the 2026-09-07 Exness book, filled the way the account
/// is filled: its market's broker minutes installed from the run's
/// `PreparedRun::fills` (see `backtest::fills`).
fn build_strategy(name: &str, prepared: &PreparedRun) -> Result<Box<dyn Strategy>, ApiError> {
    let sleeve = Sleeve::from_display(name)
        .ok_or_else(|| ApiError::BadRequest("unknown strategy".into()))?;
    let mut strategy = CfdSleeve::new(sleeve, LOT_STEP);
    if let Some(fills) = prepared.fills.get(&super::prepare::fill_key(
        sleeve.market(),
        sleeve.shift_hours(),
    )) {
        strategy.install_fills(std::sync::Arc::clone(fills));
        if sleeve.broker_hours() {
            strategy.install_quotes(std::sync::Arc::clone(fills) as std::sync::Arc<dyn crate::strategies::cfd::BrokerQuotes>);
        }
    }
    if let Some(mask) = entry_days_from_env()? {
        strategy.restrict_entry_days(mask);
    }
    Ok(Box::new(strategy))
}

/// `EXNESS_ENTRY_DAYS=mon,tue,wed,thu,fri`: a RESEARCH switch restricting new
/// signals to those New York weekdays, the Rust twin of
/// `cfd_families.ENTRY_DAYS`. Unset is every day, which is canon. A name it
/// does not know is an error rather than a silently narrower run.
fn entry_days_from_env() -> Result<Option<u8>, ApiError> {
    let Ok(text) = std::env::var("EXNESS_ENTRY_DAYS") else {
        return Ok(None);
    };
    let mut mask = 0u8;
    for part in text
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
    {
        let bit = match part.to_ascii_lowercase().get(..3) {
            Some("mon") => 0,
            Some("tue") => 1,
            Some("wed") => 2,
            Some("thu") => 3,
            Some("fri") => 4,
            Some("sat") => 5,
            Some("sun") => 6,
            _ => {
                return Err(ApiError::BadRequest(format!(
                    "EXNESS_ENTRY_DAYS: unknown day {part:?}"
                )));
            }
        };
        mask |= 1 << bit;
    }
    Ok((mask != 0).then_some(mask))
}

fn is_fundednext(name: &str) -> bool {
    crate::strategies::cfd::Sleeve::from_display(name)
        .is_some_and(|sleeve| sleeve.book() == crate::strategies::cfd::Book::FundedNext)
}

/// The instant in `(previous, now]` at which live's `session_overdue` would
/// first fire for this slot, if any: its session close (real New York minute)
/// plus one of its candles plus `SESSION_OVERDUE_GRACE_SECONDS`, provided that
/// moment is outside its trading session. Multi-day sleeves are never overdue.
fn overdue_instant(slot: &Slot, previous: i64, now: i64) -> Option<i64> {
    const GRACE: i64 = 10;
    if slot.strategy.holds_overnight() {
        return None;
    }
    let sleeve = crate::strategies::cfd::Sleeve::from_display(&slot.name)?;
    let end = slot.strategy.session_end_minute()? as i64;
    let start = slot.strategy.session_start_minute()? as i64;
    let in_session = |minute: i64| {
        if start <= end {
            (start..=end).contains(&minute)
        } else {
            minute >= start || minute <= end
        }
    };
    let delay = sleeve.candle_seconds() + GRACE;
    let today = now.div_euclid(86_400) * 86_400;
    [today - 86_400, today]
        .into_iter()
        .map(|day| day + end * 60 + delay)
        .find(|fire| {
            *fire > previous && *fire <= now && !in_session(fire.rem_euclid(86_400) / 60)
        })
}

/// Whether the slot's market has no stored data past this run.
fn data_ended(name: &str, cfg: &EngineConfig) -> bool {
    crate::strategies::cfd::Sleeve::from_display(name)
        .is_some_and(|sleeve| cfg.ended_markets.iter().any(|market| market == sleeve.market()))
}

#[derive(Clone)]
pub(crate) struct EngineConfig {
    pub(crate) initial: f64,
    pub(crate) symbol: String,
    pub(crate) start_day: i64,
    /// `RunRequest::admission_from`: the day the strategies' own trading state
    /// (and so each sleeve's admission shadow account) starts, when earlier than
    /// `start_day`. Between the two, a sleeve trades for itself only -- its
    /// actions are dropped WITHOUT `discard`, as Python's replay drops a log
    /// trade entered before `lo` -- and a position it carries into the window
    /// is not the book's. `None` is `start_day`, every ordinary run.
    pub(crate) admission_day: Option<i64>,
    /// Markets whose STORED data ends inside this run, so the run's last bar
    /// is the last bar there is. Python's replay then has no successor either:
    /// that day never ends and a position still open is never logged. Read by
    /// FundedNext sleeves only (`Strategy::set_next_bar`, the end-of-run close).
    pub(crate) ended_markets: Vec<String>,
    /// `RunRequest::canon_replay`.
    pub(crate) canon_replay: bool,
    /// Ceiling on the SIMULTANEOUS notional of everything open, in multiples of
    /// equity. `None` leaves a run uncapped, which is what every run other than
    /// the whole book gets.
    ///
    /// An entry that would push the summed notional past it is REFUSED outright
    /// rather than trimmed, because trimming would change the sleeve's own risk
    /// model instead of the book's. It is the one book-level lever that can
    /// reach an intraday drawdown -- that drawdown is made of positions open at
    /// the same moment -- and the only risk lever that has ever cleared a
    /// random-refusal control in two windows separately
    /// ([[gross-exposure-cap-beats-its-null]]).
    pub(crate) gross_cap: Option<f64>,
}

#[derive(Clone)]
struct Position {
    id: Option<u64>,
    side: Side,
    entry: f64,
    raw: f64,
    pub(crate) ts: i64,
    quantity: f64,
    /// USD value of one full price-unit movement per lot/unit.
    point_value: f64,
    /// `Strategy::entry_sizing` at the open.
    sizing: Option<(f64, f64)>,
}
pub struct RunResult {
    pub body: Value,
    pub trades: Vec<Trade>,
}

/// One strategy's own book inside a shared account: its open position, its
/// tagged positions, and the actions it emitted for the bar being processed.
struct Slot {
    strategy: Box<dyn Strategy>,
    /// Index into the run's symbol list. A slot is stepped only on its own
    /// market's bars and its open position is marked only against that market.
    symbol_index: usize,
    /// Display name, so a combined run can attribute PnL back to the strategy
    /// that earned it. Empty for a single-strategy run, which needs no split.
    name: String,
    /// The balance this slot was shown when it last decided, for `resize_entry`.
    shown_equity: f64,
    /// The timestamp of the bar this slot was last STEPPED with.
    ///
    /// Not the bar the instant happens to end on. An instant can hold several
    /// markets' bars, and a minute-level sleeve stepped at its start would
    /// otherwise book its fill against a thirty-minute cell's bar half an hour
    /// later -- which is what happened to `nq:ofi`, whose trade log came out
    /// carrying the right prices at the wrong times.
    acted_at: i64,
    /// The bar the generic session flattener must price this slot`s exit at,
    /// set while the slot is stepped and consumed when the instant settles.
    ///
    /// It cannot be decided at settlement time: the test is `is this the last
    /// bar before the close`, which is a question about ONE bar, and an instant
    /// can hold bars from several markets. Reading it off whichever bar happened
    /// to end the instant flattened the wrong slots and left the rest open.
    flatten_bar: Option<Bar>,
    /// `apply_key - bar.ts` for this slot's own bars: how far the merged
    /// stream's clock runs ahead of (or behind) this market's real New York
    /// time -- its shifted clock less its candle-flush lag. The overdue close
    /// is scheduled in REAL time and fired on the STREAM clock, so it needs it.
    frame: i64,
    /// Whether the balance currently supports this sleeve. Starts true so a run
    /// with no gate configured behaves exactly as before; `Schedule::is_on`
    /// keeps it, and the hysteresis band reads it as the previous state.
    funded: bool,
    position: Option<Position>,
    tagged_positions: Vec<Position>,
    actions: Vec<Action>,
    /// Realized PnL this slot contributed to the shared account, including its
    /// share of joint compounding: every gain is booked against the equity the
    /// other slots had already produced, so these sum to the account total but
    /// are *not* what the strategy would have made alone.
    realized: f64,
    closed_trades: usize,
    /// Realized-only equity points used for this strategy's contribution
    /// drawdown statistics. Open PnL remains an account-level concern.
    contribution_equity: Vec<(i64, f64)>,
    /// Realized PnL per calendar month, keyed `YYYY-MM` off the *exit*
    /// timestamp, so a trade is booked in the month it was actually settled.
    monthly: std::collections::BTreeMap<String, f64>,
}

impl Slot {
    fn new(strategy: Box<dyn Strategy>) -> Self {
        Self {
            strategy,
            symbol_index: 0,
            name: String::new(),
            funded: true,
            shown_equity: 0.0,
            acted_at: 0,
            flatten_bar: None,
            frame: 0,
            position: None,
            tagged_positions: Vec::new(),
            actions: Vec::new(),
            realized: 0.0,
            closed_trades: 0,
            contribution_equity: Vec::new(),
            monthly: std::collections::BTreeMap::new(),
        }
    }

    fn named(strategy: Box<dyn Strategy>, name: &str) -> Self {
        Self {
            name: name.to_owned(),
            ..Self::new(strategy)
        }
    }
}

fn slot_market<'a>(slot: &'a Slot, cfg: &'a EngineConfig) -> &'a str {
    if slot.name.is_empty() {
        &cfg.symbol
    } else {
        crate::strategies::market_symbol(&slot.name)
    }
}

/// Account-currency value of one full price unit per lot.
///
/// Read from the Exness Pro terminal on 2026-08-16 (`tick_value / tick_size`),
/// not from convention, and ALREADY converted to USD -- MT5 reports
/// `trade_tick_value` in the account currency, so the JPY-quoted markets must
/// not be converted a second time. That double conversion was a silent 159x
/// error on the JPY crosses ([[mt5-tick-value-is-account-currency]]).
///
/// These must equal `Instrument::<MARKET>.multiplier` in `oct_12_2026`;
/// `frozen_point_values_match_the_strategy_specs` is what keeps them equal.
/// The account-currency notional one open position represents.
///
/// Taken at the RAW entry price, matching Python's `abs(lots * entry * money)`:
/// the cost-adjusted `entry` already carries the spread, and billing that into
/// an exposure measure would make a wider spread look like a larger position.
fn position_notional(position: &Position) -> f64 {
    (position.quantity * position.raw * position.point_value).abs()
}
pub mod costs;
mod sizing;

#[cfg(test)]
mod tests;

use costs::*;
use sizing::*;

/// timestamp.
fn month_key(ts: i64) -> String {
    let formatted = crate::backtest::data::format_ts(ts);
    formatted.chars().take(7).collect()
}

/// Two decimal places, matching the rest of the reported figures.
fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn run_engines(bars: &[Bar], strategies: Vec<Box<dyn Strategy>>, cfg: EngineConfig) -> RunResult {
    run_engines_named(bars, strategies, &[], cfg)
}

/// `names` attributes each slot's realized PnL in the result body. Pass an empty
/// slice for a single-strategy run, where the split carries no information.
fn run_engines_named(
    bars: &[Bar],
    strategies: Vec<Box<dyn Strategy>>,
    names: &[String],
    cfg: EngineConfig,
) -> RunResult {
    let stream: Vec<(usize, Bar)> = bars.iter().map(|bar| (0usize, *bar)).collect();
    let assignment = vec![0usize; strategies.len()];
    run_engines_streams(&stream, 1, strategies, names, &assignment, cfg)
}

fn is_last_bar_before_session_end(
    stream: &[(usize, Bar)],
    next_same: &[Option<usize>],
    bar_index: usize,
    session_end: usize,
) -> bool {
    let bar = stream[bar_index].1;
    let minute = bar.ts.rem_euclid(86_400) as usize / 60;
    minute < session_end
        && next_same[bar_index].is_none_or(|next| {
            let next = stream[next].1;
            next.ts.div_euclid(86_400) != bar.ts.div_euclid(86_400)
                || next.ts.rem_euclid(86_400) as usize / 60 >= session_end
        })
}

/// The shared-account engine over one or more symbols.
///
/// `stream` is every symbol's bars merged in timestamp order, each tagged with
/// its index into the run's symbol list; `assignment` gives each strategy its
/// symbol index. A single-symbol run is the degenerate case and takes exactly
/// the same path, so the two cannot drift apart.
///
/// Point value is deliberately *not* per-slot: `Instrument::Forex` returns 1.0
/// for every symbol, and `prepare` refuses a cross-symbol set on Mini/Micro,
/// where it would differ (es 50, nq 20). That keeps the accounting core on one
/// config while staying correct.
pub(crate) fn run_engines_streams(
    stream: &[(usize, Bar)],
    symbol_count: usize,
    strategies: Vec<Box<dyn Strategy>>,
    names: &[String],
    assignment: &[usize],
    cfg: EngineConfig,
) -> RunResult {
    run_engines_streams_at(
        stream,
        &[],
        symbol_count,
        strategies,
        names,
        assignment,
        cfg,
    )
}

/// The same run, told WHICH BARS SHARE AN INSTANT.
///
/// `apply_keys[i]` is the moment `stream[i]` acts at -- the key the stream was
/// sorted on, which is not the bar's own timestamp once a market's shifted clock
/// and its candle-flush lag have been folded in. Bars sharing a key are one
/// instant of `replay`'s grid, and the account has to settle EVERY close in that
/// instant before it opens anything: an entry sized off the balance the closes
/// have not yet paid into is a different trade.
///
/// An empty slice means every bar is its own instant, which is what a
/// single-market run and every test want.
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_engines_streams_at(
    stream: &[(usize, Bar)],
    apply_keys: &[i64],
    symbol_count: usize,
    strategies: Vec<Box<dyn Strategy>>,
    names: &[String],
    assignment: &[usize],
    cfg: EngineConfig,
) -> RunResult {
    // Whether this bar is the LAST of its instant, and so whether the account
    // settles here or keeps accumulating.
    let last_of_instant = |index: usize| -> bool {
        match apply_keys.get(index) {
            Some(key) => apply_keys.get(index + 1) != Some(key),
            None => true,
        }
    };
    let combined = strategies.len() > 1;
    // Last bar seen per symbol, so a position is always marked and flattened at
    // its own market's price even on bars another symbol printed.
    let mut last_bar: Vec<Option<Bar>> = vec![None; symbol_count];
    // The generic session flattener needs to know whether this is the last bar
    // before the boundary for this particular symbol.
    let mut next_same: Vec<Option<usize>> = vec![None; stream.len()];
    {
        let mut seen: Vec<Option<usize>> = vec![None; symbol_count];
        for index in (0..stream.len()).rev() {
            let symbol = stream[index].0;
            next_same[index] = seen[symbol];
            seen[symbol] = Some(index);
        }
    }
    let mut slots: Vec<Slot> = strategies
        .into_iter()
        .enumerate()
        .map(|(index, strategy)| {
            let mut slot = match names.get(index) {
                Some(name) => Slot::named(strategy, name),
                None => Slot::new(strategy),
            };
            slot.symbol_index = assignment.get(index).copied().unwrap_or(0);
            slot
        })
        .collect();
    // THE ORDER ENTRIES ARE OFFERED THE GROSS-CAP BUDGET, and it is not `BOOK`
    // order. The cap is first-come-first-served, so whoever is asked first gets
    // it; Python's `replay` sorts its pending trades by `(entry_ts, sleeve)` and
    // a book that offers them in another order refuses a different set. Sorting
    // once here costs nothing per bar and is the whole of the difference within
    // a market -- NQ is offered as `drift_vwap, ofi, volatility_breakout` rather
    // than `ofi, drift_vwap, volatility_breakout`.
    //
    // A slot the registry does not know keeps its declaration order, which is
    // what a run outside the book and every single-strategy run get.
    let apply_order: Vec<usize> = {
        let mut order: Vec<usize> = (0..slots.len()).collect();
        order.sort_by_key(|index| {
            let name = names.get(*index).map(String::as_str).unwrap_or_default();
            (
                crate::strategies::cfd::Sleeve::from_display(name)
                    .map(|sleeve| sleeve.python_key())
                    .unwrap_or_default(),
                *index,
            )
        });
        order
    };
    let mut trades = Vec::new();
    let mut below_broker_minimum = std::collections::BTreeMap::<String, usize>::new();
    let mut refused_by_gross_cap = std::collections::BTreeMap::<String, usize>::new();
    let mut equity = cfg.initial;
    let mut equity_peak = equity;
    let mut drawdowns = DrawdownTracker::new(equity, cfg.start_day);
    let mut sizing_day = None;
    let mut trading_started = false;
    // The clock the overdue close reads: one minute past the last bar seen.
    let mut clock_before: Option<i64> = None;
    // Read once per run rather than per bar; `None` in production, where the
    // compiled book policy below governs instead.
    let exposure = exposure::schedule();
    // THE CALIBRATED THREE-SLEEVE POLICY IS GONE, with the three sleeves it was
    // calibrated on. `sizing::book_exposure` scaled `NQ Deep OFI Momentum`,
    // `NQ Hourly Delta Reversal` and `BTC Maroy Ladder` off NQ's realised
    // volatility and doubled the Ladder; none of those strategies exists any
    // more, and the 2026-09-07 book carries its risk scale, its per-sleeve
    // weights and a per-market EWMA throttle inside `oct_12_2026` itself.
    //
    // Reapplying an overlay here would charge the same regime twice and size a
    // backtest differently from the live account it is meant to mirror
    // ([[live-vs-backtest-parity]]).
    let book: Option<()> = None;
    let nq_symbol: Option<usize> = None;
    for (bar_index, (symbol_index, bar)) in stream.iter().enumerate() {
        let symbol_index = *symbol_index;
        let bar = *bar;
        last_bar[symbol_index] = Some(bar);
        let day = bar.ts.div_euclid(86400);
        if day >= cfg.admission_day.unwrap_or(cfg.start_day).min(cfg.start_day) && !trading_started {
            for slot in &mut slots {
                slot.strategy.reset_trading_state();
            }
            trading_started = true;
        }
        let day_changed = sizing_day != Some(day);
        sizing_day = Some(day);
        let _ = day_changed;
        // Every strategy sees the same account equity, so position sizing that
        // scales with the balance reflects the combined book.
        //
        // A research exposure schedule, when one is set, is applied *here* --
        // as a factor on the equity a slot is shown rather than on the quantity
        // it returns. Sizing is linear in equity through both the risk leg and
        // the margin cap, so the two agree on the quantity while only this one
        // keeps the cap and every other equity-derived quantity consistent with
        // the size actually taken. Unset in production: `schedule()` is `None`
        // and the balance passes through untouched.
        // The book policy that used to fold NQ's close into a volatility
        // tracker here is gone; `book` is now permanently `None`. The binding
        // and its `nq_symbol` partner are kept so a future overlay has an
        // obvious place to attach, and so the `visible` match below still has a
        // policy arm to select.
        let _ = (&book, nq_symbol);
        let day_key = exposure.as_ref().map(|_| format_day(day));
        for slot in &mut slots {
            // A strategy is stepped only on its own market's bars.
            let stepped = if slot.symbol_index == symbol_index {
                // A research schedule, when one is set, overrides the compiled
                // policy so a sweep can still move exposure without a rebuild.
                let visible = match (&exposure, &day_key, &book) {
                    (Some(schedule), Some(key), _) => {
                        // The gate is stateful: `funded` carries yesterday's
                        // answer so the hysteresis band means something. A
                        // stood-down sleeve still receives the bar, so its ATR,
                        // EMAs and ranges stay warm and it resumes with correct
                        // indicators rather than a cold start.
                        if !schedule.initial_balance_allows(&slot.name, cfg.initial) {
                            slot.funded = false;
                            0.0
                        } else {
                            slot.funded = schedule.is_on(&slot.name, equity, slot.funded);
                            schedule.sizing_equity(&slot.name, equity)
                                * schedule.gated_factor(&slot.name, key, equity, slot.funded)
                        }
                    }
                    _ => equity,
                };
                // The balance this slot was SHOWN when it decided, kept so
                // `resize_entry` can scale a linear request onto the balance the
                // instant's closes actually leave behind.
                slot.shown_equity = visible;
                slot.acted_at = bar.ts;
                slot.frame = apply_keys.get(bar_index).map_or(0, |key| key - bar.ts);
                // Recorded HERE, while the bar this slot is being stepped with
                // is still in hand. An instant can carry several markets' bars,
                // and the flatten prices its exit at the close of THIS one.
                if !slot.strategy.flattens_itself()
                    && slot.strategy.session_end_minute().is_some_and(|end| {
                        is_last_bar_before_session_end(stream, &next_same, bar_index, end)
                    })
                {
                    slot.flatten_bar = Some(bar);
                }
                if cfg.canon_replay {
                    slot.strategy.set_next_bar(
                        next_same[bar_index].map(|next| stream[next].1.ts),
                        !data_ended(&slot.name, &cfg),
                    );
                }
                slot.strategy.update_all(bar, visible)
            } else {
                Vec::new()
            };
            // ACCUMULATED ACROSS THE INSTANT, not overwritten. Several markets
            // can print at one key, and their actions are settled together.
            slot.actions.extend(stepped);
        }
        if day < cfg.start_day {
            for slot in &mut slots {
                let actions = std::mem::take(&mut slot.actions);
                // Inside the admission run the sleeve keeps what it did: the
                // trade exists in its standalone log, it just is not booked.
                if !trading_started {
                    slot.strategy.discard_all(actions);
                }
            }
            continue;
        }

        // THE LIVE RUNTIME'S CLOCK-DRIVEN CLOSE, for the FundedNext book: a
        // position still held one candle (+10s) past its session close, outside
        // the session, is closed whether or not its market printed
        // (`live::portfolio::Slot::session_overdue`, `forced_flatten`). Live
        // keeps the strategy holding it as a GHOST and swallows the strategy's
        // own later exit; here the engine books the close and leaves the
        // strategy alone, and that later `Close` finds no position. Priced at
        // the market's last bar, which is what live records on the order.
        let now = apply_keys.get(bar_index).copied().unwrap_or(bar.ts);
        if let Some(previous) = clock_before.filter(|_| !cfg.canon_replay) {
            for slot in &mut slots {
                if slot.position.is_none() || !is_fundednext(&slot.name) {
                    continue;
                }
                let Some(fire) = overdue_instant(slot, previous - slot.frame, now - slot.frame)
                else {
                    continue;
                };
                let Some(mark) = last_bar[slot.symbol_index] else {
                    continue;
                };
                if let Some(old) = slot.position.take() {
                    let exit = mark.close;
                    let gain = net_pnl_after_financing(&old, exit, fire, &slot.name);
                    equity += gain;
                    slot.realized += gain;
                    slot.closed_trades += 1;
                    trades.push(to_trade(old, fire, exit, exit, gain, &slot.name));
                    if let Some(closed) = trades.last() {
                        slot.contribution_equity
                            .push((closed.exit_timestamp, cfg.initial + slot.realized));
                        *slot
                            .monthly
                            .entry(month_key(closed.exit_timestamp))
                            .or_default() += gain;
                    }
                }
            }
        }
        clock_before = Some(now);

        let mut mtm = equity;
        for slot in &slots {
            // Mark against the slot's own market. Marking a BTC position at an NQ
            // price silently corrupts equity, the drawdown series, and the
            // per-strategy drawdown limit enforced immediately below.
            let Some(mark) = last_bar[slot.symbol_index] else {
                continue;
            };
            let action = slot.actions.first().copied().unwrap_or(Action::Hold);
            mtm += marked_equity(0.0, slot.position.as_ref(), action, mark);
            for tagged in &slot.tagged_positions {
                mtm += marked_equity(0.0, Some(tagged), Action::Hold, mark);
            }
        }
        // A per-strategy dollar drawdown limit is enforced against the shared
        // account: the breaching strategy is flattened, the others keep running.
        for slot in &mut slots {
            let limit = slot.strategy.max_drawdown_dollars();
            if let (Some(limit), Some(open)) = (limit, slot.position.as_ref()) {
                let floor = equity_peak - limit;
                if mtm < floor {
                    slot.actions = vec![Action::Close {
                        price: exit_raw_for_equity(open, equity, floor),
                        fraction: 1.0,
                    }];
                    mtm = floor;
                }
            }
        }
        equity_peak = equity_peak.max(mtm);
        drawdowns.observe(mtm, day);

        // Recomputed from the open book each bar rather than carried as a
        // counter, so it cannot drift out of step with the positions it is
        // meant to describe; the adjustments below then keep it right for
        // entries that arrive later in this same bar.
        let mut exposed: f64 = slots
            .iter()
            .flat_map(|slot| slot.position.iter().chain(slot.tagged_positions.iter()))
            .map(position_notional)
            .sum();
        // CLOSES BEFORE OPENS, ACROSS EVERY SLEEVE, and then entries in the
        // order Python offers them.
        //
        // `replay` walks a grid of timestamps and does two things at each one:
        // it settles every position whose exit has arrived, and only then does
        // it open anything. Both halves matter to the gross cap, because a
        // close frees budget and releases equity that the next entry is measured
        // against -- interleaving them by slot means an entry is judged against
        // an exposure that includes a position already due to close, and against
        // an equity that has not yet been paid.
        //
        // The two passes cost one small vector a bar and remove the last
        // ordering difference between the two engines.
        // NOT YET, if another market prints at this same instant. Its bar is the
        // next one in the stream, and settling now would open this bar's entries
        // against a balance that has not been paid the other market's closes.
        if !last_of_instant(bar_index) {
            continue;
        }
        let mut queued: Vec<(usize, Action)> = Vec::new();
        for &slot_index in &apply_order {
            for action in std::mem::take(&mut slots[slot_index].actions) {
                queued.push((slot_index, action));
            }
        }
        for closing_phase in [true, false] {
            for (slot_index, action) in queued.iter().copied() {
                let is_close =
                    matches!(action, Action::Close { .. } | Action::ClosePosition { .. });
                if is_close != closing_phase {
                    continue;
                }
                let slot = &mut slots[slot_index];
                // SIZED AGAINST THE BALANCE THE CLOSES LEFT BEHIND. The strategy
                // decided this entry before the instant's closes were booked --
                // it had to, because stepping it is how the engine learns it
                // wants to close -- so it is asked once more now that they are.
                // A strategy whose size does not track the balance answers with
                // what it already said.
                let action = match action {
                    Action::Enter {
                        side,
                        price,
                        quantity,
                    } => Action::Enter {
                        side,
                        price,
                        quantity: slot.strategy.resize_entry(
                            price,
                            quantity,
                            slot.shown_equity,
                            equity,
                        ),
                    },
                    Action::EnterPosition {
                        id,
                        side,
                        price,
                        quantity,
                    } => Action::EnterPosition {
                        id,
                        side,
                        price,
                        quantity: slot.strategy.resize_entry(
                            price,
                            quantity,
                            slot.shown_equity,
                            equity,
                        ),
                    },
                    other => other,
                };
                // THE CANDLE THE DECISION BELONGS TO, NOT THE BAR THAT FLUSHED
                // IT, and the difference is a real cost rather than a label.
                //
                // A 30-minute strategy fed one-minute bars only evaluates a
                // candle once the FIRST BAR OF THE NEXT SLOT arrives, so a fill
                // decided on Friday's last candle is emitted on the bar that
                // opens the next session. Booking it there counts the weekend
                // as nights the position was open: `xalusd:gated_fade` took a
                // trade that entered and exited inside 2025-06-20 and was
                // charged two nights of carry for it, because the exit was
                // recorded against the following Sunday's first bar.
                //
                // `action_timestamp` is what the strategy already reports for
                // exactly this, and it converts a shifted clock back to real New
                // York on the way out. A strategy that does not implement it
                // returns the bar's own timestamp, which is what every
                // one-bar-at-a-time strategy wants.
                let fill_timestamp = slot.strategy.action_timestamp(slot.acted_at);
                // THE EXIT LEG CAN BELONG TO AN EARLIER CANDLE THAN THE FILL,
                // and `DriftVwap` is the sleeve where it does. Every other
                // strategy answers this with `fill_timestamp`.
                let exit_timestamp = slot.strategy.exit_timestamp(slot.acted_at);
                let max_drawdown_dollars = slot.strategy.max_drawdown_dollars();
                match action {
                    Action::Hold => {}
                    Action::Enter {
                        side,
                        price,
                        quantity,
                    } => {
                        let execution_price = price;
                        if !slot.position.as_ref().is_some_and(|p| p.side == side) {
                            if let Some(old) = slot.position.take() {
                                exposed -= position_notional(&old);
                                let exit = execution_price;
                                let gain =
                                    net_pnl_after_financing(&old, exit, exit_timestamp, &slot.name);
                                equity += gain;
                                slot.realized += gain;
                                slot.closed_trades += 1;
                                trades.push(to_trade(
                                    old,
                                    exit_timestamp,
                                    exit,
                                    execution_price,
                                    gain,
                                    &slot.name,
                                ));
                                if let Some(closed) = trades.last() {
                                    slot.contribution_equity
                                        .push((closed.exit_timestamp, cfg.initial + slot.realized));
                                    *slot
                                        .monthly
                                        .entry(month_key(closed.exit_timestamp))
                                        .or_default() += gain;
                                }
                            }
                            equity_peak = equity_peak.max(equity);
                            let sized_quantity = if let (Some(risk_fraction), Some(stop)) = (
                                slot.strategy.entry_risk_fraction(),
                                slot.strategy.entry_stop_price(),
                            ) {
                                risk_limited_quantity(
                                    side,
                                    price,
                                    stop,
                                    equity,
                                    equity_peak,
                                    risk_fraction,
                                    max_drawdown_dollars,
                                    &cfg,
                                )
                            } else {
                                Some(lot_size(quantity))
                            };
                            if let Some(quantity) = sized_quantity {
                                let market = slot_market(slot, &cfg);
                                if !slot.strategy.sizes_below_broker_minimum()
                                    && quantity + 1e-9 < sleeve_broker_minimum(&slot.name, market)
                                {
                                    *below_broker_minimum.entry(slot.name.clone()).or_default() +=
                                        1;
                                    continue;
                                }
                                let point_value = sleeve_point_value(&slot.name, market);
                                let (spread, spread_bp) = sleeve_entry_cost(&slot.name);
                                // THE SPREAD THE ACCOUNT CROSSED, where the Exness
                                // fill model could read it off the broker's own
                                // minute; the market constant otherwise.
                                let spread_bp = slot.strategy.entry_cost_bp().or(spread_bp);
                                // The gross cap is FIRST-COME-FIRST-SERVED, and
                                // that is not neutral: whichever sleeve trades
                                // most often is usually already holding the
                                // exposure when the others want it, so the cap
                                // allocates the book by trade frequency rather
                                // than by quality. Refusals are counted per
                                // sleeve because a crowded-out sleeve is
                                // otherwise indistinguishable from one that had
                                // no signals.
                                let notional = (quantity * execution_price * point_value).abs();
                                if let Some(cap) = cfg.gross_cap
                                    && equity > 0.0
                                    && exposed + notional > cap * equity
                                {
                                    *refused_by_gross_cap.entry(slot.name.clone()).or_default() +=
                                        1;
                                    continue;
                                }
                                exposed += notional;
                                slot.position = Some(Position {
                                    id: None,
                                    side,
                                    entry: fill_entry(
                                        execution_price,
                                        side == Side::Long,
                                        &EntryCosts {
                                            spread,
                                            point_value,
                                            spread_bp,
                                        },
                                    ),
                                    raw: execution_price,
                                    ts: fill_timestamp,
                                    quantity,
                                    point_value,
                                    sizing: slot.strategy.entry_sizing(),
                                });
                            }
                        }
                    }
                    Action::Close { price, fraction } => {
                        let execution_price = price;
                        if let Some(mut old) = slot.position.take() {
                            exposed -= position_notional(&old);
                            let close_qty = closed_size(old.quantity, fraction);
                            let exit = execution_price;
                            let partial = Position {
                                id: old.id,
                                side: old.side,
                                entry: old.entry,
                                raw: old.raw,
                                ts: old.ts,
                                quantity: close_qty,
                                point_value: old.point_value,
                                sizing: old.sizing,
                                // A partial inherits the parent's entry rate; it
                                // is the same position being closed in pieces.
                            };
                            let gain =
                                net_pnl_after_financing(&partial, exit, exit_timestamp, &slot.name);
                            equity += gain;
                            slot.realized += gain;
                            slot.closed_trades += 1;
                            trades.push(to_trade(
                                partial,
                                exit_timestamp,
                                exit,
                                execution_price,
                                gain,
                                &slot.name,
                            ));
                            if let Some(closed) = trades.last() {
                                slot.contribution_equity
                                    .push((closed.exit_timestamp, cfg.initial + slot.realized));
                                *slot
                                    .monthly
                                    .entry(month_key(closed.exit_timestamp))
                                    .or_default() += gain;
                            }
                            old.quantity -= close_qty;
                            if old.quantity > 1e-8 {
                                exposed += position_notional(&old);
                                slot.position = Some(old);
                            }
                        }
                    }
                    Action::EnterPosition {
                        id,
                        side,
                        price,
                        quantity,
                    } => {
                        let execution_price = price;
                        let quantity = lot_size(quantity);
                        let market = slot_market(slot, &cfg);
                        if !slot.strategy.sizes_below_broker_minimum()
                            && quantity + 1e-9 < sleeve_broker_minimum(&slot.name, market)
                        {
                            *below_broker_minimum.entry(slot.name.clone()).or_default() += 1;
                            continue;
                        }
                        let point_value = sleeve_point_value(&slot.name, market);
                        let notional = (quantity * execution_price * point_value).abs();
                        if let Some(cap) = cfg.gross_cap
                            && equity > 0.0
                            && exposed + notional > cap * equity
                        {
                            *refused_by_gross_cap.entry(slot.name.clone()).or_default() += 1;
                            continue;
                        }
                        exposed += notional;
                        let (spread, spread_bp) = sleeve_entry_cost(&slot.name);
                        let spread_bp = slot.strategy.entry_cost_bp().or(spread_bp);
                        slot.tagged_positions.push(Position {
                            id: Some(id),
                            side,
                            entry: fill_entry(
                                execution_price,
                                side == Side::Long,
                                &EntryCosts {
                                    spread,
                                    point_value,
                                    spread_bp,
                                },
                            ),
                            raw: execution_price,
                            ts: fill_timestamp,
                            quantity,
                            point_value,
                            sizing: None,
                        });
                    }
                    Action::ClosePosition { id, price } => {
                        let execution_price = price;
                        if let Some(index) = slot
                            .tagged_positions
                            .iter()
                            .position(|position| position.id == Some(id))
                        {
                            let old = slot.tagged_positions.remove(index);
                            exposed -= position_notional(&old);
                            let exit = execution_price;
                            let gain =
                                net_pnl_after_financing(&old, exit, exit_timestamp, &slot.name);
                            equity += gain;
                            slot.realized += gain;
                            slot.closed_trades += 1;
                            trades.push(to_trade(
                                old,
                                exit_timestamp,
                                exit,
                                execution_price,
                                gain,
                                &slot.name,
                            ));
                            if let Some(closed) = trades.last() {
                                slot.contribution_equity
                                    .push((closed.exit_timestamp, cfg.initial + slot.realized));
                                *slot
                                    .monthly
                                    .entry(month_key(closed.exit_timestamp))
                                    .or_default() += gain;
                            }
                        }
                    }
                }
            }
        }

        for slot in &mut slots {
            // The session-end flatten prices the exit at the close of the bar
            // THIS SLOT was stepped with, which the step phase recorded. Pricing
            // it at whichever bar happened to end the instant would close a
            // JP225 position at an NQ close and book a ~74,000-point "profit"
            // that never existed.
            let Some(bar) = slot.flatten_bar.take() else {
                continue;
            };
            let max_drawdown_dollars = slot.strategy.max_drawdown_dollars();
            // THE SAFETY NET IS STILL THE STRATEGY'S EXIT, so it is stamped the
            // way that strategy stamps one. `DriftVwap` closes a survivor of a
            // short session at the NEXT five-minute slot -- Python's
            // `last.ts + 5 * 60` -- and eleven half-day exits read a minute
            // early without asking.
            let flatten_stamp = slot.strategy.exit_timestamp(bar.ts);
            if let Some(old) = slot.position.take() {
                let mut raw_exit = bar.close;
                let mut exit = raw_exit;
                if let Some(limit) = max_drawdown_dollars {
                    let floor = equity_peak - limit;
                    let closing_equity =
                        equity + net_pnl_after_financing(&old, exit, flatten_stamp, &slot.name);
                    if closing_equity < floor {
                        raw_exit = exit_raw_for_equity(&old, equity, floor);
                        exit = raw_exit;
                    }
                }
                let gain = net_pnl_after_financing(&old, exit, flatten_stamp, &slot.name);
                equity += gain;
                slot.realized += gain;
                slot.closed_trades += 1;
                trades.push(to_trade(
                    old,
                    flatten_stamp,
                    exit,
                    raw_exit,
                    gain,
                    &slot.name,
                ));
                if let Some(closed) = trades.last() {
                    slot.contribution_equity
                        .push((closed.exit_timestamp, cfg.initial + slot.realized));
                    *slot
                        .monthly
                        .entry(month_key(closed.exit_timestamp))
                        .or_default() += gain;
                }
                equity_peak = equity_peak.max(equity);
                drawdowns.observe(equity, day);
            }
            for old in slot.tagged_positions.drain(..) {
                let raw_exit = bar.close;
                let exit = raw_exit;
                let gain = net_pnl_after_financing(&old, exit, bar.ts, &slot.name);
                equity += gain;
                slot.realized += gain;
                slot.closed_trades += 1;
                trades.push(to_trade(old, bar.ts, exit, raw_exit, gain, &slot.name));
                if let Some(closed) = trades.last() {
                    slot.contribution_equity
                        .push((closed.exit_timestamp, cfg.initial + slot.realized));
                    *slot
                        .monthly
                        .entry(month_key(closed.exit_timestamp))
                        .or_default() += gain;
                }
            }
            equity_peak = equity_peak.max(equity);
            drawdowns.observe(equity, day);
        }
    }
    for slot in &mut slots {
        let max_drawdown_dollars = slot.strategy.max_drawdown_dollars();
        let slot_last = last_bar[slot.symbol_index];
        // A FundedNext position open when its market's data runs out is one
        // Python's log never closed, so it never existed; the Exness book keeps
        // its mark-to-last-bar close.
        if cfg.canon_replay
            && data_ended(&slot.name, &cfg)
            && crate::strategies::cfd::Sleeve::from_display(&slot.name)
                .is_some_and(|sleeve| sleeve.book() == crate::strategies::cfd::Book::FundedNext)
        {
            slot.position = None;
        }
        if let (Some(old), Some(last)) = (slot.position.take(), slot_last.as_ref()) {
            // Stamped by the strategy for the same reason the session-end net
            // is: a survivor closed here is that strategy's own last exit.
            let last_stamp = slot.strategy.exit_timestamp(last.ts);
            let mut raw_exit = last.close;
            let mut exit = raw_exit;
            if let Some(limit) = max_drawdown_dollars {
                let floor = equity_peak - limit;
                let closing_equity =
                    equity + net_pnl_after_financing(&old, exit, last_stamp, &slot.name);
                if closing_equity < floor {
                    raw_exit = exit_raw_for_equity(&old, equity, floor);
                    exit = raw_exit;
                }
            }
            let gain = net_pnl_after_financing(&old, exit, last_stamp, &slot.name);
            equity += gain;
            slot.realized += gain;
            slot.closed_trades += 1;
            trades.push(to_trade(old, last_stamp, exit, raw_exit, gain, &slot.name));
            if let Some(closed) = trades.last() {
                slot.contribution_equity
                    .push((closed.exit_timestamp, cfg.initial + slot.realized));
                *slot
                    .monthly
                    .entry(month_key(closed.exit_timestamp))
                    .or_default() += gain;
            }
            drawdowns.observe(equity, last.ts.div_euclid(86_400));
        }
        if let Some(last) = slot_last.as_ref() {
            for old in slot.tagged_positions.drain(..) {
                let raw_exit = last.close;
                let exit = raw_exit;
                let gain = net_pnl_after_financing(&old, exit, last.ts, &slot.name);
                equity += gain;
                slot.realized += gain;
                slot.closed_trades += 1;
                trades.push(to_trade(old, last.ts, exit, raw_exit, gain, &slot.name));
                if let Some(closed) = trades.last() {
                    slot.contribution_equity
                        .push((closed.exit_timestamp, cfg.initial + slot.realized));
                    *slot
                        .monthly
                        .entry(month_key(closed.exit_timestamp))
                        .or_default() += gain;
                }
            }
            drawdowns.observe(equity, last.ts.div_euclid(86_400));
        }
    }
    // Each slot appends its own book, so the merged log only needs re-ordering
    // when more than one strategy shared the account.
    if combined {
        trades.sort_by_key(|trade| trade.exit_timestamp);
    }
    let first = stream
        .iter()
        .map(|(_, bar)| bar)
        .find(|bar| bar.ts.div_euclid(86400) >= cfg.start_day)
        .map(|bar| bar.ts)
        .unwrap_or(stream[0].1.ts);
    let last = stream.last().unwrap().1.ts;
    let mut body = report(&trades, &cfg, first, last, equity, drawdowns.finish());
    body["below_broker_minimum"] = serde_json::json!(below_broker_minimum);
    // How much of the run the Exness fill model actually priced. A sleeve whose
    // entries fall outside the broker table's reach is on the idealised fill
    // there, and a total that hides that is kinder than the account.
    let coverage: std::collections::BTreeMap<&str, _> = slots
        .iter()
        .filter_map(|slot| Some((slot.name.as_str(), slot.strategy.fill_coverage()?)))
        .collect();
    body["fill_coverage"] = serde_json::json!(coverage);
    if cfg.gross_cap.is_some() {
        body["refused_by_gross_cap"] = serde_json::json!(refused_by_gross_cap);
    }
    let ruin_limit = slots
        .iter()
        .filter_map(|slot| slot.strategy.monte_carlo_drawdown_ruin_dollars())
        .min_by(f64::total_cmp);
    // Per-strategy attribution, most profitable first. Only meaningful for a
    // combined run: the shares reflect a shared, jointly compounding balance, so
    // they sum to the account's net profit rather than to standalone results.
    if combined && slots.iter().any(|slot| !slot.name.is_empty()) {
        let net = equity - cfg.initial;
        let gross: f64 = slots.iter().map(|slot| slot.realized.abs()).sum();
        let mut rows: Vec<serde_json::Value> = slots
            .iter()
            .map(|slot| {
                let mut tracker = DrawdownTracker::new(cfg.initial, cfg.start_day);
                for (timestamp, strategy_equity) in &slot.contribution_equity {
                    tracker.observe(*strategy_equity, timestamp.div_euclid(86_400));
                }
                let contribution_drawdowns = tracker.finish();
                serde_json::json!({
                    "strategy": slot.name,
                    "pnl": round2(slot.realized),
                    "trades": slot.closed_trades,
                    "avg_drawdown": round2(contribution_drawdowns.avg_dd),
                    "max_drawdown": round2(contribution_drawdowns.max_dd),
                    "share_pct": if gross > 0.0 {
                        round2(100.0 * slot.realized / gross)
                    } else {
                        0.0
                    },
                })
            })
            .collect();
        rows.sort_by(|a, b| {
            b["pnl"]
                .as_f64()
                .unwrap_or_default()
                .total_cmp(&a["pnl"].as_f64().unwrap_or_default())
        });
        body["contribution"] = serde_json::json!(rows);
        body["contribution_net"] = serde_json::json!(round2(net));

        // Month-by-month split, oldest first, so a strategy that earns its whole
        // share in one month is visible rather than averaged away.
        let months: std::collections::BTreeSet<&String> =
            slots.iter().flat_map(|slot| slot.monthly.keys()).collect();
        let monthly: Vec<serde_json::Value> = months
            .into_iter()
            .map(|month| {
                let by_strategy: serde_json::Map<String, serde_json::Value> = slots
                    .iter()
                    .map(|slot| {
                        (
                            slot.name.clone(),
                            serde_json::json!(round2(
                                slot.monthly.get(month).copied().unwrap_or(0.0)
                            )),
                        )
                    })
                    .collect();
                let total: f64 = slots
                    .iter()
                    .map(|slot| slot.monthly.get(month).copied().unwrap_or(0.0))
                    .sum();
                serde_json::json!({
                    "month": month,
                    "total": round2(total),
                    "by_strategy": by_strategy,
                })
            })
            .collect();
        body["contribution_monthly"] = serde_json::json!(monthly);
    }
    body["montecarlo"] = if let Some(limit) = ruin_limit {
        let pnls = trades.iter().map(|trade| trade.pnl).collect::<Vec<_>>();
        crate::backtest::monte_carlo::run_with_drawdown_ruin(&pnls, cfg.initial, limit)
    } else {
        montecarlo(&trades, cfg.initial)
    };
    RunResult { body, trades }
}
fn to_trade(
    pos: Position,
    exit_timestamp: i64,
    exit_price: f64,
    exit_raw: f64,
    gain: f64,
    strategy: &str,
) -> Trade {
    Trade {
        strategy: strategy.to_owned(),
        side: pos.side,
        entry_timestamp: pos.ts,
        exit_timestamp,
        entry_price: pos.entry,
        exit_price,
        pnl: gain,
        quantity: pos.quantity,
        entry_raw: pos.raw,
        exit_raw,
        distance: pos.sizing.map_or(f64::NAN, |sizing| sizing.0),
        vol_mult: pos.sizing.map_or(f64::NAN, |sizing| sizing.1),
    }
}
