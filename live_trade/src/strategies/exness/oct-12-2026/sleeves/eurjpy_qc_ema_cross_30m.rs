//! `eurjpy:qc_ema_cross@30m`, a TikTok-creator cell (`cfd_tt_families`,
//! `tt_quantcrawler`), seated 2026-10-12.
//!
//! EMA(5) crossing EMA(21), both sides; stop 1 ATR(14), out after one session
//! (`days_1`).

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY QC EMA Cross 30m",
    id: "eurjpy_qc_ema_cross_30m",
    code: "EURJPY_QC_EMA_CROSS_30M",
    python_key: "eurjpy:qc_ema_cross@30m",
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
        bar_minutes: 30,
        per_session: 28,
        vol_target: 0.073_098_303_217_424_5,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QcEmaCross {
            fast: 5,
            both: true,
            stop_atr: 1.0,
        },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 750,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
