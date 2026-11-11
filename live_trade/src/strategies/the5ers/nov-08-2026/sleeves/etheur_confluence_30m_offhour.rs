//! `etheur:confluence@30m-offhour`, a `cfd_families` cell of the The5ers book
//! (`the5ers_combined_strategies`, t5live-7ccde3bb0852), run on the broker day outside the cash session.
//! Generated from `fixtures/parity_etheur_confluence_30m-offhour.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHEUR Confluence 30m-offhour T5",
    id: "t5_etheur_confluence_30m-offhour",
    code: "T5_ETHEUR_CONFLUENCE_30M-OFFHOUR",
    python_key: "etheur:confluence@30m-offhour",
    market: "etheur",
    book: Book::The5ers,
    contract: Instrument::T5_ETHEUR.broker_day(),
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
        vol_target: 0.6473975318318819,
    }),
    engine: EngineKind::Family(Params {
        family: Family::ConfluenceTrend { votes: 2, rsi_period: 644, macd: (552, 1196, 414), obv_channel: 460, span: 230 },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 1319,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
