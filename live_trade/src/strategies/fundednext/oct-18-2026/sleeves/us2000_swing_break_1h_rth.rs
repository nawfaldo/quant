//! `us2000:swing_break@1h-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_us2000_swing_break_1h-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US2000 Swing Break 1h-rth FN",
    id: "fn_us2000_swing_break_1h-rth",
    code: "FN_US2000_SWING_BREAK_1H-RTH",
    python_key: "us2000:swing_break@1h-rth",
    market: "us2000",
    book: Book::FundedNext,
    contract: Instrument::FN_US2000,
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
        bar_minutes: 60,
        per_session: 7,
        vol_target: 0.19344423387824544,
    }),
    engine: EngineKind::Family(Params {
        family: Family::SwingBreak { wing: 3 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 840,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
