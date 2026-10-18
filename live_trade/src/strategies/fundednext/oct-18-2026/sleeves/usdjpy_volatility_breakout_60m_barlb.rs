//! `usdjpy:volatility_breakout@60m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_usdjpy_volatility_breakout_60m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Volatility Breakout 60m-barlb FN",
    id: "fn_usdjpy_volatility_breakout_60m-barlb",
    code: "FN_USDJPY_VOLATILITY_BREAKOUT_60M-BARLB",
    python_key: "usdjpy:volatility_breakout@60m-barlb",
    market: "usdjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_USDJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.07475183847738676,
    }),
    engine: EngineKind::Family(Params {
        family: Family::VolatilityBreakout { fraction: 0.5 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
