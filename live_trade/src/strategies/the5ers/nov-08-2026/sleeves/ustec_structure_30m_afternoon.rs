//! `ustec:structure@30m-afternoon`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_ustec_structure_30m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Structure 30m-afternoon T5",
    id: "t5_ustec_structure_30m-afternoon",
    code: "T5_USTEC_STRUCTURE_30M-AFTERNOON",
    python_key: "ustec:structure@30m-afternoon",
    market: "ustec",
    book: Book::The5ers,
    contract: Instrument::T5_USTEC.window(1080, 1439),
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
        bar_minutes: 30,
        per_session: 12,
        vol_target: 0.16618798806906648,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Structure { wing: 2, continuation: false },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
