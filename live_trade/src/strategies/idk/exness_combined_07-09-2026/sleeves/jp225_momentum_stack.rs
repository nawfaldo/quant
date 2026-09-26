//! `jp225:momentum_stack`, one `cfd_families` cell.
//!
//! Three horizons of momentum -- one, five and twenty sessions -- each divided
//! by its own dispersion, summed, and traded past three sigma.
//!
//! WHAT THE VOTING FAMILIES CANNOT SAY. A rule that counts agreement treats a
//! 0.1-sigma move and a 3-sigma move as the same vote. This keeps the magnitude,
//! so it fires on a bar where one horizon is overwhelming and the other two are
//! mildly against -- a refusal for every voting family -- and refuses a bar
//! where all three agree feebly, which they take at full size. The disagreement
//! runs in BOTH directions, which is what stops it being a filter on either.
//!
//! STANDARDISING IS ALSO WHAT MAKES SUMMING LEGAL. A one-session return and a
//! twenty-session return have different variances, so adding them raw would make
//! the longest horizon the only one that mattered and would quietly reduce this
//! to a one-horizon rule.
//!
//! THE EARLIEST CUTOFF IN THE BOOK, 06:00 on the shifted clock. JP225 opens at
//! 01:00 there, so this cell trades the first five hours of the session and
//! nothing else.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Momentum Stack",
    id: "jp225_momentum_stack",
    code: "JP225_MOMENTUM_STACK",
    python_key: "jp225:momentum_stack",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::MomentumStack {
            threshold: 3.0,
            // `ctx["periods"]["session"]`. The three spans are 1x, 5x and 20x
            // this, and each is standardised over TWICE its own span -- so the
            // deepest reading here is forty sessions of history.
            session: SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 360,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
