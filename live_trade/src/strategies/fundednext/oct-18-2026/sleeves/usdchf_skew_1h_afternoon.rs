//! `usdchf:skew@1h-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_usdchf_skew_1h-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCHF Skew 1h-afternoon FN",
    id: "fn_usdchf_skew_1h-afternoon",
    code: "FN_USDCHF_SKEW_1H-AFTERNOON",
    python_key: "usdchf:skew@1h-afternoon",
    market: "usdchf",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCHF.window(1080, 1439),
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
        bar_minutes: 60,
        per_session: 6,
        vol_target: 0.07009426634823543,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Skew { period: 120, positive: true, threshold: 0.8, long: false },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1439, // none in Python: the session close
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
