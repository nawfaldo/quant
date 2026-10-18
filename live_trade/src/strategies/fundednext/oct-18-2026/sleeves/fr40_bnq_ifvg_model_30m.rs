//! `fr40:bnq_ifvg_model@30m`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357).
//! Generated from `fixtures/parity_fr40_bnq_ifvg_model_30m.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "FR40 BNQ IFVG Model 30m FN",
    id: "fn_fr40_bnq_ifvg_model_30m",
    code: "FN_FR40_BNQ_IFVG_MODEL_30M",
    python_key: "fr40:bnq_ifvg_model@30m",
    market: "fr40",
    book: Book::FundedNext,
    contract: Instrument::FN_FR40,
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 18,
        vol_target: 0.1378523245267968,
    }),
    engine: EngineKind::Family(Params {
        family: Family::BnqIfvgModel { k: 5, disp: 0.6 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(3.0),
        last_entry_minute: 630,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
