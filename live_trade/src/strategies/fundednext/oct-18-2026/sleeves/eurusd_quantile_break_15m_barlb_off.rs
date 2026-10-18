//! `eurusd:quantile_break@15m-barlb-off`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_eurusd_quantile_break_15m-barlb-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURUSD Quantile Break 15m-barlb-off FN",
    id: "fn_eurusd_quantile_break_15m-barlb-off",
    code: "FN_EURUSD_QUANTILE_BREAK_15M-BARLB-OFF",
    python_key: "eurusd:quantile_break@15m-barlb-off",
    market: "eurusd",
    book: Book::FundedNext,
    contract: Instrument::FN_EURUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: Some((120, 780)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.049906597058243345,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QuantileBreak { channel: 20, share: 0.98, buffer_atr: 0.0 },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
