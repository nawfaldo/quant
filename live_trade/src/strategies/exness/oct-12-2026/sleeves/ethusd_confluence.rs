//! `ethusd:confluence`, one `cfd_families` cell.
//!
//! Five exhaustion readings vote; fade a margin of three or more.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Confluence",
    id: "ethusd_confluence",
    code: "ETHUSD_CONFLUENCE",
    python_key: "ethusd:confluence",
    market: "ethusd",
    book: Book::Exness,
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 3.0,
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
        family: Family::Confluence {
            votes: 3,
            zscore_period: 2 * SESSION,
            // `.max(2)` in Python, inert here: 2 * 14 is never below 2.
            rsi_period: 2 * SESSION,
        },
        // `mode: fade` is carried by the family rather than by
        // `direction`, which the confluence grid does not have.
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 900,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
