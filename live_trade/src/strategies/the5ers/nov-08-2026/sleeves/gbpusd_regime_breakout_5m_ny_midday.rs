//! `gbpusd:regime_breakout@5m-ny_midday`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w1100-1400`.
//! Generated from `fixtures/parity_gbpusd_regime_breakout_5m-ny_midday.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPUSD Regime Breakout 5m-ny_midday T5",
    id: "t5_gbpusd_regime_breakout_5m-ny_midday",
    code: "T5_GBPUSD_REGIME_BREAKOUT_5M-NY_MIDDAY",
    python_key: "gbpusd:regime_breakout@5m-ny_midday",
    market: "gbpusd",
    book: Book::The5ers,
    contract: Instrument::T5_GBPUSD.window(1080, 1260),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
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
        bar_minutes: 5,
        per_session: 37,
        vol_target: 0.06390148621785223,
    }),
    engine: EngineKind::Family(Params {
        family: Family::RegimeBreakout { fraction: 0.3, response: 0.5 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1140,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
