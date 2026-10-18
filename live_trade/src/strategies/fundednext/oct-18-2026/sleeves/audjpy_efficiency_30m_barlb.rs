//! `audjpy:efficiency@30m-barlb`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_audjpy_efficiency_30m-barlb.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, ErRegime};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDJPY Efficiency 30m-barlb FN",
    id: "fn_audjpy_efficiency_30m-barlb",
    code: "FN_AUDJPY_EFFICIENCY_30M-BARLB",
    python_key: "audjpy:efficiency@30m-barlb",
    market: "audjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 29,
        vol_target: 0.07744619089304308,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Efficiency { period: 2, min_er: 0.5, regime: ErRegime::Noisy },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 720,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
