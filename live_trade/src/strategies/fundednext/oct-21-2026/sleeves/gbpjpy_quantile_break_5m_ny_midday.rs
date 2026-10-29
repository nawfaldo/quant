//! `gbpjpy:quantile_break@5m-ny_midday`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1100-1400`.
//! Generated from `fixtures/parity_gbpjpy_quantile_break_5m-ny_midday.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Quantile Break 5m-ny_midday FN",
    id: "fn_gbpjpy_quantile_break_5m-ny_midday",
    code: "FN_GBPJPY_QUANTILE_BREAK_5M-NY_MIDDAY",
    python_key: "gbpjpy:quantile_break@5m-ny_midday",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY.window(1080, 1260),
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.08991783842565149,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QuantileBreak { channel: 148, share: 0.9, buffer_atr: 0.0 },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1140,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
