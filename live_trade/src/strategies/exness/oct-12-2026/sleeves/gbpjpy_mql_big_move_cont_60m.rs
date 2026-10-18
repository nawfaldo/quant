//! `gbpjpy:mql_big_move_cont@60m`, a TikTok-creator cell (`cfd_tt_families`,
//! `tt_marlon_quantlab`), seated 2026-10-10.
//!
//! An hourly close-to-close move over 2.5 x the previous bar's ATR(14), traded
//! with the move; stop 1.5 ATR, out after six candles (`time_6`).

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY MQL Big Move Cont 60m",
    id: "gbpjpy_mql_big_move_cont_60m",
    code: "GBPJPY_MQL_BIG_MOVE_CONT_60M",
    python_key: "gbpjpy:mql_big_move_cont@60m",
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
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.087_921_244_021_233_3,
    }),
    engine: EngineKind::Family(Params {
        family: Family::MqlBigMoveCont {
            big: 2.5,
            vol: false,
            stop_atr: 1.5,
        },
        direction: Direction::Follow,
        exit: Exit::Bars(6),
        last_entry_minute: 690,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
