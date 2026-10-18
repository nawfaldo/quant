//! `xauusd:qtt_rsi_ema_retest@5m-offhour`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_xauusd_qtt_rsi_ema_retest_5m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD QTT RSI EMA Retest 5m-offhour FN",
    id: "fn_xauusd_qtt_rsi_ema_retest_5m-offhour",
    code: "FN_XAUUSD_QTT_RSI_EMA_RETEST_5M-OFFHOUR",
    python_key: "xauusd:qtt_rsi_ema_retest@5m-offhour",
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
        bar_minutes: 5,
        per_session: 276,
        vol_target: 0.14052637876605706,
    }),
    engine: EngineKind::Family(Params {
        family: Family::QttRsiEmaRetest { retest: true },
        direction: Direction::Follow,
        exit: Exit::Signal,
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
