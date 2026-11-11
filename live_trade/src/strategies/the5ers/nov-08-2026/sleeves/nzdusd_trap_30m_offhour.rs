//! `nzdusd:trap@30m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_nzdusd_trap_30m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Trap 30m-offhour T5",
    id: "t5_nzdusd_trap_30m-offhour",
    code: "T5_NZDUSD_TRAP_30M-OFFHOUR",
    python_key: "nzdusd:trap@30m-offhour",
    market: "nzdusd",
    book: Book::The5ers,
    contract: Instrument::T5_NZDUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((120, 900)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.08914438256055761,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Trap { window: 8, channel: 184, level: BreakLevel::Pdr },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1319,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
