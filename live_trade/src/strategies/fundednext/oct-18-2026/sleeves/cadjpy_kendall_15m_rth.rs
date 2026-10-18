//! `cadjpy:kendall@15m-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_cadjpy_kendall_15m-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CADJPY Kendall 15m-rth FN",
    id: "fn_cadjpy_kendall_15m-rth",
    code: "FN_CADJPY_KENDALL_15M-RTH",
    python_key: "cadjpy:kendall@15m-rth",
    market: "cadjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CADJPY,
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
        per_session: 47,
        vol_target: 0.0898935759550134,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Kendall { period: 94, threshold: 3.5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 690,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
