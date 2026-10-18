//! `usdjpy:yf_alphatrend@30m`, a TikTok-creator cell (`cfd_tt_families`,
//! `tt_yoforex3`), seated 2026-10-11.
//!
//! AlphaTrend (SMA(14) true range x 1.5 off the bar, ratcheting while RSI(14)
//! >= 50) crossing its own 2-bar lag; out when it crosses back (`signal`).

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY YF AlphaTrend 30m",
    id: "usdjpy_yf_alphatrend_30m",
    code: "USDJPY_YF_ALPHATREND_30M",
    python_key: "usdjpy:yf_alphatrend@30m",
    market: "usdjpy",
    book: Book::Exness,
    contract: Instrument::USDJPY,
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
        bar_minutes: 30,
        per_session: 27,
        vol_target: 0.063_042_706_205_010_69,
    }),
    engine: EngineKind::Family(Params {
        family: Family::YfAlphatrend {
            coeff: 1.5,
            use_rsi: true,
        },
        direction: Direction::Follow,
        exit: Exit::Signal,
        last_entry_minute: 720,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
