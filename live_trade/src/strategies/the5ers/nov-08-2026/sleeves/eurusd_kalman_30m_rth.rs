//! `eurusd:kalman@30m-rth`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_eurusd_kalman_30m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, KalmanMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURUSD Kalman 30m-rth T5",
    id: "t5_eurusd_kalman_30m-rth",
    code: "T5_EURUSD_KALMAN_30M-RTH",
    python_key: "eurusd:kalman@30m-rth",
    market: "eurusd",
    book: Book::The5ers,
    contract: Instrument::T5_EURUSD,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        family: Family::Kalman { mode: KalmanMode::Residual, threshold: 1.5 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 720,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
