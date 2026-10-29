//! `ustec:fracdiff@5m-london`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-0800`.
//! Generated from `fixtures/parity_ustec_fracdiff_5m-london.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Fracdiff 5m-london FN",
    id: "fn_ustec_fracdiff_5m-london",
    code: "FN_USTEC_FRACDIFF_5M-LONDON",
    python_key: "ustec:fracdiff@5m-london",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC.window(720, 900),
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
        per_session: 37,
        vol_target: 0.162244618204737,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Fracdiff { order: 0.3, threshold_z: 2.5, window: 740 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 780,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
