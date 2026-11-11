//! `euraud:trap@5m-tokyo_open`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w2000-2300`.
//! Generated from `fixtures/parity_euraud_trap_5m-tokyo_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Trap 5m-tokyo_open T5",
    id: "t5_euraud_trap_5m-tokyo_open",
    code: "T5_EURAUD_TRAP_5M-TOKYO_OPEN",
    python_key: "euraud:trap@5m-tokyo_open",
    market: "euraud",
    book: Book::The5ers,
    contract: Instrument::T5_EURAUD.window(180, 360),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
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
        vol_target: 0.060626169858765344,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Trap { window: 3, channel: 148, level: BreakLevel::Swing },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 240,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
