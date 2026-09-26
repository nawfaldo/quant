//! `usdjpy:rvol`, one `cfd_families` cell.
//!
//! A bar whose body is at least one ATR on volume at least 1.5x the average for
//! its OWN clock minute over the last twenty sessions. Comparing like minutes
//! removes the U-shaped intraday profile, so it fires mid-session where
//! `volume_thrust` almost never does.
//!
//! Seated 2026-09-23 on Monte Carlo return after `ukoil:xma_cross` left.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Rvol",
    id: "usdjpy_rvol",
    code: "USDJPY_RVOL",
    python_key: "usdjpy:rvol",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Rvol {
            rvol: 1.5,
            threshold_atr: 1.0,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
