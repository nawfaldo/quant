//! The The5ers High Stakes $5K book, ported from
//! `sandbox/research/the5ers_combined_strategies.py` (final book of
//! 2026-11-08, `sandbox/results/the5ers_live_book.json`): the 32 of its 47
//! sleeves whose families the Rust engine implements, traded at dial 0.1 (the
//! user's choice for FundedNext-like speed). Same engine and search protocol as
//! the FundedNext book (`CFD_BROKER_HOURS=1`, `EXNESS_FILLS_ONLY=1`, each cell's
//! window), priced at The5ers and filled off `the5ers_<broker>_1m`; entries in a
//! related red-folder news window are refused (`ban`).
//!
//! THE BAN LAYER (`ban.rs`) applies The5ers' prohibited-practices limits to the
//! whole book live: news, 60 s between entries, at most 5 open, one per
//! instrument, no opposite positions in a correlated group, no entries
//! 16:45-18:15 NY, at most 1% of $5,000 risked per trade.

use crate::strategies::cfd::*;

/// The user's dial (`the5ers_live_book.json["risk"]`).
pub const RISK_SCALE: f64 = 0.1;

/// The $5,000 High Stakes account every window starts fresh from.
pub const INITIAL_BALANCE: f64 = 5_000.0;

/// The book this is (`the5ers_live_book.json["id"]`).
pub const SEAL: &str = "t5live-7ccde3bb0852";

/// The throttle's first bar: `LOG_START` (2024-01-01) less `WARMUP_DAYS` (180).
pub const CANON_THROTTLE_FROM: i64 = 1_704_067_200 - 180 * 86_400;
pub const THROTTLE_FROM: i64 = CANON_THROTTLE_FROM;

/// The most a trade may risk at its stop, as a share of the $5,000
/// (`the5ers_combined_strategies.MAX_TRADE_RISK`).
pub const MAX_TRADE_RISK: f64 = 0.01;

pub(crate) use crate::strategies::fundednext::oct_21_2026::throttle_marks;

/// The members, in the live book's order.
pub const BOOK: [Sleeve; 32] = [
    Sleeve::T5SolusdConfluence15mOffhour,
    Sleeve::T5ChfjpyGatedFade30mOffhour,
    Sleeve::T5EurusdRvol30mRth,
    Sleeve::T5GbpjpyDayOfWeek15mOffhour,
    Sleeve::T5UstecStructure30mAfternoon,
    Sleeve::T5EtheurConfluence30mOffhour,
    Sleeve::T5Us30TwoStage30mOffhour,
    Sleeve::T5BtceurGatedOrb15mEarlyMorning,
    Sleeve::T5De40GatedFade30mMorning,
    Sleeve::T5GbpusdValueArea30mBarlbOff,
    Sleeve::T5Us30Stochastic30mMorning,
    Sleeve::T5Us500DayOfWeek5mEvening,
    Sleeve::T5UstecSwingBreak5mNyOpen,
    Sleeve::T5NzdusdStructure15mRth,
    Sleeve::T5GbpusdRegimeBreakout5mNyMidday,
    Sleeve::T5NzdusdTrap30mOffhour,
    Sleeve::T5AudjpyGatedOrb15mNyOpen,
    Sleeve::T5UsdjpyTwoStage30mLondonOpen,
    Sleeve::T5EurusdKalman30mRth,
    Sleeve::T5UsdcadHalfLife15mOffhour,
    Sleeve::T5AudusdKendall15mMorning,
    Sleeve::T5EuraudGatedFade15mOffhour,
    Sleeve::T5EuraudTrap5mTokyoOpen,
    Sleeve::T5CadjpyMomentum5mRth,
    Sleeve::T5AudusdSwingBreak15mTokyoOpen,
    Sleeve::T5UsdchfFloorPivot15mMorning,
    Sleeve::T5Uk100RegimeBreakout30mNyOpen,
    Sleeve::T5Hk50Efficiency15mBarlb,
    Sleeve::T5Hk50DayOfWeek30mNyOpen,
    Sleeve::T5EurjpyGatedOrb30mBarlb,
    Sleeve::T5Uk100Confluence1hMorning,
    Sleeve::T5AudjpyStructure30mBarlbOff,
];

/// The book's own name.
pub const BOOK_ID: &str = "the5ers_combined_08-11-2026";

/// The same book, spelled for a person.
pub const BOOK_NAME: &str = "The5ers Combined 08-11-2026";

/// Whether `names` is the WHOLE book, in any order and with no strays.
pub fn is_whole_book<'a>(names: impl Iterator<Item = &'a str>) -> bool {
    let mut seen = [false; BOOK.len()];
    let mut count = 0usize;
    for name in names {
        let Some(sleeve) = Sleeve::from_display(name).or_else(|| Sleeve::from_id(name)) else {
            return false;
        };
        let Some(index) = BOOK.iter().position(|member| *member == sleeve) else {
            return false;
        };
        if seen[index] {
            return false;
        }
        seen[index] = true;
        count += 1;
    }
    count == BOOK.len()
}

pub(crate) mod ban;
pub(crate) mod contracts;
pub(crate) mod sleeves;

#[cfg(test)]
mod parity;
