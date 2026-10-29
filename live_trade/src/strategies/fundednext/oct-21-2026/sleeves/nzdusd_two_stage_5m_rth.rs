//! `nzdusd:two_stage@5m-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90).
//! Generated from `fixtures/parity_nzdusd_two_stage_5m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Coil};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Two Stage 5m-rth FN",
    id: "fn_nzdusd_two_stage_5m-rth",
    code: "FN_NZDUSD_TWO_STAGE_5M-RTH",
    python_key: "nzdusd:two_stage@5m-rth",
    market: "nzdusd",
    book: Book::FundedNext,
    contract: Instrument::FN_NZDUSD,
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
        vol_target: 0.0875329073449309,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwoStage { coil: Coil::Squeeze, window: 3, compress: 785, width_period: 157 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 840,
        stop_day: 0.7,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
