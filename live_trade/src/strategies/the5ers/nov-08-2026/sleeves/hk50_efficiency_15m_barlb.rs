//! `hk50:efficiency@15m-barlb`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_hk50_efficiency_15m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, ErRegime};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "HK50 Efficiency 15m-barlb T5",
    id: "t5_hk50_efficiency_15m-barlb",
    code: "T5_HK50_EFFICIENCY_15M-BARLB",
    python_key: "hk50:efficiency@15m-barlb",
    market: "hk50",
    book: Book::The5ers,
    contract: Instrument::T5_HK50,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 29,
        vol_target: 0.18837152569849425,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Efficiency { period: 10, min_er: 0.5, regime: ErRegime::Noisy },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 540,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
