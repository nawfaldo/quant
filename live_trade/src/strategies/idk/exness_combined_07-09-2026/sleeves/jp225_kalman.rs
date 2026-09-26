//! `jp225:kalman`, one `cfd_families` cell.
//!
//! The state-space SLOPE, past half a bar's typical log move.
//!
//! THE ONLY FILTER IN THE BOOK WHOSE WEIGHTING IS NOT FIXED IN ADVANCE. Every
//! moving average applies the same kernel to every bar forever; this carries a
//! covariance and applies the gain that is optimal given how noisy the series
//! has actually been -- trusting its own state through a quiet stretch and
//! re-anchoring fast after a violent bar.
//!
//! And the slope is a STATE, not a difference of two smoothed points. A
//! differenced slope is the slope of the past window, so after a turn it keeps
//! the old sign for as long as the window is wide; this turns with the data.
//! That is the bar where it and `ukoil:xma_cross` disagree, and it is every
//! turn.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, KalmanMode, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Kalman",
    id: "jp225_kalman",
    code: "JP225_KALMAN",
    python_key: "jp225:kalman",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Kalman {
            // The RATE's sign. `residual` is the other claim the same state
            // makes -- price's distance from the filtered level -- and it is a
            // mean-reversion cell rather than a trend one.
            mode: KalmanMode::Slope,
            // In units of `atr / close`, the bar's own typical log move, so the
            // number means the same thing on every market.
            threshold: 0.5,
        },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 420,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
