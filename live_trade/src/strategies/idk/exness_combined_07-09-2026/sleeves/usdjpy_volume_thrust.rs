//! `usdjpy:volume_thrust`, one `exness_families` cell.
//!
//! A directional bar on twice the 20-session average volume.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Volume Thrust",
    id: "usdjpy_volume_thrust",
    code: "USDJPY_VOLUME_THRUST",
    python_key: "usdjpy:volume_thrust",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::VolumeThrust {
            volume_period: 20 * SESSION,
            volume_mult: 2.0,
            threshold_atr: 0.5,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 720,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
