//! `eurjpy:tr_oscillator_rebound@60m`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_eurjpy_tr_oscillator_rebound_60m.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY TR Oscillator Rebound 60m FN",
    id: "fn_eurjpy_tr_oscillator_rebound_60m",
    code: "FN_EURJPY_TR_OSCILLATOR_REBOUND_60M",
    python_key: "eurjpy:tr_oscillator_rebound@60m",
    market: "eurjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_EURJPY,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 60,
        per_session: 14,
        vol_target: 0.07685894791176748,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TrOscillatorRebound { ema: 100 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.5),
        last_entry_minute: 810,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
