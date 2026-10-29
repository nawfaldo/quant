//! `usdcad:adaptive_pullback@1h-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_usdcad_adaptive_pullback_1h-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCAD Adaptive Pullback 1h-afternoon FN",
    id: "fn_usdcad_adaptive_pullback_1h-afternoon",
    code: "FN_USDCAD_ADAPTIVE_PULLBACK_1H-AFTERNOON",
    python_key: "usdcad:adaptive_pullback@1h-afternoon",
    market: "usdcad",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCAD.window(1080, 1439),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 5.0,
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
        bar_minutes: 60,
        per_session: 6,
        vol_target: 0.04728260571390687,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback { channel: 120, depth: 1.0, reference: 120, period: 64, response: 1.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
