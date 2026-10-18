//! `usdjpy:momentum_stack@30m-barlb-off`, a `cfd_families` cell from the
//! off-hours wave (`EXNESS_WINDOW=off`, `EXNESS_BAR_LOOKBACKS=1`), seated
//! 2026-10-12.
//!
//! Three horizons of momentum (1, 5 and 20 bars) each standardised by its own
//! dispersion, summed, past +-3.0; 20-bar trend gate, calm volatility, trailed
//! 1.5 daily ranges. Runs on the broker day and takes nothing while the cash
//! session (00:00-13:00 New York) is open.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDJPY Momentum Stack 30m BarLB Off-hours",
    id: "usdjpy_momentum_stack_30m-barlb-off",
    code: "USDJPY_MOMENTUM_STACK_30M-BARLB-OFF",
    python_key: "usdjpy:momentum_stack@30m-barlb-off",
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
        family: Family::MomentumStack {
            threshold: 3.0,
            session: 1,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
