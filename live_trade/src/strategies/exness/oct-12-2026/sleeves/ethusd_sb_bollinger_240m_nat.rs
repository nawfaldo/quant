//! `ethusd:sb_bollinger@240m-nat`, a native `cfd_families` cell of the
//! swing-bar wave (`sb_bollinger`, `hold="multiday"`), seated 2026-10-12.
//!
//! The close through the 50-bar Bollinger band (1.5 sigma) on 4-hour candles,
//! with the move; 50-day trend gate, calm volatility, stop 0.5 daily ranges.
//! HELD FOR DAYS: never flattened at the close, out after five sessions of
//! candles (`days_5`) or on its stop, paying the broker's rollover.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD SB Bollinger 240m",
    id: "ethusd_sb_bollinger_240m-nat",
    code: "ETHUSD_SB_BOLLINGER_240M-NAT",
    python_key: "ethusd:sb_bollinger@240m-nat",
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
    multiday: true,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 240,
        per_session: 2,
        vol_target: 0.721_530_020_111_594_4,
    }),
    engine: EngineKind::Family(Params {
        family: Family::FbBollinger {
            period: 50,
            k: 1.5,
            reenter: false,
        },
        direction: Direction::Follow,
        exit: Exit::Days(5),
        last_entry_minute: 1440,
        stop_day: 0.5,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
