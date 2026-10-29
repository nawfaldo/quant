//! `us500:day_of_week@5m-evening`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1700-2000`.
//! Generated from `fixtures/parity_us500_day_of_week_5m-evening.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Day Of Week 5m-evening FN",
    id: "fn_us500_day_of_week_5m-evening",
    code: "FN_US500_DAY_OF_WEEK_5M-EVENING",
    python_key: "us500:day_of_week@5m-evening",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(0, 180),
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.16079894287337165,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 1, long: false },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 180, // none in Python: the session close
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
