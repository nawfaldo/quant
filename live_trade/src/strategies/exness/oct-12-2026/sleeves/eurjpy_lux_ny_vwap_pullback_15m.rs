//! `eurjpy:lux_ny_vwap_pullback@15m`, a TikTok-creator cell (`cfd_tt_families`),
//! seated 2026-09-30.
//!
//! After the 30-minute opening range, inside the next 90 minutes: a new extreme
//! of the day past the range, a retrace to the session VWAP and a candle
//! closing back away from it. Stop 1.5 ATR(14), 2R.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Lux NY VWAP Pullback 15m",
    id: "eurjpy_lux_ny_vwap_pullback_15m",
    code: "EURJPY_LUX_NY_VWAP_PULLBACK_15M",
    python_key: "eurjpy:lux_ny_vwap_pullback@15m",
    market: "eurjpy",
    book: Book::Exness,
    contract: Instrument::EURJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 55,
        vol_target: 0.072_722_933_682_009_63,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxNyVwapPullback {
            or_min: 30,
            window: 90,
            stop_mult: 1.5,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 750,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
