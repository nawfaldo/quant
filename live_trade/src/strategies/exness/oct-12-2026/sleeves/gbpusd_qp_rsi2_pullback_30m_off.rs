//! `gbpusd:qp_rsi2_pullback@30m-off`, a TikTok-creator cell (`cfd_tt_families`,
//! `tt_quantpad`) from the off-hours wave (`EXNESS_WINDOW=off`), seated
//! 2026-10-12. The book's first GBPUSD sleeve.
//!
//! RSI(2) under 10 above the 200-bar SMA -> long, over 90 below it -> short;
//! stop 2 ATR(14), out after one broker day of candles (`days_1`). Runs on the
//! broker day and takes nothing while the cash session (00:30-14:00 New York)
//! is open.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPUSD QP RSI2 Pullback 30m Off-hours",
    id: "gbpusd_qp_rsi2_pullback_30m-off",
    code: "GBPUSD_QP_RSI2_PULLBACK_30M-OFF",
    python_key: "gbpusd:qp_rsi2_pullback@30m-off",
    market: "gbpusd",
    book: Book::Exness,
    contract: Instrument::GBPUSD.broker_day(),
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((30, 840)),
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.077_573_077_724_562_64,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QpRsi2Pullback {
            lo: 10.0,
            stop_atr: 2.0,
        },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
