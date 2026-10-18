//! `ustec:structure@30m-afternoon`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1100-1700`.
//! Generated from `fixtures/parity_ustec_structure_30m-afternoon.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC Structure 30m-afternoon FN",
    id: "fn_ustec_structure_30m-afternoon",
    code: "FN_USTEC_STRUCTURE_30M-AFTERNOON",
    python_key: "ustec:structure@30m-afternoon",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC.window(1080, 1439),
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
        vol_target: 0.16618798806906748,
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
