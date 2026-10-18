//! `ethusd:efficiency`, one `cfd_families` cell, WEEKEND ONLY.
//!
//! Kaufman's efficiency ratio over 28 half-hours at 0.3 or better -- a CLEAN
//! path -- then the move's own direction, with the 20-day EMA.
//!
//! Enters on Saturday and Sunday only, inside the ordinary 09:30-16:00 New York
//! session (`exness_combined_strategies.WEEKEND_ONLY`). Seated 2026-09-26.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, ErRegime, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, WEEKEND};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Efficiency (weekend)",
    id: "ethusd_efficiency",
    code: "ETHUSD_EFFICIENCY",
    python_key: "ethusd:efficiency",
    market: "ethusd",
    book: Book::Exness,
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: Some(WEEKEND),
    timeframe: None,
    engine: EngineKind::Family(Params {
        family: Family::Efficiency {
            // `p["er"]` resolves to 2 * session: 28 on ETHUSD.
            period: 2 * SESSION,
            min_er: 0.3,
            regime: ErRegime::Clean,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 840,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
