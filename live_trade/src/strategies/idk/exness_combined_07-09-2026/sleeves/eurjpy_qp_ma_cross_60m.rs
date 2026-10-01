//! `eurjpy:qp_ma_cross@60m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! EMA(10) crossing EMA(50) on hourly bars, both sides; stop 1 ATR(14), out on the
//! opposite cross (`signal`).
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 60-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY QP MA Cross 60m",
    id: "eurjpy_qp_ma_cross_60m",
    code: "EURJPY_QP_MA_CROSS_60M",
    python_key: "eurjpy:qp_ma_cross@60m",
    market: "eurjpy",
    contract: Instrument::EURJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.072_875_637_007_267_53,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QpMaCross {
            fast: 10,
            slow: 50,
            both: true,
            stop_atr: 1.0,
        },
        direction: Direction::Follow,
        exit: Exit::Signal,
        last_entry_minute: 810,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
