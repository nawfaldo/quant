//! `usdjpy:failed_break@5m-barlb`, a `cfd_families` cell from the bar-lookback
//! wave (`EXNESS_BAR_LOOKBACKS=1`), seated 2026-10-12.
//!
//! Trades through yesterday's extreme and closes back inside; `follow` takes the
//! BREAK's side. 50-bar trend gate, calm-volatility filter, trailed 1.5 daily
//! ranges, on 5-minute bars. Indicator lengths are bar counts.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Failed Break 5m BarLB",
    id: "usdjpy_failed_break_5m-barlb",
    code: "USDJPY_FAILED_BREAK_5M-BARLB",
    python_key: "usdjpy:failed_break@5m-barlb",
    market: "usdjpy",
    book: Book::Exness,
    contract: Instrument::USDJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 5,
        per_session: 157,
        vol_target: 0.047_596_925_549_415_17,
    }),
    engine: EngineKind::Family(Params {
        family: Family::FailedBreak { buffer_atr: 0.0 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
