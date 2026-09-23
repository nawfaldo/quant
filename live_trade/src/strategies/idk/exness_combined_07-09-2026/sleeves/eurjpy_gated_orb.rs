//! `eurjpy:gated_orb`, one `exness_families` cell.
//!
//! The opening-range break, taken only when the SLOW RSI is on the OTHER side of
//! fifty.
//!
//! NOT `orb` WITH THE `trend` AXIS SET, and the difference is the whole cell.
//! That axis is `price > EMA`, which on a breakout bar is very nearly implied by
//! the breakout itself, so it refuses almost nothing and the two cells are close
//! to the same trade. A slow oscillator disagrees with a break constantly, and
//! the bars it refuses are the experiment.
//!
//! `oppose` IS A HYPOTHESIS, NOT A CONTROL. It names a specific and well-known
//! setup: break the opening range while the slow oscillator is still on the
//! other side of its midline, which is the first leg out of a base rather than
//! the fifth bar of a trend.
//!
//! It shares EURJPY with `eurjpy:two_stage`, which trades a COIL rather than a
//! range and takes its break at a fixed target instead of on a trail.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Gate, Params, Polarity, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::EURJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Gated ORB",
    id: "eurjpy_gated_orb",
    code: "EURJPY_GATED_ORB",
    python_key: "eurjpy:gated_orb",
    market: "eurjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb {
            gate: Gate::Rsi,
            // The claim that the confirmer is CONTRARY here.
            polarity: Polarity::Oppose,
            // `p["rsi"][1]`, the SLOW rung -- fourteen sessions. `rsi` the
            // family trades the FAST one at an extreme; this reads the same
            // construct as a standing bias, which is why the two do not collide.
            gate_period: 14 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 750,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
