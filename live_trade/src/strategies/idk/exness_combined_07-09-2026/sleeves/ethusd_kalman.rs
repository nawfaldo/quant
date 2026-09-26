//! `ethusd:kalman`, one `cfd_families` cell.
//!
//! The state-space RESIDUAL, faded: price's distance from the filtered level.
//!
//! THE OTHER CLAIM THE SAME FILTER MAKES. `jp225:kalman` trades the RATE's
//! sign and is a trend cell; this trades the distance from the line and is a
//! mean-reversion one. One filter, two readings, opposite hypotheses -- which
//! is why the two do not collide despite sharing a name and an implementation.
//!
//! AND THE LINE IS NOT A MOVING AVERAGE. Every average in the book applies a
//! fixed kernel forever; this carries a covariance and applies the gain that is
//! optimal given how noisy the series has actually been, so it re-anchors fast
//! after a violent bar instead of dragging one through the next twenty. On
//! ETHUSD -- which has no session, a seven-day week and the book's largest bar
//! ranges -- that difference is the whole reason a residual is tradeable at all.
//!
//! Seated 2026-09-19, replacing `jp225:volatility_breakout` and
//! `jp225:break_retest`, which were dropped for concentration: eight of
//! twenty-two sleeves on one index, and JP225 carried 96% of the loss in the
//! worst opening fortnight on record ([[sep-2026-fortnight-was-a-severe-cold-start]]).
//! It is the single addition that most improves the worst cold fortnight
//! (-15.14% -> -13.48%) while lowering drawdown rather than trading it away.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, KalmanMode, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Kalman",
    id: "ethusd_kalman",
    code: "ETHUSD_KALMAN",
    python_key: "ethusd:kalman",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Kalman {
            // The RESIDUAL, in ATRs. `slope` is the other arm and is a trend
            // statement about the same state.
            mode: KalmanMode::Residual,
            threshold: 0.5,
        },
        // FADE, because the residual IS the deviation: a price far above its
        // own filtered level is the thing being sold.
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 840,
        stop_day: 0.4,
        // The only axis this cell sets beyond the family's own: a 20-day EMA
        // admission gate, which on a 24/7 market is 20 calendar days rather
        // than 20 sessions.
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
