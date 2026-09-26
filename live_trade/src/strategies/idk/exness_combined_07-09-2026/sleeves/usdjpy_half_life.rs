//! `usdjpy:half_life`, one `cfd_families` cell.
//!
//! A z-score whose LOOKBACK the market decides, followed rather than faded.
//!
//! EVERY OTHER OSCILLATOR HERE IS TOLD ITS HORIZON. `usdjpy:kendall` is given a
//! period, `audusd:zscore` is given three and the search keeps whichever paid.
//! This fits an Ornstein-Uhlenbeck process to the log series every bar --
//! regressing the one-bar change on the level -- and uses the half-life that
//! falls out as its own window. On the same series it therefore reads a
//! twelve-bar deviation in a fast-reverting stretch and a two-hundred-bar one
//! in a slow stretch, and no single fixed-period cell does both.
//!
//! IT IS SILENT WHERE A Z-SCORE IS LOUDEST, and that is the point. When the
//! fitted lambda is not negative the series is not reverting on any timescale
//! the sample can see, and there is no reading at all -- so this family sits out
//! every trending stretch by construction. A fixed-period z-score computes a
//! perfectly well-defined deviation from a mean the price is walking away from,
//! and trades it.
//!
//! AND IT IS `follow`, NOT `fade`, WHICH READS BACKWARDS UNTIL YOU SEE WHAT THE
//! REFUSAL DOES. The cell only has an opinion on bars the fit called reverting;
//! among those, it takes the side the deviation is moving TOWARD rather than
//! away from. The OU fit is the admission gate and the z-score is the trigger,
//! which is a different rule from either piece alone.
//!
//! Seated 2026-09-19 with `ethusd:kalman`, replacing
//! `jp225:volatility_breakout` and `jp225:break_retest`. Eight of twenty-two
//! sleeves stood on one index and JP225 carried 96% of the loss in the worst
//! opening fortnight on record ([[sep-2026-fortnight-was-a-severe-cold-start]]).
//! The pair takes the 1,000-path tail from p99 35.30% to 31.32% and the worst
//! path from 60.26% to 40.66%, and it is this sleeve that carries the return.
//!
//! THE FIRST FAMILY PORTED FOR A REPLACEMENT RATHER THAN FOR THE ORIGINAL BOOK.
//! `Family::HalfLife` and `OuHalfLife` exist because of this cell; nothing else
//! in the book measures its own horizon.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::USDJPY;

/// 30-minute buckets in one session, the unit the period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Half Life",
    id: "usdjpy_half_life",
    code: "USDJPY_HALF_LIFE",
    python_key: "usdjpy:half_life",
    market: "usdjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::HalfLife {
            // `p["halflife"][0]`, which is `max(60, 10 * per_session)` and on
            // USDJPY's 27-bar session is 270. It is BOTH the sample the fit is
            // taken over and the ceiling on the life it may report -- a
            // half-life longer than its own sample is an extrapolation.
            period: 10 * SESSION,
            // Two half-lives of window. One is the decay time itself; two is
            // the span over which a deviation has mostly resolved.
            multiple: 2.0,
            threshold_z: 1.5,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
