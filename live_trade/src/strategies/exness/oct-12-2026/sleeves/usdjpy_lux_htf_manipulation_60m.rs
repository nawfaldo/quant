//! `usdjpy:lux_htf_manipulation@60m`, a TikTok-creator cell (`cfd_tt_families`),
//! seated 2026-09-30.
//!
//! The manipulation candle on 4 x 60-minute candles, closing past the previous
//! one's HIGH after a candle the same way; stop 2 ATR(14), 3R.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, TtHtfClose, TtStop, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Lux HTF Manipulation 60m",
    id: "usdjpy_lux_htf_manipulation_60m",
    code: "USDJPY_LUX_HTF_MANIPULATION_60M",
    python_key: "usdjpy:lux_htf_manipulation@60m",
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
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.062_763_635_053_857_82,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxHtfManipulation {
            htf_m: 4,
            close_above: TtHtfClose::High,
            prior: true,
            ema: 0,
            stop: TtStop::Atr(2.0),
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
