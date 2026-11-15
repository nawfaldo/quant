//! `ustec:swing_break@5m-ny_open`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_ustec_swing_break_5m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Swing Break 5m-ny_open T5",
    id: "t5_ustec_swing_break_5m-ny_open",
    code: "T5_USTEC_SWING_BREAK_5M-NY_OPEN",
    python_key: "ustec:swing_break@5m-ny_open",
    market: "ustec",
    book: Book::The5ers,
    contract: Instrument::T5_USTEC.window(900, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        vol_target: 0.16224219486284341,
    }),
    engine: EngineKind::Family(Params {
        family: Family::SwingBreak { wing: 3 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1020,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
