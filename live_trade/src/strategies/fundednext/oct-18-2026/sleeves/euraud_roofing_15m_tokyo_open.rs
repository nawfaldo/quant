//! `euraud:roofing@15m-tokyo_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2000-2300`.
//! Generated from `fixtures/parity_euraud_roofing_15m-tokyo_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Roofing 15m-tokyo_open FN",
    id: "fn_euraud_roofing_15m-tokyo_open",
    code: "FN_EURAUD_ROOFING_15M-TOKYO_OPEN",
    python_key: "euraud:roofing@15m-tokyo_open",
    market: "euraud",
    book: Book::FundedNext,
    contract: Instrument::FN_EURAUD.window(180, 360),
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
        bar_minutes: 15,
        per_session: 13,
        vol_target: 0.06027867287554344,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Roofing { high_period: 26, low_period: 6, amplitude_atr: 0.25 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 300,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
