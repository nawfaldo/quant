//! `euraud:day_of_week@15m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_euraud_day_of_week_15m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Day Of Week 15m-afternoon FN",
    id: "fn_euraud_day_of_week_15m-afternoon",
    code: "FN_EURAUD_DAY_OF_WEEK_15M-AFTERNOON",
    python_key: "euraud:day_of_week@15m-afternoon",
    market: "euraud",
    book: Book::FundedNext,
    contract: Instrument::FN_EURAUD.window(1080, 1439),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        bar_minutes: 15,
        per_session: 24,
        vol_target: 0.06077693315072865,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 1, long: false },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1439, // none in Python: the session close
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
