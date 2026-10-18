//! `euraud:day_of_week@1h-night`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1700-2300`.
//! Generated from `fixtures/parity_euraud_day_of_week_1h-night.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Day Of Week 1h-night FN",
    id: "fn_euraud_day_of_week_1h-night",
    code: "FN_EURAUD_DAY_OF_WEEK_1H-NIGHT",
    python_key: "euraud:day_of_week@1h-night",
    market: "euraud",
    book: Book::FundedNext,
    contract: Instrument::FN_EURAUD.window(0, 360),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 5.0,
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
        bar_minutes: 60,
        per_session: 7,
        vol_target: 0.05921690329971856,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 1, long: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 360, // none in Python: the session close
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
