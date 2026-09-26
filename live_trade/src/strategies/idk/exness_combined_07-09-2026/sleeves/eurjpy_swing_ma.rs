//! `eurjpy:swing_ma`, one `cfd_families` cell.
//!
//! A one-session EMA crossing a five-session one. Declared a `swing` family,
//! but canon is scored under `SESSION_ONLY`, so it is flattened at the close
//! like every other member.
//!
//! IN THE BOOK FOR CORRELATION, NOT RETURN: the smallest contributor of the
//! 21, negative in 2023 and 2025. `eurjpy:xma_cross` shares this exact entry
//! (28/140) and is deliberately NOT seated beside it.
//!
//! Seated 2026-09-22.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::EURJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Swing MA",
    id: "eurjpy_swing_ma",
    code: "EURJPY_SWING_MA",
    python_key: "eurjpy:swing_ma",
    market: "eurjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::SwingMa {
            fast: SESSION,
            slow: 5 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        // Python has no cutoff for this family and `swing_ma_signal` never
        // asks. The session close is the equivalent reachable value, so the
        // "every member is a session cell" invariant holds without a special
        // case -- and nothing reads it.
        last_entry_minute: CONTRACT.session.1,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
