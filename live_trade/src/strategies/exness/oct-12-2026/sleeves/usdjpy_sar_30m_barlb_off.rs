//! `usdjpy:sar@30m-barlb-off`, a `cfd_families` cell from the off-hours wave
//! (`EXNESS_WINDOW=off`, `EXNESS_BAR_LOOKBACKS=1`), seated 2026-10-12.
//!
//! The bar Wilder's parabolic SAR flips, FADED; calm volatility, trailed 1.5
//! daily ranges. Runs on the broker day and takes nothing while the cash
//! session (00:00-13:00 New York) is open.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY SAR 30m BarLB Off-hours",
    id: "usdjpy_sar_30m-barlb-off",
    code: "USDJPY_SAR_30M-BARLB-OFF",
    python_key: "usdjpy:sar@30m-barlb-off",
    market: "usdjpy",
    book: Book::Exness,
    contract: Instrument::USDJPY.broker_day(),
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: Some((0, 780)),
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.056_441_934_763_913_45,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Sar,
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1379,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
