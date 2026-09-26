//! `usdjpy:kendall`, one `cfd_families` cell.
//!
//! FADE a Mann-Kendall trend z past two sigma, over 270 half-hours.
//!
//! ROBUST WHERE A SLOPE IS NOT, and that is the reason to carry it beside
//! `usdjpy:volume_thrust` and `usdjpy:pullback`. A least-squares slope is a
//! weighted mean and one violent bar moves it; this counts only the SIGN of
//! every pairwise comparison in the window, so a gap contributes at most its
//! share of the count. Measured on a series that ground upward and then gapped
//! 20% down on the last bar, this lost 2.0% of its value and the regression lost
//! 6.9%.
//!
//! THE THRESHOLD IS IN SIGMA AND 2.0 THEREFORE MEANS THE CONVENTIONAL THING. It
//! is the one trend reading in the study directly interpretable as significance,
//! so the number is not something the search found.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Kendall",
    id: "usdjpy_kendall",
    code: "USDJPY_KENDALL",
    python_key: "usdjpy:kendall",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Kendall {
            // `p["kendall"][1]`, which is `max(100, 10 * session)`. On USDJPY
            // the session reading wins at 270; the floor exists for daily bars,
            // where ten sessions is ten observations and the normal
            // approximation the z rests on is not one.
            period: 10 * SESSION,
            threshold: 2.0,
        },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 720,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
