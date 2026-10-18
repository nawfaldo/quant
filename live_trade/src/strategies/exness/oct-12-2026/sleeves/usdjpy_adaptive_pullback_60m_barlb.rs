//! `usdjpy:adaptive_pullback@60m-barlb`, a `cfd_families` cell from the
//! bar-lookback wave (`EXNESS_BAR_LOOKBACKS=1`), seated 2026-10-12.
//!
//! A retrace from the 20-bar extreme against the 50-bar EMA, with the depth
//! scaled by the Hurst exponent over 128 closes; FADED, trailed 1.5 daily
//! ranges, on hourly bars. Indicator lengths are bar counts.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Adaptive Pullback 60m BarLB",
    id: "usdjpy_adaptive_pullback_60m-barlb",
    code: "USDJPY_ADAPTIVE_PULLBACK_60M-BARLB",
    python_key: "usdjpy:adaptive_pullback@60m-barlb",
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
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.056_551_042_534_416_65,
    }),
    engine: EngineKind::Family(Params {
        family: Family::AdaptivePullback {
            channel: 20,
            depth: 1.0,
            reference: 50,
            period: 128,
            response: 1.0,
        },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
