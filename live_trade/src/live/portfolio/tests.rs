//! Unit tests for the live portfolio runtime.

use super::slot::{LiveSlot, LogicalPosition};
use super::*;
use std::cell::RefCell;
use std::rc::Rc;

fn warmup_target(id: i64, strategy: &str) -> LiveStrategyTarget {
    LiveStrategyTarget {
        account_strategy_id: id,
        account_id: 1,
        environment_id: 1,
        strategy: strategy.into(),
        symbol: strategy_symbol(strategy).into(),
        live_started_at: 1,
        equity: 1_000.0,
        connected: true,
    }
}

/// The regression the `QuantityUnit` distinction exists for. On 2026-08-12
/// `ethbtc_gap` asked for 0.57 ETH and the runtime sent 0.57 LOTS, which
/// ETHBTC's 100-ETH contract turned into 57 ETH -- about $107,434 of
/// notional against a ~$1,100 account
/// ([[ethbtc-live-size-is-100x-the-contract-size]]).
///
/// That sleeve is gone and every member of this book emits lots, so the
/// conversion is exercised against a synthetic name rather than deleted:
/// the arithmetic is what must not regress, and an unregistered id is the
/// only way left to reach the `Coins` branch.
#[test]
fn a_coin_quantity_would_still_be_converted_to_lots() {
    let hundred_coin_contract = LotSpec {
        contract_size: 100.0,
        min_lots: 0.01,
    };
    let lots = 0.57 / hundred_coin_contract.contract_size;
    assert!(
        (lots - 0.0057).abs() < 1e-12,
        "0.57 coins of a 100-coin contract is 0.0057 lots, got {lots}"
    );
    assert!(lots < hundred_coin_contract.min_lots);
}

/// `exness_combined` sizes through each market's own `volume_step` and
/// `contract_size`, so what it returns is already a lot volume. Converting
/// it again would divide a USDJPY order by 100,000.
#[test]
fn every_sleeve_passes_its_quantity_through_untouched() {
    for strategy in LIVE_STRATEGIES {
        assert_eq!(quantity_unit(strategy), QuantityUnit::Lots, "{strategy}");
        assert_eq!(
            to_lot_volume(strategy, 0.24),
            0.24,
            "{strategy} must not be rescaled"
        );
    }
}

/// The floor is per-market, not a single hard-coded 0.01: ETHUSD is 0.10 and
/// JP225 refuses anything under THREE whole lots. These must agree with the
/// frozen specs the strategy sizes against, or the runtime and the strategy
/// disagree about what is placeable.
#[test]
fn minimum_lots_match_the_frozen_specs() {
    assert_eq!(lot_spec("ethusd_confluence").min_lots, 0.10);
    assert_eq!(lot_spec("usdjpy_volume_thrust").min_lots, 0.01);
    assert_eq!(lot_spec("usdjpy_volume_thrust").contract_size, 100_000.0);
    // Every sleeve's floor is the one its own `Instrument` sizes against.
    for sleeve in exness_combined::BOOK {
        assert_eq!(
            lot_spec(sleeve.id()).min_lots,
            sleeve.broker_minimum_lots(),
            "{} disagrees with its frozen spec",
            sleeve.display()
        );
    }
}

/// THE CONFIGURED SYMBOL IS FREE TEXT AND MUST NOT DECIDE THE LOT FLOOR.
///
/// `create_account_strategy` stores whatever a person typed, so the same
/// sleeve can arrive as `jp225`, `JP225` or `Jp225`. The old table was keyed
/// on that string with lowercase entries and an NQ fall-through, so `JP225`
/// resolved to a 0.05 floor against a real minimum of three lots.
#[test]
fn the_lot_floor_ignores_how_the_symbol_was_spelled() {
    // ETHUSD since 2026-09-23: its 0.10 floor is the highest in the book now
    // that JP225's three lots have left, and still ten times the FX step.
    let mut slot = slot_for("ethusd_pullback");
    for spelling in ["ethusd", "ETHUSD", " Ethusd "] {
        slot.target.symbol = spelling.into();
        assert_eq!(
            lot_spec(&slot.target.strategy).min_lots,
            0.10,
            "spelling {spelling} changed the ETHUSD floor"
        );
        let under_floor = Action::Enter {
            side: Side::Long,
            price: 2_500.0,
            quantity: 0.05,
        };
        assert_eq!(
            slot.entry_refusal(under_floor, true),
            Some("strategy volume is below the broker minimum"),
            "spelling {spelling} admitted an order ETHUSD rejects"
        );
    }
}

/// The live list and the book are one decision recorded twice; this is what
/// stops them drifting apart.
#[test]
fn live_strategies_match_the_book() {
    let book: Vec<&str> = exness_combined::BOOK
        .iter()
        .map(|sleeve| sleeve.id())
        .collect();
    assert_eq!(LIVE_STRATEGIES.to_vec(), book);
}

/// The backtest display name and the live id must resolve to the same
/// sleeve, or a backtest and its live twin trade different instruments.
#[test]
fn the_two_spellings_agree_on_every_market() {
    for sleeve in exness_combined::BOOK {
        assert_eq!(
            strategy_symbol(sleeve.id()),
            crate::strategies::market_symbol(sleeve.display()),
            "{} routes differently live and in backtest",
            sleeve.display()
        );
    }
}

#[test]
fn warmup_loads_only_active_markets() {
    let plan = WarmupPlan::for_targets(&[warmup_target(1, "ukoil_level_confluence")]);
    assert_eq!(plan.calendar_days.len(), 1);
    assert!(plan.calendar_days.contains_key("ukoil"));
    assert!(!plan.calendar_days.contains_key(SYMBOL));
    // No member of this book gates on VIX or needs a quote-rate series.
}

#[test]
fn warmup_uses_longest_requirement_per_active_symbol() {
    let plan = WarmupPlan::for_targets(&[
        warmup_target(1, "ethusd_pullback"),
        warmup_target(2, "ethusd_kalman"),
        warmup_target(3, "usdjpy_rvol"),
    ]);
    // Sleeves sharing a feed collapse into ONE requirement -- the deepest of
    // them -- and a second market gets its own. The requirement is the sleeve's
    // own answer rather than a number written here, so a re-fitted cell moves
    // this test with it instead of silently outgrowing it.
    let ethusd = Sleeve::from_id("ethusd_pullback")
        .unwrap()
        .warmup_sessions()
        .max(Sleeve::from_id("ethusd_kalman").unwrap().warmup_sessions());
    assert_eq!(
        plan.calendar_days["ethusd"],
        warmup_calendar_days("ethusd", ethusd)
    );
    assert_eq!(
        plan.calendar_days["usdjpy"],
        warmup_calendar_days(
            "usdjpy",
            Sleeve::from_id("usdjpy_rvol").unwrap().warmup_sessions()
        )
    );
}

/// Crypto quotes every calendar day; a weekday market needs room for
/// weekends and exchange holidays. Getting this backwards would leave a
/// `calm` cell refusing while its neighbours traded.
#[test]
fn warmup_calendar_days_account_for_the_market_week() {
    assert_eq!(warmup_calendar_days(ETHUSD_SYMBOL, 100), 102);
    assert!(warmup_calendar_days("usdjpy", 100) > 130);
}

#[test]
fn requested_strategies_are_live_capable() {
    for strategy in LIVE_STRATEGIES {
        assert!(supports(strategy));
    }
    // A retired id must NOT build: a slot that looks enabled and trades
    // nothing is worse than one that refuses to start.
    assert!(build_strategy("ofi_momentum").is_none());
    assert!(build_strategy("hourly_delta_reversal").is_none());
    assert!(build_strategy("btc_maroy_ladder").is_none());
    // Every registered strategy must be constructible, or the slot silently
    // refuses to warm and the strategy looks enabled while trading nothing.
    for strategy in LIVE_STRATEGIES {
        assert!(
            build_strategy(strategy).is_some(),
            "{strategy} is registered but not constructible"
        );
    }
}

/// A sleeve stepped with another market's bar would break out off the wrong
/// price and send a real order for it, so the symbol split is the safety
/// property that matters most in this file.
#[test]
fn every_live_strategy_routes_to_exactly_one_market() {
    assert_eq!(strategy_symbol("usdjpy_rvol"), "usdjpy");
    assert_eq!(strategy_symbol("usdjpy_rvol"), "usdjpy");
    assert_eq!(strategy_symbol("ethusd_confluence"), ETHUSD_SYMBOL);
    let markets = crate::strategies::known_markets();
    for strategy in LIVE_STRATEGIES {
        let symbol = strategy_symbol(strategy);
        assert!(
            markets.contains(&symbol),
            "{strategy} routes to an unknown market"
        );
        assert!(
            route_symbol(symbol).is_ok(),
            "{strategy} routes to {symbol}, which the broker mapping rejects"
        );
    }
}

/// The broker's spelling is not the table's, and a wrong one sends a real
/// order for the wrong instrument.
#[test]
fn every_market_routes_to_its_broker_symbol() {
    assert_eq!(route_symbol("nq").unwrap(), "USTEC");
    assert_eq!(route_symbol("USTEC").unwrap(), "USTEC");
    assert_eq!(route_symbol("jp225").unwrap(), "JP225");
    assert_eq!(route_symbol("usdjpy").unwrap(), "USDJPY");
    // Markets that went with the previous book must now be refused rather
    // than routed to a symbol nothing trades.
    assert!(route_symbol("xalusd").is_err());
    assert_eq!(route_symbol("uk100").unwrap(), "UK100");
    assert_eq!(route_symbol("btc").unwrap(), "BTCUSD");
    assert!(route_symbol("msft").is_err());
    assert!(route_symbol("ES").is_err());
}

/// NO SLEEVE READS THE LEVEL-TWO FEED, and the routing that would give one to
/// an NQ sleeve is still in place.
///
/// NQ was barred as a symbol on 2026-09-03 and took the level-two feed with it.
/// `strategy_feed` still answers `LevelTwo` for anything on that market, so this
/// asserts the CONSEQUENCE -- every live slot is on OHLCV -- rather than
/// asserting the rule has been deleted, which it has not.
#[test]
fn no_live_strategy_reads_the_level_two_feed() {
    let on_level_two: Vec<&str> = LIVE_STRATEGIES
        .iter()
        .copied()
        .filter(|strategy| strategy_feed(strategy) == LiveFeed::LevelTwo)
        .collect();
    assert!(on_level_two.is_empty(), "{on_level_two:?}");
    // The rule itself is unchanged: an NQ id would still be routed to the
    // level-two feed if one were ever seated again.
    assert_eq!(strategy_feed("nq_ofi"), LiveFeed::Ohlcv);
    assert_eq!(strategy_feed("usdjpy_rvol"), LiveFeed::Ohlcv);
}

#[test]
fn catch_up_order_is_deterministic_across_markets_and_sources() {
    assert!(
        live_stream_rank(SYMBOL, LiveFeed::LevelTwo) < live_stream_rank(SYMBOL, LiveFeed::Ohlcv)
    );
    assert!(
        live_stream_rank(SYMBOL, LiveFeed::Ohlcv)
            < live_stream_rank(ETHUSD_SYMBOL, LiveFeed::Ohlcv)
    );
    // Every market ranks behind both NQ feeds, and the rest rank by where
    // they first appear in `BOOK`.
    assert!(
        live_stream_rank(SYMBOL, LiveFeed::Ohlcv) < live_stream_rank("usdjpy", LiveFeed::Ohlcv)
    );
    assert!(
        live_stream_rank("usdjpy", LiveFeed::Ohlcv)
            < live_stream_rank(ETHUSD_SYMBOL, LiveFeed::Ohlcv)
    );
}

/// THE WATCHDOG FOLLOWS THE SOURCE TABLE, and `xniusd` was why.
///
/// A feed cannot be stale before one of its own bars could have completed, so a
/// market served by a 30-minute table has to be given 30 minutes. Holding it to
/// the one-minute interval would declare it dead every three minutes and
/// emergency-flatten a position the market was still perfectly happy to hold.
///
/// The same rule is what steps the live cursor, so the two cannot disagree: a
/// market stepped by 60 against a 1,800-second table refetches one bar
/// twenty-nine times and hands every copy to the strategy.
#[test]
fn the_watchdog_matches_each_market_s_own_bar_size() {
    // EVERY MARKET THIS BOOK TRADES HAS A ONE-MINUTE TABLE since `xniusd` left
    // on 2026-09-03, so the 30-minute branch is unreached here -- the rule is
    // still asserted against `xniusd` itself below, because it is the rule and
    // not the membership that this test is about.
    for market in crate::strategies::known_markets()
        .into_iter()
        .chain(std::iter::once("xniusd"))
    {
        let native_30m = market == "xniusd";
        let expected = if native_30m { 1_890 } else { 180 };
        assert_eq!(
            feed_stale_after(market),
            Duration::from_secs(expected),
            "{market}"
        );
        assert_eq!(
            market_step(market),
            if native_30m { 1_800 } else { 60 },
            "{market}"
        );
    }
}

/// NO SLEEVE READS A SECOND MARKET, so the live loop fetches none.
///
/// `ethusd:idio_break` was the only one and the decay screen removed it on
/// 2026-09-03. The failure this guards against is silent -- a benchmarked sleeve
/// whose second market is not fetched does not error, it simply never fires --
/// so the assertion is kept and inverted rather than deleted.
#[test]
fn no_market_needs_a_benchmark() {
    assert!(benchmark_markets().is_empty());
    assert_eq!(benchmark_for(ETHUSD_SYMBOL), None);
    assert_eq!(benchmark_for(SYMBOL), None);
}

/// A bar at `minute` of the New York day, which is all the session-end
/// window reads off it.
fn bar_at_minute(minute: i64) -> Bar {
    bar_at_ts(minute * 60)
}

/// A bar at an absolute stamp, for the cases that care WHICH DAY it is rather
/// than only where it falls in one -- the learned clock reads a real epoch.
fn bar_at_ts(ts: i64) -> Bar {
    Bar {
        ts,
        open: 100.0,
        high: 100.0,
        low: 100.0,
        close: 100.0,
        volume: 1.0,
        volume_delta: 0.0,
        depth_events: 0,
        level_two: false,
        order_flow: Default::default(),
        benchmark: None,
    }
}

fn slot_for(strategy: &str) -> LiveSlot {
    let mut slot = test_slot();
    slot.target.strategy = strategy.into();
    slot.target.symbol = strategy_symbol(strategy).into();
    slot.strategy = build_strategy(strategy).expect("live strategy builds");
    slot
}

pub(super) fn test_slot() -> LiveSlot {
    LiveSlot {
        target: LiveStrategyTarget {
            account_strategy_id: 1,
            account_id: 1,
            environment_id: 1,
            strategy: "usdjpy_volume_thrust".into(),
            symbol: "usdjpy".into(),
            live_started_at: 0,
            equity: 1000.0,
            connected: true,
        },
        strategy: build_strategy("usdjpy_volume_thrust").unwrap(),
        positions: HashMap::new(),
        pending_opens: HashMap::new(),
        deferred_entries: HashMap::new(),
        pending_exits: HashMap::new(),
        blocked: None,
    }
}

/// JP225 trades a session that straddles New York midnight, so its
/// `session_end_minute` (02:00) sits BELOW every minute it actually trades.
/// The old open-ended `minute + 1 >= session_end` was therefore true for the
/// entire session, and live opened and closed every entry on the same bar
/// with `exit_reason = session_end` -- the 2026-08-13 21:01 AUS200 trade.
///
/// Nothing in the backtest can catch this: `engine.rs` guards its flatten
/// with `minute < session_end`, which is false all evening, so only live
/// ever flattened. JP225 is the only wrapping session left in the book,
/// which is why this test outlived the sleeves it was written for.
#[test]
#[ignore = "no wrapping-session market is live since 2026-09-23, when every jp225 sleeve left; re-enable if jp225 or hk50 is re-seated"]
fn wrapping_sessions_do_not_flatten_all_evening() {
    for (strategy, trading_minute) in [("jp225_volume_thrust", 21 * 60), ("jp225_cusum", 22 * 60)] {
        let slot = slot_for(strategy);
        assert!(
            !slot.in_session_end_window(bar_at_minute(trading_minute)),
            "{strategy} treats a mid-session bar as its session end"
        );
        let session_end = slot.strategy.session_end_minute().unwrap() as i64;
        assert!(
            slot.in_session_end_window(bar_at_minute(session_end - 1)),
            "{strategy} misses its own flatten bar"
        );
    }
}

/// A wrapping session must be recognised as a session. JP225 trades
/// 19:00-02:00, so its start (1140) is ABOVE its end (120) and the plain
/// range test is false for every minute it actually trades.
#[test]
#[ignore = "no wrapping-session market is live since 2026-09-23, when every jp225 sleeve left; re-enable if jp225 or hk50 is re-seated"]
fn a_wrapping_session_contains_the_minutes_it_trades() {
    let slot = slot_for("jp225_cusum");
    assert_eq!(slot.strategy.session_start_minute(), Some(19 * 60));
    assert_eq!(slot.strategy.session_end_minute(), Some(2 * 60));
    for minute in [19 * 60, 21 * 60, 23 * 60 + 59, 0, 60, 120] {
        assert!(
            slot.in_trading_session(minute),
            "JP225 does not recognise minute {minute} as its own session"
        );
    }
    // The two-hour daily break and the whole New York afternoon are outside
    // it, which is what stops the feed watchdog reading a scheduled close as
    // a dead feed.
    for minute in [2 * 60 + 1, 9 * 60, 14 * 60, 16 * 60, 18 * 60] {
        assert!(
            !slot.in_trading_session(minute),
            "JP225 treats minute {minute} as in-session"
        );
    }
}

/// Every sleeve must declare a session window, because the feed watchdog
/// reads it to tell a dead feed from a market that closed on schedule. A
/// missing start fails open, which is safe but silently restores the bug.
#[test]
fn every_live_strategy_declares_a_session_window() {
    for strategy in LIVE_STRATEGIES {
        let slot = slot_for(strategy);
        let start = slot
            .strategy
            .session_start_minute()
            .unwrap_or_else(|| panic!("{strategy} has no session start"));
        let end = slot
            .strategy
            .session_end_minute()
            .unwrap_or_else(|| panic!("{strategy} has no session end"));
        assert_ne!(start, end, "{strategy} reports an empty session");
        assert!(slot.in_trading_session(start as i64), "{strategy}");
        assert!(slot.in_trading_session(end as i64), "{strategy}");
    }
}

/// The grace window exists for a feed gap over the flatten minute, but it
/// must not run so long that it reaches back into the session.
#[test]
fn session_end_window_tolerates_a_feed_gap_but_stays_bounded() {
    let slot = slot_for("usdjpy_volume_thrust");
    let session_end = slot.strategy.session_end_minute().unwrap() as i64;
    assert!(slot.in_session_end_window(bar_at_minute(session_end - 1)));
    assert!(slot.in_session_end_window(bar_at_minute(session_end + 30)));
    assert!(!slot.in_session_end_window(bar_at_minute(session_end + 200)));
}

/// A strategy that emits its own session-close exit must never also be
/// flattened by the runtime: the generic flatten fires first and takes the
/// strategy's exit away, which is what made `XALUSD PDR` return -2.08%
/// against Python's +31.17% ([[double-session-flatten-cut-targets-short]]).
/// `engine.rs` has honoured `flattens_itself()` since that fix; live had
/// not, so the double flatten stayed live-only.
///
/// EVERY sleeve owns its exit since 2026-09-04. The two that did not were the
/// NQ imports, written against the runtime's flatten, and both left with the
/// symbol -- so the exception this test used to carry is gone rather than
/// merely unreached.
#[test]
fn self_flattening_strategies_are_left_alone() {
    for strategy in LIVE_STRATEGIES {
        let slot = slot_for(strategy);
        let session_end = slot.strategy.session_end_minute().unwrap() as i64;
        assert!(
            slot.strategy.flattens_itself(),
            "{strategy} no longer owns its session exit"
        );
        assert!(
            !slot.at_session_end(bar_at_minute(session_end - 1)),
            "{strategy} is flattened by the runtime on top of its own exit"
        );
    }
}

/// The clock-driven close is only as good as the offset it learns, and that
/// offset is the one thing here derived from the host machine rather than from
/// the data. A bar stamped in New York wall clock, arriving now, must place the
/// New York day where that bar says it is -- whatever timezone the machine is
/// set to, and with the feed's own few seconds of lag rounded away.
#[test]
fn the_new_york_offset_is_learned_from_a_bar_and_outlives_it() {
    let mut runtime = runtime_holding("usdjpy_rvol", 0);
    assert!(
        runtime.ny_second_of_day().is_none(),
        "a runtime that has seen no bar must not claim to know the time"
    );

    // Pretend New York is four hours behind the host, and hand over a bar that
    // closed three seconds ago in New York terms -- which is what the feed
    // actually delivers, and the only shape the hour-rounding is meant for.
    for offset in [-4 * 3_600, -5 * 3_600] {
        let utc = runtime.utc_now().expect("host clock");
        let closed_ny = utc + offset - 3;
        runtime.observe_clock("usdjpy", bar_at_ts(closed_ny - 60));

        let second = runtime.ny_second_of_day().expect("offset learned");
        let expected = (utc + offset).rem_euclid(86_400);
        let drift = (second - expected).rem_euclid(86_400);
        assert!(
            drift <= 2 || drift >= 86_398,
            "offset {offset}: clock reads {second}, expected about {expected}"
        );
    }
}

/// Every other exit here is driven by a bar ARRIVING on the market being
/// exited, so a vendor that stops publishing switches all of them off at once
/// and the broker leg is left open -- two USDJPY sleeves sat nearly six hours
/// past a 13:00 close that way on 2026-09-07. `session_overdue` is asked of the
/// clock instead, so no bar from that market is needed to answer it.
///
/// The bounds are the point. Inside its own session a slot is never overdue; one
/// bar past the close still belongs to the strategy, whose own exit rides the
/// candle AT `session_end` and cannot be seen until the next slot opens; past
/// that there is no reading in which the position should still be open.
#[test]
fn a_position_left_past_its_session_close_is_flattened_on_the_clock() {
    for strategy in LIVE_STRATEGIES {
        let mut slot = slot_for(strategy);
        let session_end = slot.strategy.session_end_minute().unwrap() as i64;
        // Seconds into the New York day, `offset` seconds past the close.
        let at = |offset: i64| (session_end * 60 + offset).rem_euclid(86_400);

        // Holding nothing is never overdue, whatever the clock says.
        assert!(
            !slot.session_overdue(at(3_600)),
            "{strategy} reports overdue while flat"
        );

        slot.positions.insert(
            "primary".to_owned(),
            LogicalPosition {
                side: Side::Long,
                volume: 3.0,
            },
        );
        // One bar past the close is the instant `fill_models.exness` prices
        // the exit at; the few seconds of grace only let a healthy feed's own
        // exit land first.
        assert!(
            !slot.session_overdue(at(30 * 60)),
            "{strategy} is closed before the model's own exit instant"
        );
        assert!(
            !slot.session_overdue(at(30 * 60 + 9)),
            "{strategy} races the strategy's own exit inside the grace"
        );
        assert!(
            slot.session_overdue(at(30 * 60 + 10)),
            "{strategy} left open past its close with no bar to trigger an exit"
        );
        assert!(
            slot.session_overdue(at(4 * 3_600)),
            "{strategy} left open past its close with no bar to trigger an exit"
        );
        // Deep inside the session -- one minute after it opens -- the ring
        // arithmetic must not read as "long past the close".
        let start = slot.strategy.session_start_minute().unwrap() as i64;
        assert!(
            !slot.session_overdue(((start + 1) * 60).rem_euclid(86_400)),
            "{strategy} is flattened one minute into its own session"
        );
    }
}

/// `ethusd_confluence` completes its 15:30 candle on the bar that OPENS the
/// 16:00 slot, so a pending entry is emitted at minute 960 -- exactly where
/// the flatten used to fire. Every live entry died on its own bar.
#[test]
fn an_entry_on_the_close_bar_survives() {
    let slot = slot_for("ethusd_confluence");
    assert!(!slot.at_session_end(bar_at_minute(16 * 60)));
}

/// Every sleeve must report a session end, because the LIVE runtime drives
/// its own flatten from it even where the strategy also flattens itself
/// ([[live-vs-backtest-parity]]).
///
/// The minute is REAL New York, not the shifted clock the Asian session is
/// evaluated on: JP225's 08:00 shifted close is 02:00 New York, and a
/// flattener working in shifted minutes would sit six hours out.
#[test]
fn every_live_strategy_reports_its_real_session_end() {
    for strategy in LIVE_STRATEGIES {
        let built =
            build_strategy(strategy).unwrap_or_else(|| panic!("{strategy} is not constructible"));
        let session_end = built
            .session_end_minute()
            .unwrap_or_else(|| panic!("{strategy} has no session end minute"));
        let expected = match strategy_symbol(strategy) {
            // Shifted six hours: 08:00 on the JP225 clock is 02:00 New York,
            // and 10:00 on the HK50 clock is 04:00.
            "jp225" => 120,
            "hk50" => 240,
            "usdjpy" => 780,
            "audusd" => 840,
            "gbpjpy" => 810,
            "eurjpy" => 870,
            "ukoil" => 870,
            "ethusd" => 960,
            other => panic!("{other} has no expected session end"),
        };
        assert_eq!(session_end, expected, "{strategy} session end changed");
    }
}

/// What the replay did, as seen from inside the strategy. Every other part
/// of a warm-up needs SQLite; the preroll boundary needs only this.
#[derive(Default)]
struct Replayed {
    stepped: Vec<i64>,
    /// How many bars had been stepped each time the runtime announced the
    /// boundary. One entry, in the right place, is the whole contract.
    reset_after: Vec<usize>,
}

/// Shared with the slot's boxed strategy so the record outlives it.
struct BoundarySpy(Rc<RefCell<Replayed>>);

impl Strategy for BoundarySpy {
    fn update(&mut self, bar: Bar, _equity: f64) -> Action {
        self.0.borrow_mut().stepped.push(bar.ts);
        Action::Hold
    }

    fn reset_trading_state(&mut self) {
        let stepped = self.0.borrow().stepped.len();
        self.0.borrow_mut().reset_after.push(stepped);
    }
}

fn replayed(live_started_at: i64, bars: &[i64]) -> Replayed {
    let record = Rc::new(RefCell::new(Replayed::default()));
    let mut slot = test_slot();
    slot.target.live_started_at = live_started_at;
    slot.strategy = Box::new(BoundarySpy(Rc::clone(&record)));
    let history: Vec<Bar> = bars
        .iter()
        .map(|ts| {
            let mut bar = bar_at_minute(0);
            bar.ts = *ts;
            bar
        })
        .collect();
    slot.replay(&history);
    drop(slot);
    Rc::try_unwrap(record)
        .unwrap_or_else(|_| unreachable!("the slot was the only other holder"))
        .into_inner()
}

/// THE PREROLL BOUNDARY IS THE ENGINE'S `lo` AND MUST BE ANNOUNCED ONCE.
///
/// `FamilyEngine` carries a shadow account that decides which trades exist,
/// and `reset_trading_state` is what returns it to `ADMISSION_BALANCE` at
/// the start of a reported window. `engine.rs` calls it at the preroll
/// boundary; live replayed a hundred sessions of warm-up straight into the
/// live bars and never called it at all, so every slot answered the
/// admission question against a compounded balance no backtest of this book
/// ever had.
#[test]
fn the_warm_up_announces_the_preroll_boundary_exactly_once() {
    // Boundary inside the history: reset after the two preroll bars and
    // before the two live ones.
    let crossed = replayed(200, &[100, 200, 300, 400]);
    assert_eq!(crossed.stepped, vec![100, 200, 300, 400]);
    assert_eq!(
        crossed.reset_after,
        vec![2],
        "the boundary must be announced once, between bar 200 and bar 300"
    );
}

/// A strategy activated right now has a `live_started_at` at or after the
/// newest warm-up bar, so the whole replay is preroll -- and that is every
/// first start, the case that matters most.
#[test]
fn a_freshly_activated_slot_still_gets_its_boundary() {
    let fresh = replayed(10_000, &[100, 200, 300]);
    assert_eq!(fresh.stepped, vec![100, 200, 300]);
    assert_eq!(
        fresh.reset_after,
        vec![3],
        "an all-preroll replay must announce the boundary at its end"
    );
}

/// A restart whose durable start predates the warm-up window has no
/// boundary inside it; the closest available one is the first bar.
#[test]
fn a_long_running_slot_resets_at_the_start_of_its_history() {
    let old = replayed(0, &[100, 200, 300]);
    assert_eq!(old.reset_after, vec![0]);
}

/// A runtime holding one slot, whose stream's newest bar is at
/// `last_bar_minute`. `Database::new` is lazy -- it opens nothing until a
/// query is issued -- so the pure feed predicates can be exercised without
/// standing up SQLite.
fn runtime_holding(strategy: &str, last_bar_minute: i64) -> PortfolioRuntime {
    let mut slot = slot_for(strategy);
    slot.positions.insert(
        "primary".to_owned(),
        LogicalPosition {
            side: Side::Long,
            volume: 3.0,
        },
    );
    let market = strategy_symbol(strategy);
    let mut runtime = PortfolioRuntime {
        db: Database::new("unused-in-this-test"),
        slots: HashMap::new(),
        level_two_history: Vec::new(),
        ohlcv_history: Vec::new(),
        market_histories: HashMap::new(),
        market_latest: HashMap::new(),
        benchmark_closes: HashMap::new(),
        benchmark_latest: HashMap::new(),
        warm_strategies: HashSet::new(),
        ny_skew_seconds: None,
    };
    let bar = bar_at_minute(last_bar_minute);
    match (market, strategy_feed(strategy)) {
        (SYMBOL, LiveFeed::LevelTwo) => runtime.level_two_history.push(bar),
        (SYMBOL, _) => runtime.ohlcv_history.push(bar),
        _ => {
            runtime.market_histories.insert(market, vec![bar]);
        }
    }
    runtime.slots.insert(slot.target.account_strategy_id, slot);
    runtime
}

/// THE REGRESSION THIS BOOK'S ONLY SWING SLEEVE DEPENDS ON.
///
/// `jp225:swing_donchian` holds through the session close, JP225 stops
/// printing for about two hours a day and for the whole weekend, and the
/// watchdog trips after 180 seconds. Judged on "is anything holding", every
/// scheduled JP225 close would have sent a real emergency close and blocked
/// the sleeve until the process restarted -- and that sleeve is what damps
/// the book's 2023 trough ([[swing-donchian-is-load-bearing-for-drawdown]]).
#[test]
fn a_scheduled_market_close_is_not_a_dead_feed() {
    // UKOIL since 2026-09-23. 16:00 New York is after its 09:00-14:30
    // session: a scheduled close, nothing to flatten.
    let closed = runtime_holding("ukoil_level_confluence", 16 * 60);
    assert!(!closed.has_unmanaged_positions("ukoil", LiveFeed::Ohlcv));

    // 12:00, mid-session: the feed really did stop early.
    let stalled = runtime_holding("ukoil_level_confluence", 12 * 60);
    assert!(stalled.has_unmanaged_positions("ukoil", LiveFeed::Ohlcv));
}

/// A FEED THAT DIES DURING ITS MARKET'S SCHEDULED CLOSE MUST STILL BE
/// CAUGHT once its session reopens.
///
/// Its own last bar is parked at 16:00 and stays there for as long as it is
/// broken, so a watchdog reading only that stream would never fire again.
/// The other eleven streams keep printing, and they are the clock.
#[test]
fn a_feed_that_never_returns_from_a_close_is_caught_at_the_reopen() {
    // UKOIL since 2026-09-23, parked at 16:00 after its 14:30 close...
    let mut runtime = runtime_holding("ukoil_level_confluence", 16 * 60);
    assert!(!runtime.has_unmanaged_positions("ukoil", LiveFeed::Ohlcv));
    // ...but ETHUSD, which quotes every minute of every day, says 10:00 the
    // NEXT day -- an hour into the UKOIL session that never printed.
    runtime
        .market_histories
        .insert(ETHUSD_SYMBOL, vec![bar_at_minute(24 * 60 + 10 * 60)]);
    assert!(runtime.has_unmanaged_positions("ukoil", LiveFeed::Ohlcv));
}

/// The watchdog must still protect an intraday sleeve that is holding
/// inside its own session, which is the case it was written for.
#[test]
fn a_stalled_feed_still_arms_the_watchdog_in_session() {
    // 12:00 New York is inside USDJPY's 00:00-13:00 window.
    let stalled = runtime_holding("usdjpy_volume_thrust", 12 * 60);
    assert!(stalled.has_unmanaged_positions("usdjpy", LiveFeed::Ohlcv));
    // ...and not on another market, which is the split that matters: one
    // stalled feed must not emergency-flatten a position on a feed that is
    // still printing.
    assert!(!stalled.has_unmanaged_positions("jp225", LiveFeed::Ohlcv));
    // Outside its session there is nothing to protect.
    let closed = runtime_holding("usdjpy_volume_thrust", 20 * 60);
    assert!(!closed.has_unmanaged_positions("usdjpy", LiveFeed::Ohlcv));
}

/// A stream that has produced no bar at all cannot say what minute it is,
/// and a watchdog that guessed would be flattening on no evidence.
#[test]
fn a_stream_with_no_bars_never_arms_the_watchdog() {
    let mut runtime = runtime_holding("ukoil_level_confluence", 10 * 60);
    runtime.market_histories.clear();
    assert!(!runtime.has_unmanaged_positions("ukoil", LiveFeed::Ohlcv));
}

/// Every sleeve declares the instrument it trades, and the live row must
/// name that instrument. `jp225_kalman` on `UKOIL` would be stepped with Nikkei
/// bars and send its real orders to Brent.
#[test]
fn a_live_row_must_name_its_sleeve_s_instrument() {
    assert_eq!(required_symbol("ethusd_pullback"), Some("ETHUSD"));
    assert_eq!(required_symbol("ukoil_level_confluence"), Some("UKOIL"));
    // Dropped 2026-09-23, so they must resolve to nothing as well.
    assert_eq!(required_symbol("jp225_kalman"), None);
    assert_eq!(required_symbol("ukoil_xma_cross"), None);
    assert_eq!(required_symbol("btc_donchian"), None);
    // A sleeve the book DROPPED resolves to nothing, so a stale live row cannot
    // pass the symbol check by naming an instrument nothing trades any more.
    assert_eq!(required_symbol("hk50_level_confluence"), None);

    for sleeve in exness_combined::BOOK {
        let symbol = required_symbol(sleeve.id()).expect("every sleeve routes");
        assert!(symbol_matches_strategy(sleeve.id(), symbol));
        assert!(symbol_matches_strategy(sleeve.id(), &symbol.to_lowercase()));
    }
    // Case is not part of it.
    assert!(symbol_matches_strategy("ukoil_level_confluence", "ukoil"));
    assert!(symbol_matches_strategy("ukoil_level_confluence", "UKOIL"));
    // A different instrument, and a symbol nothing in the book trades.
    assert!(!symbol_matches_strategy("ukoil_level_confluence", "ETHUSD"));
    assert!(!symbol_matches_strategy("ethusd_confluence", "BTCUSD"));
    assert!(!symbol_matches_strategy("usdjpy_rvol", "DE40"));
}

/// `blocked` stops new risk; it must never stop an exit.
///
/// A discarded exit is gone for good -- the strategy drops the position from
/// its own book on the bar it emits the close, so if the runtime throws that
/// close away nothing ever asks again and the position rides to the
/// end-of-day flatten. A 12:24 exit was recorded at 16:59 that way, which is
/// what `pending_exits` exists to prevent.
#[test]
fn a_failed_exit_is_owed_and_retried_only_while_held() {
    let mut slot = test_slot();
    slot.positions.insert(
        "primary".to_owned(),
        LogicalPosition {
            side: Side::Long,
            volume: 0.05,
        },
    );
    slot.owe_exit(Action::Close {
        price: 100.0,
        fraction: 1.0,
    });
    assert!(
        slot.pending_exits.contains_key("primary"),
        "a held position's exit must be owed"
    );

    // The same close against a position the runtime no longer holds is not
    // owed: retrying it would send an order for something that is flat.
    let mut flat = test_slot();
    flat.owe_exit(Action::Close {
        price: 100.0,
        fraction: 1.0,
    });
    assert!(flat.pending_exits.is_empty());
}

/// A tagged close is owed under its own key, so two positions opened an hour
/// apart cannot overwrite each other's retry.
#[test]
fn tagged_exits_are_owed_under_their_own_keys() {
    let mut slot = test_slot();
    for id in [7u64, 9] {
        slot.positions.insert(
            format!("tag:{id}"),
            LogicalPosition {
                side: Side::Short,
                volume: 0.05,
            },
        );
        slot.owe_exit(Action::ClosePosition { id, price: 100.0 });
    }
    assert!(slot.pending_exits.contains_key("tag:7"));
    assert!(slot.pending_exits.contains_key("tag:9"));
}

/// THE BENCHMARK IS ALIGNED, NOT MERELY LATEST.
///
/// NO MARKET IS HANDED A BENCHMARK, because none declares one.
///
/// `attach_benchmark` is gated on `benchmark_for`, so with the table empty the
/// alignment below it is unreachable and the column stays `None` on every bar.
/// That is the safe direction -- a cell reading a benchmark it was never given
/// simply does not fire -- and it is asserted rather than assumed, because the
/// opposite would be a live account trading on a column the backtest never had.
///
/// WHAT THE ALIGNMENT WOULD HAVE TO DO when one is seated again: take the last
/// benchmark close at or BEFORE the bar's own timestamp. The live loop advances
/// the benchmark's feed independently, so it can easily run ahead of the market
/// that reads it, and taking whatever it last printed would hand the cell a
/// close from the future. On a rule whose trigger is "the name broke out and the
/// benchmark did not", that is the most flattering bug available.
#[test]
fn no_market_is_handed_a_benchmark() {
    let mut runtime = runtime_holding("ethusd_confluence", 600);
    runtime.push_benchmark("btc", 600 * 60, 100.0);
    runtime.push_benchmark("btc", 630 * 60, 200.0);
    // The ring really does hold them, so this is the GATE refusing rather than
    // an empty store answering.
    assert_eq!(runtime.benchmark_closes["btc"].len(), 2);

    for market in crate::strategies::known_markets() {
        let mut bar = bar_at_minute(615);
        runtime.attach_benchmark(market, &mut bar);
        assert_eq!(bar.benchmark, None, "{market} was handed a benchmark");
    }
}

/// The alignment ring is bounded, and it keeps the NEWEST minutes.
///
/// Trimming the wrong end would leave the lookup with only ancient closes and
/// silently stop the sleeve.
#[test]
fn the_benchmark_ring_keeps_its_newest_minutes() {
    let mut runtime = runtime_holding("ethusd_confluence", 600);
    for minute in 0..(BENCHMARK_RING_MINUTES as i64 + 50) {
        runtime.push_benchmark("btc", minute * 60, minute as f64);
    }
    let ring = &runtime.benchmark_closes["btc"];
    assert_eq!(ring.len(), BENCHMARK_RING_MINUTES);
    assert_eq!(
        ring.back().map(|(_, close)| *close),
        Some((BENCHMARK_RING_MINUTES as i64 + 49) as f64)
    );
}

/// The held-back bar is released once the clock proves it closed, and not
/// before.
///
/// `latest_completed_ohlcv_minute` returns `newest - step` because the newest
/// row COULD still be forming. Every writer on `<symbol>_1m` in this deployment
/// finishes a bar before writing it, so that step-back is usually a minute of
/// latency bought for nothing -- and on 2026-09-07 it was half of the 125s
/// between a usdjpy candle closing and its order reaching MT5. Releasing it is
/// gated on the clock rather than on trusting the writers.
#[test]
fn a_settled_bar_is_released_and_a_forming_one_is_not() {
    // `held + step` spans `[held + 60, held + 120)` on a minute source.
    let held = 1_766_620_800;
    // Still inside that span: the row may be mid-write, so nothing moves.
    assert_eq!(
        release_settled_bar(held, "usdjpy", Some(held + 119)),
        held,
        "a bar whose own period has not ended must stay held"
    );
    // Its period has ended, so the row behind the watermark is complete.
    assert_eq!(
        release_settled_bar(held, "usdjpy", Some(held + 120)),
        held + 60,
        "a settled bar must be released rather than waited on"
    );
    // Before any bar has taught the New York offset there is no store clock,
    // and the conservative answer stands.
    assert_eq!(
        release_settled_bar(held, "usdjpy", None),
        held,
        "an unknown clock must not release anything"
    );
}

/// The release is counted in the SOURCE's own period, not in minutes.
///
/// `xniusd` has no one-minute table and is stepped by 1,800s, so a rule that
/// hard-coded 60 here would hand its strategy a half-hour bar twenty-nine
/// minutes before it closed.
#[test]
fn the_release_uses_the_sources_own_step() {
    let held = 1_766_620_800;
    assert_eq!(
        release_settled_bar(held, "xniusd", Some(held + 3_599)),
        held
    );
    assert_eq!(
        release_settled_bar(held, "xniusd", Some(held + 3_600)),
        held + 1_800
    );
}

// --------------------------------------------------------------------------- //
// starting a session late must not retire a sleeve
// --------------------------------------------------------------------------- //

/// A strategy that reports whether the runtime told it to forget a trade.
struct AbandonSpy(Rc<RefCell<usize>>);

impl Strategy for AbandonSpy {
    fn update(&mut self, _bar: Bar, _equity: f64) -> Action {
        Action::Hold
    }

    fn abandon_open_position(&mut self) {
        *self.0.borrow_mut() += 1;
    }
}

fn durable(key: &str, side: &str) -> StrategyPosition {
    StrategyPosition {
        position_key: key.to_owned(),
        side: side.to_owned(),
        remaining_volume: 1.0,
        ticket: 42,
        status: "open".to_owned(),
    }
}

/// `(blocked, positions left, times the strategy was told to forget)`
async fn reconciled(
    replayed: &[(&str, Side)],
    persisted: &[StrategyPosition],
) -> (Option<BlockReason>, usize, usize) {
    let abandons = Rc::new(RefCell::new(0usize));
    let mut slot = test_slot();
    slot.strategy = Box::new(AbandonSpy(Rc::clone(&abandons)));
    for (key, side) in replayed {
        slot.positions.insert(
            (*key).to_owned(),
            LogicalPosition {
                side: *side,
                volume: 1.0,
            },
        );
    }
    // Lazy, and `log_live_event` swallows its own write errors by design, so
    // reconciliation runs without SQLite.
    let db = Database::new("unused-in-this-test");
    slot.reconcile(&db, persisted).await;
    let left = slot.positions.len();
    let blocked = slot.blocked;
    drop(slot);
    let count = *abandons.borrow();
    (blocked, left, count)
}

/// THE CASE THAT HAS ALWAYS FIRED, AND THE ONE THE OPERATOR ASKED FOR.
///
/// Starting a session after the market opened is ordinary: the replay takes an
/// entry the runtime was not running to send, so it believes it holds something
/// the broker has never seen. Skip that trade, keep taking signals.
/// `audusd_zscore` was retired for a whole session over exactly this on
/// 2026-09-11, for a position it had never sent an order for.
#[actix_web::test]
async fn a_replayed_entry_the_broker_never_took_is_skipped_not_blocked() {
    let (blocked, left, abandons) = reconciled(&[("primary", Side::Long)], &[]).await;
    assert_eq!(blocked, None, "a phantom must not retire the sleeve");
    assert_eq!(left, 0, "the phantom must be dropped from the slot");
    assert_eq!(
        abandons, 1,
        "and the strategy must be told, or it closes a position the slot no longer has"
    );
}

/// The dangerous direction, unchanged. A real broker position with nobody
/// managing it is not something a later observation can explain away.
#[actix_web::test]
async fn a_broker_position_the_replay_does_not_know_about_still_blocks() {
    let (blocked, _, abandons) = reconciled(&[], &[durable("primary", "long")]).await;
    assert_eq!(blocked, Some(BlockReason::Sticky));
    assert_eq!(
        abandons, 0,
        "nothing may be abandoned when the broker holds it"
    );
}

/// Both books hold the key on opposite sides. Whatever else is true, one of
/// them is wrong about direction, and neither may be trusted to add risk.
#[actix_web::test]
async fn opposite_sides_still_block() {
    let (blocked, _, _) =
        reconciled(&[("primary", Side::Long)], &[durable("primary", "short")]).await;
    assert_eq!(blocked, Some(BlockReason::Sticky));
}

/// A phantom alongside a real orphan is still a block: the orphan decides.
#[actix_web::test]
async fn a_phantom_does_not_excuse_an_orphan_beside_it() {
    let (blocked, _, abandons) =
        reconciled(&[("ghost", Side::Long)], &[durable("real", "long")]).await;
    assert_eq!(blocked, Some(BlockReason::Sticky));
    assert_eq!(abandons, 0);
}

/// The ordinary case must stay silent and change nothing.
#[actix_web::test]
async fn an_agreeing_book_is_left_alone() {
    let (blocked, left, abandons) =
        reconciled(&[("primary", Side::Long)], &[durable("primary", "long")]).await;
    assert_eq!(blocked, None);
    assert_eq!(left, 1);
    assert_eq!(abandons, 0);
}

// --------------------------------------------------------------------------- //
// the strategy's own close is not an external one
// --------------------------------------------------------------------------- //

/// `usdjpy_rvol` closed itself at 17:30Z on 2026-09-24 and the terminal said
/// "closed externally". The durable row goes the moment the close fills, and
/// the per-refresh reconciler reached it before `retry_pending_exits` did.
#[test]
fn an_exit_in_flight_is_not_reported_as_an_external_close() {
    let abandons = Rc::new(RefCell::new(0usize));
    let mut slot = test_slot();
    slot.strategy = Box::new(AbandonSpy(Rc::clone(&abandons)));
    for key in ["mine", "theirs"] {
        slot.positions.insert(
            key.to_owned(),
            LogicalPosition {
                side: Side::Long,
                volume: 1.0,
            },
        );
    }
    slot.pending_exits.insert(
        "mine".to_owned(),
        super::slot::PendingExit {
            price: 1.0,
            fraction: 1.0,
        },
    );

    let flattened = slot.reconcile_external_closes(&HashSet::new());

    assert_eq!(
        flattened,
        vec!["theirs".to_owned()],
        "only the unasked-for close"
    );
    assert!(
        slot.positions.contains_key("mine"),
        "left for retry_pending_exits to acknowledge"
    );
    assert!(slot.pending_exits.contains_key("mine"));
    drop(slot);
    assert_eq!(*abandons.borrow(), 1);
}
