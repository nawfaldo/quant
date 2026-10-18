//! `xauusd:gated_swing@5m-london_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0200-0500`.
//! Generated from `fixtures/parity_xauusd_gated_swing_5m-london_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD Gated Swing 5m-london_open FN",
    id: "fn_xauusd_gated_swing_5m-london_open",
    code: "FN_XAUUSD_GATED_SWING_5M-LONDON_OPEN",
    python_key: "xauusd:gated_swing@5m-london_open",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(540, 720),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.14183000734062168,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedSwing { donchian: Some(740), fast: 0, slow: 0, gate: Gate::Rsi, polarity: Polarity::Confirm, gate_period: 518 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 720, // none in Python: the session close
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
