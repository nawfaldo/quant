//! `uk100:xma_cross`, one `cfd_families` cell, seated 2026-09-30.
//!
//! An 18/90 WMA cross, FADED, in the calm half of volatility; 2R target.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::MaKind;
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "UK100 XMA Cross",
    id: "uk100_xma_cross",
    code: "UK100_XMA_CROSS",
    python_key: "uk100:xma_cross",
    market: "uk100",
    book: Book::Exness,
    contract: Instrument::UK100,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: None,
    engine: EngineKind::Family(Params {
        family: Family::XmaCross {
            fast: 18,
            slow: 90,
            kind: MaKind::Wma,
        },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 570,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
