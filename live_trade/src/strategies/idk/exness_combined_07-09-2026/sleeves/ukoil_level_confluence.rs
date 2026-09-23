//! `ukoil:level_confluence`, one `exness_families` cell.
//!
//! Yesterday's range and today's session VWAP landing on the SAME price, and
//! the break of that cluster taken in its own direction.
//!
//! A DIFFERENT PAIR AND A DIFFERENT DIRECTION FROM `nq:level_confluence`, which
//! is why the book can hold the family twice. That one reads two STATIC levels
//! -- the floor pivots against yesterday's high and low, both fixed before the
//! session opens -- and FADES the cluster. This one reads a static level against
//! a moving one: the VWAP walks through the day, so the cluster is a coincidence
//! in TIME as well as in price, and it is FOLLOWED rather than faded. The two
//! cells take opposite sides of the same bar whenever both fire.
//!
//! The tolerance is the tighter rung, 0.25 ATR: the VWAP crosses a wide band
//! eventually on almost any day, so the agreement has to be narrow to mean
//! anything.
//!
//! Added 2026-08-29.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, LevelPair, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::UKOIL;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "UKOIL Level Confluence",
    id: "ukoil_level_confluence",
    code: "UKOIL_LEVEL_CONFLUENCE",
    python_key: "ukoil:level_confluence",
    market: "ukoil",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::LevelConfluence {
            pair: LevelPair::PdrVwap,
            tolerance: 0.25,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 750,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
