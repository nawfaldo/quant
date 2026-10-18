//! `us2000:quantile_break@15m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_us2000_quantile_break_15m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US2000 Quantile Break 15m-afternoon FN",
    id: "fn_us2000_quantile_break_15m-afternoon",
    code: "FN_US2000_QUANTILE_BREAK_15M-AFTERNOON",
    python_key: "us2000:quantile_break@15m-afternoon",
    market: "us2000",
    book: Book::FundedNext,
    contract: Instrument::FN_US2000.window(1080, 1439),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        per_session: 24,
        vol_target: 0.19522604394733759,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QuantileBreak { channel: 480, share: 0.9, buffer_atr: 0.25 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
