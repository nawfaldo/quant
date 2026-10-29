//! `usdcad:xma_cross@5m-morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0500-1100`.
//! Generated from `fixtures/parity_usdcad_xma_cross_5m-morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, MaKind};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCAD XMA Cross 5m-morning FN",
    id: "fn_usdcad_xma_cross_5m-morning",
    code: "FN_USDCAD_XMA_CROSS_5M-MORNING",
    python_key: "usdcad:xma_cross@5m-morning",
    market: "usdcad",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCAD.window(720, 1080),
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
        bar_minutes: 5,
        per_session: 73,
        vol_target: 0.04611096293403799,
    }),
    engine: EngineKind::Family(Params {
        family: Family::XmaCross { fast: 73, slow: 365, kind: MaKind::Tema },
        direction: Direction::Fade,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 960,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
