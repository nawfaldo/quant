//! `de40:gated_fade@30m-morning`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_de40_gated_fade_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, FadeTrigger, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "DE40 Gated Fade 30m-morning T5",
    id: "t5_de40_gated_fade_30m-morning",
    code: "T5_DE40_GATED_FADE_30M-MORNING",
    python_key: "de40:gated_fade@30m-morning",
    market: "de40",
    book: Book::The5ers,
    contract: Instrument::T5_DE40.window(720, 1080),
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
        bar_minutes: 30,
        per_session: 13,
        vol_target: 0.12861874268797044,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedFade { trigger: FadeTrigger::Zscore(26), gate: Gate::Macd, polarity: Polarity::Confirm, gate_period: 13 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1020,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
