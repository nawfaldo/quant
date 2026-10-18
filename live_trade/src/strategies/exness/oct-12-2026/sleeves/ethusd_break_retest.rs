//! `ethusd:break_retest`, one `cfd_families` cell.
//!
//! Yesterday's range breaks, price returns to the session VWAP within three
//! candles, and the retest holds. Same family as the retired
//! `jp225:break_retest`, on the other arm of both axes that matter: `pdr`
//! rather than a Donchian channel, and a trailing exit rather than `rr_2`.
//!
//! Seated 2026-09-22.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{BreakLevel, Direction, Exit, Family, Params, Retest, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
#[allow(dead_code, reason = "not every cell counts in sessions")]
const SESSION: usize = CONTRACT.per_session;

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Break Retest",
    id: "ethusd_break_retest",
    code: "ETHUSD_BREAK_RETEST",
    python_key: "ethusd:break_retest",
    market: "ethusd",
    book: Book::Exness,
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: None,
    engine: EngineKind::Family(Params {
        family: Family::BreakRetest {
            window: 3,
            level: BreakLevel::Pdr,
            retest: Retest::Vwap,
            // Read only under `donchian`; carried because the field is not
            // optional, and set to what `_breakout_levels` would use.
            channel: 4 * SESSION,
        },
        // No `direction` axis: the side is whichever way the level broke.
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 840,
        stop_day: 0.4,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
