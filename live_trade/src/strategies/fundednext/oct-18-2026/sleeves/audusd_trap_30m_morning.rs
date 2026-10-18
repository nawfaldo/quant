//! `audusd:trap@30m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_audusd_trap_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDUSD Trap 30m-morning FN",
    id: "fn_audusd_trap_30m-morning",
    code: "FN_AUDUSD_TRAP_30M-MORNING",
    python_key: "audusd:trap@30m-morning",
    market: "audusd",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDUSD.window(720, 1080),
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
        bar_minutes: 30,
        per_session: 13,
        vol_target: 0.0844066070646095,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Trap { window: 8, channel: 52, level: BreakLevel::Donchian },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
