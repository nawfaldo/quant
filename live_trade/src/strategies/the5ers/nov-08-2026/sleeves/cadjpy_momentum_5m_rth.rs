//! `cadjpy:momentum@5m-rth`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_cadjpy_momentum_5m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CADJPY Momentum 5m-rth T5",
    id: "t5_cadjpy_momentum_5m-rth",
    code: "T5_CADJPY_MOMENTUM_5M-RTH",
    python_key: "cadjpy:momentum@5m-rth",
    market: "cadjpy",
    book: Book::The5ers,
    contract: Instrument::T5_CADJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
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
        bar_minutes: 5,
        per_session: 157,
        vol_target: 0.09120574346875829,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Momentum { signal_minute: 270, lookback: 157, threshold_atr: 1.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 900, // none in Python: the session close
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
