//! `us2000:adaptive_pullback@30m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_us2000_adaptive_pullback_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US2000 Adaptive Pullback 30m-morning FN",
    id: "fn_us2000_adaptive_pullback_30m-morning",
    code: "FN_US2000_ADAPTIVE_PULLBACK_30M-MORNING",
    python_key: "us2000:adaptive_pullback@30m-morning",
    market: "us2000",
    book: Book::FundedNext,
    contract: Instrument::FN_US2000.window(720, 1080),
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
        per_session: 13,
        vol_target: 0.20898965431156072,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback { channel: 52, depth: 0.5, reference: 650, period: 520, response: 0.5 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 960,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
