//! `cadjpy:value_area@30m-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_cadjpy_value_area_30m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CADJPY Value Area 30m-rth FN",
    id: "fn_cadjpy_value_area_30m-rth",
    code: "FN_CADJPY_VALUE_AREA_30M-RTH",
    python_key: "cadjpy:value_area@30m-rth",
    market: "cadjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CADJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 24,
        vol_target: 0.08839463130761335,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ValueArea { buffer_atr: 0.1, poc: false },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 750,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
