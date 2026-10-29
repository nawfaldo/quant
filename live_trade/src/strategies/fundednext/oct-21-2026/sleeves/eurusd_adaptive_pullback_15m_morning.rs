//! `eurusd:adaptive_pullback@15m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_eurusd_adaptive_pullback_15m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURUSD Adaptive Pullback 15m-morning FN",
    id: "fn_eurusd_adaptive_pullback_15m-morning",
    code: "FN_EURUSD_ADAPTIVE_PULLBACK_15M-MORNING",
    python_key: "eurusd:adaptive_pullback@15m-morning",
    market: "eurusd",
    book: Book::FundedNext,
    contract: Instrument::FN_EURUSD.window(720, 1080),
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
        per_session: 25,
        vol_target: 0.06221168629001715,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback { channel: 100, depth: 0.5, reference: 500, period: 1000, response: 0.5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
