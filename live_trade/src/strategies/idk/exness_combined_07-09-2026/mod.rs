//! The 22-sleeve Exness book, ported from
//! `py_sandbox/research/exness_combined_strategies.py` as sealed on 2026-09-07
//! and re-seated on 2026-09-19, when two JP225 sleeves were exchanged for
//! `ethusd:kalman` and `usdjpy:half_life`. `BOOK_ID` deliberately does not
//! move with membership -- see the note on it below.
//!
//! ONE ACCOUNT, TWENTY-TWO SLEEVES, SEVEN MARKETS. Each sleeve is an
//! `exness_families` cell whose parameters were fitted on data ending
//! 2024-12-31 and then measured on the 2025-01-01..2026-08-20 holdout. They are
//! run together against a single compounding balance, so a JP225 trade taken
//! after ETHUSD has drawn the account down is a smaller trade than it was
//! standalone -- summing standalone return streams instead hides about half the
//! drawdown ([[blend-model-understates-portfolio-drawdown]]) and a third sleeve
//! adds a whole risk budget rather than diversifying one away
//! ([[combined-book-stacks-risk-budgets]]).
//!
//! THE MEMBERS, in `BOOK` order, and the market each is routed to:
//!
//! ```text
//!  1  usdjpy:volume_thrust          usdjpy
//!  2  audusd:zscore                 audusd
//!  3  ethusd:confluence             ethusd
//!  4  ethusd:volatility_breakout    ethusd
//!  5  gbpjpy:trap                   gbpjpy
//!  6  ukoil:xma_cross               ukoil
//!  7  eurjpy:two_stage              eurjpy
//!  8  usdjpy:pullback               usdjpy
//!  9  ethusd:obv_break              ethusd
//! 10  jp225:volume_thrust           jp225            SHIFTED CLOCK, +6h
//! 11  ukoil:level_confluence        ukoil
//! 12  jp225:vol_regime              jp225
//! 13  jp225:momentum_stack          jp225
//! 14  usdjpy:kendall                usdjpy
//! 15  jp225:cusum                   jp225
//! 16  jp225:obv_divergence          jp225
//! 17  jp225:kalman                  jp225
//! 18  eurjpy:gated_orb              eurjpy
//! 19  usdjpy:fracdiff               usdjpy
//! 20  ethusd:pullback               ethusd
//! 21  ethusd:kalman                 ethusd           SEATED 2026-09-19
//! 22  usdjpy:half_life              usdjpy           SEATED 2026-09-19
//! ```
//!
//! JP225 CARRIES SIX OF THE TWENTY-TWO, down from eight on 2026-09-19. They are
//! not one rule counted six times -- a CUSUM event sampler, an OBV divergence,
//! a Kalman slope, a volatility-regime momentum, a three-horizon stack and a
//! volume thrust all fire on different bars -- but they share one session, one
//! clock and one gap, and on 2026-09-07..18 that is exactly what happened: the
//! index ended the fortnight where it began, +0.07%, and every JP225 cell was
//! sawn up together. At the broker's 3.0-lot floor on a $450 account there is
//! no sizing left to give ([[risk-dial-is-inert-on-a-pinned-account]]), so the
//! only lever is membership ([[membership-beats-sizing-for-drawdown]]).
//!
//! `jp225:volatility_breakout` and `jp225:break_retest` left and
//! `ethusd:kalman` and `usdjpy:half_life` took their places. Both departures
//! were PROFITABLE over 2022-2026; they left on concentration. On 1,000
//! resampled paths at live fills the swap takes p99 marked drawdown from 35.30%
//! to 31.32% and the worst path from 60.26% to 40.66%, with median return
//! 13,065% -> 22,261% and no path halving the account where canon had one. The
//! worst COLD opening fortnight at $450 goes -28.12% -> -16.86%. It costs a
//! worse MEDIAN path drawdown, 13.56% -> 15.52%
//! ([[sep-2026-fortnight-was-a-severe-cold-start]]).
//! USDJPY and ETHUSD carry five each, UKOIL and EURJPY two, and AUDUSD and
//! GBPJPY one apiece -- the swap moved one slot onto each of the two markets
//! the live feed already carried, which is why it needed no feed change.
//!
//! HOW IT REACHED TWENTY-TWO, because the order is the method. NQ WAS BARRED
//! AS A SYMBOL on 2026-09-03 by operator decision and re-tested the following
//! day; the bar stands. Five NQ sleeves went at once, and with them the
//! level-two feed and both hand-written imports -- which is why this file is one
//! engine rather than three. A decay screen measured in R-MULTIPLES then took
//! seven more: `gross / distance` is the trade's outcome in units of the risk it
//! took, which is the same quantity on any balance and in any year, unlike a
//! dollar contribution inside a compounding book.
//!
//! THE REFILL WAS TRIED AND REJECTED, AND THAT IS THE LESSON. A greedy forward
//! selection put seven fresh cells into the freed slots and looked excellent on
//! the realised path -- +19,274% at 15.6% marked drawdown. Its Monte Carlo p95
//! was 35.13%, because the greedy was guarded on REALISED drawdown rather than
//! on the distribution ([[canon-drawdown-is-sequence-risk]],
//! [[refill-guarded-on-one-path-hides-its-tail]]). Three cells were seated on
//! the HOLDOUT instead, on 2026-09-04. A fourth, `es:regime_breakout`, was
//! seated and removed the same day on a DATA DEPENDENCY rather than on its
//! record: it signals off the back-adjusted futures continuum and the operator
//! does not carry that feed ([[one-sleeve-cannot-pay-for-a-data-feed]]).
//!
//! THE TWENTY-THIRD LEFT ON 2026-09-07, and it is the third cell lost to
//! something other than its record. `hk50:level_confluence` was dropped on DATA
//! QUALITY: `hk50` is the gappiest symbol the book has held -- 6% of weekdays
//! carry no 30-minute bar at all, against 0-1% everywhere else, and only 70% of
//! its holdout entries fall on a bar the broker maps can reprice against 99-100%
//! for the rest, so a third of its trades were scored at the constant spread
//! while the others paid measured costs. Removing it moved nothing outside the
//! Monte Carlo's own sampling error ([[hk50-stops-for-lunch-inside-its-session]]).
//!
//! The gross cap went off before any of that: at twenty-plus sleeves it is a
//! first-come-first-served QUEUE rather than a risk control.
//!
//! ALL TWENTY-TWO SHARE ONE ENGINE. `FamilyEngine` below is a streaming port
//! of `exness_families.backtest`: 30-minute candles built from the `<symbol>_1m`
//! table, filtered to the symbol's session, one entry a day, a signal read on
//! bar `i`'s close filling at bar `i + 1`'s OPEN. Consuming the same bar's close
//! manufactures the fake edge recorded in [[entry-must-be-next-bar-open]], so
//! the `pending` slot is not an optimisation and must not be removed.
//!
//! WHERE THE WHOLE BOOK STANDS AGAINST THE PYTHON, measured over
//! 2025-01-01..2026-08-20 on $500 (`exness_book` against `_canon_book.py`):
//!
//! ```text
//!   trades          3,196  =  3,196     every sleeve, exactly
//!   every trade              = sleeve, entry bar, EXIT bar, lots, P&L
//!   final balance  3,645.76 = 3,645.76
//!   return         629.15% =  629.15%
//!   closed drawdown 18.00% =  18.00%
//!   marked drawdown 19.18% vs 18.60%    the engine marks a fuller book
//! ```
//!
//! ALL 3,196 TRADES AGREE, and so does every sleeve's P&L to the cent. Four
//! defects were between the port and that, and NONE of them was in a family
//! cell -- every one was a market-level table or a clock. THREE OF THE FOUR
//! WERE FOUND ON HK50, which is worth recording now that HK50 has left: a market
//! that is merely unusual finds the bugs the ordinary ones hide, and JP225 --
//! the market that shares its clock -- was concealing two of them by quoting
//! both swap legs at exactly zero.
//!
//!   1. HK50 HAD NO LINE IN `market_point_value`, which ends in `_ => 1.0`. Its
//!      point value is 0.1274, so the sleeve was paid 7.85x its real P&L and
//!      read as the third-largest contributor in the book. The map's doc comment
//!      had promised a test by name for months and the test did not exist; it
//!      does now, and it checks the lot floor and the spread as well.
//!   2. FINANCING NIGHTS WERE COUNTED ON THE REAL CLOCK. `ef.backtest` counts
//!      date boundaries on stamps `all_bars` has already SHIFTED. HK50 trades
//!      21:00-04:00 New York, so every one of its positions crosses a real
//!      boundary and none crosses a shifted one: one night of carry was billed
//!      on 43 of its 122 trades, a tenth of the sleeve's contribution. JP225 hid
//!      it by quoting both swap legs at exactly 0.0.
//!   3. `last_of_day` READ THE ARRIVING BAR'S MINUTE INSTEAD OF ITS SLOT.
//!      `context` session-filters the AGGREGATED bar, so a candle is in session
//!      when its SLOT is -- a slot opening at 10:00 qualifies even if its only
//!      minute printed at 10:15. HK50 gaps 03:29-04:15 real on some days, so the
//!      candle after its 09:00 one is built entirely from out-of-window minutes;
//!      reading the bar's own minute called that the end of the session and
//!      flattened an hour early at a worse price.
//!   4. THE LOADER CUT THE WINDOW ON THE REAL STAMP. A run ending on a date
//!      hands a shifted market one extra session -- the one belonging to the
//!      NEXT shifted day, because its cash session opens the evening before.
//!      Worth one trade each to `jp225:volatility_breakout` and
//!      `hk50:level_confluence`; `trim_to_shifted_window` cuts it for a book run.
//!
//! EVERY LOT SIZE MATCHES, which is the assertion worth having: sizing is where
//! the shared balance, the vol throttle, the shown equity and the two-stage
//! admission all meet, and a single disagreement there cascades through every
//! later entry.
//!
//! THE MARKED DRAWDOWN IS THE ONE FIGURE THAT DOES NOT MATCH, AND THE ENGINE IS
//! THE ONE THAT IS RIGHT. `replay` marks a position only at stamps where its own
//! symbol printed and CONTINUES past it everywhere else -- so at any given stamp
//! the book is marked as though it were flat in every market that did not print.
//! The engine sums every open position at its own market's last known price on
//! every instant, which is what an account actually experiences. Measured rather
//! than argued: making the engine skip a slot whose market did not print
//! reproduces Python's figure to a hundredth, while coarsening the marks to
//! 30-minute boundaries alone closes only a quarter of the gap. The divergence
//! is the skip rule, not the sampling rate
//! ([[mtm-drawdown-cannot-match-the-python-replay]]).
//!
//! THE CLOSED DRAWDOWN DOES MATCH, and it took the SHIFTED CLOCK to get there.
//! Python loads JP225 with `SHIFT_HOURS` already added, so `replay` settles its
//! exits six hours later than real time relative to every other market, and ties
//! inside one stamp settle in the order the positions were OPENED. The engine records real stamps -- a shifted one would be wrong for
//! anything but this comparison -- so `exness_book` re-applies the shift and
//! that tie-break when it walks the log.
//!
//! NO SLEEVE READS A SECOND MARKET. `ethusd:idio_break` did -- it asked whether
//! ETHUSD broke its own channel while BTC stayed inside its own -- and the decay
//! screen removed it. The loader still knows how to serve the join, because the
//! failure a missing benchmark causes is SILENT: the cell does not error, it
//! simply never fires.
//!
//! RISK IS DAY-ANCHORED. `stop_day` is a fraction of the average true DAILY
//! range over the prior 14 days, not a multiple of bar ATR, so a cell means the
//! same risk at every timeframe ([[bar-size-confound-is-stop-distance]]). ATR
//! still exists, but only as a signal input -- nothing sizes off it.
//!
//! SIZING IS THE BOOK'S, NOT THE FAMILY'S. `exness_families.quantity` throttles
//! with a one-sided `min(1, vol_target/realized)`; the book replaces that with
//! the engine's two-sided EWMA multiplier and adds two knobs the standalone cell
//! has no concept of:
//!
//! ```text
//! risk  = equity * SHOWN_EQUITY * RISK_FRACTION
//!         * SLEEVE_SCALE * CANON_RISK_SCALE * vol_multiplier
//! lots  = floor(min(risk / (stop * multiplier), margin_ceiling) / step) * step
//! ```
//!
//! `SHOWN_EQUITY` is a virtual-equity multiplier, and it buys ACCESS rather than
//! leverage. On a small balance a risk-sized order lands under the broker's
//! `volume_min` and simply never fills; showing the sleeve a larger equity
//! raises the request over that floor. Quantity is linear in the equity a sleeve
//! is handed, so it is a plain multiplier. Two ETHUSD entries and one JP225 one
//! exist because EWMA vol targeting cut those sleeves to a third of their old
//! size and pushed them under the floor -- the multiplier restores what they had
//! BEFORE the throttle, it does not add risk on top of it
//! ([[virtual-equity-unlocks-every-sleeve-at-1point5x]]). `ethusd:pullback`
//! joined without one, which is the evidence it is a measured setting rather
//! than a per-market constant.
//!
//! THE CANON RUN IS UNCAPPED. `sizing_equity_cap` is empty in the sealed record
//! and `uncapped` is true, so every sleeve compounds against the full shared
//! balance. `SIZING_EQUITY_CAP` was a real knob on earlier books -- without one
//! the ORDER of a sleeve's returns becomes its contribution -- and it is
//! deliberately absent here rather than forgotten.
//!
//! SIZING IS RE-ASKED AT FILL TIME. `replay` settles every position whose exit
//! has arrived and only THEN opens anything, so an entry is sized off the
//! balance the closes left behind. The engine has to step a strategy to learn it
//! wants to close, so `Strategy::resize_entry` asks again once they are booked.
//!
//! FINANCING IS QUOTED AND ALMOST NEVER PAID. ETHUSD charges 0.411 points a
//! night long, UKOIL 0.0753 short, EURJPY 0.0089; JP225 quotes both legs at
//! exactly 0.0. Every family here is a session family and the flatten always
//! fires, so a charge lands only where a holiday or a half day leaves a position
//! asleep -- and it is counted on the sleeve's OWN clock, or JP225 would pay for
//! a date boundary it never crosses. That last rule is currently worth nothing,
//! because the one market it bites on quotes zero; it was worth a tenth of
//! HK50's contribution while HK50 was here, and JP225 hid it for a fortnight.
//!
//! IT USED TO GO UNPAID ENTIRELY, AND THE FLATTEN IS WHY IT DOES NOT. The old
//! flatten needed a candle to print at or after the configured close, and on a
//! native 30-minute market that candle was usually missing, so a session cell
//! ran on its trailing stop and slept through nights it was never meant to see
//! ([[overnight-financing-is-not-charged-at-all]],
//! [[session-filtered-bars-make-overnight-stops-inert]]).
//!
//! COSTS ARE THE ENGINE'S. Python charges `spread_bp + 0.2` slippage once at
//! entry; the engine bills the same figure through `sleeve_spread_bp`, so
//! nothing here worsens its own fill.
//!
//! THE SESSIONS WERE DERIVED, NOT GUESSED. `ukoil`, `jp225` and `ethusd` are
//! pinned by `exness_families.SESSION`; the rest come from `derive_session`, the
//! shortest contiguous run of 30-minute buckets holding 70% of 2024's volume.
//! Every cell's `last_entry_minute` lands on `{close - 120, close - 60}`, which
//! is what the parameter grid can produce and is the cross-check that these
//! windows are the ones the cells were fitted on.
//!
//! NONE OF THIS IS VALIDATED EDGE ON AN UNTOUCHED WINDOW. The pool was screened
//! on 2025-2026 ([[exness-survivor-pool-is-oos-conditioned]]) and a coin-flip
//! book of the same shape returns +450%
//! ([[combined-book-null-returns-450-percent]]). Read a run of this book against
//! its null, never against zero.

use std::collections::VecDeque;

use crate::backtest::types::{Action, Bar, Side, Strategy};

// -------------------------------------------------------------------------- //
// book constants
// -------------------------------------------------------------------------- //

/// `exness_families.RISK_FRACTION`. The per-trade stop risk a sleeve asks for
/// before any book-level dial is applied.
const RISK_FRACTION: f64 = 0.015;

/// `exness_families.MARGIN_FRACTION`: a self-imposed 4x notional ceiling. It
/// reads REAL equity rather than the scaled or capped figure, because margin is
/// a broker constraint on the account and does not shrink because the book chose
/// to bet less ([[shown-equity-cannot-fix-a-margin-refusal]]).
const MARGIN_FRACTION: f64 = 0.25;

/// `exness_combined_strategies.CANON_RISK_SCALE`, operator decision 2026-09-03.
///
/// CHOSEN ON THE DISTRIBUTION RATHER THAN ON A PATH. 1,000 block-bootstrap paths
/// on live execution put 0.130 at p95 21.66% / median +6,250% over 2022-2026 and
/// p95 19.81% / +708% on the 2025-2026 holdout; 0.190 reads 25.22% and 20.50%.
/// 0.130 is the lowest tail available -- the only setting in that study that
/// puts BOTH windows near 20% and the holdout, the window the account is about
/// to trade, under it.
///
/// THE DIAL WORKS AGAIN AND THAT IS AN EFFECT OF THE SCREEN, NOT OF THE DIAL. On
/// the 26-sleeve predecessor p95 moved 23.63 -> 24.02 across risk 0.080 to
/// 0.150, because minimum-lot-pinned sleeves place the broker minimum whatever
/// the request says ([[oos-drawdown-ignores-the-risk-dial]]). Several of the
/// sleeves the decay screen removed were exactly those, so sizing regained its
/// grip.
///
/// CALIBRATED AFTER MEMBERSHIP WAS FIXED, NEVER JOINTLY WITH IT. A sizing knob
/// inside a selection grid scores every cell at a size the search itself chose
/// and manufactures its own winner ([[usoil-intraday-fails-twice]]).
pub const CANON_RISK_SCALE: f64 = 0.130;

/// `exness_combined_strategies.CANON_GROSS_CAP`, in multiples of equity.
///
/// OFF SINCE 2026-08-25, and switching it off is a risk decision rather than a
/// relaxation. The cap beat a random-refusal control on both windows and was
/// canon at 8x then 6x ([[gross-exposure-cap-beats-its-null]]) -- but at this
/// many sleeves it stops being a risk control and becomes a QUEUE. Every refusal
/// is first-come-first-served, so it allocates the book by trade frequency
/// rather than by quality: at 6.0x this book refused 413 entries. With it off
/// all 413 are taken and the drawdown is held by `CANON_RISK_SCALE` instead.
/// `off` and 20x are identical here, evidence that nothing above ~12x ever
/// binds.
pub const CANON_GROSS_CAP: Option<f64> = None;

/// The balance the book is funded with, RAISED $400 -> $500 by operator decision
/// on 2026-09-03. Canon is sized and reported on this and NOT on
/// `exness_families.INITIAL_BALANCE`: on a small balance the broker's lot floor
/// makes each fill a large share of equity, so a book validated at $1,000 is a
/// different strategy from the same book at $500, and every affordability result
/// measured at $400 had to be re-run rather than re-scaled
/// ([[minimum-lot-de-diversifies-pro-cyclically]],
/// [[cold-start-at-400-is-the-binding-test]]).
///
/// Recorded rather than enforced: the run's own starting balance is the caller's
/// to set, and a book scored on any other figure is not this book.
#[allow(dead_code)]
pub(crate) const CANON_INITIAL: f64 = 500.0;

/// `exness_combined_strategies.FORCE_MINIMUM_LOT`, which the canon run has ON.
///
/// An opt-in execution policy for an operator who requires every positive sizing
/// request to trade: a request that lands under `volume_min` is rounded UP to it
/// rather than refused. The excess risk from that rounding stays in the
/// marked-to-market curve and is not netted out anywhere.
///
/// It is not a rounding convenience. Without it a $400 account drops the trades
/// it cannot afford, and a dropped trade means the backtest and the live account
/// are running different strategies
/// ([[small-balance-hides-drawdown-by-dropping-trades]]). The margin ceiling
/// still refuses outright, because that one is the broker's and not a policy.
const FORCE_MINIMUM_LOT: bool = true;

/// `a_margin_refusal_survives_force_minimum_lot` only tests a MARGIN refusal
/// while the policy above is on; flipping it off would silently turn that test
/// into an assertion about something else, so fail the build instead.
#[allow(clippy::assertions_on_constants, reason = "pinning a policy constant")]
const _: () = assert!(FORCE_MINIMUM_LOT);

/// `exness_families.INITIAL_BALANCE`, and the equity that decides WHICH TRADES
/// EXIST rather than how large they are.
///
/// THE BOOK IS SIZED IN TWO STAGES AND THIS IS THE FIRST. `sleeve_trades` builds
/// each cell's log by running `exness_families.backtest` at this balance with
/// the FAMILY sizing rule -- plain `RISK_FRACTION`, a one-sided volatility
/// throttle, no book scale -- and `quantity` returns 0 for any order that lands
/// under `volume_min`. Such a trade never enters the log at all. The portfolio
/// replay then re-sizes the SURVIVORS against the live shared balance using the
/// book's own rule, which is `FamilyEngine::quantity`.
///
/// THE ADMISSION EQUITY COMPOUNDS. `backtest` starts that standalone run at
/// this balance and books each cell's own P&L into it, so the admission
/// decision late in the window is made against whatever the cell had made by
/// then -- `FamilyEngine` therefore carries a SHADOW ACCOUNT, sized and settled
/// by the standalone rule, purely to answer "does this trade exist". Holding it
/// at a constant $1,000 is close but not the same, and on
/// `jp225:swing_donchian` the difference was three trades and a cascade: a
/// refusal frees the position slot, so the next signal fires where it would
/// otherwise have been blocked.
///
/// So a sleeve's trade universe is fixed by this balance and its trade SIZES are
/// not. Collapsing the two stages into one looks like a simplification and is
/// not: it changed `jp225:swing_donchian` from 52 trades to 70 and its share of
/// the book's P&L from 3.6% to 12.7%. Every sleeve reconciles as
/// `(trades admitted here) - (gross-cap refusals) = trades in the book`, which is
/// what pins this reading of the Python.
///
/// `SHOWN_EQUITY` MULTIPLIES IT, AND THAT IS THE WHOLE POINT OF THE KNOB.
/// `sleeve_trades` passes `initial=ef.INITIAL_BALANCE * shown`, so a sleeve shown
/// 2.5x is admitted against $2,500 rather than $400 -- because the refusal the
/// multiplier exists to lift happens INSIDE `ef.backtest`, where an order under
/// `volume_min` never becomes a trade at all. Applying it only in the replay
/// re-sizes the orders that got through and does nothing about the ones that did
/// not, which is the circular measurement that once reported a 100% fill rate for
/// every sleeve. Reading it as a flat $1,000 here cost `jp225:swing_donchian` 29
/// trades and `jp225:volume_thrust` 14.
///
/// The two imported sleeves are NOT filtered: `external_trades` carries a
/// symbolic `units_per_dollar` and never calls `quantity`, so nothing is dropped
/// before the replay sees it.
const ADMISSION_BALANCE: f64 = 1_000.0;

/// `exness_families.DAILY_RANGE_BARS`: days behind the average true daily range
/// that every stop, target and trail is quoted in.
const DAILY_RANGE_DAYS: usize = 14;

/// `exness_families.SLIPPAGE_BP`, the conservative allowance added to every
/// quoted spread. The engine folds the same 0.2 into `sleeve_spread_bp`.
const SLIPPAGE_BP: f64 = 0.2;

/// Candle size the whole study runs on.
const BAR_SECONDS: i64 = 1_800;

/// `daily_multipliers`, which is `VolTargetConfig::default` written out. Two
/// sided on purpose: the old `min(1, target/realized)` throttle could only ever
/// cut risk, so in a calm regime every sleeve sat at full size and the book's
/// risk was whatever the raw fraction happened to produce.
const VOL_TARGET: f64 = 0.20;
const VOL_HALFLIFE: f64 = 20.0;
const VOL_MAX_MULTIPLIER: f64 = 3.0;
const VOL_MIN_DAYS: u32 = 30;

// -------------------------------------------------------------------------- //
// the members
// -------------------------------------------------------------------------- //

/// One member of the book. The display name is `"<SYMBOL> <Family>"` so
/// `strategies::market_symbol` can route it by its first word, which is the
/// convention that stopped a BTC strategy being stepped with NQ prices.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Sleeve {
    UsdjpyVolumeThrust,
    UsdjpyPullback,
    UsdjpyKendall,
    UsdjpyFracdiff,
    UsdjpyHalfLife,
    AudusdZscore,
    EthusdConfluence,
    EthusdKalman,
    EthusdVolatilityBreakout,
    EthusdObvBreak,
    EthusdPullback,
    /// DROPPED FROM `BOOK` 2026-09-19, KEPT AS CODE, and the two are different
    /// claims. The enum is what this engine can run; `BOOK` is what it is
    /// seated to run. Both cells were profitable over 2022-2026 and left on
    /// CONCENTRATION -- eight of twenty-two sleeves on one index -- so deleting
    /// their implementations would throw away the parity fixtures that hold
    /// `BreakRetest` and the `rr_N` exit path to their Python, and make
    /// re-seating a port rather than a line.
    ///
    /// `Family::BreakRetest` now has no seated cell and is reached only by its
    /// fixture. That is the one place this book carries a family the book does
    /// not select, and it is deliberate rather than an oversight.
    Jp225BreakRetest,
    Jp225VolatilityBreakout,
    GbpjpyTrap,
    UkoilXmaCross,
    UkoilLevelConfluence,
    EurjpyTwoStage,
    EurjpyGatedOrb,
    Jp225VolumeThrust,
    Jp225VolRegime,
    Jp225MomentumStack,
    Jp225Cusum,
    Jp225ObvDivergence,
    Jp225Kalman,
}

/// The canon book, in the order `exness_combined_strategies.BOOK` records it.
///
/// TWENTY-TWO SLEEVES, SET 2026-09-07, on a $500 account at risk 0.130 with the
/// gross cap OFF.
///
/// `hk50:level_confluence` LEFT ON DATA QUALITY, NOT ON ITS RECORD, and the two
/// are worth keeping apart. Removing it changed nothing measurable -- full p95
/// 21.30 -> 21.46, OOS p95 19.69 -> 19.64, full p99 27.94 -> 27.57, returns
/// -1.4% and -2.8%, months and underwater days identical, every move inside the
/// Monte Carlo's own sampling error. What it removes is an UNMEASURED sleeve:
/// `hk50` is the gappiest symbol the book has held, 74 of 1,215 weekdays (6%)
/// carrying no 30-minute bar at all against 0-1% everywhere else, and only 70%
/// of its holdout entries fall on a bar the broker maps can reprice against
/// 99-100% for the rest -- so a third of its trades were scored at the constant
/// spread while the others paid measured costs. It was also the smallest
/// contributor in the book at $14 on 122 trades, 0.4% of P&L and 0.0% of the
/// worst marked fall ([[hk50-stops-for-lunch-inside-its-session]]).
///
/// NQ IS BARRED AS A SYMBOL, operator decision 2026-09-03, and that is what
/// makes this a different book rather than a revision of the 29-08 one. Five NQ
/// sleeves went at once, and with them both hand-written imports and the
/// level-two feed -- so the engine that runs the book is now one machine instead
/// of three. The bar was re-tested on 2026-09-04 and stands.
///
/// SEVEN MORE WENT TO A DECAY SCREEN, 2026-09-03, measured in R-MULTIPLES rather
/// than in dollars: `gross / distance` is the trade's outcome in units of the
/// risk it took, which is the same quantity on any balance and in any year,
/// unlike a dollar contribution inside a compounding book. `gbpusd:obv_break`
/// scored a 2026 mean R of +0.006; `ethusd:idio_break` -4.1R over 49 trades.
///
/// THE REFILL WAS TRIED AND REJECTED, AND THAT IS THE LESSON. A greedy forward
/// selection put seven fresh cells into the freed slots and looked excellent on
/// the realised path -- +19,274% at 15.6% marked drawdown. Its Monte Carlo p95
/// was 35.13%. The realised ordering was a lucky draw and the greedy could not
/// see it, because it was guarded on REALISED drawdown instead of on the
/// distribution ([[canon-drawdown-is-sequence-risk]],
/// [[refill-guarded-on-one-path-hides-its-tail]]). The screen alone beats every
/// refill of it on the tail by 6 to 22 points.
///
/// THREE CELLS WERE SEATED ON THE HOLDOUT, 2026-09-04, not on the long window.
/// `es:regime_breakout` was seated with them and removed the same day on a DATA
/// DEPENDENCY rather than on its record: it signals off `es_1m`, the
/// back-adjusted futures continuum, and the operator does not carry that feed
/// ([[one-sleeve-cannot-pay-for-a-data-feed]]). It was the best holdout-tail
/// sleeve measured that session and nothing replaces it; `ethusd:pullback` and
/// `hk50:level_confluence` were taken together to recover the RETURN and a
/// positive month, and they give back tail.
///
/// JP225 IS 8 OF 23, which is the concentration to watch. The eight are not one
/// rule counted eight times -- a CUSUM event sampler, an OBV divergence, a
/// Kalman slope, a volatility-regime momentum, a three-horizon stack, a
/// range-fraction breakout, a break-and-retest and a volume thrust all fire on
/// different bars -- but they share one session, one clock and one gap.
pub const BOOK: [Sleeve; 22] = [
    Sleeve::UsdjpyVolumeThrust,
    Sleeve::AudusdZscore,
    Sleeve::EthusdConfluence,
    Sleeve::EthusdVolatilityBreakout,
    Sleeve::GbpjpyTrap,
    Sleeve::UkoilXmaCross,
    Sleeve::EurjpyTwoStage,
    Sleeve::UsdjpyPullback,
    Sleeve::EthusdObvBreak,
    Sleeve::Jp225VolumeThrust,
    Sleeve::UkoilLevelConfluence,
    Sleeve::Jp225VolRegime,
    Sleeve::Jp225MomentumStack,
    Sleeve::UsdjpyKendall,
    Sleeve::Jp225Cusum,
    Sleeve::Jp225ObvDivergence,
    Sleeve::Jp225Kalman,
    Sleeve::EurjpyGatedOrb,
    Sleeve::UsdjpyFracdiff,
    Sleeve::EthusdPullback,
    Sleeve::EthusdKalman,
    Sleeve::UsdjpyHalfLife,
];

/// The book's own name, for the one place it is a THING rather than a list.
///
/// Twenty-two sleeves is how the book is built and how it is routed -- each
/// trades its own market and each needs its own live row, because the per-row
/// symbol check is what stops a sleeve being sent to the wrong instrument. But
/// it is ONE selection, sealed on one day against one data snapshot, and a saved
/// run of it is a run of the book rather than of twenty-two things that happened
/// to be ticked together. Stored under this id, a run stays findable when the
/// membership changes; stored under a twenty-two-name join it does not.
///
/// The id matches the directory, so the record and the code that produced it
/// carry the same name.
pub const BOOK_ID: &str = "exness_combined_07-09-2026";

/// The same book, spelled for a person.
pub const BOOK_NAME: &str = "Exness Combined 07-09-2026";

/// Whether `names` is the WHOLE book, in any order and with no strays.
///
/// Order-insensitive on purpose: a caller that ticks every sleeve in the UI has
/// selected the book whatever sequence the checkboxes came back in, and a run
/// that is the book should be recorded as the book. Anything short of all
/// twenty-two is a research selection and keeps its own composite name -- it is
/// NOT this book, and filing it under this book's id would make the record a
/// lie.
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

/// The book a live row belongs to, DERIVED from the strategy it names.
///
/// Never asked of the caller. `create_account_strategy` is reached from a form
/// where the strategy is typed, so a book taken from the request would be a
/// second free-text field to get wrong -- and a row claiming to be part of this
/// book while naming something else is worse than no label at all.
///
/// An id the book does not carry returns the empty string, which is what a row
/// written before books existed also holds: "no book recorded", not "belongs to
/// the current one".
pub fn book_of(strategy_id: &str) -> &'static str {
    match Sleeve::from_id(strategy_id) {
        Some(_) => BOOK_ID,
        None => "",
    }
}

impl Sleeve {
    /// Everything this sleeve is, in one place: `sleeves/<id>.rs`.
    fn spec(self) -> &'static SleeveSpec {
        sleeves::spec(self)
    }

    pub fn display(self) -> &'static str {
        self.spec().display
    }

    pub fn id(self) -> &'static str {
        self.spec().id
    }

    pub fn code(self) -> &'static str {
        self.spec().code
    }

    pub fn python_key(self) -> &'static str {
        self.spec().python_key
    }

    pub fn market(self) -> &'static str {
        self.spec().market
    }

    pub fn from_display(name: &str) -> Option<Self> {
        BOOK.into_iter().find(|sleeve| sleeve.display() == name)
    }

    pub fn from_id(id: &str) -> Option<Self> {
        BOOK.into_iter().find(|sleeve| sleeve.id() == id)
    }

    fn scale(self) -> f64 {
        self.spec().scale
    }

    fn shown_equity(self) -> f64 {
        self.spec().shown_equity
    }

    /// Hours `exness_families.all_bars` adds to this market's timestamps as it
    /// loads them, before anything else sees them.
    ///
    /// The Asian cash sessions straddle New York midnight, so without the shift
    /// one session lands in two calendar days and every prior-day anchor breaks.
    /// `FamilyEngine` applies it internally; this exposes it to the merged
    /// stream, which has to ORDER a shifted market against unshifted ones the
    /// way Python's replay does.
    pub fn shift_hours(self) -> i64 {
        self.spec().contract.shift_hours
    }

    /// `(open, close)` in minutes past New York midnight on the SHIFTED clock,
    /// inclusive of both ends -- the window `exness_families.in_session` keeps.
    pub fn session(self) -> (i64, i64) {
        self.spec().contract.session
    }

    /// Whether a timestamp falls in this sleeve's session, shift applied.
    ///
    /// `exness_families.in_session`: `opened <= ts % 86400 // 60 <= closed`,
    /// against bars `all_bars` has ALREADY shifted. Rust loads unshifted, so the
    /// shift is applied here.
    pub fn in_session(self, ts: i64) -> bool {
        let (open, close) = self.session();
        let minute = (ts + self.shift_hours() * 3_600).rem_euclid(86_400) / 60;
        (open..=close).contains(&minute)
    }

    /// One night of carry on this sleeve, in PRICE units per lot, always >= 0.
    ///
    /// CHARGED ON EVERY MEMBER, because every member is an `ef.backtest` cell
    /// and that function bills `nights * financing_price(...)`. The exemption
    /// this used to carry was for the two imports, whose Python path bills only
    /// `entry_cost` and never looks at swap; both left the book on 2026-09-03.
    ///
    /// It is nearly always zero anyway: a session cell spans no date boundary,
    /// so the charge lands on holidays and half days alone.
    pub fn financing_per_night(self, long: bool) -> f64 {
        let contract = self.spec().contract;
        if long {
            contract.financing_long
        } else {
            contract.financing_short
        }
    }

    /// Whether this sleeve builds its own THIRTY-MINUTE CANDLES out of the bars
    /// it is fed, and so cannot act on one until the next slot opens.
    ///
    /// EVERY MEMBER DOES, since the book lost the two imports that read the bar
    /// they were given and acted on it. The distinction is not cosmetic to a
    /// combined run: an aggregating sleeve emits candle S's exit and fill on the
    /// FIRST BAR OF SLOT S+1, so half an hour of the rest of the book lands
    /// between the decision and its application, and the shared balance it sizes
    /// against has moved. `execute_combined` offers these streams half an hour
    /// early to put them back where Python acts.
    pub fn aggregates_candles(self) -> bool {
        self.params().is_some()
    }

    fn sized_as_import(self) -> bool {
        self.spec().sized_as_import
    }

    pub(super) fn contract(self) -> Instrument {
        self.spec().contract
    }

    /// The broker's `volume_min` for this sleeve's market, in MT5 lots.
    ///
    /// THE LIVE RUNTIME MUST ASK THIS RATHER THAN KEEP A TABLE OF ITS OWN. The
    /// floor is not 0.01 everywhere -- NQ is 0.05, ETHUSD 0.10, and JP225
    /// refuses anything under THREE whole lots -- and
    /// `mt5_account_strategies.symbol` is free text a person types into a form.
    /// A runtime that keyed its own copy of this table on that string resolved
    /// `"JP225"` and `"ETHUSD"` to no entry at all and fell through to a 0.05
    /// default, which both admitted orders JP225 rejects and refused ETHUSD
    /// orders the broker would have taken. Reading the frozen spec the sleeve
    /// already sizes against is what makes the runtime and the strategy agree
    /// on what is placeable.
    pub fn broker_minimum_lots(self) -> f64 {
        self.contract().volume_min
    }

    /// Units of the base asset in one lot of this sleeve's market.
    pub fn contract_size(self) -> f64 {
        self.contract().contract_size
    }

    /// Account-currency value of one full price unit per lot.
    ///
    /// The engine keeps its own copy in `costs::market_point_value`, because it
    /// prices runs that are not this book. They must agree, and
    /// `frozen_point_values_match_the_strategy_specs` is what keeps them equal:
    /// that map falls through to 1.0, so a market seated without a line in it is
    /// not a build error but a silently wrong P&L.
    pub fn point_value(self) -> f64 {
        self.contract().multiplier
    }

    fn params(self) -> Option<Params> {
        self.spec().params()
    }
}

/// One completed 30-minute candle on the sleeve's SHIFTED clock.
///
/// Shared by the modules below it -- `family` builds them and `indicators` folds
/// them -- so it lives here rather than inside either.
#[derive(Clone, Copy)]
struct Candle {
    ts: i64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

mod contracts;
mod family;
mod indicators;
mod sleeves;
pub mod warmup;

use contracts::Instrument;
use family::*;
use indicators::*;
use sleeves::SleeveSpec;

#[cfg(test)]
mod parity;
#[cfg(test)]
mod tests;

// -------------------------------------------------------------------------- //
// the strategy the engine builds
// -------------------------------------------------------------------------- //

/// One member of the 2026-09-07 Exness book.
///
/// A combined run builds all twenty-two and steps each with its own market's
/// bars; the engine's shared balance is what couples them, which is the whole
/// reason this is not an addition of return streams.
///
/// A THIN WRAPPER SINCE 2026-09-04, AND IT IS KEPT ANYWAY. Until then it chose
/// between three engines and carried a book-level multiplier, a lot floor and a
/// level-two rebuild for the two that sized themselves. Both were NQ cells and
/// left with the symbol, so every member now runs `FamilyEngine`, which applies
/// the book's dials inside its own `size`. What survives here is the TYPE the
/// engine builds and the `Strategy` surface it is reached through -- deleting
/// that would push the sleeve type into the backtest engine, which is the
/// coupling this file exists to avoid.
pub struct ExnessCombined {
    sleeve: Sleeve,
    engine: Box<FamilyEngine>,
}

impl ExnessCombined {
    pub fn new(sleeve: Sleeve, quantity_step: f64) -> Self {
        Self {
            sleeve,
            engine: Box::new(FamilyEngine::new(sleeve, quantity_step)),
        }
    }

    #[allow(dead_code)]
    pub(crate) fn sleeve(&self) -> Sleeve {
        self.sleeve
    }

    /// Steps the sleeve on one bar, for callers outside the crate.
    ///
    /// `Strategy` is crate-private -- nothing outside the engine should be
    /// deciding what a fill means -- but `exness_sleeve_probe` has to drive a
    /// sleeve by hand to establish which layer a wrong number came from, and
    /// that is worth one delegating method.
    pub fn step(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.update_all(bar, equity)
    }
}

impl Strategy for ExnessCombined {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        self.engine.update(bar, equity)
    }

    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.engine.update_all(bar, equity)
    }

    /// FALSE FOR EVERY MEMBER, and the override is kept because the answer is a
    /// property of the sleeve rather than a constant.
    ///
    /// It was true for the two imports, whose Python path reads `trade["step"]`
    /// and never consults `volume_min` at all -- so on NQ it filled 0.01-lot
    /// orders against a broker floor of 0.05. That is a gap in the Python model
    /// rather than a rule worth having, and nothing in this book reaches it now.
    fn sizes_below_broker_minimum(&self) -> bool {
        self.sleeve.sized_as_import()
    }

    fn resize_entry(&self, price: f64, quantity: f64, shown: f64, actual: f64) -> f64 {
        self.engine.resize_entry(price, quantity, shown, actual)
    }

    fn discard(&mut self, action: Action) {
        self.engine.discard(action);
    }

    fn discard_all(&mut self, actions: Vec<Action>) {
        self.engine.discard_all(actions);
    }

    fn action_timestamp(&self, default: i64) -> i64 {
        self.engine.action_timestamp(default)
    }

    fn exit_timestamp(&self, default: i64) -> i64 {
        self.engine.exit_timestamp(default)
    }

    fn reset_trading_state(&mut self) {
        self.engine.reset_trading_state();
    }

    fn abandon_open_position(&mut self) {
        self.engine.abandon_open_position();
    }

    fn session_end_minute(&self) -> Option<usize> {
        self.engine.session_end_minute()
    }

    fn session_start_minute(&self) -> Option<usize> {
        self.engine.session_start_minute()
    }

    fn flattens_itself(&self) -> bool {
        self.engine.flattens_itself()
    }

    fn entry_risk_fraction(&self) -> Option<f64> {
        self.engine.entry_risk_fraction()
    }

    fn entry_stop_price(&self) -> Option<f64> {
        self.engine.entry_stop_price()
    }
}
