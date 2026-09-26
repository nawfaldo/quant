//! `usdjpy:fracdiff`, one `cfd_families` cell.
//!
//! A fractionally differenced log price at order 0.6, z-scored over twenty
//! sessions and FOLLOWED past two and a half sigma.
//!
//! THE FAMILY TO HOLD THIS AGAINST IS `zscore`, AND THE DIFFERENCE IS
//! STRUCTURAL. A z-score subtracts a rolling mean, which is a full difference of
//! a smoothed series: beyond the window it has forgotten everything. A
//! fractional difference of order d in (0, 1) is the continuum between a price
//! and a return -- stationary enough to threshold, with weights that decay as a
//! POWER LAW rather than being truncated, so the reading still carries
//! information from hundreds of bars back.
//!
//! They disagree in one specific and very common situation: after a long slow
//! drift. The z-score's mean has followed the drift and reports no deviation at
//! all; this has not forgotten where the series started and reports a large one.
//!
//! ACCELERATING RATHER THAN MERELY CLEAN. 18.5R in eight months of 2026 against
//! 5.3R in all of 2025, a 2.49x ratio -- which is why it was seated on the
//! HOLDOUT rather than on the long window.
//!
//! Seated 2026-09-04.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Fracdiff",
    id: "usdjpy_fracdiff",
    code: "USDJPY_FRACDIFF",
    python_key: "usdjpy:fracdiff",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Fracdiff {
            // 0.6 is past the point where most price series pass an ADF test and
            // still keeps far more history than a first difference. The axis
            // offers 0.3 as well; neither is searched finer, because sweeping d
            // would be fitting the transform to the answer.
            order: 0.6,
            threshold_z: 2.5,
            // `20 * p["session"]`, the window the differenced series is
            // standardised against. Its OWN trailing statistics: the series is
            // not zero-mean and its scale depends on the order, so a raw
            // threshold would mean a different thing at each d.
            window: 20 * SESSION,
        },
        // INVERTS THE RAW SIDE. The raw reading fades the deviation, so a
        // `follow` cell buys a series that has stretched upward. See
        // `FamilyEngine::fracdiff_signal`.
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
