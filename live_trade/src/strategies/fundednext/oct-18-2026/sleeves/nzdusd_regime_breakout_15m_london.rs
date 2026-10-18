//! `nzdusd:regime_breakout@15m-london`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-0800`.
//! Generated from `fixtures/parity_nzdusd_regime_breakout_15m-london.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Regime Breakout 15m-london FN",
    id: "fn_nzdusd_regime_breakout_15m-london",
    code: "FN_NZDUSD_REGIME_BREAKOUT_15M-LONDON",
    python_key: "nzdusd:regime_breakout@15m-london",
    market: "nzdusd",
    book: Book::FundedNext,
    contract: Instrument::FN_NZDUSD.window(720, 900),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: true,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 13,
        vol_target: 0.08604264755056655,
    }),
    engine: EngineKind::Family(Params {
        family: Family::RegimeBreakout { fraction: 0.5, response: 0.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 840,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
