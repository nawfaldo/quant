//! How much history a sleeve must be replayed over before its first traded bar.
//!
//! ASK THE SLEEVE, DO NOT PICK A NUMBER. Both callers used to carry a constant
//! of their own: the backtest loaded a flat 400 calendar days for every market
//! in a book run, and the live runtime warmed a flat 100 sessions. Neither was
//! derived from anything and they did not agree with each other.
//!
//! A CALENDAR CONSTANT CANNOT BE RIGHT FOR MORE THAN ONE MARKET, which is the
//! specific failure the 400 had. The deepest requirement in the book is the
//! sizing throttle, an EWMA over DAYS THE MARKET PRINTED, so 400 calendar days
//! bought 400 observations of it on ETHUSD's seven-day week and about 286 on
//! every weekday market. One number therefore over-warmed the single market that
//! needed least and under-warmed the six that needed most, while reading as
//! uniform. Measured: at a balance where sizes are not pinned to the minimum
//! lot, a book run under the flat 400 does NOT match the same run given
//! unlimited warm-up, and one under this rule does.
//!
//! WHAT THE NUMBER IS MADE OF. Three kinds of requirement, taken at their
//! maximum:
//!
//! - EXACT WINDOWS refuse to answer until they are full, so they need exactly
//!   their own period: `MeanSigma`, `RollingMean`, `RollingMinInclusive`,
//!   `RollingExtreme` read through a channel, `MannKendall`, `CloseHistory`.
//!   Short of the window the cell does not trade a different size, it does not
//!   trade at all -- which is the failure that matters, because a `calm` cell
//!   sitting refused while a `none` cell trades is a DIFFERENT BOOK from the one
//!   that was measured.
//! - RECURSIVE FILTERS never refuse; they answer from their first observation
//!   and converge. `Ema` is seeded at its first value, `Tema` chains three of
//!   those, `WilderRsi` smooths at `1/period`, and the sizing throttle's EWMA
//!   has a 20-day half-life. For these the requirement is a CONVERGENCE budget
//!   rather than a window -- see `EFOLDS`.
//! - DAILY state is counted in days rather than candles: the 14-day average
//!   daily range, and the 30 days the volatility throttle needs before it stops
//!   answering 1.0.
//!
//! The answer is in SESSIONS, which is what both callers want: `calendar_days`
//! turns it into the wall-clock span to load, and only it knows that ETHUSD
//! prints every calendar day while every other market in the book keeps
//! exchange hours.

use super::family::{Coil, Family, Gate, Params, Trend, VolMode};
use super::{DAILY_RANGE_DAYS, Sleeve, VOL_HALFLIFE, VOL_MIN_DAYS};

/// How many e-folds of decay a recursive filter is given before its seed is
/// treated as forgotten.
///
/// A filter with smoothing constant `alpha` retains about `e^-(alpha * m)` of
/// its seed after `m` observations, so `m = EFOLDS / alpha` bounds the residual
/// at `e^-EFOLDS`. Twelve puts it at 6e-6 relative, which is well under both the
/// 0.01-lot step every size is rounded to and the price tick every trend gate is
/// compared against.
///
/// IT IS NOT A SAFETY FACTOR TO BE RAISED WHEN A NUMBER LOOKS WRONG, and the
/// measurement says raising it buys nothing: the whole book's output at this
/// setting is byte-identical to the same runs given a flat 800 and a flat 1,000
/// calendar days, over windows from five months to four years and at balances
/// both pinned and unpinned to the minimum lot. Lowering it is the speed dial
/// and it costs correctness -- a flat 400 differs from all three.
const EFOLDS: f64 = 12.0;

/// Observations needed for an `Ema` of `period` to forget its seed.
///
/// `Ema::new` uses `alpha = 2 / (period + 1)`, so this is `EFOLDS * (period + 1)
/// / 2`.
fn ema_convergence(period: usize) -> usize {
    (EFOLDS * (period as f64 + 1.0) / 2.0).ceil() as usize
}

/// Observations needed for a Wilder smoother of `period`, whose `alpha` is
/// `1 / period`, plus the `period` bars it spends seeding before it answers at
/// all.
fn wilder_convergence(period: usize) -> usize {
    period + (EFOLDS * period as f64).ceil() as usize
}

/// Candles this cell's own family state needs before it answers.
///
/// Only the block `arm_family_state` actually builds for the family is counted;
/// a cell pays for the windows it reads and no others.
fn family_candles(family: Family, session_bars: usize) -> usize {
    match family {
        // `RollingMean` over raw volume.
        Family::VolumeThrust { volume_period, .. } => volume_period,
        // `MeanSigma` over closes.
        Family::Zscore { period, .. } => period,
        // Reads today's range against the daily risk alone.
        Family::VolatilityBreakout { .. } => 0,
        Family::Confluence {
            zscore_period,
            rsi_period,
            ..
        } => zscore_period.max(wilder_convergence(rsi_period)),
        // The channel, plus the ring of past readings the trap walks back over.
        Family::Trap { window, channel } => channel + window,
        // OBV is cumulative and its LEVEL is never read -- only extremes of it
        // are, and a different starting point shifts every extreme by the same
        // constant -- so the channel is the whole requirement.
        Family::ObvBreak { channel } => channel,
        // `Tema` refuses for `3 * period` and chains three seeded EMAs behind
        // that, so it needs the guard AND the convergence of the chain.
        Family::XmaCross { fast, slow } => {
            let deepest = fast.max(slow);
            (3 * deepest).max(ema_convergence(deepest))
        }
        Family::Pullback {
            channel, reference, ..
        } => channel.max(ema_convergence(reference)),
        // The opening range is today's own first candle. A gated cell adds its
        // confirmer; the VWAP gate is built from the session path every cell
        // already carries.
        Family::GatedOrb {
            gate, gate_period, ..
        } => match gate {
            Gate::Rsi => wilder_convergence(gate_period),
            Gate::Vwap => 0,
        },
        // `window + 1` matches `arm_family_state`: the arming scan and the range
        // scan together read one bar more than `window`.
        Family::TwoStage {
            coil,
            window,
            compress,
            width_period,
        } => {
            let coil_bars = match coil {
                Coil::Squeeze => width_period.max(compress),
                Coil::Nr => compress,
            };
            coil_bars + window + 1
        }
        Family::BreakRetest {
            window, channel, ..
        } => channel + window,
        // Pivots and yesterday's range are daily state, counted in `daily_days`.
        Family::LevelConfluence { .. } => 0,
        // The two volatility readings are shared and counted below; this is the
        // horizon the DIRECTION is read over.
        Family::VolRegime { lookback, .. } => lookback,
        // Three spans at `1x`, `5x` and `20x` the session period, each
        // standardised over TWICE its own span.
        Family::MomentumStack { session, .. } => 40 * session,
        Family::Kendall { period, .. } => period,
        // Stateful and self-resetting: it accumulates from wherever it starts
        // and its threshold is the shared ATR, which is counted below.
        Family::Cusum { .. } => 0,
        Family::ObvDivergence { channel } => channel,
        // The filter's covariance converges from its own first observation.
        // There is no period to read it off, so it is given the same budget as
        // a one-session EMA -- the fastest thing in the vocabulary, which is
        // what a state-space gain adapts like.
        Family::Kalman { .. } => ema_convergence(session_bars),
        // `FracDiff::WIDTH` is the longest weight vector the power law can
        // produce before the threshold truncates it, so 64 bounds the memory.
        Family::Fracdiff { window, .. } => 64 + window,
        // TWO WINDOWS IN SERIES, AND THE SECOND IS NOT `period`.
        //
        // The OU fit answers nothing until `period + 1` bars have passed, which
        // is the first requirement. The z-score it then asks for is
        // `round(life * multiple)` bars wide, and `life` is capped at `period`
        // by the fit itself -- so the deepest window this cell can ever request
        // is `multiple * period`, and asking for `period` alone would leave it
        // reading a half-warm mean on exactly the slow-reverting stretches the
        // family exists to trade.
        //
        // Counted at the CEILING rather than at the typical life, because
        // warm-up is a guarantee and a typical value is not one.
        Family::HalfLife {
            period, multiple, ..
        } => (period + 1).max((multiple * period as f64).ceil() as usize),
    }
}

/// Candles of shared state every cell carries, whatever its family.
fn shared_candles(params: Params, session_bars: usize) -> usize {
    let atr = 2 * session_bars;
    let short_volatility = 20 * session_bars;
    // The `calm` gate is the only reader of the 100-session figure, and it
    // refuses outright until the window is full. A `none` cell never asks.
    let long_volatility = match params.vol_mode {
        VolMode::Calm => 100 * session_bars,
        VolMode::Any => 0,
    };
    let trend = match params.trend {
        Trend::None => 0,
        Trend::Ema20d => ema_convergence(20 * session_bars),
        Trend::Ema50d => ema_convergence(50 * session_bars),
    };
    atr.max(short_volatility).max(long_volatility).max(trend)
}

/// Whole DAYS of state, which no candle count can express.
fn daily_days() -> usize {
    // `DailyRisk` publishes only once it holds a full window of COMPLETED days,
    // so it needs one more day than the window to have a reading on the first
    // traded bar.
    let daily_risk = DAILY_RANGE_DAYS + 1;
    // The throttle answers a flat 1.0 for its first `VOL_MIN_DAYS`, then an
    // EWMA whose seed decays by half every `VOL_HALFLIFE` days. `EFOLDS` of
    // decay is `EFOLDS / ln 2` half-lives.
    let throttle =
        VOL_MIN_DAYS as usize + (EFOLDS / std::f64::consts::LN_2 * VOL_HALFLIFE).ceil() as usize;
    daily_risk.max(throttle)
}

impl Sleeve {
    /// Completed SESSIONS this sleeve must be replayed over before its first
    /// traded bar, so that every indicator, filter and sizing input it reads
    /// answers the same value it would after unlimited history.
    pub fn warmup_sessions(self) -> i64 {
        let Some(params) = self.params() else {
            // An imported sleeve carries its own engine and this rule cannot
            // speak for it. One session is the honest floor rather than a guess
            // at its state; a seated import must answer here itself.
            return 1;
        };
        let session_bars = self.contract().per_session;
        let candles =
            shared_candles(params, session_bars).max(family_candles(params.family, session_bars));
        // Candles round UP to a whole session: a requirement of one candle past
        // a session boundary still needs that whole extra session loaded.
        let sessions = candles.div_ceil(session_bars.max(1));
        sessions.max(daily_days()) as i64
    }

    /// Calendar days of history to load so that `warmup_sessions` of this
    /// sleeve's own market fit inside them.
    ///
    /// THE CALENDAR IS PER MARKET, which is the whole reason a single constant
    /// could not be right for the book. ETHUSD prints every day of the week, so
    /// its sessions and its calendar days are very nearly the same count; every
    /// other market in the book keeps exchange hours, so the same number of
    /// sessions spans about 1.4 times as many days, plus room for holidays.
    pub fn warmup_calendar_days(self) -> i64 {
        calendar_days(self.market(), self.warmup_sessions())
    }
}

/// Calendar days spanning `sessions` trading days on `market`.
pub fn calendar_days(market: &str, sessions: i64) -> i64 {
    if market == "ethusd" {
        // Crypto quotes every calendar day, which is also why its realised
        // volatility annualises over 365 rather than 252.
        sessions + 2
    } else {
        // Weekday markets need room for weekends and exchange holidays.
        (sessions * 7 + 4).div_euclid(5) + 7
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategies::idk::exness_combined::BOOK;

    /// The rule must never answer less than the shared state every cell reads,
    /// whatever its family: a cell warmed for its own channel alone would size
    /// its first trades off a throttle still answering 1.0.
    #[test]
    fn every_sleeve_clears_the_shared_daily_state() {
        let floor = daily_days() as i64;
        for sleeve in BOOK {
            assert!(
                sleeve.warmup_sessions() >= floor,
                "{} warms for {} sessions, under the {floor}-day shared floor",
                sleeve.id(),
                sleeve.warmup_sessions()
            );
        }
    }

    /// The `calm` cells are the deepest exact window in the book, and the rule
    /// has to reach it or they sit refused into the report window.
    #[test]
    fn a_calm_cell_gets_its_hundred_sessions() {
        let calm: Vec<Sleeve> = BOOK
            .into_iter()
            .filter(|sleeve| {
                sleeve
                    .params()
                    .is_some_and(|params| matches!(params.vol_mode, VolMode::Calm))
            })
            .collect();
        assert!(!calm.is_empty(), "the book has calm cells to test");
        for sleeve in calm {
            assert!(
                sleeve.warmup_sessions() >= 100,
                "{} is a calm cell warmed for only {} sessions",
                sleeve.id(),
                sleeve.warmup_sessions()
            );
        }
    }

    /// ETHUSD's week has no weekend, so the same session count must not be
    /// inflated for it the way a weekday market's is.
    #[test]
    fn the_crypto_calendar_is_not_padded_for_weekends() {
        assert_eq!(calendar_days("ethusd", 100), 102);
        assert_eq!(calendar_days("usdjpy", 100), 147);
    }

    /// WHAT THE FLAT 400 GOT WRONG, pinned as a number rather than left as a
    /// claim in a comment.
    ///
    /// The deepest requirement in the book is the sizing throttle, and it counts
    /// DAYS THE MARKET PRINTED. 400 calendar days is 400 of those on ETHUSD's
    /// seven-day week and about 286 on a weekday market, so the one constant was
    /// simultaneously generous to the market that needed least and short for the
    /// six that needed most. The derived rule inverts both.
    #[test]
    fn the_flat_four_hundred_was_long_for_crypto_and_short_for_the_rest() {
        let ethusd = Sleeve::EthusdConfluence;
        assert_eq!(ethusd.market(), "ethusd");
        assert!(
            ethusd.warmup_calendar_days() < 400,
            "ETHUSD asks for {} days, so 400 was not generous to it",
            ethusd.warmup_calendar_days()
        );
        for sleeve in BOOK.into_iter().filter(|s| s.market() != "ethusd") {
            assert!(
                sleeve.warmup_calendar_days() > 400,
                "{} asks for only {} days, so 400 was not short for it",
                sleeve.id(),
                sleeve.warmup_calendar_days()
            );
        }
    }

    /// A sleeve's answer must be reachable through the registry under both
    /// spellings the two runtimes use, or one of them silently falls back to no
    /// warm-up at all.
    #[test]
    fn the_registry_answers_for_both_spellings() {
        for sleeve in BOOK {
            assert_eq!(
                crate::strategies::warmup_calendar_days(sleeve.display()),
                Some(sleeve.warmup_calendar_days()),
                "{} is unreachable by display name",
                sleeve.id()
            );
            assert_eq!(
                crate::strategies::warmup_calendar_days(sleeve.id()),
                Some(sleeve.warmup_calendar_days()),
                "{} is unreachable by id",
                sleeve.id()
            );
        }
    }
}
