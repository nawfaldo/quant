//! `gbpjpy:luxalgo_manipulation@120m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! A 2-hour candle sweeping the previous one's low and closing over its body,
//! after one candle the same way, on the right side of EMA(100); stop at the
//! sweep, trailed 0.25 daily ranges.
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 120-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, TtEngulf, TtStop, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY LuxAlgo Manipulation 120m",
    id: "gbpjpy_luxalgo_manipulation_120m",
    code: "GBPJPY_LUXALGO_MANIPULATION_120M",
    python_key: "gbpjpy:luxalgo_manipulation@120m",
    market: "gbpjpy",
    contract: Instrument::GBPJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 120,
        per_session: 7,
        vol_target: 0.088_059_658_589_140_53,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxalgoManipulation {
            prior_bars: 1,
            ema: 100,
            engulf: TtEngulf::Body,
            stop: TtStop::Sweep,
            fade: false,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(0.25),
        last_entry_minute: 690,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
