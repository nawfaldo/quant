//! `xauusd:rvol@5m-tokyo_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2000-2300`.
//! Generated from `fixtures/parity_xauusd_rvol_5m-tokyo_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD RVOL 5m-tokyo_open FN",
    id: "fn_xauusd_rvol_5m-tokyo_open",
    code: "FN_XAUUSD_RVOL_5M-TOKYO_OPEN",
    python_key: "xauusd:rvol@5m-tokyo_open",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(180, 360),
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.13890512327814805,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Rvol { rvol: 1.5, threshold_atr: 1.0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 300,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
