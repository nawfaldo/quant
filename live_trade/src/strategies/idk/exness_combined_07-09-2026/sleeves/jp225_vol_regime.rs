//! `jp225:vol_regime`, one `cfd_families` cell.
//!
//! A one-session momentum, taken only while short-horizon volatility sits BELOW
//! 1.3 times its own hundred-session baseline.
//!
//! THE SAME TWO READINGS `vol_mode` FILTERS ON, USED AS THE TRIGGER. Every other
//! member of this book treats the volatility ratio as an admission gate -- it
//! removes bars and never fires on one. This asks whether the state itself is
//! tradeable, so the ratio decides WHEN and a plain fifteen-bar move decides
//! WHICH WAY. `vol_mode: calm` is still set on top, and that is not a duplicate:
//! `contracting` is measured against `ratio` and `calm` against the raw
//! comparison, so the cell wants a market that is quiet AND getting quieter.
//!
//! `time_4` -- two hours -- is the shortest exit the grid offers and the only
//! one that makes sense here: the thesis is about a regime, not about a level,
//! so there is no price the position is waiting for.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Regime, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Vol Regime",
    id: "jp225_vol_regime",
    code: "JP225_VOL_REGIME",
    python_key: "jp225:vol_regime",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::VolRegime {
            ratio: 1.3,
            // The QUIET side, which is the arm that survived. An `expanding`
            // cell is the same rule waiting for the eruption instead.
            regime: Regime::Contracting,
            // `ctx["periods"]["session"]`, the horizon the direction is read
            // over -- one session, so the move is today's rather than the
            // week's.
            lookback: SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 420,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
