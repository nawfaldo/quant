//! `us30:stochastic@30m-morning`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_us30_stochastic_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Stochastic 30m-morning T5",
    id: "t5_us30_stochastic_30m-morning",
    code: "T5_US30_STOCHASTIC_30M-MORNING",
    python_key: "us30:stochastic@30m-morning",
    market: "us30",
    book: Book::The5ers,
    contract: Instrument::T5_US30.window(720, 1080),
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
        vol_target: 0.11635214726093872,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Stochastic { period: 65, smooth: 6, threshold: 30.0, sum_seeds: &[] },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1020,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
