//! `xauusd:obv_divergence@15m-evening`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1700-2000`.
//! Generated from `fixtures/parity_xauusd_obv_divergence_15m-evening.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD OBV Divergence 15m-evening FN",
    id: "fn_xauusd_obv_divergence_15m-evening",
    code: "FN_XAUUSD_OBV_DIVERGENCE_15M-EVENING",
    python_key: "xauusd:obv_divergence@15m-evening",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(0, 180),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: true,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 13,
        vol_target: 0.18605156388139424,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ObvDivergence { channel: 52 },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 60,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
