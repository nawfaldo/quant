//! `gbpjpy:lux_htf_manipulation@15m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! The manipulation candle on 8 x 15-minute candles, closing past the previous
//! one's close after a candle the same way; stop past the candle, 3R.
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 15-minute candles with the timeframe below.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, TtHtfClose, TtStop, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Lux HTF Manipulation 15m",
    id: "gbpjpy_lux_htf_manipulation_15m",
    code: "GBPJPY_LUX_HTF_MANIPULATION_15M",
    python_key: "gbpjpy:lux_htf_manipulation@15m",
    market: "gbpjpy",
    book: Book::Exness,
    contract: Instrument::GBPJPY,
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
        vol_target: 0.089_074_103_364_924_44,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxHtfManipulation {
            htf_m: 8,
            close_above: TtHtfClose::Close,
            prior: true,
            ema: 0,
            stop: TtStop::Candle,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 690,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
