//! `xauusd:regime_breakout@5m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_xauusd_regime_breakout_5m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD Regime Breakout 5m-ny_open FN",
    id: "fn_xauusd_regime_breakout_5m-ny_open",
    code: "FN_XAUUSD_REGIME_BREAKOUT_5M-NY_OPEN",
    python_key: "xauusd:regime_breakout@5m-ny_open",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(900, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 5.0,
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.13138201541708433,
    }),
    engine: EngineKind::Family(Params {
        family: Family::RegimeBreakout { fraction: 0.3, response: 0.0 },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
