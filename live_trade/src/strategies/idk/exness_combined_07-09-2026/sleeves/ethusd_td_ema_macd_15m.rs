//! `ethusd:td_ema_macd@15m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! Close over EMA(200), MACD above zero and crossed its signal within 3 bars, on
//! 15-minute bars; stop 1.5 ATR(14), 3R. Sealed under the wide-stop protocol
//! (`stop_widen` 1.0, in-sample from 2021), so its `vol_target` is the 2021 one.
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 15-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD TD EMA MACD 15m",
    id: "ethusd_td_ema_macd_15m",
    code: "ETHUSD_TD_EMA_MACD_15M",
    python_key: "ethusd:td_ema_macd@15m",
    market: "ethusd",
    contract: Instrument::ETHUSD,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 27,
        vol_target: 0.624_355_716_388_901_2,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TdEmaMacd {
            within: 3,
            both: true,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 840,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
