//! `eurjpy:rvol@60m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90).
//! Generated from `fixtures/parity_eurjpy_rvol_60m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY RVOL 60m-barlb FN",
    id: "fn_eurjpy_rvol_60m-barlb",
    code: "FN_EURJPY_RVOL_60M-BARLB",
    python_key: "eurjpy:rvol@60m-barlb",
    market: "eurjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_EURJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
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
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.06706668030154755,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Rvol { rvol: 1.5, threshold_atr: 0.5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 750,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
