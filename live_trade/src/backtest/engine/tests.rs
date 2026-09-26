//! Engine unit tests: sizing, session flattening, costs and reporting.

use super::*;

struct IntradayOnce {
    bars_seen: usize,
}

struct RiskSizedOnce {
    bars_seen: usize,
}

struct DrawdownLimitedOnce {
    bars_seen: usize,
}

impl Strategy for IntradayOnce {
    fn update(&mut self, bar: Bar, _equity: f64) -> Action {
        self.bars_seen += 1;
        if self.bars_seen == 1 {
            Action::Enter {
                side: Side::Long,
                price: bar.close,
                quantity: 1.0,
            }
        } else {
            Action::Hold
        }
    }

    fn session_end_minute(&self) -> Option<usize> {
        Some(930)
    }
}

impl Strategy for RiskSizedOnce {
    fn update(&mut self, _bar: Bar, _equity: f64) -> Action {
        self.bars_seen += 1;
        if self.bars_seen == 1 {
            Action::Enter {
                side: Side::Long,
                price: 100.0,
                quantity: 999.0,
            }
        } else {
            Action::Close {
                price: 90.0,
                fraction: 1.0,
            }
        }
    }

    fn entry_risk_fraction(&self) -> Option<f64> {
        Some(0.03)
    }

    fn entry_stop_price(&self) -> Option<f64> {
        Some(90.0)
    }
}

impl Strategy for DrawdownLimitedOnce {
    fn update(&mut self, _bar: Bar, _equity: f64) -> Action {
        self.bars_seen += 1;
        if self.bars_seen == 1 {
            Action::Enter {
                side: Side::Long,
                price: 100.0,
                quantity: 10.0,
            }
        } else {
            Action::Hold
        }
    }

    fn max_drawdown_dollars(&self) -> Option<f64> {
        Some(300.0)
    }
}

fn bar(ts: i64, close: f64) -> Bar {
    Bar {
        ts,
        open: close,
        high: close,
        low: close,
        close,
        volume: 1.0,
        volume_delta: 0.0,
        depth_events: 0,
        // These tests drive `IntradayOnce`, not a level-two strategy, so
        // provenance is irrelevant to them.
        level_two: false,
        order_flow: Default::default(),
        benchmark: None,
    }
}

/// Records the equity it was shown on every bar and never trades, so a run
/// isolates the exposure policy from anything a real strategy would do.
struct RecordsEquity {
    seen: std::rc::Rc<std::cell::RefCell<Vec<f64>>>,
}

impl Strategy for RecordsEquity {
    fn update(&mut self, _bar: Bar, equity: f64) -> Action {
        self.seen.borrow_mut().push(equity);
        Action::Hold
    }
}

/// One slot per name, each recording the equity the engine shows it.
fn shown_equity(names: &[&str]) -> Vec<Vec<f64>> {
    let recorders: Vec<std::rc::Rc<std::cell::RefCell<Vec<f64>>>> = names
        .iter()
        .map(|_| std::rc::Rc::new(std::cell::RefCell::new(Vec::new())))
        .collect();
    let strategies: Vec<Box<dyn Strategy>> = recorders
        .iter()
        .map(|seen| {
            Box::new(RecordsEquity {
                seen: std::rc::Rc::clone(seen),
            }) as Box<dyn Strategy>
        })
        .collect();
    // Two markets: NQ sleeves on symbol index 0, everything else on 1,
    // matching how a mixed run assigns them.
    let assignment: Vec<usize> = names
        .iter()
        .map(|name| usize::from(crate::strategies::market_symbol(name) != "nq"))
        .collect();
    // 400 calendar days of violent NQ so the volatility target is well past
    // warm-up and biting, plus a quiet BTC series on its own symbol index.
    let mut stream: Vec<(usize, Bar)> = Vec::new();
    let mut close = 20_000.0;
    for day in 0..400i64 {
        close *= if day % 2 == 0 { 1.04 } else { 1.0 / 1.04 };
        stream.push((0, bar(day * 86_400 + 3_600, close)));
        stream.push((1, bar(day * 86_400 + 3_600, 50_000.0)));
    }
    let owned: Vec<String> = names.iter().map(|name| (*name).to_string()).collect();
    let cfg = EngineConfig {
        initial: 1_000.0,
        symbol: "nq".into(),
        start_day: 0,
        gross_cap: None,
    };
    run_engines_streams(&stream, 2, strategies, &owned, &assignment, cfg);
    recorders.iter().map(|seen| seen.borrow().clone()).collect()
}

/// THE THREE-SLEEVE EXPOSURE POLICY IS GONE, and so are the tests that
/// pinned it. It scaled `NQ Deep OFI Momentum`, `NQ Hourly Delta Reversal`
/// and `BTC Maroy Ladder` off NQ's realised volatility and showed the Ladder
/// twice what the other two saw; none of those strategies exists any more.
///
/// What replaces it is the assertion below: every sleeve of the 2026-09-07
/// book must be shown the balance untouched, because that book carries its
/// risk scale, its per-sleeve weights and its per-market volatility throttle
/// inside `exness_combined` itself. An overlay here would charge the same
/// regime twice and size a backtest differently from the live account it is
/// meant to mirror ([[live-vs-backtest-parity]]).
#[test]
fn every_sleeve_is_shown_the_untouched_balance() {
    let book: Vec<&str> = crate::strategies::idk::exness_combined::BOOK
        .iter()
        .map(|sleeve| sleeve.display())
        .collect();
    for shown in shown_equity(&book) {
        assert!(
            shown.iter().all(|value| (value - 1_000.0).abs() < 1e-9),
            "a sleeve was shown a scaled balance: {shown:?}"
        );
    }
}

#[test]
fn civil_dates_round_trip() {
    let d = parse_iso_days("2026-07-15").unwrap();
    assert_eq!(format_day(d), "2026-07-15");
}
#[test]
fn fill_cost_is_adverse() {
    let absolute = |raw: f64, buying: bool, point_value: f64| {
        fill_entry(
            raw,
            buying,
            &EntryCosts {
                spread: 0.2,
                point_value,
                spread_bp: None,
            },
        )
    };
    // The absolute charge is account currency per lot, so it is divided by the
    // point value before being added to a price.
    assert_eq!(absolute(100.0, true, 1.0), 100.2);
    assert_eq!(absolute(100.0, false, 1.0), 99.8);
    assert!((absolute(3.0, true, 10_000.0) - 3.00002).abs() < 1e-12);

    // The proportional charge is already in price units and takes no
    // conversion: 1% of 100 is 1.
    let proportional = |buying: bool| {
        fill_entry(
            100.0,
            buying,
            &EntryCosts {
                spread: 0.0,
                point_value: 1.0,
                spread_bp: Some(100.0),
            },
        )
    };
    assert_eq!(proportional(true), 101.0);
    assert_eq!(proportional(false), 99.0);

    // THE EXIT IS FREE. The whole round trip was taken at entry, so a long
    // round trip costs exactly the entry charge and no more.
    let long_round_trip = 100.0 - absolute(100.0, true, 1.0);
    assert!((long_round_trip - -0.2).abs() < 1e-9);
}
/// The entry cost is the sealed session spread plus the 0.2 bp slippage
/// allowance, which is exactly what Python's `cost_bp` charges -- and nothing
/// else, because a measured minimum-lot round trip booked 0.0000 commission on
/// both deals ([[pro-commission-is-measured-zero]]).
#[test]
fn the_pro_model_is_a_proportional_spread_and_nothing_else() {
    assert_eq!(
        sleeve_spread_bp("USDJPY Volume Thrust"),
        Some(0.439_8 + 0.2)
    );
    // The widest quote in the book, by a factor of two.
    assert_eq!(sleeve_spread_bp("ETHUSD Confluence"), Some(3.721_1 + 0.2));
    assert_eq!(sleeve_spread_bp("USDJPY Rvol"), Some(0.439_8 + 0.2));
    // An unregistered name has no market and so no charge to look up.
    assert_eq!(sleeve_spread_bp("ETHUSD VWAP"), None);
    assert_eq!(market_spread_bp("unlisted"), None);

    // Every sleeve bills proportionally except the two imports, which carry a
    // flat allowance instead. The two must never both apply.
    for definition in crate::strategies::registered_sleeves("idk") {
        let (absolute, bp) = sleeve_entry_cost(definition.name);
        assert!(
            (absolute > 0.0) ^ bp.is_some_and(|bp| bp > 0.0),
            "{} is charged both ways or neither",
            definition.name
        );
    }
}

/// The rule the API reports must be built from the tables the engine charges,
/// or the environment screen and the backtest describe different accounts.
#[test]
fn the_reported_cost_model_covers_every_market() {
    let model = crate::backtest::cost_model();
    assert_eq!(model.id, "exness_pro");
    assert_eq!(model.name, "Exness Pro");
    assert_eq!(
        model.markets.len(),
        crate::strategies::known_markets().len()
    );
    for line in &model.markets {
        assert_eq!(Some(line.spread_bp), market_spread_bp(line.market));
        assert!(line.minimum_lots > 0.0, "{} has no floor", line.market);
    }
}

/// The engine and the strategy must agree on what the broker will accept.
/// JP225's floor is THREE whole lots, not a hundredth of one, and a
/// disagreement here means one of them sizes a trade the other refuses.
#[test]
fn broker_minimums_match_the_strategy_specs() {
    assert_eq!(broker_minimum("nq"), 0.05);
    assert_eq!(broker_minimum("ethusd"), 0.10);
    assert_eq!(broker_minimum("jp225"), 3.0);
    assert_eq!(broker_minimum("usdjpy"), 0.01);
    assert_eq!(broker_minimum("uk100"), 0.05);
    assert_eq!(broker_minimum("xalusd"), 0.01);
    for market in crate::strategies::known_markets() {
        assert!(
            broker_minimum(market) > 0.0,
            "{market} has no broker minimum"
        );
    }
}

/// Every registered market must have a point value, a spread and a floor. A
/// market that reaches the engine without one is charged nothing at all -- a
/// silent free trade.
#[test]
fn every_market_is_fully_priced() {
    // SLEEVES ONLY. The registry also advertises the book, which is a selection
    // rather than a strategy: it has no market and so no spread to look up.
    for definition in crate::strategies::registered_sleeves("idk") {
        let market = crate::strategies::market_symbol(definition.name);
        assert!(
            sleeve_spread_bp(definition.name).is_some_and(|bp| bp > 0.0),
            "{} has no entry spread",
            definition.name
        );
        assert!(
            market_point_value(market) > 0.0,
            "{market} has no point value"
        );
    }
}

#[test]
fn intraday_strategy_closes_before_exact_session_boundary() {
    let bars = [
        bar(600 * 60, 100.0),
        bar(601 * 60, 110.0),
        // The next print is the following morning, whose minute-of-day is
        // also before the session boundary. The prior day must still be
        // flattened on its final available bar.
        bar(86_400 + 600 * 60, 50.0),
    ];
    let cfg = EngineConfig {
        initial: 100.0,
        symbol: "nq".into(),
        start_day: 0,
        gross_cap: None,
    };

    let result = run_engines(&bars, vec![Box::new(IntradayOnce { bars_seen: 0 })], cfg);

    assert_eq!(result.trades.len(), 1);
    assert_eq!(result.trades[0].entry_timestamp, 600 * 60);
    assert_eq!(result.trades[0].exit_timestamp, 601 * 60);
    assert_eq!(result.trades[0].exit_raw, 110.0);
    assert_eq!(result.trades[0].pnl, 10.0);
    assert_eq!(result.body["final_bal"], 110.0);
}

/// A risk-sized entry may never lose more than its budget.
///
/// The budget here is 3% of 5,000, and the stop is ten points away. Nothing is
/// charged at entry any more -- Exness Pro bills proportionally and this test
/// strategy names no sleeve -- so the budget divides exactly and the floor to
/// the 0.01 lot step takes nothing off.
#[test]
fn risk_sizing_never_loses_more_than_its_budget() {
    let bars = [bar(600 * 60, 100.0), bar(601 * 60, 90.0)];
    let cfg = EngineConfig {
        initial: 5_000.0,
        symbol: "nq".into(),
        start_day: 0,
        gross_cap: None,
    };

    let result = run_engines(&bars, vec![Box::new(RiskSizedOnce { bars_seen: 0 })], cfg);

    assert_eq!(result.trades.len(), 1);
    assert!((result.trades[0].quantity - 15.0).abs() < 1e-9);
    assert!(result.trades[0].pnl.abs() <= 150.0);
    assert!((result.trades[0].pnl + 150.0).abs() < 1e-9);
}

#[test]
fn combined_strategies_share_one_balance_and_hold_separate_positions() {
    let bars = [bar(600 * 60, 100.0), bar(601 * 60, 110.0)];
    let cfg = EngineConfig {
        initial: 1_000.0,
        symbol: "nq".into(),
        start_day: 0,
        gross_cap: None,
    };

    let result = run_engines(
        &bars,
        vec![
            Box::new(IntradayOnce { bars_seen: 0 }),
            Box::new(IntradayOnce { bars_seen: 0 }),
        ],
        cfg,
    );

    // Both strategies opened their own unit and both exits land in the one
    // account, so the balance moves by the sum of the two trades.
    assert_eq!(result.trades.len(), 2);
    assert_eq!(result.body["final_bal"], 1_020.0);
}

#[test]
fn strategy_drawdown_limit_forces_an_account_level_exit() {
    let bars = [bar(600 * 60, 100.0), bar(601 * 60, 50.0)];
    let cfg = EngineConfig {
        initial: 1_000.0,
        symbol: "nq".into(),
        start_day: 0,
        gross_cap: None,
    };

    let result = run_engines(
        &bars,
        vec![Box::new(DrawdownLimitedOnce { bars_seen: 0 })],
        cfg,
    );

    assert_eq!(result.trades.len(), 1);
    assert_eq!(result.trades[0].exit_raw, 70.0);
    assert_eq!(result.trades[0].pnl, -300.0);
    assert_eq!(result.body["max_drawdown_dollars"], 300.0);
    assert_eq!(result.body["final_bal"], 700.0);
}

/// EVERY MARKET THE BOOK TRADES HAS A POINT VALUE AND A LOT FLOOR HERE, and both
/// equal the frozen `Instrument` the sleeve sizes against.
///
/// `mod.rs` has promised this test by name since the map was written and it did
/// not exist. `market_point_value` falls through to `1.0` and `broker_minimum`
/// to `0.0`, so a market seated without a line in either is not a build error --
/// it is a silently wrong number. HK50 joined the book on 2026-09-04 with a
/// point value of 0.1274 and was paid at 1.0, which multiplied its P&L by 7.85
/// and made it the third-largest contributor in the book.
#[test]
fn frozen_point_values_match_the_strategy_specs() {
    use crate::strategies::idk::exness_combined::{BOOK, Sleeve};
    for sleeve in BOOK {
        let market = sleeve.market();
        assert!(
            (market_point_value(market) - sleeve.point_value()).abs()
                < 1e-9 * sleeve.point_value().max(1.0),
            "{market} is paid at {} against a frozen {}",
            market_point_value(market),
            sleeve.point_value()
        );
        assert!(
            (broker_minimum(market) - sleeve.broker_minimum_lots()).abs() < 1e-12,
            "{market} floors at {} against a frozen {}",
            broker_minimum(market),
            sleeve.broker_minimum_lots()
        );
        // And the spread, which `every_sleeve_spec_is_internally_consistent`
        // checks from the other side.
        assert!(
            market_spread_bp(market).is_some(),
            "{market} has no quoted spread"
        );
    }
    // And a market the book DROPPED must no longer resolve, or the table would
    // be keeping a line alive for a sleeve nothing can build.
    assert_eq!(Sleeve::from_display("HK50 Level Confluence"), None);
    assert_eq!(broker_minimum("hk50"), 0.0);
    assert_eq!(market_spread_bp("hk50"), None);
}

/// A SHIFTED MARKET IS CHARGED ON ITS OWN CLOCK, not on New York's.
///
/// HK50 trades 21:00-04:00 New York, so every one of its positions crosses a
/// real date boundary and none crosses a shifted one -- and `ef.backtest` counts
/// on the shifted stamps, because `all_bars` shifts before the loop ever sees a
/// bar. Counting on the real clock charged one night on 43 of its 122 trades and
/// took a tenth of the sleeve's contribution.
#[test]
fn financing_nights_are_counted_on_the_sleeve_s_own_clock() {
    // 2026-01-05 22:00 New York to 2026-01-06 02:00: a real date boundary, and
    // no boundary at all on the +6 clock HK50 is evaluated on.
    let entry = 1_767_650_400;
    let exit = entry + 4 * 3_600;
    assert_eq!(
        financing_nights(entry, exit),
        1,
        "the real clock sees a night"
    );

    let position = Position {
        id: None,
        side: Side::Long,
        entry: 25_000.0,
        raw: 25_000.0,
        ts: entry,
        quantity: 0.07,
        point_value: 0.127_411_582_732_163_02,
    };
    let gross = net_pnl(&position, 25_100.0);
    let net = net_pnl_after_financing(&position, 25_100.0, exit, "HK50 Level Confluence");
    assert!(
        (net - gross).abs() < 1e-12,
        "a shifted sleeve inside its own session pays no carry: {net} against {gross}"
    );

    // An UNSHIFTED market over the same stamps is charged, so the test is about
    // the clock rather than about the charge being switched off.
    let ukoil = Position {
        side: Side::Short,
        entry: 87.0,
        raw: 87.0,
        point_value: 1_000.0,
        ..position
    };
    let charged = net_pnl_after_financing(&ukoil, 86.5, exit, "UKOIL Level Confluence");
    assert!(
        charged < net_pnl(&ukoil, 86.5),
        "an unshifted market over the same stamps is charged, so this test is          about the clock rather than about the charge being switched off"
    );
}
