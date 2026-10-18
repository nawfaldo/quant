//! `eurjpy:cusum@1h-early_morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2300-0500`.
//! Generated from `fixtures/parity_eurjpy_cusum_1h-early_morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Cusum 1h-early_morning FN",
    id: "fn_eurjpy_cusum_1h-early_morning",
    code: "FN_EURJPY_CUSUM_1H-EARLY_MORNING",
    python_key: "eurjpy:cusum@1h-early_morning",
    market: "eurjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_EURJPY.window(360, 720),
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
        vol_target: 0.07097494885958938,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Cusum { multiple: 2.0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 600,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
