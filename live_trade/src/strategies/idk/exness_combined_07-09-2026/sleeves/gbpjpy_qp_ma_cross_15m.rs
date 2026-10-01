//! `gbpjpy:qp_ma_cross@15m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! EMA(10) crossing EMA(100) on 15-minute bars, both sides; stop 2 ATR(14), out
//! on the opposite cross (`signal`).
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 15-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY QP MA Cross 15m",
    id: "gbpjpy_qp_ma_cross_15m",
    code: "GBPJPY_QP_MA_CROSS_15M",
    python_key: "gbpjpy:qp_ma_cross@15m",
    market: "gbpjpy",
    contract: Instrument::GBPJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 55,
        vol_target: 0.089_074_103_364_924_44,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QpMaCross {
            fast: 10,
            slow: 100,
            both: true,
            stop_atr: 2.0,
        },
        direction: Direction::Follow,
        exit: Exit::Signal,
        last_entry_minute: 690,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
