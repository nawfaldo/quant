//! `usdjpy:kalman@30m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_usdjpy_kalman_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, KalmanMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Kalman 30m-ny_open FN",
    id: "fn_usdjpy_kalman_30m-ny_open",
    code: "FN_USDJPY_KALMAN_30M-NY_OPEN",
    python_key: "usdjpy:kalman@30m-ny_open",
    market: "usdjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_USDJPY.window(900, 1080),
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.0941038643850521,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Kalman { mode: KalmanMode::Residual, threshold: 1.5 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 1020,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
