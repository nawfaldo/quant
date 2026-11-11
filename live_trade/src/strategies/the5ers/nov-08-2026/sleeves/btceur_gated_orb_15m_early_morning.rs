//! `btceur:gated_orb@15m-early_morning`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w2300-0500`.
//! Generated from `fixtures/parity_btceur_gated_orb_15m-early_morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "BTCEUR Gated ORB 15m-early_morning T5",
    id: "t5_btceur_gated_orb_15m-early_morning",
    code: "T5_BTCEUR_GATED_ORB_15M-EARLY_MORNING",
    python_key: "btceur:gated_orb@15m-early_morning",
    market: "btceur",
    book: Book::The5ers,
    contract: Instrument::T5_BTCEUR.window(360, 720),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 5.0,
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
        per_session: 25,
        vol_target: 0.4450722669900305,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedOrb { gate: Gate::Rvol, polarity: Polarity::Oppose, gate_period: 0 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 600,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
