//! `jp225:volatility_breakout`, one `exness_families` cell.
//!
//! A close beyond the open plus three tenths of yesterday's range.
//!
//! THE SAME SHAPE AS `ethusd:volatility_breakout` ON A DIFFERENT MARKET AND A
//! DIFFERENT EXIT. That one trails and takes half the prior range; this takes
//! three tenths and leaves at a fixed two-to-one target, so it enters earlier
//! and stops arguing sooner. On a market carrying eight sleeves, the exit is
//! most of what keeps them from being one trade.
//!
//! THE BUSIEST CELL IN THE BOOK at 341 trades over the holdout, and that is why
//! `stop_day` sits on the tightest rung: at that frequency a wide stop pays the
//! spread three hundred times for one thesis.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 Volatility Breakout",
    id: "jp225_volatility_breakout",
    code: "JP225_VOLATILITY_BREAKOUT",
    python_key: "jp225:volatility_breakout",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::VolatilityBreakout { fraction: 0.3 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 420,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
