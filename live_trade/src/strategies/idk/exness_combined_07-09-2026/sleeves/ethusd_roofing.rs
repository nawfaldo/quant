//! `ethusd:roofing`, one `cfd_families` cell.
//!
//! A zero crossing of Ehlers' roofing bandpass (28-bar highpass, 7-bar
//! SuperSmoother), taken only when the band has swung at least a quarter ATR in
//! the last 21 readings, and only with the 20-day EMA.
//!
//! Seated 2026-09-23 on Monte Carlo return after `ukoil:xma_cross` left.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Roofing",
    id: "ethusd_roofing",
    code: "ETHUSD_ROOFING",
    python_key: "ethusd:roofing",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Roofing {
            // `band = (28, 7)`: 2 * session and half a session on ETHUSD.
            high_period: 2 * SESSION,
            low_period: 7,
            amplitude_atr: 0.25,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 900,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
