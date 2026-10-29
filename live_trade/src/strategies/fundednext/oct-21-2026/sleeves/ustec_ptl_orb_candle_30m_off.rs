//! `ustec:ptl_orb_candle@30m-off`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_ustec_ptl_orb_candle_30m-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USTEC PTL ORB Candle 30m-off FN",
    id: "fn_ustec_ptl_orb_candle_30m-off",
    code: "FN_USTEC_PTL_ORB_CANDLE_30M-OFF",
    python_key: "ustec:ptl_orb_candle@30m-off",
    market: "ustec",
    book: Book::FundedNext,
    contract: Instrument::FN_USTEC.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((570, 960)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 46,
        vol_target: 0.16289564386051525,
    }),
    engine: EngineKind::Family(Params {
        family: Family::PtlOrbCandle { or_min: 5 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(10.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
