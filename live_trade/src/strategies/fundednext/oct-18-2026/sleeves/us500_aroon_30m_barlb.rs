//! `us500:aroon@30m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_us500_aroon_30m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Aroon 30m-barlb FN",
    id: "fn_us500_aroon_30m-barlb",
    code: "FN_US500_AROON_30M-BARLB",
    python_key: "us500:aroon@30m-barlb",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        bar_minutes: 30,
        per_session: 14,
        vol_target: 0.09788952071008077,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Aroon { period: 20, min_strength: 70.0 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 900,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
