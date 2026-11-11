//! `audjpy:gated_orb@15m-ny_open`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_audjpy_gated_orb_15m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDJPY Gated ORB 15m-ny_open T5",
    id: "t5_audjpy_gated_orb_15m-ny_open",
    code: "T5_AUDJPY_GATED_ORB_15M-NY_OPEN",
    python_key: "audjpy:gated_orb@15m-ny_open",
    market: "audjpy",
    book: Book::The5ers,
    contract: Instrument::T5_AUDJPY.window(900, 1080),
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
        bar_minutes: 15,
        per_session: 13,
        vol_target: 0.09503201098501386,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb { gate: Gate::Macd, polarity: Polarity::Oppose, gate_period: 13 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1020,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
