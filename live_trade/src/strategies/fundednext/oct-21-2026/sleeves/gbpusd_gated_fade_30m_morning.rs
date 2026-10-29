//! `gbpusd:gated_fade@30m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_gbpusd_gated_fade_30m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, FadeTrigger, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPUSD Gated Fade 30m-morning FN",
    id: "fn_gbpusd_gated_fade_30m-morning",
    code: "FN_GBPUSD_GATED_FADE_30M-MORNING",
    python_key: "gbpusd:gated_fade@30m-morning",
    market: "gbpusd",
    book: Book::FundedNext,
    contract: Instrument::FN_GBPUSD.window(720, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        vol_target: 0.06607476829245648,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedFade { trigger: FadeTrigger::VwapDev, gate: Gate::Rsi, polarity: Polarity::Oppose, gate_period: 182 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
