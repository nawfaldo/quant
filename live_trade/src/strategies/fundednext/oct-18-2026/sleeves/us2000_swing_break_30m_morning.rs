//! `us2000:swing_break@30m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_us2000_swing_break_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US2000 Swing Break 30m-morning FN",
    id: "fn_us2000_swing_break_30m-morning",
    code: "FN_US2000_SWING_BREAK_30M-MORNING",
    python_key: "us2000:swing_break@30m-morning",
    market: "us2000",
    book: Book::FundedNext,
    contract: Instrument::FN_US2000.window(720, 1080),
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
        bar_minutes: 30,
        per_session: 13,
        vol_target: 0.20780555875252776,
    }),
    engine: EngineKind::Family(Params {
        family: Family::SwingBreak { wing: 5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
