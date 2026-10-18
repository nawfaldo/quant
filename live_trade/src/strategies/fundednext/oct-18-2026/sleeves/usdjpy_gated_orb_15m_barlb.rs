//! `usdjpy:gated_orb@15m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_usdjpy_gated_orb_15m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Gated ORB 15m-barlb FN",
    id: "fn_usdjpy_gated_orb_15m-barlb",
    code: "FN_USDJPY_GATED_ORB_15M-BARLB",
    python_key: "usdjpy:gated_orb@15m-barlb",
    market: "usdjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_USDJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 53,
        vol_target: 0.06601336604311148,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb { gate: Gate::Supertrend, polarity: Polarity::Oppose, gate_period: 0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 660,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
