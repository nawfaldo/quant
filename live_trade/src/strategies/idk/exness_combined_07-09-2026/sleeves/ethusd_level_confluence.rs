//! `ethusd:level_confluence`, one `cfd_families` cell.
//!
//! The floor-pivot R1/S1 landing within half an ATR of yesterday's high/low --
//! the `pivot_pdr` arm, two level systems derived differently, which the book
//! had never reached until now.
//!
//! Seated 2026-09-23 on Monte Carlo return after `ukoil:xma_cross` left.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, LevelPair, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Level Confluence",
    id: "ethusd_level_confluence",
    code: "ETHUSD_LEVEL_CONFLUENCE",
    python_key: "ethusd:level_confluence",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
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
