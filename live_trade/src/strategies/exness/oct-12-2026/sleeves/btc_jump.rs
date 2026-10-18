//! `btc:jump`, one `cfd_families` cell, seated 2026-09-30 in place of
//! `usdjpy:kendall`.
//!
//! The one-session move, followed, but only while the last 30 bars' variance
//! was at least a fifth JUMP (bipower variation against realised variance);
//! above the 20-day EMA, in the calm half of volatility, trailed 1.5 days.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::BTC;

/// 30-minute buckets in one session, the horizon the move is read over.
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "BTC Jump",
    id: "btc_jump",
    code: "BTC_JUMP",
    python_key: "btc:jump",
    market: "btc",
    book: Book::Exness,
    contract: CONTRACT,
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
    timeframe: None,
    engine: EngineKind::Family(Params {
        family: Family::Jump {
            period: 30,
            share: 0.2,
            jumpy: true,
            lookback: SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 900,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
