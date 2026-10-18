//! `usoil:xma_cross`, one `cfd_families` cell, seated 2026-09-30.
//!
//! A 24/60 Hull-average cross, taken in its direction, in the calm half of
//! volatility; 2R target.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::MaKind;
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USOIL XMA Cross",
    id: "usoil_xma_cross",
    code: "USOIL_XMA_CROSS",
    python_key: "usoil:xma_cross",
    market: "usoil",
    book: Book::Exness,
    contract: Instrument::USOIL,
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
            fast: 24,
            slow: 60,
            kind: MaKind::Hma,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 810,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
