//! `ethusd:volatility_breakout`, one `cfd_families` cell.
//!
//! A range-fraction breakout on the same shape as the NQ cell.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Volatility Breakout",
    id: "ethusd_volatility_breakout",
    code: "ETHUSD_VOLATILITY_BREAKOUT",
    python_key: "ethusd:volatility_breakout",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 3.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::VolatilityBreakout { fraction: 0.5 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 840,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
