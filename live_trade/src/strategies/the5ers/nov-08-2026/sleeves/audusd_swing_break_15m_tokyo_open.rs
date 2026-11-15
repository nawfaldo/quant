//! `audusd:swing_break@15m-tokyo_open`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w2000-2300`.
//! Generated from `fixtures/parity_audusd_swing_break_15m-tokyo_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDUSD Swing Break 15m-tokyo_open T5",
    id: "t5_audusd_swing_break_15m-tokyo_open",
    code: "T5_AUDUSD_SWING_BREAK_15M-TOKYO_OPEN",
    python_key: "audusd:swing_break@15m-tokyo_open",
    market: "audusd",
    book: Book::The5ers,
    contract: Instrument::T5_AUDUSD.window(180, 360),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        vol_target: 0.08017361114999486,
    }),
    engine: EngineKind::Family(Params {
        family: Family::SwingBreak { wing: 5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 300,
        stop_day: 0.7,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
