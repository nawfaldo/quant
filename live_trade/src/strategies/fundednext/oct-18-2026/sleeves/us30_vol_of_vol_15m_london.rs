//! `us30:vol_of_vol@15m-london`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-0800`.
//! Generated from `fixtures/parity_us30_vol_of_vol_15m-london.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Vol Of Vol 15m-london FN",
    id: "fn_us30_vol_of_vol_15m-london",
    code: "FN_US30_VOL_OF_VOL_15M-LONDON",
    python_key: "us30:vol_of_vol@15m-london",
    market: "us30",
    book: Book::FundedNext,
    contract: Instrument::FN_US30.window(720, 900),
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
        vol_target: 0.11551859125314345,
    }),
    engine: EngineKind::Family(Params {
        family: Family::VolOfVol { period: 260, dispersion: 0.35, unsteady: false, lookback: 13 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 840,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
