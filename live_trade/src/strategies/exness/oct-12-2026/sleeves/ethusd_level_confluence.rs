//! `ethusd:level_confluence`, one `cfd_families` cell.
//!
//! The floor-pivot R1/S1 landing within half an ATR of yesterday's high/low --
//! the `pivot_pdr` arm, two level systems derived differently, which the book
//! had never reached until now.
//!
//! Seated 2026-09-23 on Monte Carlo return after `ukoil:xma_cross` left.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, LevelPair, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Level Confluence",
    id: "ethusd_level_confluence",
    code: "ETHUSD_LEVEL_CONFLUENCE",
    python_key: "ethusd:level_confluence",
    market: "ethusd",
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
        family: Family::LevelConfluence {
            pair: LevelPair::PivotPdr,
            tolerance: 0.5,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 900,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
