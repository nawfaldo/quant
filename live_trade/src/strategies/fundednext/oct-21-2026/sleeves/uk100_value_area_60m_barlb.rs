//! `uk100:value_area@60m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90).
//! Generated from `fixtures/parity_uk100_value_area_60m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "UK100 Value Area 60m-barlb FN",
    id: "fn_uk100_value_area_60m-barlb",
    code: "FN_UK100_VALUE_AREA_60M-BARLB",
    python_key: "uk100:value_area@60m-barlb",
    market: "uk100",
    book: Book::FundedNext,
    contract: Instrument::FN_UK100,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        bar_minutes: 60,
        per_session: 9,
        vol_target: 0.08671743292354012,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ValueArea { buffer_atr: 0.1, poc: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 630,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
