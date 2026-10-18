//! `eurjpy:swing_break@30m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_eurjpy_swing_break_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Swing Break 30m-ny_open FN",
    id: "fn_eurjpy_swing_break_30m-ny_open",
    code: "FN_EURJPY_SWING_BREAK_30M-NY_OPEN",
    python_key: "eurjpy:swing_break@30m-ny_open",
    market: "eurjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_EURJPY.window(900, 1080),
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.07581491205918268,
    }),
    engine: EngineKind::Family(Params {
        family: Family::SwingBreak { wing: 2 },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 960,
        stop_day: 0.7,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
