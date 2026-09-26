//! `jp225:volume_thrust`, one `cfd_families` cell.
//!
//! A directional bar on twice the five-session average volume, taken in its own
//! direction, in a CALM regime and only in the first seven hours of the session.
//!
//! THE SAME FAMILY AS `usdjpy:volume_thrust` AND NOT THE SAME CELL. That one
//! runs a twenty-session volume window with no volatility gate and trails its
//! exit; this one reads a five-session window, refuses to fire unless short
//! realised volatility is under the long reading, and takes a fixed two-to-one
//! target. A shared trigger with an opposite exit and an opposite regime gate is
//! close to the least correlated way to hold a family twice.
//!
//! `shown_equity` 2.0 is ACCESS, not leverage: at unit scale the sleeve filled
//! 90.1% of its orders and the rest landed under JP225's THREE-lot floor.
//!
//! Added 2026-08-29.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Volume Thrust",
    id: "jp225_volume_thrust",
    code: "JP225_VOLUME_THRUST",
    python_key: "jp225:volume_thrust",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    // `SHOWN_EQUITY["jp225:volume_thrust"]`: 90.1% fills -> 100.0%.
    shown_equity: 2.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::VolumeThrust {
            volume_period: 5 * SESSION,
            volume_mult: 2.0,
            threshold_atr: 0.5,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 420,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
