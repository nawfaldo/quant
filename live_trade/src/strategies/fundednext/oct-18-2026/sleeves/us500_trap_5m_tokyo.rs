//! `us500:trap@5m-tokyo`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2300-0200`.
//! Generated from `fixtures/parity_us500_trap_5m-tokyo.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Trap 5m-tokyo FN",
    id: "fn_us500_trap_5m-tokyo",
    code: "FN_US500_TRAP_5M-TOKYO",
    python_key: "us500:trap@5m-tokyo",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(360, 540),
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
        vol_target: 0.11450219120638447,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Trap { window: 8, channel: 148, level: BreakLevel::Pdr },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 420,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
