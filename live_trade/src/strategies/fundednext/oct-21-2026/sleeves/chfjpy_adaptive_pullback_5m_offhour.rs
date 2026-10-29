//! `chfjpy:adaptive_pullback@5m-offhour`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_chfjpy_adaptive_pullback_5m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Adaptive Pullback 5m-offhour FN",
    id: "fn_chfjpy_adaptive_pullback_5m-offhour",
    code: "FN_CHFJPY_ADAPTIVE_PULLBACK_5M-OFFHOUR",
    python_key: "chfjpy:adaptive_pullback@5m-offhour",
    market: "chfjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CHFJPY.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((60, 840)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 5,
        per_session: 276,
        vol_target: 0.07614340794782835,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback { channel: 1104, depth: 0.5, reference: 5520, period: 11040, response: 1.0 },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1379,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
