//! `us30:amihud@30m-ny_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_us30_amihud_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Amihud 30m-ny_open FN",
    id: "fn_us30_amihud_30m-ny_open",
    code: "FN_US30_AMIHUD_30M-NY_OPEN",
    python_key: "us30:amihud@30m-ny_open",
    market: "us30",
    book: Book::FundedNext,
    contract: Instrument::FN_US30.window(900, 1080),
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
        vol_target: 0.11077010238157799,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Amihud { short: 35, long: 140, ratio: 1.3, illiquid: false, lookback: 7 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
