//! `chfjpy:estimator@30m-offhour`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_chfjpy_estimator_30m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "CHFJPY Estimator 30m-offhour FN",
    id: "fn_chfjpy_estimator_30m-offhour",
    code: "FN_CHFJPY_ESTIMATOR_30M-OFFHOUR",
    python_key: "chfjpy:estimator@30m-offhour",
    market: "chfjpy",
    book: Book::FundedNext,
    contract: Instrument::FN_CHFJPY.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((0, 870)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.07116584166389588,
    }),
    engine: EngineKind::Family(Params {
        family: Family::Estimator { period: 230, parkinson: true, ratio: 1.5, whipping: false, lookback: 46 },
        direction: Direction::Fade,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1319,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
