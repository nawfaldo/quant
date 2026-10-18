//! `fr40:half_life@1h-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_fr40_half_life_1h-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "FR40 Half Life 1h-morning FN",
    id: "fn_fr40_half_life_1h-morning",
    code: "FN_FR40_HALF_LIFE_1H-MORNING",
    python_key: "fr40:half_life@1h-morning",
    market: "fr40",
    book: Book::FundedNext,
    contract: Instrument::FN_FR40.window(720, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
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
        vol_target: 0.13820565817207367,
    }),
    engine: EngineKind::Family(Params {
        family: Family::HalfLife { period: 280, multiple: 1.0, threshold_z: 1.5 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
