//! `eurusd:rvol@30m-rth`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_eurusd_rvol_30m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURUSD RVOL 30m-rth T5",
    id: "t5_eurusd_rvol_30m-rth",
    code: "T5_EURUSD_RVOL_30M-RTH",
    python_key: "eurusd:rvol@30m-rth",
    market: "eurusd",
    book: Book::The5ers,
    contract: Instrument::T5_EURUSD,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 23,
        vol_target: 0.06346951275304781,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Rvol { rvol: 1.5, threshold_atr: 0.5 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 720,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
