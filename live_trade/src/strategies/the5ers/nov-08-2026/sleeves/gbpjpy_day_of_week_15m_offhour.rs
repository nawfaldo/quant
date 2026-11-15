//! `gbpjpy:day_of_week@15m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_gbpjpy_day_of_week_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Day Of Week 15m-offhour T5",
    id: "t5_gbpjpy_day_of_week_15m-offhour",
    code: "T5_GBPJPY_DAY_OF_WEEK_15M-OFFHOUR",
    python_key: "gbpjpy:day_of_week@15m-offhour",
    market: "gbpjpy",
    book: Book::The5ers,
    contract: Instrument::T5_GBPJPY.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((0, 810)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.0822278472724299,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DayOfWeek { weekday: 0, long: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1439, // none in Python: the session close
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
