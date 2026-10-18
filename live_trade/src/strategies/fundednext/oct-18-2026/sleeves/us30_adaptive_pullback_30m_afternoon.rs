//! `us30:adaptive_pullback@30m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_us30_adaptive_pullback_30m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Adaptive Pullback 30m-afternoon FN",
    id: "fn_us30_adaptive_pullback_30m-afternoon",
    code: "FN_US30_ADAPTIVE_PULLBACK_30M-AFTERNOON",
    python_key: "us30:adaptive_pullback@30m-afternoon",
    market: "us30",
    book: Book::FundedNext,
    contract: Instrument::FN_US30.window(1080, 1439),
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
        bar_minutes: 30,
        per_session: 12,
        vol_target: 0.11642500364593822,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback { channel: 48, depth: 1.0, reference: 600, period: 480, response: 1.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1379,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
