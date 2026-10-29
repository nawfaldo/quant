//! `xauusd:xma_cross@30m-ny_close`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run in the New York window `w1400-1700`.
//! Generated from `fixtures/parity_xauusd_xma_cross_30m-ny_close.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, MaKind};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD XMA Cross 30m-ny_close FN",
    id: "fn_xauusd_xma_cross_30m-ny_close",
    code: "FN_XAUUSD_XMA_CROSS_30M-NY_CLOSE",
    python_key: "xauusd:xma_cross@30m-ny_close",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(1260, 1439),
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
        per_session: 6,
        vol_target: 0.14227619574186887,
    }),
    engine: EngineKind::Family(Params {
        family: Family::XmaCross { fast: 6, slow: 30, kind: MaKind::Tema },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
