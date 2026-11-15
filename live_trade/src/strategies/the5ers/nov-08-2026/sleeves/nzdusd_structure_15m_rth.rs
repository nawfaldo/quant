//! `nzdusd:structure@15m-rth`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_nzdusd_structure_15m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Structure 15m-rth T5",
    id: "t5_nzdusd_structure_15m-rth",
    code: "T5_NZDUSD_STRUCTURE_15M-RTH",
    python_key: "nzdusd:structure@15m-rth",
    market: "nzdusd",
    book: Book::The5ers,
    contract: Instrument::T5_NZDUSD,
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
        per_session: 53,
        vol_target: 0.08727196082115221,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Structure { wing: 2, continuation: false },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 840,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
