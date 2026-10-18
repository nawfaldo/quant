//! `audusd:zscore`, one `cfd_families` cell.
//!
//! Fade a 1.5-sigma stretch of the 5-session close distribution.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::AUDUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDUSD Z-score",
    id: "audusd_zscore",
    code: "AUDUSD_ZSCORE",
    python_key: "audusd:zscore",
    market: "audusd",
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
        family: Family::Zscore {
            period: 5 * SESSION,
            threshold_z: 1.5,
        },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 780,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
