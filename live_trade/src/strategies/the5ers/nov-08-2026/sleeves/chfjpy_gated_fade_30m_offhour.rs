//! `chfjpy:gated_fade@30m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_chfjpy_gated_fade_30m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, FadeTrigger, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Gated Fade 30m-offhour T5",
    id: "t5_chfjpy_gated_fade_30m-offhour",
    code: "T5_CHFJPY_GATED_FADE_30M-OFFHOUR",
    python_key: "chfjpy:gated_fade@30m-offhour",
    market: "chfjpy",
    book: Book::The5ers,
    contract: Instrument::T5_CHFJPY.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 4.0,
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
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.07107435919038796,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedFade { trigger: FadeTrigger::Keltner(920), gate: Gate::Supertrend, polarity: Polarity::Oppose, gate_period: 0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.7,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
