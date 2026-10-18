//! `gbpaud:structure@15m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_gbpaud_structure_15m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPAUD Structure 15m-ny_open FN",
    id: "fn_gbpaud_structure_15m-ny_open",
    code: "FN_GBPAUD_STRUCTURE_15M-NY_OPEN",
    python_key: "gbpaud:structure@15m-ny_open",
    market: "gbpaud",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPAUD.window(900, 1080),
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
        bar_minutes: 15,
        per_session: 13,
        vol_target: 0.06171562182130209,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Structure { wing: 5, continuation: false },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
