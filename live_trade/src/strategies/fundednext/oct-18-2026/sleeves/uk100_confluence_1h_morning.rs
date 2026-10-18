//! `uk100:confluence@1h-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_uk100_confluence_1h-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "UK100 Confluence 1h-morning FN",
    id: "fn_uk100_confluence_1h-morning",
    code: "FN_UK100_CONFLUENCE_1H-MORNING",
    python_key: "uk100:confluence@1h-morning",
    market: "uk100",
    book: Book::FundedNext,
    contract: Instrument::FN_UK100.window(720, 1080),
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
        vol_target: 0.09774213707772142,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Confluence { votes: 2, zscore_period: 14, rsi_period: 14 },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
