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
        .join("src/strategies/fundednext/oct-18-2026/fixtures")
        .join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()))
}

fn assert_reproduces(fixture: &Fixture) {
    assert_matches(fixture, &replay(fixture));
}

/// One fixture per member, in `BOOK` order.
const ALL: [(Sleeve, &str); 80] = [
    (
        Sleeve::FnUstecConfluence15mTokyoOpen,
        "parity_ustec_confluence_15m-tokyo_open.json",
    ),
    (
        Sleeve::FnXauusdGatedSwing5mLondonOpen,
        "parity_xauusd_gated_swing_5m-london_open.json",
    ),
    (
        Sleeve::FnXauusdRvol5mTokyoOpen,
        "parity_xauusd_rvol_5m-tokyo_open.json",
    ),
    (
        Sleeve::FnUs2000KeyReversal5mMorning,
        "parity_us2000_key_reversal_5m-morning.json",
    ),
    (
        Sleeve::FnUstecStructure30mAfternoon,
        "parity_ustec_structure_30m-afternoon.json",
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
        Sleeve::FnUs500Aroon30mBarlb,
        "parity_us500_aroon_30m-barlb.json",
    ),
    (
        Sleeve::FnXauusdRegimeBreakout5mNyOpen,
        "parity_xauusd_regime_breakout_5m-ny_open.json",
    ),
    (
        Sleeve::FnCadjpyValueArea30mRth,
        "parity_cadjpy_value_area_30m-rth.json",
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
        Sleeve::FnXauusdQuantileBreak5mEvening,
        "parity_xauusd_quantile_break_5m-evening.json",
    ),
    (
        Sleeve::FnUs500AvFirstHourFib30mAfternoon,
        "parity_us500_av_first_hour_fib_30m-afternoon.json",
    ),
    (
        Sleeve::FnNzdusdTrap1hOffhour,
        "parity_nzdusd_trap_1h-offhour.json",
    ),
    (
        Sleeve::FnXauusdObvDivergence15mEvening,
        "parity_xauusd_obv_divergence_15m-evening.json",
    ),
    (
        Sleeve::FnEuraudDayOfWeek1hNight,
        "parity_euraud_day_of_week_1h-night.json",
    ),
    (
        Sleeve::FnUstecGatedOrb30mLondonOpen,
        "parity_ustec_gated_orb_30m-london_open.json",
    ),
    (
        Sleeve::FnEuraudDayOfWeek15mAfternoon,
        "parity_euraud_day_of_week_15m-afternoon.json",
    ),
    (
        Sleeve::FnUs500XmaCross30mLondonOpen,
        "parity_us500_xma_cross_30m-london_open.json",
    ),
    (
        Sleeve::FnUs30VolOfVol15mLondon,
        "parity_us30_vol_of_vol_15m-london.json",
    ),
    (
        Sleeve::FnUsdchfSkew1hAfternoon,
        "parity_usdchf_skew_1h-afternoon.json",
    ),
    (
        Sleeve::FnUstecKendall5mLondonOpen,
        "parity_ustec_kendall_5m-london_open.json",
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
        Sleeve::FnAudusdTrap30mMorning,
        "parity_audusd_trap_30m-morning.json",
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
        Sleeve::FnUs2000SwingBreak30mMorning,
        "parity_us2000_swing_break_30m-morning.json",
    ),
    (
        Sleeve::FnAudjpyDayOfWeek30mLondon,
        "parity_audjpy_day_of_week_30m-london.json",
    ),
    (
        Sleeve::FnGbpjpyQuantileBreak30mAfternoon,
        "parity_gbpjpy_quantile_break_30m-afternoon.json",
    ),
    (
        Sleeve::FnNzdusdTrap15mOffhour,
        "parity_nzdusd_trap_15m-offhour.json",
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
        Sleeve::FnXauusdQttRsiEmaRetest5mOffhour,
        "parity_xauusd_qtt_rsi_ema_retest_5m-offhour.json",
    ),
    (
        Sleeve::FnXauusdCci30mEarlyMorning,
        "parity_xauusd_cci_30m-early_morning.json",
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
        Sleeve::FnChfjpyStructure15mRth,
        "parity_chfjpy_structure_15m-rth.json",
    ),
    (
        Sleeve::FnUsdcadSkew1hNight,
        "parity_usdcad_skew_1h-night.json",
    ),
    (
        Sleeve::FnNzdusdCusum1hRth,
        "parity_nzdusd_cusum_1h-rth.json",
    ),
    (
        Sleeve::FnEurjpyCusum1hEarlyMorning,
        "parity_eurjpy_cusum_1h-early_morning.json",
    ),
    (
        Sleeve::FnGbpaudStructure15mNyOpen,
        "parity_gbpaud_structure_15m-ny_open.json",
    ),
    (
        Sleeve::FnHk50LevelConfluence30mNight,
        "parity_hk50_level_confluence_30m-night.json",
    ),
    (
        Sleeve::FnGbpusdLinregTrend30mBarlbOff,
        "parity_gbpusd_linreg_trend_30m-barlb-off.json",
    ),
    (
        Sleeve::FnUs30LinregTrend30mLondonOpen,
        "parity_us30_linreg_trend_30m-london_open.json",
    ),
    (
        Sleeve::FnUk100Confluence1hMorning,
        "parity_uk100_confluence_1h-morning.json",
    ),
    (
        Sleeve::FnXauusdBreakRetest30mNyClose,
        "parity_xauusd_break_retest_30m-ny_close.json",
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
        Sleeve::FnUs30BreakRetest5mAfternoon,
        "parity_us30_break_retest_5m-afternoon.json",
    ),
    (
        Sleeve::FnChfjpyEstimator30mOffhour,
        "parity_chfjpy_estimator_30m-offhour.json",
    ),
    (
        Sleeve::FnGbpjpyXmaCross60mBarlbOff,
        "parity_gbpjpy_xma_cross_60m-barlb-off.json",
    ),
    (
        Sleeve::FnUs2000QuantileBreak15mAfternoon,
        "parity_us2000_quantile_break_15m-afternoon.json",
    ),
    (
        Sleeve::FnUsdjpyGatedOrb15mBarlb,
        "parity_usdjpy_gated_orb_15m-barlb.json",
    ),
    (
        Sleeve::FnNzdusdRegimeBreakout15mLondon,
        "parity_nzdusd_regime_breakout_15m-london.json",
    ),
    (
        Sleeve::FnUs30LinregTrend30mEarlyMorning,
        "parity_us30_linreg_trend_30m-early_morning.json",
    ),
    (
        Sleeve::FnUsdchfTwoStage15mRth,
        "parity_usdchf_two_stage_15m-rth.json",
    ),
    (
        Sleeve::FnEuraudRoofing15mTokyoOpen,
        "parity_euraud_roofing_15m-tokyo_open.json",
    ),
    (
        Sleeve::FnUs500Trap5mTokyo,
        "parity_us500_trap_5m-tokyo.json",
    ),
    (
        Sleeve::FnUsdcadTrRvi60mOff,
        "parity_usdcad_tr_rvi_60m-off.json",
    ),
    (
        Sleeve::FnUsdjpyVolatilityBreakout60mBarlb,
        "parity_usdjpy_volatility_breakout_60m-barlb.json",
    ),
    (
        Sleeve::FnEurjpySwingBreak30mNyOpen,
        "parity_eurjpy_swing_break_30m-ny_open.json",
    ),
    (
        Sleeve::FnFr40HalfLife1hMorning,
        "parity_fr40_half_life_1h-morning.json",
    ),
    (
        Sleeve::FnUs30AdaptivePullback30mAfternoon,
        "parity_us30_adaptive_pullback_30m-afternoon.json",
    ),
    (
        Sleeve::FnUstecTwpAsiaLondonNy15m,
        "parity_ustec_twp_asia_london_ny_15m.json",
    ),
    (
        Sleeve::FnGbpjpyMacdHist15mBarlbOff,
        "parity_gbpjpy_macd_hist_15m-barlb-off.json",
    ),
    (
        Sleeve::FnUsdchfTrap15mMorning,
        "parity_usdchf_trap_15m-morning.json",
    ),
    (
        Sleeve::FnGbpaudFailedBreak30mEarlyMorning,
        "parity_gbpaud_failed_break_30m-early_morning.json",
    ),
    (
        Sleeve::FnFr40BnqIfvgModel30m,
        "parity_fr40_bnq_ifvg_model_30m.json",
    ),
    (
        Sleeve::FnEuraudMomentum30mMorning,
        "parity_euraud_momentum_30m-morning.json",
    ),
    (
        Sleeve::FnGbpaudObvDivergence1hMorning,
        "parity_gbpaud_obv_divergence_1h-morning.json",
    ),
    (
        Sleeve::FnGbpjpyHalfLife30mNyOpen,
        "parity_gbpjpy_half_life_30m-ny_open.json",
    ),
    (
        Sleeve::FnUs2000SwingBreak1hRth,
        "parity_us2000_swing_break_1h-rth.json",
    ),
    (
        Sleeve::FnCadjpyKendall15mRth,
        "parity_cadjpy_kendall_15m-rth.json",
    ),
    (
        Sleeve::FnAudusdLuxalgoManipulation30mNyOpen,
        "parity_audusd_luxalgo_manipulation_30m-ny_open.json",
    ),
    (
        Sleeve::FnUs500Efficiency30mNyOpen,
        "parity_us500_efficiency_30m-ny_open.json",
    ),
    (
        Sleeve::FnAudusdObvDivergence30mBarlbOff,
        "parity_audusd_obv_divergence_30m-barlb-off.json",
    ),
    (
        Sleeve::FnEurusdQuantileBreak15mBarlbOff,
        "parity_eurusd_quantile_break_15m-barlb-off.json",
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
