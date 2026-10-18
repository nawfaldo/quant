//! `de40:lux_sweep_reclaim@60m`, a TikTok-creator cell (`cfd_tt_families`),
//! seated 2026-09-30.
//!
//! An hourly candle trades through the last 10-bar swing and closes back
//! through it on the same bar; stop beyond the sweep, target the opposite
//! swing (`level`).

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "DE40 Lux Sweep Reclaim 60m",
    id: "de40_lux_sweep_reclaim_60m",
    code: "DE40_LUX_SWEEP_RECLAIM_60M",
    python_key: "de40:lux_sweep_reclaim@60m",
    market: "de40",
    book: Book::Exness,
    contract: Instrument::DE40,
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
        per_session: 9,
        vol_target: 0.142_513_482_238_327_72,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxSweepReclaim {
            swing: 10,
            confirm_bars: 1,
        },
        direction: Direction::Follow,
        exit: Exit::Level,
        last_entry_minute: 570,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
