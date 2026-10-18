//! `audusd:obv_divergence@30m-barlb-off`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_audusd_obv_divergence_30m-barlb-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDUSD OBV Divergence 30m-barlb-off FN",
    id: "fn_audusd_obv_divergence_30m-barlb-off",
    code: "FN_AUDUSD_OBV_DIVERGENCE_30M-BARLB-OFF",
    python_key: "audusd:obv_divergence@30m-barlb-off",
    market: "audusd",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: true,
    off_hours: Some((60, 840)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.07206058242871943,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ObvDivergence { channel: 4 },
        direction: Direction::Fade,
        exit: Exit::Bars(4),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
