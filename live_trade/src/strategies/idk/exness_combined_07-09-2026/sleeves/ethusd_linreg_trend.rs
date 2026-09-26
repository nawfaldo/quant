//! `ethusd:linreg_trend`, one `cfd_families` cell, WEEKEND ONLY.
//!
//! A 28-bar least-squares slope worth half a day's risk or more, on a fit of
//! 0.3 or better, in a calm tape, with the 20-day EMA.
//!
//! Enters on Saturday and Sunday only, inside the ordinary 09:30-16:00 New York
//! session (`exness_combined_strategies.WEEKEND_ONLY`). Seated 2026-09-26.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, WEEKEND};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Linreg trend (weekend)",
    id: "ethusd_linreg_trend",
    code: "ETHUSD_LINREG_TREND",
    python_key: "ethusd:linreg_trend",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: Some(WEEKEND),
    engine: EngineKind::Family(Params {
        family: Family::LinregTrend {
            // `p["linreg"]` resolves to 2 * session: 28 on ETHUSD.
            period: 2 * SESSION,
            slope: 0.5,
            min_fit: 0.3,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 900,
        stop_day: 0.7,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
