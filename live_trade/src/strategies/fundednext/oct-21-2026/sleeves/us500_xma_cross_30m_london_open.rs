//! `us500:xma_cross@30m-london_open`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w0200-0500`.
//! Generated from `fixtures/parity_us500_xma_cross_30m-london_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, MaKind};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 XMA Cross 30m-london_open FN",
    id: "fn_us500_xma_cross_30m-london_open",
    code: "FN_US500_XMA_CROSS_30M-LONDON_OPEN",
    python_key: "us500:xma_cross@30m-london_open",
    market: "us500",
    book: Book::FundedNext,
    contract: Instrument::FN_US500.window(540, 720),
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
        per_session: 7,
        vol_target: 0.11434163181135605,
    }),
    engine: EngineKind::Family(Params {
        family: Family::XmaCross { fast: 7, slow: 140, kind: MaKind::Hma },
        direction: Direction::Follow,
        exit: Exit::Bars(4),
        last_entry_minute: 660,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
