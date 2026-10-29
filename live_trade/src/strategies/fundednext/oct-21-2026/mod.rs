//! The FundedNext book, ported from
//! `sandbox/research/fundednext_combined_strategies.py` as sealed on
//! 2026-10-21 (`sandbox/results/fundednext_challenge_seal.json`, seal
//! f61f9b946b90): 50 `cfd_families` / `cfd_tt_families` cells on 20 markets,
//! admitted by the `down_rho` <= 0.50 / `loss_lift` <= 1.60 gates and cut to the
//! sleeves whose minimum lot fits 1.5x their asked risk at the sealed dial.
//!
//! LIVE on the FundedNext account (`tools/go_live_fundednext.py`). Replayed on
//! ONE shared $6,000 balance at `RISK_SCALE` 0.03, each sleeve at its ladder
//! leverage (`SleeveSpec::scale`, capped at 5x), broker minimum forced, no gross
//! cap and no sizing cap -- `fundednext_combined_strategies.replay`.
//!
//! THE SEARCH PROTOCOL IS PART OF EVERY CELL: `CFD_BROKER_HOURS=1` (a vendor
//! candle the broker did not quote is dropped) and `EXNESS_FILLS_ONLY=1` (a
//! signal whose fill candle has no broker price is not taken), plus each
//! cell's own window -- `rth`, `off`, or a New York clock window.
//!
//! PRICED AT FUNDEDNEXT: each contract carries FundedNext's spread snapshot and
//! commission ($3 a lot on forex and metals, indices free), charged once at the
//! open; fills are read off `fundednext_<broker>_1m` (`backtest::fills`). Every
//! cell SIGNALS on its Dukascopy vendor table.
//!
//! THE POOL WAS SCREENED ON 2026, so the book's 2026 numbers are
//! selection-conditioned; the clean test is data after 2026-10-13.
//!
//! Every member reproduces its Python trade log through `parity`, and the whole
//! book is diffed trade for trade against
//! `sandbox/results/fundednext_canon_trades.json` by the `fundednext_book` tool.

use crate::strategies::cfd::*;

/// The seal's dial (`fundednext_challenge_seal.json["risk"]`).
pub const RISK_SCALE: f64 = 0.03;

/// `fundednext_combined_strategies.ACCOUNT`: the $6,000 two-step challenge
/// account every window starts fresh from.
pub const INITIAL_BALANCE: f64 = 6_000.0;

/// The seal this book is (`fundednext_challenge_seal.json["id"]`).
pub const SEAL: &str = "f61f9b946b90";

/// 2023-07-05 00:00, `LOG_START` less `WARMUP_DAYS` (180): the first bar the
/// canon replay's volatility throttle sees, on the series' own clock.
pub const CANON_THROTTLE_FROM: i64 = 1_704_067_200 - 180 * 86_400;

/// Where every live-rules run and the live account start the sizing throttle:
/// the canon's own start, so neither depends on the day it was started
/// (`FamilyEngine::prime_throttle`).
pub const THROTTLE_FROM: i64 = CANON_THROTTLE_FROM;

/// The market's last close in each 30-minute slot, `(bar_ts, close)` --
/// everything `prime_throttle` reads of a bar.
pub(crate) fn throttle_marks(bars: &[crate::backtest::types::Bar]) -> Vec<(i64, f64)> {
    let mut out: Vec<(i64, f64)> = Vec::new();
    for bar in bars {
        let slot = bar.ts.div_euclid(1_800);
        match out.last_mut() {
            Some(last) if last.0.div_euclid(1_800) == slot => *last = (bar.ts, bar.close),
            _ => out.push((bar.ts, bar.close)),
        }
    }
    out
}

/// The sealed members, in the seal's `members` order.
pub const BOOK: [Sleeve; 50] = [
    Sleeve::FnUsdchfSkew1hAfternoon,
    Sleeve::FnChfjpyOvernight5mNight,
    Sleeve::FnXauusdObvDivergence5mLondonOpen,
    Sleeve::FnUstecConfluence15mTokyoOpen,
    Sleeve::FnGbpaudCci1hMorning,
    Sleeve::FnUstecPtlOrbCandle30mOff,
    Sleeve::FnUs500DayOfWeek5mEvening,
    Sleeve::FnUs30Amihud30mNyOpen,
    Sleeve::FnChfjpyStochastic15mEvening,
    Sleeve::FnUsdcadXmaCross5mMorning,
    Sleeve::FnXauusdFloorPivot15mMorning,
    Sleeve::FnGbpusdGatedFade30mMorning,
    Sleeve::FnUsdjpyTwoStage30mLondonOpen,
    Sleeve::FnAudjpyGatedDonchian1hMorning,
    Sleeve::FnGbpjpyQuantileBreak5mNyMidday,
    Sleeve::FnUs500AvFirstHourFib30mAfternoon,
    Sleeve::FnXauusdQuantileBreak15mEvening,
    Sleeve::FnUs500TwoStage15mNyOpen,
    Sleeve::FnFr40Efficiency1hMorning,
    Sleeve::FnUstecGatedOrb30mLondonOpen,
    Sleeve::FnUs2000AdaptivePullback30mMorning,
    Sleeve::FnUs500XmaCross30mLondonOpen,
    Sleeve::FnXauusdQpRsiExtreme15mOffhour,
    Sleeve::FnUs2000SwingBreak30mMorning,
    Sleeve::FnXauusdXmaCross30mNyClose,
    Sleeve::FnNzdusdTrap1hOffhour,
    Sleeve::FnUstecKendall5mLondonOpen,
    Sleeve::FnGbpusdRegimeBreakout5mNyMidday,
    Sleeve::FnGbpjpyConfluence30mTokyo,
    Sleeve::FnEurusdStructure30mBarlbOff,
    Sleeve::FnEurjpyRvol60mBarlb,
    Sleeve::FnUstecFracdiff5mLondon,
    Sleeve::FnGbpjpyDayOfWeek30mBarlb,
    Sleeve::FnUsdjpyKalman30mNyOpen,
    Sleeve::FnAudjpyDayOfWeek30mLondon,
    Sleeve::FnUs500LinregTrend30mAfternoon,
    Sleeve::FnGbpjpyQuantileBreak30mAfternoon,
    Sleeve::FnUsdcadAdaptivePullback1hAfternoon,
    Sleeve::FnAudjpyEfficiency30mBarlb,
    Sleeve::FnEuraudDayOfWeek5mAfternoon,
    Sleeve::FnEurusdAdaptivePullback15mMorning,
    Sleeve::FnEurjpyTrOscillatorRebound60m,
    Sleeve::FnNzdusdTwoStage5mRth,
    Sleeve::FnEurjpyCusum1hEarlyMorning,
    Sleeve::FnChfjpyAdaptivePullback5mOffhour,
    Sleeve::FnUsdchfFloorPivot15mMorning,
    Sleeve::FnUk100Confluence1hMorning,
    Sleeve::FnUk100ValueArea60mBarlb,
    Sleeve::FnGbpusdValueArea30mBarlbOff,
    Sleeve::FnCadjpyMomentum5mRth,
];

/// The book's own name, for the one place it is a THING rather than a list.
pub const BOOK_ID: &str = "fundednext_combined_21-10-2026";

/// The same book, spelled for a person.
pub const BOOK_NAME: &str = "FundedNext Combined 21-10-2026";

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

pub(crate) mod contracts;
pub(crate) mod sleeves;

#[cfg(test)]
mod parity;
