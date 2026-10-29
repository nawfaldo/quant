//! `gbpjpy:quantile_break@30m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_gbpjpy_quantile_break_30m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Quantile Break 30m-afternoon FN",
    id: "fn_gbpjpy_quantile_break_30m-afternoon",
    code: "FN_GBPJPY_QUANTILE_BREAK_30M-AFTERNOON",
    python_key: "gbpjpy:quantile_break@30m-afternoon",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY.window(1080, 1439),
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
        vol_target: 0.08678395390957036,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QuantileBreak { channel: 240, share: 0.9, buffer_atr: 0.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
