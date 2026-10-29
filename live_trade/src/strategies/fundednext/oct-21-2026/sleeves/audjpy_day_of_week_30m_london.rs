//! `audjpy:day_of_week@30m-london`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-0800`.
//! Generated from `fixtures/parity_audjpy_day_of_week_30m-london.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDJPY Day Of Week 30m-london FN",
    id: "fn_audjpy_day_of_week_30m-london",
    code: "FN_AUDJPY_DAY_OF_WEEK_30M-LONDON",
    python_key: "audjpy:day_of_week@30m-london",
    market: "audjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDJPY.window(720, 900),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        per_session: 7,
        vol_target: 0.08908572555485048,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 3, long: false },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 900, // none in Python: the session close
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
