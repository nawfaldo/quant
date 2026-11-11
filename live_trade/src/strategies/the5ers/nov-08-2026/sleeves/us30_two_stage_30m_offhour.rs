//! `us30:two_stage@30m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_us30_two_stage_30m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Coil};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Two Stage 30m-offhour T5",
    id: "t5_us30_two_stage_30m-offhour",
    code: "T5_US30_TWO_STAGE_30M-OFFHOUR",
    python_key: "us30:two_stage@30m-offhour",
    market: "us30",
    book: Book::The5ers,
    contract: Instrument::T5_US30.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((570, 960)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.11555723056364633,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TwoStage { coil: Coil::Nr, window: 6, compress: 230, width_period: 46 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
