//! `nzdusd:trap@15m-offhour`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_nzdusd_trap_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Trap 15m-offhour FN",
    id: "fn_nzdusd_trap_15m-offhour",
    code: "FN_NZDUSD_TRAP_15M-OFFHOUR",
    python_key: "nzdusd:trap@15m-offhour",
    market: "nzdusd",
    book: Book::FundedNext,
    contract: Instrument::FN_NZDUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((90, 810)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.08828249399926362,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Trap { window: 8, channel: 368, level: BreakLevel::Pdr },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1379,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
