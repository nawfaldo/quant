//! Trade-for-trade agreement with `cfd_families.backtest` at The5ers.
//!
//! Each fixture is written by `sandbox/research/_the5ers_parity.py` under
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
        .join("src/strategies/the5ers/nov-08-2026/fixtures")
        .join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()))
}

fn assert_reproduces(fixture: &Fixture) {
    assert_matches(fixture, &replay(fixture));
}

/// One fixture per member, in `BOOK` order.
const ALL: [(Sleeve, &str); 32] = [
    (
        Sleeve::T5SolusdConfluence15mOffhour,
        "parity_solusd_confluence_15m-offhour.json",
    ),
    (
        Sleeve::T5ChfjpyGatedFade30mOffhour,
        "parity_chfjpy_gated_fade_30m-offhour.json",
    ),
    (
        Sleeve::T5EurusdRvol30mRth,
        "parity_eurusd_rvol_30m-rth.json",
    ),
    (
        Sleeve::T5GbpjpyDayOfWeek15mOffhour,
        "parity_gbpjpy_day_of_week_15m-offhour.json",
    ),
    (
        Sleeve::T5UstecStructure30mAfternoon,
        "parity_ustec_structure_30m-afternoon.json",
    ),
    (
        Sleeve::T5EtheurConfluence30mOffhour,
        "parity_etheur_confluence_30m-offhour.json",
    ),
    (
        Sleeve::T5Us30TwoStage30mOffhour,
        "parity_us30_two_stage_30m-offhour.json",
    ),
    (
        Sleeve::T5BtceurGatedOrb15mEarlyMorning,
        "parity_btceur_gated_orb_15m-early_morning.json",
    ),
    (
        Sleeve::T5De40GatedFade30mMorning,
        "parity_de40_gated_fade_30m-morning.json",
    ),
    (
        Sleeve::T5GbpusdValueArea30mBarlbOff,
        "parity_gbpusd_value_area_30m-barlb-off.json",
    ),
    (
        Sleeve::T5Us30Stochastic30mMorning,
        "parity_us30_stochastic_30m-morning.json",
    ),
    (
        Sleeve::T5Us500DayOfWeek5mEvening,
        "parity_us500_day_of_week_5m-evening.json",
    ),
    (
        Sleeve::T5UstecSwingBreak5mNyOpen,
        "parity_ustec_swing_break_5m-ny_open.json",
    ),
    (
        Sleeve::T5NzdusdStructure15mRth,
        "parity_nzdusd_structure_15m-rth.json",
    ),
    (
        Sleeve::T5GbpusdRegimeBreakout5mNyMidday,
        "parity_gbpusd_regime_breakout_5m-ny_midday.json",
    ),
    (
        Sleeve::T5NzdusdTrap30mOffhour,
        "parity_nzdusd_trap_30m-offhour.json",
    ),
    (
        Sleeve::T5AudjpyGatedOrb15mNyOpen,
        "parity_audjpy_gated_orb_15m-ny_open.json",
    ),
    (
        Sleeve::T5UsdjpyTwoStage30mLondonOpen,
        "parity_usdjpy_two_stage_30m-london_open.json",
    ),
    (
        Sleeve::T5EurusdKalman30mRth,
        "parity_eurusd_kalman_30m-rth.json",
    ),
    (
        Sleeve::T5UsdcadHalfLife15mOffhour,
        "parity_usdcad_half_life_15m-offhour.json",
    ),
    (
        Sleeve::T5AudusdKendall15mMorning,
        "parity_audusd_kendall_15m-morning.json",
    ),
    (
        Sleeve::T5EuraudGatedFade15mOffhour,
        "parity_euraud_gated_fade_15m-offhour.json",
    ),
    (
        Sleeve::T5EuraudTrap5mTokyoOpen,
        "parity_euraud_trap_5m-tokyo_open.json",
    ),
    (
        Sleeve::T5CadjpyMomentum5mRth,
        "parity_cadjpy_momentum_5m-rth.json",
    ),
    (
        Sleeve::T5AudusdSwingBreak15mTokyoOpen,
        "parity_audusd_swing_break_15m-tokyo_open.json",
    ),
    (
        Sleeve::T5UsdchfFloorPivot15mMorning,
        "parity_usdchf_floor_pivot_15m-morning.json",
    ),
    (
        Sleeve::T5Uk100RegimeBreakout30mNyOpen,
        "parity_uk100_regime_breakout_30m-ny_open.json",
    ),
    (
        Sleeve::T5Hk50Efficiency15mBarlb,
        "parity_hk50_efficiency_15m-barlb.json",
    ),
    (
        Sleeve::T5Hk50DayOfWeek30mNyOpen,
        "parity_hk50_day_of_week_30m-ny_open.json",
    ),
    (
        Sleeve::T5EurjpyGatedOrb30mBarlb,
        "parity_eurjpy_gated_orb_30m-barlb.json",
    ),
    (
        Sleeve::T5Uk100Confluence1hMorning,
        "parity_uk100_confluence_1h-morning.json",
    ),
    (
        Sleeve::T5AudjpyStructure30mBarlbOff,
        "parity_audjpy_structure_30m-barlb-off.json",
    ),
];

#[test]
fn every_member_has_a_fixture_and_nothing_else_does() {
    let covered: Vec<Sleeve> = ALL.iter().map(|(sleeve, _)| *sleeve).collect();
    assert_eq!(covered, BOOK.to_vec());
}

#[test]
fn every_member_is_priced_at_the5ers() {
    for sleeve in BOOK {
        assert_eq!(sleeve.book(), Book::The5ers, "{}", sleeve.display());
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
    let wanted = std::env::var("T5_DUMP").unwrap_or_default();
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
