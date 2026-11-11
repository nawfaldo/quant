//! `solusd:confluence@15m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_solusd_confluence_15m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "SOLUSD Confluence 15m-offhour T5",
    id: "t5_solusd_confluence_15m-offhour",
    code: "T5_SOLUSD_CONFLUENCE_15M-OFFHOUR",
    python_key: "solusd:confluence@15m-offhour",
    market: "solusd",
    book: Book::The5ers,
    contract: Instrument::T5_SOLUSD.broker_day(),
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
        bar_minutes: 15,
        per_session: 92,
        vol_target: 0.8190290903850517,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ConfluenceTrend { votes: 2, rsi_period: 1288, macd: (1104, 2392, 828), obv_channel: 920, span: 460 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 1319,
        stop_day: 0.4,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Calm,
    }),
};
