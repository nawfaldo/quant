//! `xauusd:break_retest@30m-ny_close`, a `cfd_families` cell of the FundedNext book
//! (`fundednext_combined_strategies`, seal 9e0e0b402357), run in the New York window `w1400-1700`.
//! Generated from `fixtures/parity_xauusd_break_retest_30m-ny_close.json`, which the parity test holds it to.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode, BreakLevel, Retest};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "XAUUSD Break Retest 30m-ny_close FN",
    id: "fn_xauusd_break_retest_30m-ny_close",
    code: "FN_XAUUSD_BREAK_RETEST_30M-NY_CLOSE",
    python_key: "xauusd:break_retest@30m-ny_close",
    market: "xauusd",
    book: Book::FundedNext,
    contract: Instrument::FN_XAUUSD.window(1260, 1439),
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
        per_session: 6,
        vol_target: 0.14476128917637737,
    }),
    engine: EngineKind::Family(Params {
        family: Family::BreakRetest { window: 3, level: BreakLevel::Donchian, retest: Retest::Vwap, channel: 24 },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 1319,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
