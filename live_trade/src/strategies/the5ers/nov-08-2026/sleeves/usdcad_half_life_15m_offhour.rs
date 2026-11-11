//! `usdcad:half_life@15m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_usdcad_half_life_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCAD Half Life 15m-offhour T5",
    id: "t5_usdcad_half_life_15m-offhour",
    code: "T5_USDCAD_HALF_LIFE_15M-OFFHOUR",
    python_key: "usdcad:half_life@15m-offhour",
    market: "usdcad",
    book: Book::The5ers,
    contract: Instrument::T5_USDCAD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((150, 870)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.04867314036926646,
    }),
    engine: EngineKind::Family(Params {
        family: Family::HalfLife { period: 920, multiple: 1.0, threshold_z: 1.5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
