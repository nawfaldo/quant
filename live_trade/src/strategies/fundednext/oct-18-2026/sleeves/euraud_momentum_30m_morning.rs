//! `euraud:momentum@30m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_euraud_momentum_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Momentum 30m-morning FN",
    id: "fn_euraud_momentum_30m-morning",
    code: "FN_EURAUD_MOMENTUM_30M-MORNING",
    python_key: "euraud:momentum@30m-morning",
    market: "euraud",
    book: Book::FundedNext,
    contract: Instrument::FN_EURAUD.window(720, 1080),
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
        per_session: 13,
        vol_target: 0.059879835830409074,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Momentum { signal_minute: 780, lookback: 13, threshold_atr: 1.0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1080, // none in Python: the session close
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
