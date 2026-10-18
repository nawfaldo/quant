//! The 22-sleeve Exness book, ported from
//! `py_sandbox/research/exness_combined_strategies.py` as sealed on 2026-09-07
//! and re-seated on 2026-09-19, when two JP225 sleeves were exchanged for
//! `ethusd:kalman` and `usdjpy:half_life`. `BOOK_ID` deliberately does not
//! move with membership -- see the note on it below.
//!
//! ONE ACCOUNT, TWENTY-TWO SLEEVES, SEVEN MARKETS. Each sleeve is an
//! `cfd_families` cell whose parameters were fitted on data ending
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
//! of `cfd_families.backtest`: 30-minute candles built from the `<symbol>_1m`
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
//! SIZING IS THE BOOK'S, NOT THE FAMILY'S. `cfd_families.quantity` throttles
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
//! pinned by `cfd_families.SESSION`; the rest come from `derive_session`, the
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


use crate::strategies::cfd::*;
#[cfg(test)]
use crate::backtest::types::{Action, Bar, Side, Strategy};

// -------------------------------------------------------------------------- //
// book constants
// -------------------------------------------------------------------------- //

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
pub const CANON_RISK_SCALE: f64 = 0.100;

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
/// `cfd_families.INITIAL_BALANCE`: on a small balance the broker's lot floor
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

/// TWENTY-FIVE SINCE 2026-10-12, the canon `exness_combined_strategies.BOOK`
/// run at `CANON_RISK_SCALE` 0.10: fifteen members of the 09-30 book, plus the
/// TikTok cells yf_alphatrend@30m, mql_big_move_cont@60m, qc_ema_cross@30m and
/// dt_rsi_dip_vol@15m, the bar-lookback cells adaptive_pullback@60m and
/// failed_break@5m, three OFF-HOURS cells (momentum_stack and sar on USDJPY,
/// qp_rsi2_pullback on GBPUSD) and the MULTI-DAY ethusd:sb_bollinger@240m.
/// GBPUSD is a new market. Left: ethusd break_retest, lux_body_momentum@15m;
/// usdjpy half_life, rvol, qp_ma_cross@30m, lux_htf_manipulation@60m;
/// de40 lux_sweep_reclaim@60m; eurjpy lux_ny_vwap_pullback@15m -- kept as code.

/// The canon book, in the order `exness_combined_strategies.BOOK` records it.
///
/// TWENTY-ONE SINCE 2026-09-23: every jp225 cell and `ukoil:xma_cross` left;
/// `usdjpy:aroon`, `ethusd:break_retest`, `eurjpy:swing_ma`,
/// `ethusd:roofing`, `ethusd:level_confluence` and `usdjpy:rvol` joined. The
/// departed variants stay in `Sleeve` as code, with their fixtures, per the
/// precedent below. The history that follows describes earlier books.
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
/// TT27, SET 2026-09-28: the 19-sleeve research canon (the three weekend-only
/// ETHUSD cells removed) plus eight TikTok-creator cells, chosen by a greedy
/// add over 2022-26, 2022-24, 2025-26 and 2026 and checked on the full canon
/// report. Two new markets, USTEC and US500, arrive with them.
///
/// TWENTY-THREE SINCE 2026-09-30, the canon `exness_combined_strategies.BOOK`
/// ("Combo C with btc:jump replacing usdjpy:kendall"): 15 `cfd_families` cells
/// and 8 TikTok cells, in its order. Twelve TT27 members left (eurjpy
/// two_stage, gated_orb; usdjpy aroon, fracdiff; ethusd pullback, kalman,
/// roofing, level_confluence, td_ema_macd@15m; us500 swing_sweep_mss@5m;
/// gbpjpy qp_ma_cross@15m, lux_htf_manipulation@15m) and stay as code with
/// their fixtures. Eleven markets: DE40, UK100, USOIL and BTC arrive, US500
/// leaves.
pub const BOOK: [Sleeve; 25] = [
    Sleeve::UsdjpyVolumeThrust,
    Sleeve::AudusdZscore,
    Sleeve::EthusdConfluence,
    Sleeve::EthusdVolatilityBreakout,
    Sleeve::GbpjpyTrap,
    Sleeve::UsdjpyPullback,
    Sleeve::EthusdObvBreak,
    Sleeve::UkoilLevelConfluence,
    Sleeve::UstecLuxBodyMomentum5m,
    Sleeve::EurjpyQpMaCross60m,
    Sleeve::GbpjpyLuxalgoManipulation120m,
    Sleeve::GbpjpyLuxSupplyDemand30m,
    Sleeve::Uk100XmaCross,
    Sleeve::UsoilXmaCross,
    Sleeve::BtcJump,
    Sleeve::UsdjpyYfAlphatrend30m,
    Sleeve::GbpjpyMqlBigMoveCont60m,
    Sleeve::UsdjpyAdaptivePullback60mBarlb,
    Sleeve::UsdjpyFailedBreak5mBarlb,
    Sleeve::UsdjpyMomentumStack30mBarlbOff,
    Sleeve::UsdjpySar30mBarlbOff,
    Sleeve::GbpusdQpRsi2Pullback30mOff,
    Sleeve::EurjpyQcEmaCross30m,
    Sleeve::EurjpyDtRsiDipVol15m,
    Sleeve::EthusdSbBollinger240mNat,
];

/// EVERY SLEEVE THIS ENGINE CAN RUN, members and retired cells alike.
///
/// The live runtime and the API never see it: `registry` answers `BOOK` unless
/// a research tool has called `open_retired_for_research`. It exists so a past
/// live window -- traded by an earlier membership -- can be backtested with the
/// sleeves that actually ran then (`tools/compare_live_backtest.py`).
pub const ALL: [Sleeve; 59] = [
    Sleeve::UsdjpyVolumeThrust,
    Sleeve::UsdjpyPullback,
    Sleeve::UsdjpyKendall,
    Sleeve::UsdjpyFracdiff,
    Sleeve::UsdjpyHalfLife,
    Sleeve::AudusdZscore,
    Sleeve::EthusdConfluence,
    Sleeve::EthusdKalman,
    Sleeve::EthusdVolatilityBreakout,
    Sleeve::EthusdObvBreak,
    Sleeve::EthusdPullback,
    Sleeve::Jp225BreakRetest,
    Sleeve::Jp225VolatilityBreakout,
    Sleeve::GbpjpyTrap,
    Sleeve::UkoilXmaCross,
    Sleeve::UkoilLevelConfluence,
    Sleeve::EurjpyTwoStage,
    Sleeve::EurjpyGatedOrb,
    Sleeve::Jp225VolumeThrust,
    Sleeve::Jp225VolRegime,
    Sleeve::Jp225MomentumStack,
    Sleeve::Jp225Cusum,
    Sleeve::Jp225ObvDivergence,
    Sleeve::Jp225Kalman,
    Sleeve::UsdjpyAroon,
    Sleeve::EthusdBreakRetest,
    Sleeve::EurjpySwingMa,
    Sleeve::EthusdRoofing,
    Sleeve::EthusdLevelConfluence,
    Sleeve::UsdjpyRvol,
    Sleeve::EthusdEfficiency,
    Sleeve::EthusdCci,
    Sleeve::EthusdLinregTrend,
    Sleeve::UstecLuxBodyMomentum5m,
    Sleeve::UsdjpyQpMaCross30m,
    Sleeve::Us500LuxSwingSweepMss5m,
    Sleeve::GbpjpyQpMaCross15m,
    Sleeve::EurjpyQpMaCross60m,
    Sleeve::EthusdTdEmaMacd15m,
    Sleeve::GbpjpyLuxalgoManipulation120m,
    Sleeve::GbpjpyLuxHtfManipulation15m,
    Sleeve::EthusdLuxBodyMomentum15m,
    Sleeve::GbpjpyLuxSupplyDemand30m,
    Sleeve::De40LuxSweepReclaim60m,
    Sleeve::UsdjpyLuxHtfManipulation60m,
    Sleeve::EurjpyLuxNyVwapPullback15m,
    Sleeve::Uk100XmaCross,
    Sleeve::UsoilXmaCross,
    Sleeve::BtcJump,
    Sleeve::UsdjpyYfAlphatrend30m,
    Sleeve::GbpjpyMqlBigMoveCont60m,
    Sleeve::EurjpyQcEmaCross30m,
    Sleeve::EurjpyDtRsiDipVol15m,
    Sleeve::UsdjpyAdaptivePullback60mBarlb,
    Sleeve::UsdjpyFailedBreak5mBarlb,
    Sleeve::UsdjpyMomentumStack30mBarlbOff,
    Sleeve::UsdjpySar30mBarlbOff,
    Sleeve::GbpusdQpRsi2Pullback30mOff,
    Sleeve::EthusdSbBollinger240mNat,
];

/// Set once, by a research binary, never by the server.
static RETIRED_OPEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Lets this PROCESS resolve and run retired sleeves. Research binaries only:
/// the live runtime must keep refusing a retired id, or a stale live row could
/// trade a sleeve the book dropped.
pub fn open_retired_for_research() {
    RETIRED_OPEN.store(true, std::sync::atomic::Ordering::SeqCst);
}

/// The sleeves names resolve against: the book, or every sleeve once a
/// research tool has opened the retired ones.
pub fn registry() -> &'static [Sleeve] {
    if RETIRED_OPEN.load(std::sync::atomic::Ordering::SeqCst) {
        &ALL
    } else {
        &BOOK
    }
}

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

pub(crate) mod contracts;
pub(crate) mod sleeves;

#[cfg(test)]
pub(crate) mod parity;
#[cfg(test)]
mod tests;
