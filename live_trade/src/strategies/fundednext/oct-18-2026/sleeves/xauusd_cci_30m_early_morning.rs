//! `xauusd:cci@30m-early_morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2300-0500`.
//! Generated from `fixtures/parity_xauusd_cci_30m-early_morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD CCI 30m-early_morning FN",
    id: "fn_xauusd_cci_30m-early_morning",
    code: "FN_XAUUSD_CCI_30M-EARLY_MORNING",
    python_key: "xauusd:cci@30m-early_morning",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(360, 720),
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
        per_session: 13,
        vol_target: 0.14056937575417486,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Cci { period: 26, threshold: 100.0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 600,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
