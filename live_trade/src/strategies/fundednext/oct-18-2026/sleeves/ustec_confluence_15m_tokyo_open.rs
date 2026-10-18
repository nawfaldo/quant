//! `ustec:confluence@15m-tokyo_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2000-2300`.
//! Generated from `fixtures/parity_ustec_confluence_15m-tokyo_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Confluence 15m-tokyo_open FN",
    id: "fn_ustec_confluence_15m-tokyo_open",
    code: "FN_USTEC_CONFLUENCE_15M-TOKYO_OPEN",
    python_key: "ustec:confluence@15m-tokyo_open",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC.window(180, 360),
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
        per_session: 13,
        vol_target: 0.1582061222941497,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Confluence { votes: 3, zscore_period: 26, rsi_period: 26 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 300,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
