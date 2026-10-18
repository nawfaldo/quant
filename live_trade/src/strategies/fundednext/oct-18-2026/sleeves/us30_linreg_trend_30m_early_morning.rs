//! `us30:linreg_trend@30m-early_morning`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w2300-0500`.
//! Generated from `fixtures/parity_us30_linreg_trend_30m-early_morning.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "US30 Linreg Trend 30m-early_morning FN",
    id: "fn_us30_linreg_trend_30m-early_morning",
    code: "FN_US30_LINREG_TREND_30M-EARLY_MORNING",
    python_key: "us30:linreg_trend@30m-early_morning",
    market: "us30",
    book: Book::FundedNext,
    contract: Instrument::FN_US30.window(360, 720),
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
        vol_target: 0.11480673594160093,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LinregTrend { period: 130, slope: 0.5, min_fit: 0.6 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 660,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
