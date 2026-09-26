//! `usdjpy:aroon`, one `cfd_families` cell.
//!
//! Aroon-up crossing Aroon-down over 135 half-hours (five sessions), taken only
//! once the stronger of the two has reached 70 -- the extreme it is naming must
//! be RECENT. Measured in time rather than price, which nothing else here is.
//!
//! Seated 2026-09-22 with the jp225 exit: best win rate of the three arrivals
//! (43 holdout trades, pf 1.88).

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Aroon",
    id: "usdjpy_aroon",
    code: "USDJPY_AROON",
    python_key: "usdjpy:aroon",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Aroon {
            // `p["aroon"]` resolves to `5 * session` here: 135 on USDJPY.
            period: 5 * SESSION,
            min_strength: 70.0,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 720,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
