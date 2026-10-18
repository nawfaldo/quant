//! `ustec:gated_orb@30m-london_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0200-0500`.
//! Generated from `fixtures/parity_ustec_gated_orb_30m-london_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Gated ORB 30m-london_open FN",
    id: "fn_ustec_gated_orb_30m-london_open",
    code: "FN_USTEC_GATED_ORB_30M-LONDON_OPEN",
    python_key: "ustec:gated_orb@30m-london_open",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC.window(540, 720),
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.16210880005626954,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb { gate: Gate::Obv, polarity: Polarity::Oppose, gate_period: 70 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 600,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
