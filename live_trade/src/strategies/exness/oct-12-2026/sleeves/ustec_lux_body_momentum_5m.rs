//! `ustec:lux_body_momentum@5m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! Two same-colour 5-minute bodies averaging 2.5x the 20-bar average body; stop at
//! the last 3-bar swing, target 1R.
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 5-minute candles with the timeframe below.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, TtStop, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Lux Body Momentum 5m",
    id: "ustec_lux_body_momentum_5m",
    code: "USTEC_LUX_BODY_MOMENTUM_5M",
    python_key: "ustec:lux_body_momentum@5m",
    market: "ustec",
    book: Book::Exness,
    contract: Instrument::USTEC,
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
        bar_minutes: 5,
        per_session: 79,
        vol_target: 0.180_187_400_401_049_89,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxBodyMomentum {
            mult: 2.5,
            stop: TtStop::Swing,
            widen: 1.0,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 900,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
