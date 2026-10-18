//! `eurjpy:dt_rsi_dip_vol@15m`, a TikTok-creator cell (`cfd_tt_families`,
//! `tt_deltatrendtrading`), seated 2026-10-12.
//!
//! RSI(2) under 30 with the close under its 20-bar SMA, only while 20-bar
//! realised volatility is above its 100-bar median -> long; stop 2 ATR(14),
//! out after one session (`days_1`).

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY DT RSI Dip Vol 15m",
    id: "eurjpy_dt_rsi_dip_vol_15m",
    code: "EURJPY_DT_RSI_DIP_VOL_15M",
    python_key: "eurjpy:dt_rsi_dip_vol@15m",
    market: "eurjpy",
    book: Book::Exness,
    contract: Instrument::EURJPY,
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
        vol_target: 0.072_722_933_682_009_63,
    }),
    engine: EngineKind::Family(Params {
        family: Family::DtRsiDipVol {
            n: 2,
            lo: 30.0,
            vol_high: true,
            stop_atr: 2.0,
        },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 810,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
