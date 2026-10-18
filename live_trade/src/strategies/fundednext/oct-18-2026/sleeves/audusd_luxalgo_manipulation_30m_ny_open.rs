//! `audusd:luxalgo_manipulation@30m-ny_open`, a TikTok-creator cell (`cfd_tt_families`) of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w0800-1100`.
//! Generated from `fixtures/parity_audusd_luxalgo_manipulation_30m-ny_open.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, TtEngulf, TtStop};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "AUDUSD Luxalgo Manipulation 30m-ny_open FN",
    id: "fn_audusd_luxalgo_manipulation_30m-ny_open",
    code: "FN_AUDUSD_LUXALGO_MANIPULATION_30M-NY_OPEN",
    python_key: "audusd:luxalgo_manipulation@30m-ny_open",
    market: "audusd",
    book: Book::FundedNext,
    contract: Instrument::FN_AUDUSD.window(900, 1080),
    // The ladder leverage `_lev` sealed, capped at `LEV_CAP`.
    scale: 3.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: true,
    broker_hours: true,
    fills_only: true,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 7,
        vol_target: 0.08494531932786449,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxalgoManipulation { prior_bars: 0, ema: 100, engulf: TtEngulf::Body, stop: TtStop::Atr(2.0), fade: true },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1020,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
