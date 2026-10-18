//! `ustec:twp_asia_london_ny@15m`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_ustec_twp_asia_london_ny_15m.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC TWP Asia London NY 15m FN",
    id: "fn_ustec_twp_asia_london_ny_15m",
    code: "FN_USTEC_TWP_ASIA_LONDON_NY_15M",
    python_key: "ustec:twp_asia_london_ny@15m",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        bar_minutes: 15,
        per_session: 27,
        vol_target: 0.16230042749126433,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwpAsiaLondonNy { tight: 1.0, win: 120 },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 840,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
