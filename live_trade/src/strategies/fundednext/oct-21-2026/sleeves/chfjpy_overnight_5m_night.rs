//! `chfjpy:overnight@5m-night`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1700-2300`.
//! Generated from `fixtures/parity_chfjpy_overnight_5m-night.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Overnight 5m-night FN",
    id: "fn_chfjpy_overnight_5m-night",
    code: "FN_CHFJPY_OVERNIGHT_5M-NIGHT",
    python_key: "chfjpy:overnight@5m-night",
    market: "chfjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CHFJPY.window(0, 360),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        per_session: 73,
        vol_target: 0.08524175711445207,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Overnight { buffer_atr: 0.0 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 240,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
