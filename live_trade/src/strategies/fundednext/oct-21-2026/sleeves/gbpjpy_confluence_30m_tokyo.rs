//! `gbpjpy:confluence@30m-tokyo`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w2300-0200`.
//! Generated from `fixtures/parity_gbpjpy_confluence_30m-tokyo.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Confluence 30m-tokyo FN",
    id: "fn_gbpjpy_confluence_30m-tokyo",
    code: "FN_GBPJPY_CONFLUENCE_30M-TOKYO",
    python_key: "gbpjpy:confluence@30m-tokyo",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY.window(360, 540),
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
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.0804692105276058,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Confluence { votes: 2, zscore_period: 14, rsi_period: 14 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 480,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
