//! The FundedNext book driven down the LIVE path over history, so a week of it
//! can be checked trade for trade against the backtest of that week
//! (`fundednext_live_replay`).
//!
//! Every sleeve is built by `build_strategy` -- the constructor the runtime
//! uses, early fills and the store-backed broker quotes included -- warmed from
//! the store exactly as `run` warms it, reset at the live boundary as
//! `LiveSlot::replay` resets it, and stepped minute by minute. The clock-driven
//! close (`LiveSlot::session_overdue`) is modelled from the bar stamps; a close
//! it takes leaves the strategy holding a ghost whose own exit is swallowed,
//! as the runtime does. No database, no orders.

use super::*;
use crate::api::parquet_store::ParquetStore;
use crate::backtest::data::{format_day, parse_iso_days};

/// One action the live runtime would have sent.
#[derive(Clone, Debug)]
pub struct ReplayEvent {
    pub strategy: String,
    /// `enter`, `exit` or `overdue` (a clock close).
    pub kind: &'static str,
    /// The action's research stamp (`action_timestamp`), New York seconds.
    pub ts: i64,
    /// The stamp of the bar that produced it.
    pub bar_ts: i64,
    /// The candle the action belongs to (`action_timestamp`): for an entry,
    /// the fill candle the backtest books it on.
    pub candle: i64,
    pub long: bool,
    pub quantity: f64,
    pub price: f64,
    pub distance: f64,
    pub mult: f64,
}

/// Replays `[first, last]` (New York days, inclusive) for every FundedNext
/// sleeve, warmed as a server started on `server_start` would warm it, sized
/// at a constant `balance`.
pub async fn replay_fundednext(
    store: &ParquetStore,
    first: &str,
    last: &str,
    balance: f64,
    server_start: &str,
) -> Result<Vec<ReplayEvent>, crate::error::ApiError> {
    let bad = |what: &str| crate::error::ApiError::BadRequest(format!("bad {what} date"));
    let first_day = parse_iso_days(first).ok_or_else(|| bad("first"))?;
    let last_day = parse_iso_days(last).ok_or_else(|| bad("last"))?;
    let start_day = parse_iso_days(server_start).ok_or_else(|| bad("server start"))?;
    quotes::set_store(store);
    // `LiveSlot::replay`'s boundary: the first bar AFTER `live_started_at`.
    let live_started_at = first_day * 86_400 - 1;
    let end = (last_day + 1) * 86_400;
    let book = crate::strategies::fundednext::oct_21_2026::BOOK;
    let mut markets: Vec<&'static str> = Vec::new();
    for sleeve in book.iter() {
        if !markets.contains(&sleeve.market()) {
            markets.push(sleeve.market());
        }
    }
    let mut events = Vec::new();
    for market in markets {
        let sleeves: Vec<_> = book.iter().filter(|s| s.market() == market).collect();
        // `WarmupPlan`: the longest requirement among the market's sleeves,
        // counted back from the server's start day.
        let days = sleeves
            .iter()
            .map(|s| warmup_calendar_days(market, warmup_sessions(s.id())))
            .max()
            .unwrap_or(1);
        let from = format_day(start_day - days);
        let mut bars =
            load_live_ohlcv_history(store, market, &from, &format_day(last_day)).await?;
        normalize_history(&mut bars, end - 1);
        let step = market_step(market);
        // The throttle seed, as `run` loads it.
        let prime = match bars.first() {
            Some(first) => {
                use crate::strategies::fundednext::oct_21_2026::{THROTTLE_FROM, throttle_marks};
                let first_day = first.ts.div_euclid(86_400);
                let from_day = THROTTLE_FROM.div_euclid(86_400);
                if first_day <= from_day {
                    Vec::new()
                } else {
                    let mut older = load_live_ohlcv_history(
                        store,
                        market,
                        &format_day(from_day),
                        &format_day(first_day - 1),
                    )
                    .await?;
                    older.retain(|bar| bar.ts < first.ts);
                    throttle_marks(&older)
                }
            }
            None => Vec::new(),
        };
        for sleeve in sleeves {
            let mut strategy = if std::env::var("REPLAY_NO_EARLY").is_ok() {
                // Diagnostic: the live sleeve without early fills.
                let mut plain = crate::strategies::cfd::CfdSleeve::new(*sleeve, 0.01);
                plain.set_sizing_cap(crate::strategies::fundednext::oct_21_2026::INITIAL_BALANCE);
                if let Some(table) = crate::backtest::fills::broker_minute_table(sleeve.market(), sleeve.book())
                    && let Some(quotes) = quotes::for_table(&table)
                {
                    plain.install_quotes(quotes);
                }
                Box::new(plain) as Box<dyn Strategy>
            } else {
                build_strategy(sleeve.id()).ok_or_else(|| {
                    crate::error::ApiError::Internal(format!("{} not buildable", sleeve.id()))
                })?
            };
            // Diagnostic: the same sleeve built the BACKTEST way (broker fills,
            // bars pre-filtered at its width), stepped beside it, both traced.
            let trace = std::env::var("REPLAY_TRACE").is_ok_and(|id| id == sleeve.id());
            let mut twin = None;
            if trace {
                let fills = crate::backtest::fills::MarketFills::load(
                    store,
                    sleeve.market(),
                    sleeve.book(),
                    sleeve.shift_hours(),
                    None,
                    None,
                )?
                .map(std::sync::Arc::new);
                let mut b = crate::strategies::cfd::CfdSleeve::new(*sleeve, 0.01);
                b.set_sizing_cap(crate::strategies::fundednext::oct_21_2026::INITIAL_BALANCE);
                if let Some(fills) = &fills {
                    b.install_fills(std::sync::Arc::clone(fills));
                    b.install_quotes(std::sync::Arc::clone(fills) as std::sync::Arc<dyn crate::strategies::cfd::BrokerQuotes>);
                }
                twin = Some((b, fills, false));
            }
            strategy.prime_throttle(&prime);
            let overdue = session_overdue_seconds(sleeve.id()) + SESSION_OVERDUE_GRACE_SECONDS;
            let mut trading = false;
            let mut holding = false;
            let mut ghost = false;
            let mut previous: Option<i64> = None;
            for bar in &bars {
                // THE CLOCK CLOSE, between the previous bar's arrival and this
                // one's: the first instant the runtime's 200ms poll would find
                // the slot outside its session and one candle plus the grace
                // past its close.
                if let (Some(prev), true, false) = (previous, holding, ghost)
                    && !strategy.holds_overnight()
                    && let (Some(start), Some(close)) =
                        (strategy.session_start_minute(), strategy.session_end_minute())
                {
                    let arrived = prev + step;
                    let next_arrival = bar.ts + step;
                    let first_day_of = arrived.div_euclid(86_400);
                    for day in first_day_of..=next_arrival.div_euclid(86_400) {
                        let deadline = day * 86_400 + close as i64 * 60 + overdue;
                        let minute = deadline.rem_euclid(86_400) / 60;
                        let (s, e) = (start as i64, close as i64);
                        let inside = if s <= e {
                            (s..=e).contains(&minute)
                        } else {
                            minute >= s || minute <= e
                        };
                        if deadline > arrived && deadline <= next_arrival && !inside {
                            events.push(ReplayEvent {
                                strategy: sleeve.id().to_owned(),
                                kind: "overdue",
                                ts: deadline,
                                bar_ts: prev,
                                candle: deadline,
                                long: false,
                                quantity: 0.0,
                                price: 0.0,
                                distance: f64::NAN,
                                mult: f64::NAN,
                            });
                            ghost = true;
                            break;
                        }
                    }
                }
                previous = Some(bar.ts);
                if !trading && bar.ts > live_started_at {
                    trading = true;
                    strategy.reset_trading_state();
                }
                if let Some((b, fills, reset)) = twin.as_mut() {
                    let width = sleeve.candle_seconds();
                    let shift = sleeve.shift_hours() * 3_600;
                    let keep = fills.as_ref().is_none_or(|f| {
                        f.quoted((bar.ts + shift).div_euclid(width) * width, width)
                    });
                    if keep {
                        if !*reset && bar.ts > live_started_at {
                            *reset = true;
                            b.reset_trading_state();
                        }
                        let acts = b.update_all(*bar, balance);
                        if bar.ts > live_started_at {
                            for a in &acts {
                                eprintln!("BT   bar {} at {} {:?}", format_ts(bar.ts), format_ts(b.action_timestamp(bar.ts)), a);
                            }
                        }
                    }
                }
                let actions = strategy.update_all(*bar, balance);
                if trace && bar.ts > live_started_at {
                    for a in &actions {
                        eprintln!("LIVE bar {} at {} {:?}", format_ts(bar.ts), format_ts(strategy.action_timestamp(bar.ts)), a);
                    }
                }
                if bar.ts <= live_started_at {
                    strategy.discard_all(actions);
                    continue;
                }
                let at = strategy.action_timestamp(bar.ts);
                for action in actions {
                    match action {
                        Action::Enter { side, price, quantity } => {
                            let (distance, mult) =
                                strategy.entry_sizing().unwrap_or((f64::NAN, f64::NAN));
                            events.push(ReplayEvent {
                                strategy: sleeve.id().to_owned(),
                                kind: "enter",
                                // When the order goes out: the arrival of the
                                // bar that produced it (the fill candle's open
                                // for an entry sent at its signal).
                                ts: bar.ts + step,
                                bar_ts: bar.ts,
                                candle: at,
                                long: side == Side::Long,
                                quantity,
                                price,
                                distance,
                                mult,
                            });
                            holding = true;
                            ghost = false;
                        }
                        Action::Close { price, fraction } if fraction >= 1.0 - 1e-9 => {
                            holding = false;
                            if std::mem::take(&mut ghost) {
                                continue;
                            }
                            events.push(ReplayEvent {
                                strategy: sleeve.id().to_owned(),
                                kind: "exit",
                                ts: strategy.exit_timestamp(at),
                                bar_ts: bar.ts,
                                candle: at,
                                long: false,
                                quantity: 0.0,
                                price,
                                distance: f64::NAN,
                                mult: f64::NAN,
                            });
                        }
                        _ => {}
                    }
                }
            }
        }
    }
    Ok(events)
}
