//! `us500:av_first_hour_fib@30m-afternoon`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_us500_av_first_hour_fib_30m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 AV First Hour Fib 30m-afternoon FN",
    id: "fn_us500_av_first_hour_fib_30m-afternoon",
    code: "FN_US500_AV_FIRST_HOUR_FIB_30M-AFTERNOON",
    python_key: "us500:av_first_hour_fib@30m-afternoon",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(1080, 1439),
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
        bar_minutes: 30,
        per_session: 12,
        vol_target: 0.12152916165604162,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AvFirstHourFib { entry: 0.618, both: false },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
