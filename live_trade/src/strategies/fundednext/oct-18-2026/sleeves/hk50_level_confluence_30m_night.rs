//! `hk50:level_confluence@30m-night`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1700-2300`.
//! Generated from `fixtures/parity_hk50_level_confluence_30m-night.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, LevelPair};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "HK50 Level Confluence 30m-night FN",
    id: "fn_hk50_level_confluence_30m-night",
    code: "FN_HK50_LEVEL_CONFLUENCE_30M-NIGHT",
    python_key: "hk50:level_confluence@30m-night",
    market: "hk50",
    book: Book::FundedNext,
    contract: Instrument::FN_HK50.window(0, 360),
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
        bar_minutes: 30,
        per_session: 13,
        vol_target: 0.34567963654913286,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LevelConfluence { pair: LevelPair::PivotVwap, tolerance: 0.5 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 300,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
