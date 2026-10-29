//! `us500:two_stage@15m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_us500_two_stage_15m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Coil};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Two Stage 15m-ny_open FN",
    id: "fn_us500_two_stage_15m-ny_open",
    code: "FN_US500_TWO_STAGE_15M-NY_OPEN",
    python_key: "us500:two_stage@15m-ny_open",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(900, 1080),
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
        vol_target: 0.11888674880586478,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwoStage { coil: Coil::Squeeze, window: 6, compress: 65, width_period: 13 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1020,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
