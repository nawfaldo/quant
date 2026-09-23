//! `usdjpy:pullback`, one `exness_families` cell.
//!
//! A retracement from a recent extreme, measured in DAILY RANGE, inside a trend
//! that is still intact.
//!
//! The one family in the book that waits to be paid a WORSE price. Every other
//! member fires at the edge of a move -- a channel break, a new extreme, a
//! thrust bar -- so their entries cluster on exactly the bars this one is
//! sitting out, which makes it close to mutually exclusive with them by
//! construction rather than by measurement.
//!
//! It shares USDJPY with `usdjpy:volume_thrust`, and that is the reason it was
//! admissible: the two trade opposite parts of the same move.
//!
//! Added 2026-08-26 by greedy search over the survivor pool.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Pullback",
    id: "usdjpy_pullback",
    code: "USDJPY_PULLBACK",
    python_key: "usdjpy:pullback",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::Pullback {
            channel: 4 * SESSION,
            // Half a daily range back from the extreme, and no further. A deeper
            // retracement is not a pullback in a trend, it is a reversal the
            // trend filter has not caught up with yet.
            depth: 0.5,
            // `ema_50d`, the SLOWER of the two the axis offers -- and its own
            // setting, not the shared `trend` gate, which this cell leaves off.
            reference: 50 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 720,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
