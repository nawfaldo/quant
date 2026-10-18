//! `nzdusd:cusum@1h-rth`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_nzdusd_cusum_1h-rth.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "NZDUSD Cusum 1h-rth FN",
    id: "fn_nzdusd_cusum_1h-rth",
    code: "FN_NZDUSD_CUSUM_1H-RTH",
    python_key: "nzdusd:cusum@1h-rth",
    market: "nzdusd",
    book: Book::FundedNext,
    contract: Instrument::FN_NZDUSD,
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
        bar_minutes: 60,
        per_session: 13,
        vol_target: 0.08961234462406567,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Cusum { multiple: 1.0 },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 750,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
