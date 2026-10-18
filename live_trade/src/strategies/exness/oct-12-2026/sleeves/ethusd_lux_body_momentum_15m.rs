//! `ethusd:lux_body_momentum@15m`, a TikTok-creator cell (`cfd_tt_families`),
//! seated 2026-09-30.
//!
//! Two same-colour 15-minute bodies averaging 1.5x the 20-bar average body;
//! stop 1 ATR(14) WIDENED 4x, target 1R of the widened stop.
//!
//! SEALED UNDER THE WIDE-STOP PROTOCOL (`EXNESS_TT_STOP_WIDEN=1,2,4`,
//! `EXNESS_IS_FIRST_YEAR=ethusd:2021`), so `widen` is 4.0 and the vol target is
//! the one its own in-sample window gave, not the 15-minute default.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, TtStop, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Lux Body Momentum 15m",
    id: "ethusd_lux_body_momentum_15m",
    code: "ETHUSD_LUX_BODY_MOMENTUM_15M",
    python_key: "ethusd:lux_body_momentum@15m",
    market: "ethusd",
    book: Book::Exness,
    contract: Instrument::ETHUSD,
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
        per_session: 27,
        vol_target: 0.624_355_716_388_901_2,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxBodyMomentum {
            mult: 1.5,
            stop: TtStop::Atr(1.0),
            widen: 4.0,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 840,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
