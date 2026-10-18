//! `xauusd:quantile_break@5m-evening`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1700-2000`.
//! Generated from `fixtures/parity_xauusd_quantile_break_5m-evening.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD Quantile Break 5m-evening FN",
    id: "fn_xauusd_quantile_break_5m-evening",
    code: "FN_XAUUSD_QUANTILE_BREAK_5M-EVENING",
    python_key: "xauusd:quantile_break@5m-evening",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(0, 180),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        vol_target: 0.18989265653667717,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QuantileBreak { channel: 740, share: 0.98, buffer_atr: 0.0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 120,
        stop_day: 0.4,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
