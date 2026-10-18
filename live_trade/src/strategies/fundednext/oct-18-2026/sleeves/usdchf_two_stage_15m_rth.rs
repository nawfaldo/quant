//! `usdchf:two_stage@15m-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_usdchf_two_stage_15m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Coil};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCHF Two Stage 15m-rth FN",
    id: "fn_usdchf_two_stage_15m-rth",
    code: "FN_USDCHF_TWO_STAGE_15M-RTH",
    python_key: "usdchf:two_stage@15m-rth",
    market: "usdchf",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCHF,
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
        bar_minutes: 15,
        per_session: 49,
        vol_target: 0.0709522448728707,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwoStage { coil: Coil::Nr, window: 3, compress: 245, width_period: 49 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 780,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
