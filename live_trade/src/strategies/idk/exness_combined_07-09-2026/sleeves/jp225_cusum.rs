//! `jp225:cusum`, one `cfd_families` cell.
//!
//! Lopez de Prado's symmetric CUSUM filter at one ATR of accumulated deviation.
//!
//! THE ONE ENTRY IN THE BOOK THAT IS NOT AN EXTREME OF ANYTHING. Every other
//! breakout member fires when one reading crosses one level, so a move made of
//! twenty small steps in the same direction is invisible to all of them until it
//! happens to clear a channel. This accumulates the returns since its last event
//! and fires when the running sum exceeds a volatility-scaled threshold -- on
//! the twentieth small step, at the point where it became a move.
//!
//! THE RESET IS WHAT MAKES IT AN EVENT SAMPLER. The same drift cannot fire it
//! twice without an intervening retracement, so its trades are spaced by market
//! structure instead of by a bar count -- which is precisely the property
//! `jp225:volatility_breakout` and `jp225:break_retest` lack, and the reason
//! three JP225 breakout cells are not one rule counted three times.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 CUSUM",
    id: "jp225_cusum",
    code: "JP225_CUSUM",
    python_key: "jp225:cusum",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Cusum {
            // ONE ATR, the tighter of the two rungs. The filter resets after
            // every event, so the multiple sets how many events a year there
            // are rather than how extreme each one is.
            multiple: 1.0,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 420,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Calm,
    }),
};
