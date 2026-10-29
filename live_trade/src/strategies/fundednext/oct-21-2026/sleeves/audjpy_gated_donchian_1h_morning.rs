//! `audjpy:gated_donchian@1h-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_audjpy_gated_donchian_1h-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, Gate, Polarity};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDJPY Gated Donchian 1h-morning FN",
    id: "fn_audjpy_gated_donchian_1h-morning",
    code: "FN_AUDJPY_GATED_DONCHIAN_1H-MORNING",
    python_key: "audjpy:gated_donchian@1h-morning",
    market: "audjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDJPY.window(720, 1080),
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
        bar_minutes: 60,
        per_session: 7,
        vol_target: 0.09806286879393086,
    }),
    engine: EngineKind::Family(Params {
        family: Family::GatedDonchian { channel: 28, gate: Gate::Supertrend, polarity: Polarity::Confirm, gate_period: 0 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 960,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
