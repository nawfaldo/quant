//! `ukoil:xma_cross`, one `exness_families` cell.
//!
//! A fast/slow TEMA cross, taken in the direction of the cross.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::UKOIL;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "UKOIL XMA Cross",
    id: "ukoil_xma_cross",
    code: "UKOIL_XMA_CROSS",
    python_key: "ukoil:xma_cross",
    market: "ukoil",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::XmaCross {
            fast: SESSION,
            slow: 5 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 750,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
