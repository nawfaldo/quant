//! `ethusd:obv_break`, one `cfd_families` cell.
//!
//! On-balance volume breaking its own channel, price ignored.
//!
//! The SAME family as `gbpusd:obv_break` at a different channel and a different
//! exit, and the pair is worth keeping in mind when reading the dependence
//! matrix: two cells of one family on two markets are one hypothesis tested
//! twice, not two hypotheses.
//!
//! Added 2026-08-26 by greedy search over the survivor pool.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD OBV Break",
    id: "ethusd_obv_break",
    code: "ETHUSD_OBV_BREAK",
    python_key: "ethusd:obv_break",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::ObvBreak {
            // `p["obv"][0]`. Longer than the price channels on purpose: OBV is
            // a cumulative line, so a one-sided stretch needs room to show.
            channel: 10 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 900,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
