//! `euraud:gated_fade@15m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_euraud_gated_fade_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, FadeTrigger, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURAUD Gated Fade 15m-offhour T5",
    id: "t5_euraud_gated_fade_15m-offhour",
    code: "T5_EURAUD_GATED_FADE_15M-OFFHOUR",
    python_key: "euraud:gated_fade@15m-offhour",
    market: "euraud",
    book: Book::The5ers,
    contract: Instrument::T5_EURAUD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((60, 840)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.06585119909173189,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedFade { trigger: FadeTrigger::Zscore(184), gate: Gate::Macd, polarity: Polarity::Confirm, gate_period: 92 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1379,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
