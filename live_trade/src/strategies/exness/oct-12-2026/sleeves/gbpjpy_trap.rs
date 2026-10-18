//! `gbpjpy:trap`, one `cfd_families` cell.
//!
//! A failed breakout of the prior session's range, faded back inside.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{BreakLevel, Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::GBPJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Trap",
    id: "gbpjpy_trap",
    code: "GBPJPY_TRAP",
    python_key: "gbpjpy:trap",
    market: "gbpjpy",
    book: Book::Exness,
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: None,
    engine: EngineKind::Family(Params {
        family: Family::Trap {
            window: 8,
            channel: 4 * SESSION,
            level: BreakLevel::Donchian,
        },
        // The thesis IS the fade; the grid carries no `direction` axis.
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 690,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
