//! The FundedNext book, ported from
//! `sandbox/research/fundednext_combined_strategies.py` as sealed on
//! 2026-10-18 (`sandbox/results/fundednext_challenge_seal.json`, seal
//! 9e0e0b402357): 80 `cfd_families` / `cfd_tt_families` cells on 22 markets,
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
//! open; fills are read off `fundednext_<broker>_1m` (`backtest::fills`). Nine
//! markets have no vendor table and SIGNAL off those same broker minutes.
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

/// 2023-07-05 00:00, `LOG_START` less `WARMUP_DAYS` (180): the first bar the
/// canon replay's volatility throttle sees, on the series' own clock.
pub const CANON_THROTTLE_FROM: i64 = 1_704_067_200 - 180 * 86_400;

/// The sealed members, in the seal's `members` order.
pub const BOOK: [Sleeve; 80] = [
    Sleeve::FnUstecConfluence15mTokyoOpen,
    Sleeve::FnXauusdGatedSwing5mLondonOpen,
    Sleeve::FnXauusdRvol5mTokyoOpen,
    Sleeve::FnUs2000KeyReversal5mMorning,
    Sleeve::FnUstecStructure30mAfternoon,
    Sleeve::FnUstecPtlOrbCandle30mOff,
    Sleeve::FnUs500DayOfWeek5mEvening,
    Sleeve::FnUs500Aroon30mBarlb,
    Sleeve::FnXauusdRegimeBreakout5mNyOpen,
    Sleeve::FnCadjpyValueArea30mRth,
    Sleeve::FnGbpusdGatedFade30mMorning,
    Sleeve::FnUsdjpyTwoStage30mLondonOpen,
    Sleeve::FnAudjpyGatedDonchian1hMorning,
    Sleeve::FnXauusdQuantileBreak5mEvening,
    Sleeve::FnUs500AvFirstHourFib30mAfternoon,
    Sleeve::FnNzdusdTrap1hOffhour,
    Sleeve::FnXauusdObvDivergence15mEvening,
    Sleeve::FnEuraudDayOfWeek1hNight,
    Sleeve::FnUstecGatedOrb30mLondonOpen,
    Sleeve::FnEuraudDayOfWeek15mAfternoon,
    Sleeve::FnUs500XmaCross30mLondonOpen,
    Sleeve::FnUs30VolOfVol15mLondon,
    Sleeve::FnUsdchfSkew1hAfternoon,
    Sleeve::FnUstecKendall5mLondonOpen,
    Sleeve::FnGbpjpyConfluence30mTokyo,
    Sleeve::FnEurusdStructure30mBarlbOff,
    Sleeve::FnAudusdTrap30mMorning,
    Sleeve::FnGbpjpyDayOfWeek30mBarlb,
    Sleeve::FnUsdjpyKalman30mNyOpen,
    Sleeve::FnUs2000SwingBreak30mMorning,
    Sleeve::FnAudjpyDayOfWeek30mLondon,
    Sleeve::FnGbpjpyQuantileBreak30mAfternoon,
    Sleeve::FnNzdusdTrap15mOffhour,
    Sleeve::FnUsdcadAdaptivePullback1hAfternoon,
    Sleeve::FnAudjpyEfficiency30mBarlb,
    Sleeve::FnXauusdQttRsiEmaRetest5mOffhour,
    Sleeve::FnXauusdCci30mEarlyMorning,
    Sleeve::FnEurusdAdaptivePullback15mMorning,
    Sleeve::FnEurjpyTrOscillatorRebound60m,
    Sleeve::FnChfjpyStructure15mRth,
    Sleeve::FnUsdcadSkew1hNight,
    Sleeve::FnNzdusdCusum1hRth,
    Sleeve::FnEurjpyCusum1hEarlyMorning,
    Sleeve::FnGbpaudStructure15mNyOpen,
    Sleeve::FnHk50LevelConfluence30mNight,
    Sleeve::FnGbpusdLinregTrend30mBarlbOff,
    Sleeve::FnUs30LinregTrend30mLondonOpen,
    Sleeve::FnUk100Confluence1hMorning,
    Sleeve::FnXauusdBreakRetest30mNyClose,
    Sleeve::FnUk100ValueArea60mBarlb,
    Sleeve::FnGbpusdValueArea30mBarlbOff,
    Sleeve::FnUs30BreakRetest5mAfternoon,
    Sleeve::FnChfjpyEstimator30mOffhour,
    Sleeve::FnGbpjpyXmaCross60mBarlbOff,
    Sleeve::FnUs2000QuantileBreak15mAfternoon,
    Sleeve::FnUsdjpyGatedOrb15mBarlb,
    Sleeve::FnNzdusdRegimeBreakout15mLondon,
    Sleeve::FnUs30LinregTrend30mEarlyMorning,
    Sleeve::FnUsdchfTwoStage15mRth,
    Sleeve::FnEuraudRoofing15mTokyoOpen,
    Sleeve::FnUs500Trap5mTokyo,
    Sleeve::FnUsdcadTrRvi60mOff,
    Sleeve::FnUsdjpyVolatilityBreakout60mBarlb,
    Sleeve::FnEurjpySwingBreak30mNyOpen,
    Sleeve::FnFr40HalfLife1hMorning,
    Sleeve::FnUs30AdaptivePullback30mAfternoon,
    Sleeve::FnUstecTwpAsiaLondonNy15m,
    Sleeve::FnGbpjpyMacdHist15mBarlbOff,
    Sleeve::FnUsdchfTrap15mMorning,
    Sleeve::FnGbpaudFailedBreak30mEarlyMorning,
    Sleeve::FnFr40BnqIfvgModel30m,
    Sleeve::FnEuraudMomentum30mMorning,
    Sleeve::FnGbpaudObvDivergence1hMorning,
    Sleeve::FnGbpjpyHalfLife30mNyOpen,
    Sleeve::FnUs2000SwingBreak1hRth,
    Sleeve::FnCadjpyKendall15mRth,
    Sleeve::FnAudusdLuxalgoManipulation30mNyOpen,
    Sleeve::FnUs500Efficiency30mNyOpen,
    Sleeve::FnAudusdObvDivergence30mBarlbOff,
    Sleeve::FnEurusdQuantileBreak15mBarlbOff,
];

/// The book's own name, for the one place it is a THING rather than a list.
pub const BOOK_ID: &str = "fundednext_combined_18-10-2026";

/// The same book, spelled for a person.
pub const BOOK_NAME: &str = "FundedNext Combined 18-10-2026";

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
