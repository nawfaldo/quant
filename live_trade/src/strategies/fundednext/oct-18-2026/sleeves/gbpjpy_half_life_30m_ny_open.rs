//! `gbpjpy:half_life@30m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_gbpjpy_half_life_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Half Life 30m-ny_open FN",
    id: "fn_gbpjpy_half_life_30m-ny_open",
    code: "FN_GBPJPY_HALF_LIFE_30M-NY_OPEN",
    python_key: "gbpjpy:half_life@30m-ny_open",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY.window(900, 1080),
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
        per_session: 7,
        vol_target: 0.08466136210960404,
    }),
    engine: EngineKind::Family(Params {
        family: Family::HalfLife { period: 280, multiple: 1.0, threshold_z: 1.5 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
