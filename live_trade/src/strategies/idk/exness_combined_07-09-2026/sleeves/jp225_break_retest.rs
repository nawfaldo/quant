//! `jp225:break_retest`, one `cfd_families` cell.
//!
//! A Donchian level breaks, price comes back to the SESSION VWAP, and it holds.
//!
//! The entry every breakout family is structurally unable to take: they fire on
//! the bar that closes through the level and are then in a position, so the
//! pullback that follows is something they sit through rather than something
//! they can act on -- and the engine allows one entry a day, so even a flat rule
//! could not re-enter.
//!
//! `retest: Vwap` is a genuinely different location from the broken level. After
//! a wide break the VWAP can sit a long way under it, so the two ask for
//! different pullback depths on the same day and disagree about whether one
//! happened at all.
//!
//! Added 2026-08-26 by greedy search, replacing `jp225:swing_break` in the
//! book's JP225 slot alongside `jp225:swing_donchian`.

use super::super::contracts::Instrument;
use super::super::family::{BreakLevel, Direction, Exit, Family, Params, Retest, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Break Retest",
    id: "jp225_break_retest",
    code: "JP225_BREAK_RETEST",
    python_key: "jp225:break_retest",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::BreakRetest {
            // How long the break is allowed to take to come back. The ONLY
            // swept axis of the three: tolerance is fixed at a quarter ATR,
            // because sweeping both would turn one hypothesis into a grid over
            // what the word "retest" means.
            window: 8,
            level: BreakLevel::Donchian,
            retest: Retest::Vwap,
            // `_breakout_levels("donchian")` is FIXED at `4 * session` and does
            // not read the cell's own channel axis -- `break_retest` has none.
            channel: 4 * SESSION,
        },
        // `break_retest` carries no `direction` axis: the retest holding IS the
        // thesis, and its side is whichever way the level broke.
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 420,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
