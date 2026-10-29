//! `usdchf:floor_pivot@15m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_usdchf_floor_pivot_15m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCHF Floor Pivot 15m-morning FN",
    id: "fn_usdchf_floor_pivot_15m-morning",
    code: "FN_USDCHF_FLOOR_PIVOT_15M-MORNING",
    python_key: "usdchf:floor_pivot@15m-morning",
    market: "usdchf",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCHF.window(720, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
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
        per_session: 25,
        vol_target: 0.0695731583188818,
    }),
    engine: EngineKind::Family(Params {
        family: Family::FloorPivot { buffer_atr: 0.0, level: 2 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1020,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
