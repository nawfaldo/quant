//! `usdcad:tr_rvi@60m-off`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_usdcad_tr_rvi_60m-off.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "USDCAD TR RVI 60m-off FN",
    id: "fn_usdcad_tr_rvi_60m-off",
    code: "FN_USDCAD_TR_RVI_60M-OFF",
    python_key: "usdcad:tr_rvi@60m-off",
    market: "usdcad",
    book: Book::FundedNext,
    contract: Instrument::FN_USDCAD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 2.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((150, 870)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 60,
        per_session: 23,
        vol_target: 0.04921015675420101,
    }),
    engine: EngineKind::Family(Params {
        family: Family::TrRvi { below: true, ema: 200 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
