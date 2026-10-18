//! `gbpjpy:macd_hist@15m-barlb-off`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_gbpjpy_macd_hist_15m-barlb-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY MACD Hist 15m-barlb-off FN",
    id: "fn_gbpjpy_macd_hist_15m-barlb-off",
    code: "FN_GBPJPY_MACD_HIST_15M-BARLB-OFF",
    python_key: "gbpjpy:macd_hist@15m-barlb-off",
    market: "gbpjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPJPY.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: Some((0, 810)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.06809065058885642,
    }),
    engine: EngineKind::Family(Params {
        family: Family::MacdHist { fast: 12, slow: 26, signal: 9, zero: true },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
