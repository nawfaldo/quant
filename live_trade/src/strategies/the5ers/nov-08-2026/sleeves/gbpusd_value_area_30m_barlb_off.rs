//! `gbpusd:value_area@30m-barlb-off`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_gbpusd_value_area_30m-barlb-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPUSD Value Area 30m-barlb-off T5",
    id: "t5_gbpusd_value_area_30m-barlb-off",
    code: "T5_GBPUSD_VALUE_AREA_30M-BARLB-OFF",
    python_key: "gbpusd:value_area@30m-barlb-off",
    market: "gbpusd",
    book: Book::The5ers,
    contract: Instrument::T5_GBPUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: Some((30, 840)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.054462797423436536,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ValueArea { buffer_atr: 0.25, poc: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
