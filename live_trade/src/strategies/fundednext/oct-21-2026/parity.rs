//! Trade-for-trade agreement with `cfd_families.backtest` at FundedNext.
//!
//! Each fixture is written by `sandbox/research/_fundednext_parity.py` under
//! the protocol its cell was sealed with, on IDEALISED fills: it proves the
//! SIGNAL. The harness is the Exness book's (`exness::oct_12_2026::parity`) --
//! one engine, one way of holding it to its Python.

use super::*;
use crate::strategies::exness::oct_12_2026::parity::{
    Entry, Fixture, agrees, assert_matches, expected, load, replay_stream,
};
use crate::backtest::types::Bar;

/// The fixture's bars plus one flush bar a DAY later.
///
/// Python's series simply ends, so its last bar is the last of its day and an
/// open session position is flattened there (`reason: "session"`). The Exness
/// harness flushes one slot later on the same day, which reads as mid-session
/// and leaves that position open; two of these fixtures end on a Friday with a
/// position open. A flush on the next day is what "no more bars" means.
fn replay(fixture: &Fixture) -> Vec<(Entry, i64)> {
    let flush = fixture.bars.last().map(|last| Bar {
        ts: last.ts + 86_400,
        ..*last
    });
    let stream: Vec<Bar> = fixture.bars.iter().chain(flush.iter()).copied().collect();
    replay_stream(fixture, &stream)
}

/// The fixtures are read from disk: at 295 MB they are too large to compile in.
fn read(name: &str) -> String {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src/strategies/fundednext/oct-21-2026/fixtures")
        .join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()))
}

fn assert_reproduces(fixture: &Fixture) {
    assert_matches(fixture, &replay(fixture));
}

/// One fixture per member, in `BOOK` order.
const ALL: [(Sleeve, &str); 50] = [
    (
        Sleeve::FnUsdchfSkew1hAfternoon,
        "parity_usdchf_skew_1h-afternoon.json",
    ),
    (
        Sleeve::FnChfjpyOvernight5mNight,
        "parity_chfjpy_overnight_5m-night.json",
    ),
    (
        Sleeve::FnXauusdObvDivergence5mLondonOpen,
        "parity_xauusd_obv_divergence_5m-london_open.json",
    ),
    (
        Sleeve::FnUstecConfluence15mTokyoOpen,
        "parity_ustec_confluence_15m-tokyo_open.json",
    ),
    (
        Sleeve::FnGbpaudCci1hMorning,
        "parity_gbpaud_cci_1h-morning.json",
    ),
    (
        Sleeve::FnUstecPtlOrbCandle30mOff,
        "parity_ustec_ptl_orb_candle_30m-off.json",
    ),
    (
        Sleeve::FnUs500DayOfWeek5mEvening,
        "parity_us500_day_of_week_5m-evening.json",
    ),
    (
        Sleeve::FnUs30Amihud30mNyOpen,
        "parity_us30_amihud_30m-ny_open.json",
    ),
    (
        Sleeve::FnChfjpyStochastic15mEvening,
        "parity_chfjpy_stochastic_15m-evening.json",
    ),
    (
        Sleeve::FnUsdcadXmaCross5mMorning,
        "parity_usdcad_xma_cross_5m-morning.json",
    ),
    (
        Sleeve::FnXauusdFloorPivot15mMorning,
        "parity_xauusd_floor_pivot_15m-morning.json",
    ),
    (
        Sleeve::FnGbpusdGatedFade30mMorning,
        "parity_gbpusd_gated_fade_30m-morning.json",
    ),
    (
        Sleeve::FnUsdjpyTwoStage30mLondonOpen,
        "parity_usdjpy_two_stage_30m-london_open.json",
    ),
    (
        Sleeve::FnAudjpyGatedDonchian1hMorning,
        "parity_audjpy_gated_donchian_1h-morning.json",
    ),
    (
        Sleeve::FnGbpjpyQuantileBreak5mNyMidday,
        "parity_gbpjpy_quantile_break_5m-ny_midday.json",
    ),
    (
        Sleeve::FnUs500AvFirstHourFib30mAfternoon,
        "parity_us500_av_first_hour_fib_30m-afternoon.json",
    ),
    (
        Sleeve::FnXauusdQuantileBreak15mEvening,
        "parity_xauusd_quantile_break_15m-evening.json",
    ),
    (
        Sleeve::FnUs500TwoStage15mNyOpen,
        "parity_us500_two_stage_15m-ny_open.json",
    ),
    (
        Sleeve::FnFr40Efficiency1hMorning,
        "parity_fr40_efficiency_1h-morning.json",
    ),
    (
        Sleeve::FnUstecGatedOrb30mLondonOpen,
        "parity_ustec_gated_orb_30m-london_open.json",
    ),
    (
        Sleeve::FnUs2000AdaptivePullback30mMorning,
        "parity_us2000_adaptive_pullback_30m-morning.json",
    ),
    (
        Sleeve::FnUs500XmaCross30mLondonOpen,
        "parity_us500_xma_cross_30m-london_open.json",
    ),
    (
        Sleeve::FnXauusdQpRsiExtreme15mOffhour,
        "parity_xauusd_qp_rsi_extreme_15m-offhour.json",
    ),
    (
        Sleeve::FnUs2000SwingBreak30mMorning,
        "parity_us2000_swing_break_30m-morning.json",
    ),
    (
        Sleeve::FnXauusdXmaCross30mNyClose,
        "parity_xauusd_xma_cross_30m-ny_close.json",
    ),
    (
        Sleeve::FnNzdusdTrap1hOffhour,
        "parity_nzdusd_trap_1h-offhour.json",
    ),
    (
        Sleeve::FnUstecKendall5mLondonOpen,
        "parity_ustec_kendall_5m-london_open.json",
    ),
    (
        Sleeve::FnGbpusdRegimeBreakout5mNyMidday,
        "parity_gbpusd_regime_breakout_5m-ny_midday.json",
    ),
    (
        Sleeve::FnGbpjpyConfluence30mTokyo,
        "parity_gbpjpy_confluence_30m-tokyo.json",
    ),
    (
        Sleeve::FnEurusdStructure30mBarlbOff,
        "parity_eurusd_structure_30m-barlb-off.json",
    ),
    (
        Sleeve::FnEurjpyRvol60mBarlb,
        "parity_eurjpy_rvol_60m-barlb.json",
    ),
    (
        Sleeve::FnUstecFracdiff5mLondon,
        "parity_ustec_fracdiff_5m-london.json",
    ),
    (
        Sleeve::FnGbpjpyDayOfWeek30mBarlb,
        "parity_gbpjpy_day_of_week_30m-barlb.json",
    ),
    (
        Sleeve::FnUsdjpyKalman30mNyOpen,
        "parity_usdjpy_kalman_30m-ny_open.json",
    ),
    (
        Sleeve::FnAudjpyDayOfWeek30mLondon,
        "parity_audjpy_day_of_week_30m-london.json",
    ),
    (
        Sleeve::FnUs500LinregTrend30mAfternoon,
        "parity_us500_linreg_trend_30m-afternoon.json",
    ),
    (
        Sleeve::FnGbpjpyQuantileBreak30mAfternoon,
        "parity_gbpjpy_quantile_break_30m-afternoon.json",
    ),
    (
        Sleeve::FnUsdcadAdaptivePullback1hAfternoon,
        "parity_usdcad_adaptive_pullback_1h-afternoon.json",
    ),
    (
        Sleeve::FnAudjpyEfficiency30mBarlb,
        "parity_audjpy_efficiency_30m-barlb.json",
    ),
    (
        Sleeve::FnEuraudDayOfWeek5mAfternoon,
        "parity_euraud_day_of_week_5m-afternoon.json",
    ),
    (
        Sleeve::FnEurusdAdaptivePullback15mMorning,
        "parity_eurusd_adaptive_pullback_15m-morning.json",
    ),
    (
        Sleeve::FnEurjpyTrOscillatorRebound60m,
        "parity_eurjpy_tr_oscillator_rebound_60m.json",
    ),
    (
        Sleeve::FnNzdusdTwoStage5mRth,
        "parity_nzdusd_two_stage_5m-rth.json",
    ),
    (
        Sleeve::FnEurjpyCusum1hEarlyMorning,
        "parity_eurjpy_cusum_1h-early_morning.json",
    ),
    (
        Sleeve::FnChfjpyAdaptivePullback5mOffhour,
        "parity_chfjpy_adaptive_pullback_5m-offhour.json",
    ),
    (
        Sleeve::FnUsdchfFloorPivot15mMorning,
        "parity_usdchf_floor_pivot_15m-morning.json",
    ),
    (
        Sleeve::FnUk100Confluence1hMorning,
        "parity_uk100_confluence_1h-morning.json",
    ),
    (
        Sleeve::FnUk100ValueArea60mBarlb,
        "parity_uk100_value_area_60m-barlb.json",
    ),
    (
        Sleeve::FnGbpusdValueArea30mBarlbOff,
        "parity_gbpusd_value_area_30m-barlb-off.json",
    ),
    (
        Sleeve::FnCadjpyMomentum5mRth,
        "parity_cadjpy_momentum_5m-rth.json",
    ),
];

#[test]
fn every_member_has_a_fixture_and_nothing_else_does() {
    let covered: Vec<Sleeve> = ALL.iter().map(|(sleeve, _)| *sleeve).collect();
    assert_eq!(covered, BOOK.to_vec());
}

#[test]
fn every_member_is_priced_at_fundednext() {
    for sleeve in BOOK {
        assert_eq!(sleeve.book(), Book::FundedNext, "{}", sleeve.display());
    }
}

/// Each member on its own, so a failure names the sleeve.
#[test]
fn every_fixture_reproduces_the_python_trade_log() {
    let mut failed = Vec::new();
    for (sleeve, name) in ALL {
        let fixture = load(sleeve, &read(name));
        if std::panic::catch_unwind(|| assert_reproduces(&fixture)).is_err() {
            failed.push(sleeve.python_key());
        }
    }
    assert!(failed.is_empty(), "{} of {} diverge: {failed:?}", failed.len(), ALL.len());
}

#[test]
#[ignore = "a diagnostic, not an assertion"]
fn dump() {
    let wanted = std::env::var("FN_DUMP").unwrap_or_default();
    for (sleeve, name) in ALL {
        if sleeve.python_key() != wanted {
            continue;
        }
        let fixture = load(sleeve, &read(name));
        let got = replay(&fixture);
        let want: Vec<_> = fixture.trades.iter().map(|t| (expected(t), t["exit_ts"].as_i64().unwrap())).collect();
        for (g, x) in &got {
            if !want.iter().any(|(w, wx)| agrees(g, w) && wx == x) {
                println!("RUST ONLY {g:?} exit {x}");
            }
        }
        for (w, wx) in &want {
            if !got.iter().any(|(g, x)| agrees(g, w) && wx == x) {
                println!("PY ONLY {w:?} exit {wx}");
            }
        }
    }
}

/// The live day-end schedule on dates whose answer is known from the data
/// (`last_print_minute`), on each sleeve's own clock.
#[test]
fn last_print_schedule_matches_the_measured_feed() {
    use super::contracts::last_print_minute;
    let day = |y: i64, m: i64, d: i64| crate::backtest::data::parse_iso_days(&format!("{y:04}-{m:02}-{d:02}")).unwrap();
    // DE40 off-hours (+7, close 23:59): 23:14 Mon-Thu, 22:59 Fri, summer and winter.
    assert_eq!(last_print_minute("de40", 7, 1439, day(2026, 7, 14)), Some(23 * 60 + 14));
    assert_eq!(last_print_minute("de40", 7, 1439, day(2026, 7, 17)), Some(22 * 60 + 59));
    assert_eq!(last_print_minute("de40", 7, 1439, day(2026, 1, 14)), Some(23 * 60 + 14));
    // US on summer time, EU not yet: the DAX prints to the broker day's close.
    assert_eq!(last_print_minute("de40", 7, 1439, day(2026, 3, 17)), None);
    // USTEC / US500: 16:14 New York on either clock -- past the cash cells'
    // 16:00 close, inside their last candle.
    assert_eq!(last_print_minute("ustec", 7, 1439, day(2026, 7, 14)), Some(23 * 60 + 14));
    assert_eq!(last_print_minute("ustec", 0, 960, day(2026, 7, 14)), Some(16 * 60 + 14));
    assert_eq!(last_print_minute("us500", 0, 960, day(2026, 1, 14)), Some(16 * 60 + 14));
    // HK50 (+6, close 10:00): 09:29 in New York winter; in summer 10:29, past
    // the 10:00 candle, which the feed prints through.
    assert_eq!(last_print_minute("hk50", 6, 600, day(2026, 1, 14)), Some(9 * 60 + 29));
    assert_eq!(last_print_minute("hk50", 6, 600, day(2026, 7, 14)), Some(10 * 60 + 29));
    assert_eq!(last_print_minute("usdjpy", 0, 780, day(2026, 7, 14)), None);
}
