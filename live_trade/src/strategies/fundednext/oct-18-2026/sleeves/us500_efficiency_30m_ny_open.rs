//! `us500:efficiency@30m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_us500_efficiency_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, ErRegime};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Efficiency 30m-ny_open FN",
    id: "fn_us500_efficiency_30m-ny_open",
    code: "FN_US500_EFFICIENCY_30M-NY_OPEN",
    python_key: "us500:efficiency@30m-ny_open",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(900, 1080),
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
        per_session: 7,
        vol_target: 0.11736644236922708,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Efficiency { period: 14, min_er: 0.3, regime: ErRegime::Noisy },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
