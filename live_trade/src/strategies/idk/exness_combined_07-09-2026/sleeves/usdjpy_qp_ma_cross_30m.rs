//! `usdjpy:qp_ma_cross@30m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! EMA(5) crossing EMA(50) on 30-minute bars, both sides; stop 2 ATR(14), held to
//! the session close (`days_1`).
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 30-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY QP MA Cross 30m",
    id: "usdjpy_qp_ma_cross_30m",
    code: "USDJPY_QP_MA_CROSS_30M",
    python_key: "usdjpy:qp_ma_cross@30m",
    market: "usdjpy",
    contract: Instrument::USDJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 27,
        vol_target: 0.063_042_706_205_010_69,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QpMaCross {
            fast: 5,
            slow: 50,
            both: true,
            stop_atr: 2.0,
        },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 720,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
