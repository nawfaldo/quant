//! `chfjpy:stochastic@15m-evening`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1700-2000`.
//! Generated from `fixtures/parity_chfjpy_stochastic_15m-evening.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Stochastic 15m-evening FN",
    id: "fn_chfjpy_stochastic_15m-evening",
    code: "FN_CHFJPY_STOCHASTIC_15M-EVENING",
    python_key: "chfjpy:stochastic@15m-evening",
    market: "chfjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CHFJPY.window(0, 180),
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
        vol_target: 0.08185404063862496,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Stochastic { period: 13, smooth: 6, threshold: 20.0, sum_seeds: &[] },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 60,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
