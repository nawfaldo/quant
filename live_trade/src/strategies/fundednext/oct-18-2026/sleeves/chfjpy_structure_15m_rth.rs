//! `chfjpy:structure@15m-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_chfjpy_structure_15m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Structure 15m-rth FN",
    id: "fn_chfjpy_structure_15m-rth",
    code: "FN_CHFJPY_STRUCTURE_15M-RTH",
    python_key: "chfjpy:structure@15m-rth",
    market: "chfjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CHFJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        bar_minutes: 15,
        per_session: 59,
        vol_target: 0.07309100724589312,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Structure { wing: 2, continuation: false },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 810,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
