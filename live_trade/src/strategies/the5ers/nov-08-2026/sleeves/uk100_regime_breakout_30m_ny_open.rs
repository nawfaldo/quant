//! `uk100:regime_breakout@30m-ny_open`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_uk100_regime_breakout_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "UK100 Regime Breakout 30m-ny_open T5",
    id: "t5_uk100_regime_breakout_30m-ny_open",
    code: "T5_UK100_REGIME_BREAKOUT_30M-NY_OPEN",
    python_key: "uk100:regime_breakout@30m-ny_open",
    market: "uk100",
    book: Book::The5ers,
    contract: Instrument::T5_UK100.window(900, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.09704271023780585,
    }),
    engine: EngineKind::Family(Params {
        family: Family::RegimeBreakout { fraction: 0.3, response: 0.5 },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1020,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
