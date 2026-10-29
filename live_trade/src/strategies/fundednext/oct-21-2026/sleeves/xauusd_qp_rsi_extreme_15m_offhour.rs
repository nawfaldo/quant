//! `xauusd:qp_rsi_extreme@15m-offhour`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal f61f9b946b90), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_xauusd_qp_rsi_extreme_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD QP RSI Extreme 15m-offhour FN",
    id: "fn_xauusd_qp_rsi_extreme_15m-offhour",
    code: "FN_XAUUSD_QP_RSI_EXTREME_15M-OFFHOUR",
    python_key: "xauusd:qp_rsi_extreme@15m-offhour",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.broker_day(),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: Some((480, 960)),
    clock_window: false,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.14077113604065217,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QpRsiExtreme { level: 70.0, both: true, stop_atr: 1.0 },
        direction: Direction::Follow,
        exit: Exit::Days(1),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
