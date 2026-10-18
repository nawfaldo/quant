//! `gbpjpy:day_of_week@30m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_gbpjpy_day_of_week_30m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Day Of Week 30m-barlb FN",
    id: "fn_gbpjpy_day_of_week_30m-barlb",
    code: "FN_GBPJPY_DAY_OF_WEEK_30M-BARLB",
    python_key: "gbpjpy:day_of_week@30m-barlb",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 28,
        vol_target: 0.06909091510271062,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 0, long: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 810, // none in Python: the session close
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
