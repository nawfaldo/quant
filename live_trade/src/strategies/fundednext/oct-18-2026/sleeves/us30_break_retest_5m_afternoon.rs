//! `us30:break_retest@5m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_us30_break_retest_5m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel, Retest};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Break Retest 5m-afternoon FN",
    id: "fn_us30_break_retest_5m-afternoon",
    code: "FN_US30_BREAK_RETEST_5M-AFTERNOON",
    python_key: "us30:break_retest@5m-afternoon",
    market: "us30",
    book: Book::FundedNext,
    contract: Instrument::FN_US30.window(1080, 1439),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: true,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 5,
        per_session: 72,
        vol_target: 0.11666950983428687,
    }),
    engine: EngineKind::Family(Params {
        family: Family::BreakRetest { window: 3, level: BreakLevel::Pdr, retest: Retest::Vwap, channel: 288 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
