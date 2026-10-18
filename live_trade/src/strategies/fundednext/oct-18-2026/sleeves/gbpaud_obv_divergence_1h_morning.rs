//! `gbpaud:obv_divergence@1h-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_gbpaud_obv_divergence_1h-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPAUD OBV Divergence 1h-morning FN",
    id: "fn_gbpaud_obv_divergence_1h-morning",
    code: "FN_GBPAUD_OBV_DIVERGENCE_1H-MORNING",
    python_key: "gbpaud:obv_divergence@1h-morning",
    market: "gbpaud",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPAUD.window(720, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
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
        bar_minutes: 60,
        per_session: 7,
        vol_target: 0.06079924985412422,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ObvDivergence { channel: 28 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
