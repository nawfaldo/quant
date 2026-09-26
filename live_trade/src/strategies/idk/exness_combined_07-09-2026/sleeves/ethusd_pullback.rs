//! `ethusd:pullback`, one `cfd_families` cell.
//!
//! A retracement of at most half a daily range from a four-session extreme,
//! inside a twenty-session trend.
//!
//! THE SECOND PULLBACK IN THE BOOK, and the pair is deliberate rather than a
//! duplicate: `usdjpy:pullback` measures its trend against `ema_50d` on an FX
//! cross, this against `ema_20d` on crypto. A faster reference on a market that
//! realises about 60% annualised is not the same rule -- it re-arms after a
//! shallower correction, which on ETHUSD is most of them.
//!
//! IT DOES NOT CARRY ETHUSD's 3.0 SHOWN EQUITY. The two crypto sleeves that do
//! were calibrated when the EWMA throttle sat at a 0.33 median and every order
//! was sized to a third; this one fills without it, and handing it a multiplier
//! it does not need would be leverage rather than access.
//!
//! Seated 2026-09-04 on the HOLDOUT, together with `hk50:level_confluence`, to
//! replace the ES cell the operator cannot carry a feed for.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::ETHUSD;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "ETHUSD Pullback",
    id: "ethusd_pullback",
    code: "ETHUSD_PULLBACK",
    python_key: "ethusd:pullback",
    market: "ethusd",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    engine: EngineKind::Family(Params {
        family: Family::Pullback {
            channel: 4 * SESSION,
            // Half a daily range back from the extreme, and no further. A deeper
            // retracement is not a pullback in a trend, it is a reversal the
            // trend filter has not caught up with yet.
            depth: 0.5,
            // `ema_20d`, the FASTER of the two the axis offers -- and its own
            // setting, not the shared `trend` gate, which this cell leaves off.
            reference: 20 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 840,
        stop_day: 0.7,
        trend: Trend::None,
        vol_mode: VolMode::Calm,
    }),
};
