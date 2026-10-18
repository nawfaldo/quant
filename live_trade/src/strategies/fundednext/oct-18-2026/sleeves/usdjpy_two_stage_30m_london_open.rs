//! `usdjpy:two_stage@30m-london_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0200-0500`.
//! Generated from `fixtures/parity_usdjpy_two_stage_30m-london_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Coil};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Two Stage 30m-london_open FN",
    id: "fn_usdjpy_two_stage_30m-london_open",
    code: "FN_USDJPY_TWO_STAGE_30M-LONDON_OPEN",
    python_key: "usdjpy:two_stage@30m-london_open",
    market: "usdjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_USDJPY.window(540, 720),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.0900713004385092,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwoStage { coil: Coil::Squeeze, window: 3, compress: 35, width_period: 7 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 600,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
