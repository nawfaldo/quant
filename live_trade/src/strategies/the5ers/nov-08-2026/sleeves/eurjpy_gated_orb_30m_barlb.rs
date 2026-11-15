//! `eurjpy:gated_orb@30m-barlb`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852).
//! Generated from `fixtures/parity_eurjpy_gated_orb_30m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Gated ORB 30m-barlb T5",
    id: "t5_eurjpy_gated_orb_30m-barlb",
    code: "T5_EURJPY_GATED_ORB_30M-BARLB",
    python_key: "eurjpy:gated_orb@30m-barlb",
    market: "eurjpy",
    book: Book::The5ers,
    contract: Instrument::T5_EURJPY,
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
        bar_minutes: 30,
        per_session: 28,
        vol_target: 0.06388021526979704,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb { gate: Gate::Adx, polarity: Polarity::Oppose, gate_period: 14 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 810,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
