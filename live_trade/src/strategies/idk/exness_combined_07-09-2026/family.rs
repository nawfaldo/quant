//! The shared family engine: the parameter grid every sleeve is expressed in,
//! and the streaming machine that trades a cell of it.
//!
//! ONE ENGINE FOR THE WHOLE BOOK SINCE 2026-09-04. The 29-08 book carried two
//! hand-written imports beside this -- `nq:ofi` and `nq:drift_vwap`, neither an
//! `cfd_families` cell, both on one-minute bars and one of them on the
//! level-two feature table. NQ was barred as a symbol on 2026-09-03 and the
//! decay screen took the rest, so every member now IS a cell of this grid and
//! the two second engines are gone rather than idle.

use super::*;
use crate::backtest::fills::{FillCoverage, MarketFills};
use std::sync::Arc;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Direction {
    /// Take the raw side the trigger produces.
    Follow,
    /// Take the opposite side.
    Fade,
}

/// Python's `round`: ties go to the EVEN integer, not away from zero.
///
/// Only `half_life` needs it, and only because its window length is an
/// `int(round(...))` of a measured quantity. Everywhere else the book rounds
/// nothing that a tie could reach.
pub(super) fn round_half_even(value: f64) -> f64 {
    let floor = value.floor();
    let fraction = value - floor;
    if fraction > 0.5 {
        floor + 1.0
    } else if fraction < 0.5 {
        floor
    } else if (floor as i64).rem_euclid(2) == 0 {
        floor
    } else {
        floor + 1.0
    }
}

impl Direction {
    pub(super) fn apply(self, side: Side) -> Side {
        match self {
            Self::Follow => side,
            Self::Fade => match side {
                Side::Long => Side::Short,
                Side::Short => Side::Long,
            },
        }
    }
}

/// The `trend` axis: `price > EMA` for a long, `price < EMA` for a short.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Trend {
    None,
    Ema20d,
    Ema50d,
}

/// The `vol_mode` axis, a boolean on short-against-long realised volatility.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum VolMode {
    /// `none`: no filter at all.
    Any,
    /// `calm`: 20-session realised volatility below the 100-session reading.
    Calm,
}

/// `exit_plan_for`, with the day counts already resolved.
///
/// THREE OF THE FOUR `EXIT_MODES` ARE REACHED and the fourth is `days_N`, which
/// belongs to the swing grid this book no longer selects from -- so it is absent
/// rather than written and unreachable. `time_N` was absent for the same reason
/// until 2026-09-04, when the holdout seated three cells that use it.
#[derive(Clone, Copy, Debug)]
pub(super) enum Exit {
    /// `rr_N`: a fixed target at N times the stop distance.
    RewardMultiple(f64),
    /// `trail_N`: a stop that ratchets N daily ranges behind the best close.
    Trail(f64),
    /// `time_N`: leave at the open once N CANDLES have passed since entry.
    ///
    /// BARS, NOT DAYS. `exit_plan` reads `int(mode[5:])` straight through while
    /// `days_N` multiplies by `per_session`, so `time_4` is four 30-minute
    /// candles on every market -- two hours, not four sessions. Getting that
    /// wrong is a plausible-looking number and a completely different strategy.
    Bars(usize),
}

#[derive(Clone, Copy, Debug)]
pub(super) enum Family {
    VolumeThrust {
        volume_period: usize,
        volume_mult: f64,
        threshold_atr: f64,
    },
    Zscore {
        period: usize,
        threshold_z: f64,
    },
    VolatilityBreakout {
        fraction: f64,
    },
    Confluence {
        votes: i32,
        zscore_period: usize,
        rsi_period: usize,
    },
    Trap {
        window: usize,
        channel: usize,
    },
    ObvBreak {
        channel: usize,
    },
    XmaCross {
        fast: usize,
        slow: usize,
    },
    /// A retracement from a recent extreme, measured in DAILY RANGE, inside a
    /// trend that is still intact.
    Pullback {
        channel: usize,
        depth: f64,
        /// The trend the pullback is measured against -- its own axis, and NOT
        /// the shared `trend` gate, which this cell leaves at `none`.
        reference: usize,
    },
    /// The OPENING-RANGE break, taken only when a second reading agrees.
    ///
    /// NOT `orb` WITH THE `trend` AXIS SET. That axis is `price > EMA`, which on
    /// a breakout bar is very nearly implied by the breakout itself, so it
    /// refuses almost nothing. Every gate in the vocabulary is orthogonal to
    /// price level by construction, and the bars they refuse are the whole
    /// experiment.
    GatedOrb {
        gate: Gate,
        polarity: Polarity,
        /// `p["rsi"][1]`, the SLOW RSI. Resolved in the sleeve file so the
        /// engine holds no period table.
        gate_period: usize,
    },
    /// A coil arms the trade; the break of the coil's own range takes it.
    TwoStage {
        coil: Coil,
        window: usize,
        /// `p["compress"][0]`, the floor both coils are measured against.
        compress: usize,
        /// `p["session"]`, the Bollinger width's own period.
        width_period: usize,
    },
    /// A level breaks, price comes back to it, and it holds.
    BreakRetest {
        window: usize,
        level: BreakLevel,
        retest: Retest,
        /// `4 * p["session"]`, the Donchian channel `_breakout_levels` reads.
        channel: usize,
    },
    /// Two independent level systems landing on the same price.
    LevelConfluence {
        /// WHICH two systems. `LEVEL_PAIRS` offers three combinations and this
        /// book reaches two of them, so the axis is carried rather than pinned:
        /// `hk50` reads the floor pivots against the session VWAP and `ukoil`
        /// reads yesterday's range against it, which are different claims about
        /// what a level is.
        pair: LevelPair,
        tolerance: f64,
    },
    /// SHORT-HORIZON VOLATILITY crossing a multiple of its own long baseline.
    ///
    /// `vol_mode` uses the same two readings as a FILTER -- it asks whether a
    /// cell only works in the quiet half. This asks the opposite question: is
    /// the TRANSITION itself tradeable, and in which direction. A filter can
    /// never answer that, because it never fires on anything; it only removes
    /// bars.
    VolRegime {
        ratio: f64,
        regime: Regime,
        /// `ctx["periods"]["session"]`, the horizon the direction is read over.
        lookback: usize,
    },
    /// Three horizons of momentum, each weighted by how UNUSUAL it is.
    ///
    /// A voting rule throws magnitude away: a 0.1-sigma move and a 3-sigma move
    /// are the same vote. This standardises each horizon's return by that
    /// horizon's own dispersion and SUMS the three z-scores, so it fires where
    /// one horizon is overwhelming and the other two are mildly against -- a
    /// refusal for every voting family -- and refuses a bar where all three
    /// agree feebly, which they take at full size.
    MomentumStack {
        threshold: f64,
        /// `ctx["periods"]["session"]`. The three spans are `1x`, `5x` and `20x`
        /// this, and each is standardised over TWICE its own span.
        session: usize,
    },
    /// Mann-Kendall's monotone-trend z, which one outlier cannot move.
    ///
    /// `threshold` is in SIGMA and 2.0 therefore means the conventional thing,
    /// rather than being a number the search happened to find.
    Kendall {
        period: usize,
        threshold: f64,
    },
    /// A threshold on ACCUMULATED deviation, which resets after every event.
    ///
    /// Every other breakout family here fires when one reading crosses one
    /// level, so a move made of twenty small steps is invisible to all of them
    /// until it happens to clear a channel. This fires on that move, at the
    /// point where it became one.
    Cusum {
        /// The threshold in multiples of `atr / close`, which is also the key
        /// the Python tabulates the event series under.
        multiple: f64,
    },
    /// Price makes a new channel extreme; cumulative volume does not follow.
    ///
    /// It fires on exactly `donchian`'s trigger bars and takes the other side of
    /// the SUBSET where the flow disagreed, so it is not merely uncorrelated
    /// with the breakout family -- it is a partition of it.
    ObvDivergence {
        channel: usize,
    },
    /// The state-space SLOPE, from a filter whose gain adapts to the noise.
    ///
    /// The slope is a STATE rather than a difference of two smoothed points, so
    /// after a turn it changes sign with the data instead of carrying the old
    /// one for as long as a window is wide.
    Kalman {
        mode: KalmanMode,
        threshold: f64,
    },
    /// A stationary series that KEEPS its memory, z-scored.
    ///
    /// `zscore` subtracts a rolling mean, which is a full difference of a
    /// smoothed series: beyond the window it has forgotten everything. A
    /// fractional difference of order d in (0, 1) is the continuum between a
    /// price and a return, with weights that decay as a POWER LAW. They disagree
    /// after a long slow drift -- the z-score's mean has followed it and reports
    /// nothing, and this has not forgotten where the series started.
    Fracdiff {
        order: f64,
        threshold_z: f64,
        /// `20 * p["session"]`, the window the differenced series is
        /// standardised against. Its own trailing statistics, because the series
        /// is not zero-mean and its scale depends on the order.
        window: usize,
    },
    /// A z-score whose LOOKBACK is the estimated mean-reversion half-life.
    ///
    /// `zscore` takes its period from a fixed tuple and the search picks
    /// whichever of three paid. This fits an Ornstein-Uhlenbeck process every
    /// bar and uses the half-life that falls out as its own window, so on one
    /// series it reads a twelve-bar deviation in a fast-reverting stretch and a
    /// two-hundred-bar one in a slow stretch -- and no single cell of `zscore`
    /// does both.
    ///
    /// IT ALSO REFUSES WHERE `zscore` CANNOT. When the fit says the series is
    /// not reverting there is no reading at all, so this family is silent
    /// through every trending stretch by construction. `zscore` computes a
    /// perfectly well-defined deviation from a mean the price is walking away
    /// from, and trades it. That is the largest behavioural difference between
    /// them and it is not a tuning choice.
    HalfLife {
        /// `p["halflife"]`, the sample the OU fit is taken over AND the ceiling
        /// on the life it may report.
        period: usize,
        /// How many half-lives wide the z-score window is.
        multiple: f64,
        threshold_z: f64,
    },
    /// Aroon-up crossing Aroon-down: which extreme is more RECENT.
    ///
    /// Denominated in TIME, not price -- the only reading in the book that is.
    /// Seated 2026-09-22 as `usdjpy:aroon`.
    Aroon {
        period: usize,
        /// `max(up, down)` must reach this before a cross counts.
        min_strength: f64,
    },
    /// A multi-day EMA cross (`es.ema`, seeded at the first close).
    ///
    /// Declared `swing` in the study, but canon runs `SESSION_ONLY`, so it is
    /// flattened at the close and admitted once a day like every other cell.
    /// It has NO `last_entry_minute` in Python and calls no `_late`, so the
    /// signal below never asks. Seated 2026-09-22 as `eurjpy:swing_ma`.
    SwingMa {
        fast: usize,
        slow: usize,
    },
    /// A zero crossing of Ehlers' roofing BANDPASS, gated on the band's own
    /// recent amplitude in ATRs. Seated 2026-09-23 as `ethusd:roofing`.
    Roofing {
        high_period: usize,
        low_period: usize,
        amplitude_atr: f64,
    },
    /// A directional bar on volume unusual FOR ITS CLOCK MINUTE. Seated
    /// 2026-09-23 as `usdjpy:rvol`.
    Rvol {
        rvol: f64,
        threshold_atr: f64,
    },
    /// Kaufman's efficiency ratio: the move is traded only when its PATH was
    /// clean (or, on the other arm, noisy). Seated 2026-09-26 as the
    /// weekend-only `ethusd:efficiency`.
    Efficiency {
        period: usize,
        min_er: f64,
        regime: ErRegime,
    },
    /// CCI past a threshold -- a z-score with a mean-ABSOLUTE denominator.
    /// Python's raw side is the FADE (short above +threshold) and `follow`
    /// inverts it, so `Direction::Follow` here means Python's `follow`.
    /// Seated 2026-09-26 as the weekend-only `ethusd:cci`.
    Cci {
        period: usize,
        threshold: f64,
    },
    /// A least-squares slope that is both steep enough (in daily risk) and well
    /// fitted enough. Seated 2026-09-26 as the weekend-only
    /// `ethusd:linreg_trend`.
    LinregTrend {
        period: usize,
        slope: f64,
        min_fit: f64,
    },
}

/// `efficiency`'s `regime` axis: trade a clean path, or a noisy one.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum ErRegime {
    Clean,
    Noisy,
}

/// `LEVEL_PAIRS`: which two level systems `level_confluence` asks to agree.
///
/// The unreached combination is kept, because a two-of-three axis is only
/// legible next to the one the book did not take.
#[allow(dead_code, reason = "the unreached arm is what the reached ones mean")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum LevelPair {
    /// `pivot_pdr`: the floor-trader R1/S1 against yesterday's high and low.
    /// Two systems derived DIFFERENTLY, which is the family's own hypothesis in
    /// its purest form -- and the arm this book does not reach.
    PivotPdr,
    /// `pivot_vwap`: the pivots against the session VWAP.
    PivotVwap,
    /// `pdr_vwap`: yesterday's range against the session VWAP. The VWAP is ONE
    /// price acting as both sides, so this asks whether today's average trade
    /// has come to rest on yesterday's extreme.
    PdrVwap,
}

/// THE UNREACHED ARM OF EACH AXIS IS KEPT, unlike the unreached FAMILIES, which
/// are absent. A family the book does not select is dead code; one ARM of an
/// axis inside a family the book DOES select is what the other arm means.
/// Deleting `Confirm` would leave `Oppose` reading as the only thing a gate can
/// do, when the whole point of `eurjpy:gated_orb` is that it chose to take the
/// break the slow oscillator disagreed with.
///
/// The confirmers `gate_opinion` can be asked for. Only the two this book has
/// ever named are spelled out; a cell naming another would not compile, which is
/// the point.
///
/// `Vwap` is the unreached arm and is kept because it is what `Rsi` MEANS here:
/// one asks whether the close is above today's average trade, which on a
/// breakout bar is close to implied by the breakout, and the other asks whether
/// a SLOW oscillator agrees -- a reading that disagrees with a break constantly
/// and is the entire experiment `gated_orb` runs.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Gate {
    /// Close against the SESSION VWAP.
    Vwap,
    /// THE SLOW RSI, at 50. `rsi` the family trades the FAST one at an extreme;
    /// this reads the same construct as a standing bias, which is the opposite
    /// use of it and is why the two do not collide.
    Rsi,
}

/// `vol_regime`'s `regime` axis: which side of the ratio is the event.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Regime {
    /// Short-horizon volatility at or above `ratio` times its own baseline.
    Expanding,
    /// Below it. `jp225:vol_regime` takes this one, so the cell is a breakout
    /// entered while the market is QUIETENING rather than while it erupts.
    Contracting,
}

/// `kalman`'s `mode` axis: which of the two claims the filter's state makes.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum KalmanMode {
    /// Trade the RATE's sign, against a threshold in units of `atr / close` --
    /// the bar's own typical log move.
    Slope,
    /// Trade the price's distance from the filtered LEVEL, in ATRs. A
    /// mean-reversion statement about a line no other family here computes.
    Residual,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// `gate_accepts`. `oppose` is NOT the null of `confirm` -- it is the claim that
/// the confirmer is a contrary indicator.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Polarity {
    Confirm,
    Oppose,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// `two_stage`'s coil: what counts as compression.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Coil {
    /// Bollinger bandwidth within 10% of its own recent floor.
    Squeeze,
    /// A narrow-range bar: this bar's span at the window's minimum.
    Nr,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// `_breakout_levels`: which level system `break_retest` watches break.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum BreakLevel {
    /// Yesterday's high and low.
    Pdr,
    /// The `4 * session` Donchian channel, read AS IT STOOD when it broke.
    Donchian,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// What price has to come back TO for the retest to count.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum Retest {
    /// The broken level itself -- the textbook version.
    Level,
    /// Session VWAP, which after a wide break can sit a long way under the
    /// level, so the two ask for different pullback depths on the same day.
    Vwap,
}

#[derive(Clone, Copy, Debug)]
pub(super) struct Params {
    pub(super) family: Family,
    pub(super) direction: Direction,
    pub(super) exit: Exit,
    pub(super) last_entry_minute: i64,
    pub(super) stop_day: f64,
    pub(super) trend: Trend,
    pub(super) vol_mode: VolMode,
}

/// A signal read on a closed candle, waiting for the next candle's OPEN.
#[derive(Clone, Copy)]
pub(super) struct Pending {
    pub(super) side: Side,
    pub(super) day: i64,
    /// `stop_day * daily_risk`, resolved at the signal bar.
    pub(super) distance: f64,
    /// 20-session realised volatility at the SIGNAL bar, which is where Python
    /// reads it. Only the admission test uses it -- the book's own sizing
    /// replaced the one-sided throttle with the two-sided EWMA one.
    pub(super) realized: f64,
}

pub(super) struct Position {
    pub(super) side: Side,
    /// The fill price, kept for the shadow account's own P&L. The live book's
    /// accounting is the engine's and never reads this.
    pub(super) entry: f64,
    /// Lots the STANDALONE rule would have taken, which is what settles into
    /// `shadow_equity` when this position closes.
    pub(super) shadow_lots: f64,
    pub(super) stop: f64,
    /// `stop_day * daily_risk` as it stood at the SIGNAL bar.
    ///
    /// Kept rather than recovered from `entry - stop`, which is the same number
    /// only up to a floating-point subtraction, and `resize_entry` divides by
    /// it: a stop that has since RATCHETED under a trailing exit would give a
    /// different answer altogether.
    pub(super) distance: f64,
    pub(super) target: Option<f64>,
    /// `trail_N`'s multiple, in daily ranges behind the best close.
    pub(super) trail: Option<f64>,
    /// `time_N`'s bar count, or `None` for the two price-driven shapes.
    pub(super) max_bars: Option<usize>,
    /// `position["index"]`, as `FamilyEngine::candles` counted it.
    ///
    /// Python compares `index - position["index"]` over the SESSION-FILTERED bar
    /// list, so the unit is in-session candles rather than wall-clock time -- an
    /// overnight gap costs a `time_4` position nothing at all. `candles` counts
    /// the same bars in the same order, which is what makes the two agree.
    pub(super) entry_candle: u64,
    pub(super) best: f64,
    /// The spread this position crossed at entry, in bp WITHOUT the slippage
    /// allowance: the broker's quote in the entry minute where the fill model
    /// reaches it, the sealed constant where it does not. RESOLVED AT ENTRY --
    /// `cfd_families.backtest` stores it on the position for the same reason:
    /// a quote read at exit would be hours later, on the wrong side of the move.
    pub(super) spread_bp: f64,
}

/// One bar of the history `trap` walks backwards over: what the level WAS when
/// this candle closed, and where the candle closed against it.
///
/// The level is read AT THE BREAK BAR rather than now, so a channel that has
/// since widened past the failure cannot make the trap look like a level still
/// holding.
///
/// `break_retest` and `two_stage` walk the SAME ring for the same reason -- both
/// need a reading as it stood at an earlier bar rather than now -- so the extra
/// fields live here rather than in three parallel deques that could fall out of
/// step with each other.
#[derive(Clone, Copy)]
pub(super) struct LevelHistory {
    pub(super) day: i64,
    pub(super) high: f64,
    pub(super) low: f64,
    pub(super) close: f64,
    pub(super) top: Option<f64>,
    pub(super) bottom: Option<f64>,
    /// `(high, low)` of the day BEFORE this candle's, for `_breakout_levels`
    /// under `pdr`.
    pub(super) prior_range: Option<(f64, f64)>,
    /// `ctx["width"][index]`: Bollinger bandwidth, `sigma / mean`.
    pub(super) width: Option<f64>,
    /// `ctx["width_min"][compress][index]`, inclusive of this bar.
    pub(super) width_min: Option<f64>,
    /// `ctx["span_min"][compress][index]`, inclusive of this bar.
    pub(super) span_min: Option<f64>,
}

/// The streaming port of `cfd_families.backtest` for one sealed cell.
///
/// ORDERING MATCHES THE PYTHON AND THE RUST ENGINE: session flatten first, then
/// the stop, then the target, then the trail ratchet. An ambiguous candle is
/// read as the unfavourable sequence throughout.
pub(super) struct FamilyEngine {
    pub(super) sleeve: Sleeve,
    pub(super) params: Params,
    pub(super) spec: Instrument,
    pub(super) quantity_step: f64,

    // ---- candle assembly ----
    pub(super) slot: Option<i64>,
    pub(super) building: Option<Candle>,
    /// Seconds between one SOURCE bar and the next, learned from the stream.
    ///
    /// Read rather than configured so it cannot disagree with what the runtime
    /// is feeding: `ohlcv_step` is 60 for every market in this book and 1,800
    /// for a 30-minute source, and a constant transcribed here would be a second
    /// copy of that rule. Held as the SMALLEST positive gap seen, because every
    /// larger one is a session break, a weekend or a hole.
    pub(super) source_step: Option<i64>,
    /// The previous source bar's timestamp, on the shifted clock, for the above.
    pub(super) last_bar_ts: Option<i64>,

    // ---- indicators, in the order `context` builds them ----
    pub(super) atr: AverageTrueRange,
    pub(super) risk: DailyRisk,
    pub(super) trend: Option<Ema>,
    pub(super) short_volatility: TrailingVolatility,
    pub(super) long_volatility: TrailingVolatility,
    pub(super) session: SessionPath,
    pub(super) prior: PriorDay,
    pub(super) volatility_multiplier: VolatilityMultiplier,
    /// `ctx["vol_target"]`, the ADMISSION throttle's target.
    ///
    /// Seeded from the frozen per-instrument figure and overridden to
    /// `LEVEL_TWO_SEGMENT_VOL_TARGET` when the sleeve is rebuilt at the
    /// level-two handover, because Python recomputes it there from a series
    /// with no in-sample rows. Carried on the engine rather than read off
    /// `spec` for exactly that reason: it is a property of the SEGMENT, not of
    /// the instrument.
    pub(super) vol_target: f64,

    // ---- per-family state, built only for the cell that reads it ----
    pub(super) channel_high: Option<RollingExtreme>,
    pub(super) channel_low: Option<RollingExtreme>,
    pub(super) volume_mean: Option<RollingMean>,
    pub(super) zscore: Option<MeanSigma>,
    pub(super) rsi: Option<WilderRsi>,
    pub(super) obv: Option<OnBalanceVolume>,
    pub(super) obv_high: Option<RollingExtreme>,
    pub(super) obv_low: Option<RollingExtreme>,
    pub(super) fast_tema: Option<Tema>,
    pub(super) slow_tema: Option<Tema>,
    /// The previous candle's `fast - slow`, which is what makes a CROSS
    /// distinguishable from a standing difference.
    pub(super) previous_cross: Option<f64>,
    /// The backward lookback `trap`, `break_retest` and `two_stage` walk. Length
    /// is set by whichever of them this cell is; every other family leaves it
    /// empty and pays nothing.
    pub(super) levels: VecDeque<LevelHistory>,
    /// How many candles `levels` retains. Zero for a family that never looks
    /// back, which is what keeps the ring off the hot path for eleven of them.
    pub(super) level_window: usize,
    /// `pullback`'s own trend anchor. SEPARATE FROM `trend`: that one is the
    /// shared admission gate this book's cells leave at `none`, and conflating
    /// them would apply one axis twice.
    pub(super) reference_ema: Option<Ema>,
    /// `ctx["width"]`: the Bollinger stats `two_stage`'s squeeze is measured
    /// from, at `p["session"]` rather than at the `zscore` periods.
    pub(super) width_stats: Option<MeanSigma>,
    pub(super) width_min: Option<RollingMinInclusive>,
    pub(super) span_min: Option<RollingMinInclusive>,
    /// `ctx["kendall"][period]`, the Mann-Kendall z.
    pub(super) kendall: Option<MannKendall>,
    /// `ctx["kalman_level"]` and `ctx["kalman_slope"]`, from one filter.
    pub(super) kalman: Option<KalmanTrend>,
    /// `ctx["fracdiff"][order]` and the trailing statistics it is standardised
    /// against. TWO PIECES, because the raw differenced series is not zero-mean
    /// and its scale depends on the order, so a threshold on it would mean a
    /// different thing at each `d`.
    pub(super) fracdiff: Option<FracDiff>,
    pub(super) fracdiff_stats: Option<MeanSigma>,
    /// `ctx["cusum"][multiple]`, an event series rather than a level: the filter
    /// is STATEFUL and resets after every event, so it cannot be evaluated at a
    /// threshold the signal picks.
    pub(super) cusum: Option<CusumFilter>,
    /// `ctx["prefix_sum"]` / `ctx["prefix_square"]`, and the closes themselves.
    /// Shared by the two families that read a close a fixed number of bars back.
    pub(super) closes: Option<CloseHistory>,
    /// `ctx["halflife"][period]`, the OU fit `half_life` reads its own lookback
    /// from. It is a SECOND pass over the same closes rather than a method on
    /// `CloseHistory`, because it regresses the LOG series and that history has
    /// to be kept at log scale to stay exact.
    pub(super) half_life: Option<OuHalfLife>,
    /// `ctx["opening"][1]`, today's first in-session candle.
    pub(super) opening: Option<OpeningRange>,
    /// The three inclusive readings `two_stage` records into `levels`, captured
    /// as this candle is folded in.
    pub(super) current_width: Option<f64>,
    pub(super) current_width_min: Option<f64>,
    pub(super) current_span_min: Option<f64>,
    /// `ctx["aroon_up"]` / `ctx["aroon_down"]`, current and previous.
    pub(super) aroon: Option<Aroon>,
    /// `ctx["fast"][n]` / `ctx["slow"][n]`: plain seeded EMAs, NOT the TEMAs
    /// `xma_cross` reads. The cross itself goes through `previous_cross`.
    pub(super) fast_ema: Option<Ema>,
    pub(super) slow_ema: Option<Ema>,
    /// `ctx["roofing"][band]`.
    pub(super) roofing: Option<Roofing>,
    /// `ctx["rvol"]`.
    pub(super) relative_volume: Option<RelativeVolume>,
    /// `ctx["er"][period]`, with the net move it was measured over.
    pub(super) efficiency: Option<EfficiencyRatio>,
    /// `ctx["cci"][period]`.
    pub(super) cci: Option<Cci>,
    /// `ctx["slope"][period]` / `ctx["fit"][period]`.
    pub(super) linreg: Option<LinearRegression>,

    // ---- trading state ----
    /// How many in-session candles this engine has stepped, which is Python's
    /// `index` into the context's bar list. Only `time_N` reads it.
    pub(super) candles: u64,
    pub(super) traded_day: Option<i64>,
    pub(super) pending: Option<Pending>,
    pub(super) position: Option<Position>,
    pub(super) action_timestamp: Option<i64>,
    /// The standalone account that decides which trades EXIST. See
    /// `ADMISSION_BALANCE`; it never touches the live book's balance.
    pub(super) shadow_equity: f64,

    // ---- the Exness live-fill model (`backtest::fills`) ----
    /// The broker's minute series for this sleeve's market. Installed by the
    /// backtest and never by the live runtime, which is filled by the broker
    /// itself and has no future minute to read.
    pub(super) fills: Option<Arc<MarketFills>>,
    /// The whole entry charge, bp of the fill, for the fill just emitted --
    /// `Some` only when the broker table priced its spread, so the engine keeps
    /// its constant whenever this sleeve fell back to it.
    pub(super) entry_cost_bp: Option<f64>,
    pub(super) coverage: FillCoverage,

    // ---- live execution ----
    /// Fill a pending signal on the FIRST MINUTE of its fill candle instead of
    /// once that candle has closed. LIVE ONLY: see `enable_early_fills`.
    pub(super) fill_early: bool,
    /// The `candles` index of a candle whose entry was filled early, so its own
    /// `on_candle` can skip what the ordinary path never saw that position do.
    pub(super) early_filled_candle: Option<u64>,
    /// Weekdays a new signal may be taken on, Monday = bit 0; `None` for every
    /// day. RESEARCH ONLY -- set by the backtest from `EXNESS_ENTRY_DAYS`, the
    /// same switch `cfd_families.ENTRY_DAYS` reads, and never by the live
    /// runtime.
    pub(super) entry_days: Option<u8>,
}

impl FamilyEngine {
    pub(super) fn new(sleeve: Sleeve, quantity_step: f64) -> Self {
        let spec = sleeve.contract();
        let params = sleeve
            .params()
            .expect("FamilyEngine built for an imported sleeve");
        let session_bars = spec.per_session;
        let annual = spec.calendar * session_bars as f64;
        let trend = match params.trend {
            Trend::None => None,
            Trend::Ema20d => Some(Ema::new(20 * session_bars)),
            Trend::Ema50d => Some(Ema::new(50 * session_bars)),
        };

        let mut engine = Self {
            sleeve,
            params,
            spec,
            quantity_step,
            slot: None,
            building: None,
            source_step: None,
            last_bar_ts: None,
            atr: AverageTrueRange::new(2 * session_bars),
            risk: DailyRisk::new(),
            trend,
            short_volatility: TrailingVolatility::new(20 * session_bars, annual),
            long_volatility: TrailingVolatility::new(100 * session_bars, annual),
            session: SessionPath::new(),
            prior: PriorDay::new(),
            volatility_multiplier: VolatilityMultiplier::new(),
            vol_target: spec.vol_target,
            channel_high: None,
            channel_low: None,
            volume_mean: None,
            zscore: None,
            rsi: None,
            obv: None,
            obv_high: None,
            obv_low: None,
            fast_tema: None,
            slow_tema: None,
            previous_cross: None,
            levels: VecDeque::new(),
            level_window: 0,
            reference_ema: None,
            width_stats: None,
            width_min: None,
            span_min: None,
            kendall: None,
            kalman: None,
            fracdiff: None,
            fracdiff_stats: None,
            cusum: None,
            closes: None,
            half_life: None,
            opening: None,
            current_width: None,
            current_width_min: None,
            current_span_min: None,
            aroon: None,
            fast_ema: None,
            slow_ema: None,
            roofing: None,
            relative_volume: None,
            efficiency: None,
            cci: None,
            linreg: None,
            candles: 0,
            traded_day: None,
            pending: None,
            position: None,
            action_timestamp: None,
            shadow_equity: ADMISSION_BALANCE * sleeve.shown_equity(),
            fills: None,
            entry_cost_bp: None,
            coverage: FillCoverage::default(),
            fill_early: false,
            early_filled_candle: None,
            entry_days: sleeve.entry_days(),
        };
        engine.arm_family_state();
        engine
    }

    /// Fills the pending signal at the open of the candle stamped `candle_ts`,
    /// if it still may: `cfd_families.backtest`'s fill block.
    ///
    /// Intraday that candle must still be in the SAME session, so a signal on
    /// the last bar of the day is dropped rather than filled at tomorrow's open.
    /// The pending signal is consumed either way, exactly as Python's is.
    ///
    /// AT THE PRICE THE ACCOUNT GETS, and everything downstream keys on it: both
    /// sizing stages, the stop, the target and the trail's first `best`.
    /// `ef.backtest` sets all of them from `fill_price`, so a broker fill that
    /// lands away from the vendor open moves the stop with it rather than
    /// leaving it measured from a price nobody paid.
    ///
    /// `entry_candle` is the index the fill candle has (or will have) in
    /// `candles`, which only `time_N` reads.
    fn fill_pending(
        &mut self,
        candle_ts: i64,
        candle_open: f64,
        equity: f64,
        entry_candle: u64,
    ) -> Option<Action> {
        if self.position.is_some() {
            return None;
        }
        let pending = self.pending.take()?;
        let day = self.day_of(candle_ts);
        let minute = self.minute_of(candle_ts);
        if pending.day != day || minute >= self.spec.session.1 {
            return None;
        }
        let broker_entry = self
            .fills
            .as_ref()
            .and_then(|fills| fills.entry(candle_ts, candle_open));
        let entry = broker_entry.unwrap_or(candle_open);
        // Stage one: does this trade exist at all? See `ADMISSION_BALANCE`.
        let shadow_lots = self.admission_lots(entry, pending.distance, pending.realized)?;
        // Stage two: how large is it against the shared balance?
        let quantity = self.quantity(equity, entry, pending.distance)?;
        let broker_spread = self
            .fills
            .as_ref()
            .and_then(|fills| fills.spread_bp(candle_ts));
        self.coverage.entries += 1;
        if broker_entry.is_some() && broker_spread.is_some() {
            self.coverage.entries_priced += 1;
        }
        self.entry_cost_bp = broker_spread.map(|spread| spread + SLIPPAGE_BP);
        let (target, trail, max_bars) = self.exit_plan(pending.distance);
        self.position = Some(Position {
            side: pending.side,
            entry,
            shadow_lots,
            stop: match pending.side {
                Side::Long => entry - pending.distance,
                Side::Short => entry + pending.distance,
            },
            distance: pending.distance,
            target: target.map(|distance| match pending.side {
                Side::Long => entry + distance,
                Side::Short => entry - distance,
            }),
            trail,
            max_bars,
            entry_candle,
            best: entry,
            spread_bp: broker_spread.unwrap_or(self.spec.spread_bp),
        });
        self.traded_day = Some(day);
        Some(Action::Enter {
            side: pending.side,
            price: entry,
            quantity,
        })
    }

    pub(super) fn install_fills(&mut self, fills: Arc<MarketFills>) {
        self.fills = Some(fills);
    }

    /// Sends entries when the fill candle OPENS rather than when it closes.
    ///
    /// WHY LIVE NEEDS IT AND THE BACKTEST DOES NOT. A signal read on candle S-1
    /// fills at candle S's OPEN, and both engines book that price. But the
    /// fill used to be EMITTED only when candle S closed -- `on_candle(S)` does
    /// the exits, then the fill, then the next signal -- so the backtest filled
    /// at 10:30's open while the live account's market order went out at
    /// 11:00, half an hour later, at whatever the market then was. Measured on
    /// 2026-09-24/25: every one of five entries was sent 30 minutes after the
    /// price the engine recorded (ethusd_kalman 2694.02 booked, 2654.37 got).
    ///
    /// Everything the fill reads is known on the first minute of S: the
    /// pending signal, the open, the day and minute, the shadow balance, and
    /// the sizing multiplier (which moves only on a day's first candle, and an
    /// entry never fills on one because its signal must be the same day). So
    /// the fill is done there, and `on_candle(S)` then skips the two steps the
    /// ordinary path never ran against this position: the exit test on S (the
    /// ordinary fill comes AFTER it) and a new signal on S (the ordinary path
    /// holds the position through it, even one that is later discarded).
    ///
    /// The backtest keeps the ordinary path, where both give the same trades
    /// and the Python parity fixtures stay authoritative.
    /// Restricts new signals to the weekdays in `mask` (Monday = bit 0). Gates
    /// the SIGNAL, as `cfd_families.backtest` gates `tradeable`, so a position
    /// already held still exits by its own rules.
    pub(super) fn restrict_entry_days(&mut self, mask: u8) {
        self.entry_days = Some(mask);
    }

    pub(super) fn enable_early_fills(&mut self) {
        self.fill_early = true;
    }

    /// Where the account really got out of a position the candle stamped
    /// `candle_ts` closed, given the price the bar itself says.
    ///
    /// `ef.backtest`'s `exit_at(ts, price)`: EVERY reason is repriced -- stop,
    /// target, session flatten, clock -- because every one of them is a market
    /// order the runtime sends once the candle has rolled. The idealised price
    /// survives only where the broker table does not reach.
    fn exit_fill(&mut self, candle_ts: i64, candle_open: f64, idealised: f64) -> f64 {
        self.coverage.exits += 1;
        match self
            .fills
            .as_ref()
            .and_then(|fills| fills.exit(candle_ts, candle_open))
        {
            Some(price) => {
                self.coverage.exits_priced += 1;
                price
            }
            None => idealised,
        }
    }

    /// Builds only the context blocks this cell's family declares.
    ///
    /// `extra_context` used to build every block unconditionally and it cost a
    /// crashed machine; the same discipline is cheap to keep here and makes the
    /// dependency legible.
    pub(super) fn arm_family_state(&mut self) {
        match self.params.family {
            Family::VolumeThrust { volume_period, .. } => {
                self.volume_mean = Some(RollingMean::new(volume_period));
            }
            Family::Zscore { period, .. } => {
                self.zscore = Some(MeanSigma::new(period));
            }
            Family::VolatilityBreakout { .. } => {}
            Family::Confluence {
                zscore_period,
                rsi_period,
                ..
            } => {
                self.zscore = Some(MeanSigma::new(zscore_period));
                self.rsi = Some(WilderRsi::new(rsi_period));
            }
            Family::Trap { channel, window } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.level_window = window;
            }
            Family::ObvBreak { channel } => {
                self.obv = Some(OnBalanceVolume::new());
                self.obv_high = Some(RollingExtreme::new(channel, true));
                self.obv_low = Some(RollingExtreme::new(channel, false));
            }
            Family::XmaCross { fast, slow } => {
                self.fast_tema = Some(Tema::new(fast));
                self.slow_tema = Some(Tema::new(slow));
            }
            Family::Pullback {
                channel, reference, ..
            } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.reference_ema = Some(Ema::new(reference));
            }
            Family::GatedOrb {
                gate, gate_period, ..
            } => {
                self.opening = Some(OpeningRange::new());
                if gate == Gate::Rsi {
                    self.rsi = Some(WilderRsi::new(gate_period));
                }
                // `vwap` needs no state of its own: `SessionPath` is built on
                // every candle for every cell already.
            }
            Family::TwoStage {
                coil,
                window,
                compress,
                width_period,
            } => {
                match coil {
                    Coil::Squeeze => {
                        self.width_stats = Some(MeanSigma::new(width_period));
                        self.width_min = Some(RollingMinInclusive::new(compress));
                    }
                    Coil::Nr => self.span_min = Some(RollingMinInclusive::new(compress)),
                }
                // `window + 1`, because the arming scan reads
                // `range(index - window, index)` and the range scan then reads
                // from the armed bar up to `index - 1`.
                self.level_window = window + 1;
            }
            Family::BreakRetest {
                window,
                level,
                channel,
                ..
            } => {
                if level == BreakLevel::Donchian {
                    self.channel_high = Some(RollingExtreme::new(channel, true));
                    self.channel_low = Some(RollingExtreme::new(channel, false));
                }
                self.level_window = window;
            }
            Family::LevelConfluence { .. } => {}
            Family::VolRegime { .. } => {
                // The two volatility readings are built for every cell already,
                // because `vol_mode` is a shared axis. Only the close history the
                // direction is read over is this family's own.
                self.closes = Some(CloseHistory::new());
            }
            Family::MomentumStack { .. } => {
                self.closes = Some(CloseHistory::new());
            }
            Family::Kendall { period, .. } => {
                self.kendall = Some(MannKendall::new(period));
            }
            Family::Cusum { multiple } => {
                self.cusum = Some(CusumFilter::new(multiple));
            }
            Family::ObvDivergence { channel } => {
                // FOUR CHANNELS, not two. The family compares a price extreme
                // with an OBV extreme over the same window, so it needs both
                // pairs -- which is also why it cannot be expressed as
                // `obv_break` with a flag.
                self.obv = Some(OnBalanceVolume::new());
                self.obv_high = Some(RollingExtreme::new(channel, true));
                self.obv_low = Some(RollingExtreme::new(channel, false));
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
            }
            Family::Kalman { .. } => {
                self.kalman = Some(KalmanTrend::new());
            }
            Family::Fracdiff { order, window, .. } => {
                self.fracdiff = Some(FracDiff::new(order));
                self.fracdiff_stats = Some(MeanSigma::new(window));
            }
            Family::HalfLife { period, .. } => {
                // TWO PIECES, exactly as Python declares `reads=("halflife",
                // "prefix")`: the OU fit supplies the lookback and the prefix
                // sums answer the z-score over it in two subtractions.
                self.half_life = Some(OuHalfLife::new(period));
                self.closes = Some(CloseHistory::new());
            }
            Family::Aroon { period, .. } => {
                self.aroon = Some(Aroon::new(period));
            }
            Family::SwingMa { fast, slow } => {
                self.fast_ema = Some(Ema::new(fast));
                self.slow_ema = Some(Ema::new(slow));
            }
            Family::Roofing {
                high_period,
                low_period,
                ..
            } => {
                self.roofing = Some(Roofing::new(high_period, low_period));
            }
            Family::Rvol { .. } => {
                self.relative_volume = Some(RelativeVolume::new());
            }
            Family::Efficiency { period, .. } => {
                self.efficiency = Some(EfficiencyRatio::new(period));
            }
            Family::Cci { period, .. } => {
                self.cci = Some(Cci::new(period));
            }
            Family::LinregTrend { period, .. } => {
                self.linreg = Some(LinearRegression::new(period));
            }
        }
    }

    pub(super) fn day_of(&self, ts: i64) -> i64 {
        ts.div_euclid(86_400)
    }

    pub(super) fn minute_of(&self, ts: i64) -> i64 {
        ts.rem_euclid(86_400) / 60
    }

    pub(super) fn in_session(&self, minute: i64) -> bool {
        let (open, close) = self.spec.session;
        (open..=close).contains(&minute)
    }

    /// `_late`: true once the session is past this cell's entry cutoff. A swing
    /// cell has no cutoff, and its `last_entry_minute` is set past the close so
    /// this can never fire.
    pub(super) fn late(&self, minute: i64) -> bool {
        minute > self.params.last_entry_minute
    }

    /// `accepts_vol`, resolved once per candle.
    pub(super) fn accepts_vol(&self) -> bool {
        match self.params.vol_mode {
            VolMode::Any => true,
            VolMode::Calm => match (self.short_volatility.value(), self.long_volatility.value()) {
                (Some(short), Some(long)) if long > 0.0 => short < long,
                // `calm` is a three-valued reading in Python and `None` refuses,
                // because the comparison has not been made rather than because
                // it came out false.
                _ => false,
            },
        }
    }

    /// `accepts_trend`: price above the EMA for a long, below it for a short.
    pub(super) fn accepts_trend(&self, close: f64, side: Side) -> bool {
        let Some(trend) = &self.trend else {
            return true;
        };
        match trend.value() {
            Some(reference) => match side {
                Side::Long => close > reference,
                Side::Short => close < reference,
            },
            None => false,
        }
    }

    /// Folds this candle into every indicator the cell reads.
    ///
    /// CALLED AFTER THE SIGNAL IS EVALUATED for the readings a signal consumes
    /// at its own index, and BEFORE for the ones Python has already folded in by
    /// then. The split is not cosmetic: `rolling_extreme` excludes the current
    /// bar by construction, so its channel must be read before the push, while
    /// `atr`, `mean` and `sigma` are inclusive and must be read after.
    pub(super) fn push_inclusive(&mut self, candle: &Candle, day: i64) {
        self.atr.push(candle);
        self.risk.push(candle, day);
        self.short_volatility.push(candle.close);
        self.long_volatility.push(candle.close);
        if let Some(trend) = &mut self.trend {
            trend.push(candle.close);
        }
        if let Some(mean) = &mut self.volume_mean {
            mean.push(candle.volume);
        }
        if let Some(stats) = &mut self.zscore {
            stats.push(candle.close);
        }
        if let Some(rsi) = &mut self.rsi {
            rsi.push(candle.close);
        }
        // THE OBV LINE IS INCLUSIVE AND ITS CHANNEL IS NOT, which is why the two
        // are advanced in different places. `obv_break` compares `obv[i]` with a
        // channel over `obv[i-n .. i-1]`, so reading the line before this bar was
        // folded in tests the PREVIOUS bar's flow against the right channel --
        // close enough to agree on most bars and wrong on the ones where the
        // break happens, which are the only bars the family trades.
        if let Some(obv) = &mut self.obv {
            obv.push(candle);
        }
        if let Some(fast) = &mut self.fast_tema {
            fast.push(candle.close);
        }
        if let Some(slow) = &mut self.slow_tema {
            slow.push(candle.close);
        }
        if let Some(reference) = &mut self.reference_ema {
            reference.push(candle.close);
        }
        // ALL INCLUSIVE READINGS, folded in here rather than with the channels
        // because each is read AT this index by the family that carries it.
        if let Some(kendall) = &mut self.kendall {
            kendall.push(candle.close);
        }
        if let Some(kalman) = &mut self.kalman {
            kalman.push(candle.close);
        }
        // THE STATISTICS ADVANCE ONLY WHERE THE SERIES HAS A READING, which is
        // Python cutting the leading `None`s before the rolling window rather
        // than filling them: standardising against a window padded with
        // placeholders leaves every mean wrong for a further `window` bars, with
        // numbers that are present, finite and meaningless.
        if let Some(fracdiff) = &mut self.fracdiff {
            fracdiff.push(candle.close);
            if let (Some(value), Some(stats)) = (fracdiff.value(), &mut self.fracdiff_stats) {
                stats.push(value);
            }
        }
        // AFTER `atr`, because the threshold is `multiple * atr / close` and
        // `ctx["atr"]` is an inclusive indicator holding THIS bar's reading.
        if let Some(cusum) = &mut self.cusum {
            cusum.push(candle.close, self.atr.value());
        }
        if let Some(closes) = &mut self.closes {
            closes.push(candle.close);
        }
        // Fed from the same close and on the same bar as the prefix sums, so
        // the lookback and the window it indexes can never be a bar apart.
        if let Some(half_life) = &mut self.half_life {
            half_life.push(candle.close);
        }
        // All four read THIS index inclusively in Python: `aroon` includes the
        // current bar, `es.ema` and the roofing recursion are defined at every
        // index through this one, and `relative_volume` scores this bar
        // against strictly earlier sessions before appending it.
        if let Some(aroon) = &mut self.aroon {
            aroon.push(candle.high, candle.low);
        }
        if let Some(fast) = &mut self.fast_ema {
            fast.push(candle.close);
        }
        if let Some(slow) = &mut self.slow_ema {
            slow.push(candle.close);
        }
        if let Some(roofing) = &mut self.roofing {
            roofing.push(candle.close);
        }
        // Inclusive in Python too: `er`, `cci` and `linreg` are defined at every
        // index through this one.
        if let Some(efficiency) = &mut self.efficiency {
            efficiency.push(candle.close);
        }
        if let Some(cci) = &mut self.cci {
            cci.push(candle.high, candle.low, candle.close);
        }
        if let Some(linreg) = &mut self.linreg {
            linreg.push(candle.close);
        }
        let minute = self.minute_of(candle.ts);
        if let Some(relative) = &mut self.relative_volume {
            relative.push(minute, candle.volume);
        }
        // `opening_ranges` is built over the whole bar list up front, so on the
        // day's FIRST candle the window is already that candle's own high and
        // low -- which is why this is fed before the signal rather than after.
        if let Some(opening) = &mut self.opening {
            opening.push(candle, day);
        }
        // `width` is `sigma / mean` on the SESSION period, and its floor is the
        // inclusive rolling minimum of that. Both are recorded at this index for
        // the ring `two_stage` walks; the family reads them at EARLIER bars, so
        // computing them lazily inside the signal would be a different number.
        self.current_width = None;
        if let Some(stats) = &mut self.width_stats {
            stats.push(candle.close);
            self.current_width = stats
                .value()
                .filter(|(mean, _)| *mean > 0.0)
                .map(|(mean, sigma)| sigma / mean);
        }
        self.current_width_min = self
            .width_min
            .as_mut()
            // A missing width enters the floor as `+inf`, which is Python's
            // substitution and is what keeps the window length honest while
            // never being the minimum itself.
            .map(|floor| floor.push(self.current_width.unwrap_or(f64::INFINITY)))
            .unwrap_or(None);
        self.current_span_min = self
            .span_min
            .as_mut()
            .map(|floor| floor.push(candle.high - candle.low))
            .unwrap_or(None);
    }

    /// The channel readings, which EXCLUDE this candle and so are pushed after
    /// the signal has read them.
    pub(super) fn push_channels(&mut self, candle: &Candle) {
        if let Some(high) = &mut self.channel_high {
            high.push(candle.high);
        }
        if let Some(low) = &mut self.channel_low {
            low.push(candle.low);
        }
        // The line itself was advanced in `push_inclusive`; only the channel
        // over it belongs here, where it becomes visible to the NEXT bar.
        if let Some(obv) = &self.obv {
            let line = obv.total;
            if let Some(high) = &mut self.obv_high {
                high.push(line);
            }
            if let Some(low) = &mut self.obv_low {
                low.push(line);
            }
        }
    }

    /// `size`, the book's sizing rule rather than `cfd_families.quantity`.
    ///
    /// The margin ceiling deliberately reads FULL equity, not the shown figure:
    /// margin is a broker constraint on the real account and does not shrink or
    /// grow because the book chose to size differently. That asymmetry is the
    /// whole reason `SHOWN_EQUITY` cannot rescue a sleeve the account cannot
    /// margin.
    ///
    /// `FORCE_MINIMUM_LOT` rounds a positive request UP to `volume_min`, but
    /// only when the margin ceiling could have afforded that lot anyway. A
    /// margin refusal stays a refusal.
    pub(super) fn quantity(&self, equity: f64, entry: f64, distance: f64) -> Option<f64> {
        let money = self.spec.multiplier;
        if money <= 0.0 || distance <= 0.0 || equity <= 0.0 || entry <= 0.0 {
            return None;
        }
        let factor = self.sleeve.scale() * CANON_RISK_SCALE;
        if factor <= 0.0 {
            return None;
        }
        // Uncapped: the canon run records an empty `sizing_equity_cap`, so a
        // sleeve compounds against the whole shared balance.
        let sizing_equity = equity * self.sleeve.shown_equity();
        let risk = sizing_equity * RISK_FRACTION * factor * self.volatility_multiplier.value();
        let raw = risk / (distance * money);
        let step = self.quantity_step.max(self.spec.volume_step);
        // THE IMPORT-SIZED BRANCH IS A DIFFERENT RULE, NOT A RELAXED ONE.
        //
        // `nq:volatility_breakout` IS an `cfd_families` cell -- selected and
        // admitted like one -- but `exness_combined_strategies.EXTERNAL` lists
        // it, so `replay` sizes it on the imported branch:
        //
        //     raw  = units_per_dollar * sizing_equity * factor * mult
        //     lots = floor(raw / step) * step
        //     if force_minimum and raw > 0 and lots <= 0: lots = step
        //
        // That branch consults NEITHER the margin ceiling NOR `volume_max` NOR
        // `volume_min`: `units_per_dollar` is `0.015 / distance` and nothing
        // else, and the only floor is the lot step. Applying the cell ceiling
        // here made the port refuse orders Python fills, so it is skipped --
        // deliberately, the same way the `volume_min` gap already was. Both are
        // gaps in the Python model rather than rules worth having, and both are
        // reproduced because a book that charges itself a constraint its
        // reference run never paid is not the same book.
        //
        // It is safe on the canon account rather than merely tolerated: the
        // ceiling binds only when 0.00049 * mult * entry exceeds the stop
        // distance, which on NQ needs a 14-day mean daily range under ~164
        // points at the 3.0 multiplier cap.
        //
        // NO EPSILON ON THIS PATH EITHER. `size` floors `raw / step + 1e-10`;
        // the external branch is a plain `math.floor(raw / step)`.
        if self.sleeve.sized_as_import() {
            if raw <= 0.0 || !raw.is_finite() {
                return None;
            }
            let lots = (raw / step).floor() * step;
            return Some(if lots <= 0.0 && FORCE_MINIMUM_LOT {
                step
            } else {
                lots
            })
            .filter(|lots| *lots > 0.0);
        }
        let margin_per_lot =
            entry * self.spec.contract_size * self.spec.fx_to_usd * MARGIN_FRACTION;
        let ceiling = if margin_per_lot > 0.0 {
            self.spec.volume_max.min(equity / margin_per_lot)
        } else {
            0.0
        };
        let floor = self.spec.volume_min;
        let mut lots = (raw.min(ceiling) / step + 1e-10).floor() * step;
        if FORCE_MINIMUM_LOT && raw > 0.0 && lots + 1e-10 < floor && ceiling + 1e-10 >= floor {
            lots = floor;
        }
        (lots + 1e-10 >= floor && lots.is_finite()).then_some(lots)
    }

    /// `cfd_families.quantity`: the STANDALONE rule, at `ADMISSION_BALANCE`.
    ///
    /// Not a sizing function -- only its zero/non-zero answer is used. A cell's
    /// trade log is what this admits, and the book then re-sizes those trades
    /// against the shared balance. It differs from `quantity` in every term that
    /// matters: no risk scale, no shown equity, and a ONE-SIDED throttle that
    /// can only ever cut, against the frozen per-instrument `vol_target` rather
    /// than the book's EWMA multiplier.
    ///
    /// There is no force-to-minimum branch here, deliberately: `ef.quantity` has
    /// none, so a request under `volume_min` is a hard refusal and the trade
    /// does not exist.
    pub(super) fn admission_lots(&self, entry: f64, distance: f64, realized: f64) -> Option<f64> {
        let money = self.spec.multiplier;
        let equity = self.shadow_equity;
        if money <= 0.0 || distance <= 0.0 || entry <= 0.0 || equity <= 0.0 {
            return None;
        }
        let mut risk = equity * RISK_FRACTION;
        if realized > 0.0 {
            risk *= (self.vol_target / realized).min(1.0);
        }
        let raw = risk / (distance * money);
        let margin_per_lot =
            entry * self.spec.contract_size * self.spec.fx_to_usd * MARGIN_FRACTION;
        let ceiling = if margin_per_lot > 0.0 {
            self.spec.volume_max.min(equity / margin_per_lot)
        } else {
            0.0
        };
        let step = self.quantity_step.max(self.spec.volume_step);
        let lots = (raw.min(ceiling) / step + 1e-10).floor() * step;
        (lots + 1e-10 >= self.spec.volume_min && lots.is_finite()).then_some(lots)
    }

    /// Settles a closed trade into the shadow account.
    ///
    /// `cfd_families.backtest`'s own accounting: the gross move less the
    /// entry cost, times the standalone lots and the contract multiplier.
    /// Financing is omitted because it is zero for every sleeve in this book --
    /// thirteen never span a date boundary and JP225 quotes both swap legs at
    /// exactly 0.0.
    pub(super) fn settle_shadow(&mut self, position: &Position, exit: f64) {
        let gross = match position.side {
            Side::Long => exit - position.entry,
            Side::Short => position.entry - exit,
        };
        let cost = position.entry * (position.spread_bp + SLIPPAGE_BP) / 10_000.0;
        self.shadow_equity += (gross - cost) * position.shadow_lots * self.spec.multiplier;
    }

    /// `exit_plan_for`: `(target distance, trail multiple, bar count)`.
    ///
    /// Exactly one leg is ever set. The Python returns a three-tuple with two
    /// `None`s for the same reason: the axis picks one shape, and a cell that
    /// carried both a target and a trail would be two exits racing.
    pub(super) fn exit_plan(&self, distance: f64) -> (Option<f64>, Option<f64>, Option<usize>) {
        match self.params.exit {
            Exit::RewardMultiple(reward) => (Some(reward * distance), None, None),
            Exit::Trail(multiple) => (None, Some(multiple), None),
            Exit::Bars(bars) => (None, None, Some(bars)),
        }
    }
}

// -------------------------------------------------------------------------- //
// the signals
// -------------------------------------------------------------------------- //
//
// THE POLARITY CONVENTION IS NOT UNIFORM AND MUST NOT BE MADE SO. Most families
// end `return -side if direction == "fade" else side`; `floor_pivot` inverts on
// `follow` instead, because a touch-and-reject at R1 is already the fade and
// "following" it means buying into the level. `trap` has no direction axis at
// all -- the thesis IS the fade, and a `follow` cell would be "buy a breakout
// that has already failed", which is a Donchian entered late and worse.
// `confluence` spells the same idea as `mode`.
//
// So each signal below applies its own source's convention and returns the FINAL
// side. Routing them all through one `Direction::apply` would silently invert
// `tsla:floor_pivot`, which is a `fade` cell that returns its raw side.

impl FamilyEngine {
    /// The side to fill at the next candle's open, or `None` for no entry.
    ///
    /// Every reading here is the value the Python array holds at THIS index:
    /// inclusive indicators have already been folded in, and the channels have
    /// not, because `rolling_extreme` excludes the bar it is asked about.
    pub(super) fn signal(&self, candle: &Candle, minute: i64) -> Option<Side> {
        match self.params.family {
            Family::VolumeThrust {
                volume_mult,
                threshold_atr,
                ..
            } => self.volume_thrust(candle, minute, volume_mult, threshold_atr),
            Family::Zscore { threshold_z, .. } => self.zscore_signal(candle, minute, threshold_z),
            Family::VolatilityBreakout { fraction } => {
                self.volatility_breakout(candle, minute, fraction)
            }
            Family::Confluence { votes, .. } => self.confluence(candle, minute, votes),
            Family::Trap { window, .. } => self.trap(candle, minute, window),
            Family::ObvBreak { .. } => self.obv_break(candle, minute),
            Family::XmaCross { .. } => self.xma_cross(candle, minute),
            Family::Pullback { depth, .. } => self.pullback(candle, minute, depth),
            Family::GatedOrb { gate, polarity, .. } => {
                self.gated_orb(candle, minute, gate, polarity)
            }
            Family::TwoStage { coil, window, .. } => self.two_stage(candle, minute, coil, window),
            Family::BreakRetest {
                window,
                level,
                retest,
                ..
            } => self.break_retest(candle, minute, window, level, retest),
            Family::LevelConfluence { pair, tolerance } => {
                self.level_confluence(candle, minute, pair, tolerance)
            }
            Family::VolRegime {
                ratio,
                regime,
                lookback,
            } => self.vol_regime(minute, ratio, regime, lookback),
            Family::MomentumStack { threshold, session } => {
                self.momentum_stack(candle, minute, threshold, session)
            }
            Family::Kendall { threshold, .. } => self.kendall_signal(minute, threshold),
            Family::Cusum { .. } => self.cusum_signal(minute),
            Family::ObvDivergence { .. } => self.obv_divergence(candle, minute),
            Family::Kalman { mode, threshold } => {
                self.kalman_signal(candle, minute, mode, threshold)
            }
            Family::Fracdiff { threshold_z, .. } => self.fracdiff_signal(minute, threshold_z),
            Family::HalfLife {
                multiple,
                threshold_z,
                ..
            } => self.half_life_signal(candle, minute, multiple, threshold_z),
            Family::Aroon { min_strength, .. } => self.aroon_signal(minute, min_strength),
            Family::SwingMa { .. } => self.swing_ma_signal(),
            Family::Roofing { amplitude_atr, .. } => self.roofing_signal(minute, amplitude_atr),
            Family::Rvol {
                rvol,
                threshold_atr,
            } => self.rvol_signal(candle, minute, rvol, threshold_atr),
            Family::Efficiency { min_er, regime, .. } => {
                self.efficiency_signal(minute, min_er, regime)
            }
            Family::Cci { threshold, .. } => self.cci_signal(minute, threshold),
            Family::LinregTrend {
                period,
                slope,
                min_fit,
            } => self.linreg_trend_signal(minute, period, slope, min_fit),
        }
    }

    /// `efficiency_signal`: the move over `period`, if its path was the kind
    /// of path this cell trades.
    pub(super) fn efficiency_signal(&self, minute: i64, min_er: f64, regime: ErRegime) -> Option<Side> {
        let (ratio, net) = self.efficiency.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let clean = ratio >= min_er;
        if clean != (regime == ErRegime::Clean) {
            return None;
        }
        if net == 0.0 {
            return None;
        }
        let raw = if net > 0.0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// `cci_signal`. The raw side FADES the extreme; `follow` inverts it.
    pub(super) fn cci_signal(&self, minute: i64, threshold: f64) -> Option<Side> {
        let value = self.cci.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let raw = if value > threshold {
            Side::Short
        } else if value < -threshold {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Fade => raw,
            Direction::Follow => Direction::Fade.apply(raw),
        })
    }

    /// `linreg_trend_signal`: slope times period, in daily risk, past a
    /// threshold -- on a window whose fit is good enough.
    pub(super) fn linreg_trend_signal(
        &self,
        minute: i64,
        period: usize,
        threshold: f64,
        min_fit: f64,
    ) -> Option<Side> {
        let (slope, fit) = self.linreg.as_ref()?.value()?;
        let fit = fit?;
        let risk = self.risk.value().filter(|risk| *risk != 0.0)?;
        if self.late(minute) {
            return None;
        }
        if fit < min_fit {
            return None;
        }
        let travel = slope * period as f64 / risk;
        let raw = if travel > threshold {
            Side::Long
        } else if travel < -threshold {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `aroon_signal`: up crossing down, once either is strong enough.
    pub(super) fn aroon_signal(&self, minute: i64, min_strength: f64) -> Option<Side> {
        let ((up, down), (up_before, down_before)) = self.aroon.as_ref()?.readings()?;
        if self.late(minute) {
            return None;
        }
        if up.max(down) < min_strength {
            return None;
        }
        let now = up - down;
        let before = up_before - down_before;
        let raw = if before <= 0.0 && now > 0.0 {
            Side::Long
        } else if before >= 0.0 && now < 0.0 {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `swing_ma_signal`: the EMA cross. No `_late` -- Python never asks.
    pub(super) fn swing_ma_signal(&self) -> Option<Side> {
        let fast = self.fast_ema.as_ref()?.value()?;
        let slow = self.slow_ema.as_ref()?.value()?;
        let before = self.previous_cross?;
        let now = fast - slow;
        let raw = if before <= 0.0 && now > 0.0 {
            Side::Long
        } else if before >= 0.0 && now < 0.0 {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `roofing_signal`: a zero crossing of the band, if the band has been
    /// wide enough in ATRs over the last 21 readings to be worth trading.
    pub(super) fn roofing_signal(&self, minute: i64, amplitude_atr: f64) -> Option<Side> {
        let roofing = self.roofing.as_ref()?;
        let (value, previous) = roofing.readings()?;
        if self.late(minute) {
            return None;
        }
        let raw = if previous <= 0.0 && value > 0.0 {
            Side::Long
        } else if previous >= 0.0 && value < 0.0 {
            Side::Short
        } else {
            return None;
        };
        let atr = self
            .atr
            .value()
            .filter(|atr| *atr != 0.0 && atr.is_finite())?;
        if roofing.recent_amplitude()? < amplitude_atr * atr {
            return None;
        }
        Some(self.params.direction.apply(raw))
    }

    /// `rvol_signal`: unusual volume for this minute on a bar whose body is at
    /// least `threshold_atr` ATRs; the body's sign is the side.
    pub(super) fn rvol_signal(
        &self,
        candle: &Candle,
        minute: i64,
        rvol: f64,
        threshold_atr: f64,
    ) -> Option<Side> {
        let value = self.relative_volume.as_ref()?.value()?;
        let atr = self.atr.value().filter(|atr| *atr != 0.0)?;
        if self.late(minute) {
            return None;
        }
        if value < rvol {
            return None;
        }
        let body = candle.close - candle.open;
        if body.abs() < threshold_atr * atr {
            return None;
        }
        let raw = if body > 0.0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// A retracement from a recent extreme, measured in DAILY RANGE, inside a
    /// trend that is still intact.
    ///
    /// The one family here that requires a trend and then waits to be paid a
    /// WORSE price. Everything else fires at the edge of a move -- a channel
    /// break, a new extreme, a thrust bar -- so its entries cluster on the bars
    /// this one is explicitly sitting out, which makes them close to mutually
    /// exclusive by construction.
    pub(super) fn pullback(&self, candle: &Candle, minute: i64, depth: f64) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let top = self.channel_high.as_ref()?.value()?;
        let bottom = self.channel_low.as_ref()?.value()?;
        let reference = self.reference_ema.as_ref()?.value()?;
        let risk = self.risk.value().filter(|risk| *risk != 0.0)?;
        let raw = if candle.close > reference && {
            let retrace = (top - candle.close) / risk;
            retrace > 0.0 && retrace <= depth
        } {
            Side::Long
        } else if candle.close < reference && {
            let retrace = (candle.close - bottom) / risk;
            retrace > 0.0 && retrace <= depth
        } {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `gate_opinion`: one confirmer's view -- long, short, or NO VIEW.
    ///
    /// THE THREE-VALUED RETURN IS THE POINT. A gate that is merely readable is
    /// not the same as a gate that has something to say, and collapsing the two
    /// would turn a deliberately silent reading into a coin flip on exactly the
    /// bars it was built to sit out. `Some(0)` refuses under either polarity;
    /// `None` refuses too, but because the reading is missing rather than
    /// because the market was undecided.
    pub(super) fn gate_opinion(&self, candle: &Candle, gate: Gate) -> Option<i32> {
        match gate {
            Gate::Vwap => {
                let value = self.session.vwap()?;
                Some(if candle.close > value {
                    1
                } else if candle.close < value {
                    -1
                } else {
                    0
                })
            }
            Gate::Rsi => {
                let value = self.rsi.as_ref()?.value()?;
                Some(if value > 50.0 {
                    1
                } else if value < 50.0 {
                    -1
                } else {
                    0
                })
            }
        }
    }

    /// `gate_accepts`: whether the gate lets `side` through.
    pub(super) fn gate_accepts(
        &self,
        candle: &Candle,
        gate: Gate,
        side: Side,
        polarity: Polarity,
    ) -> bool {
        let Some(view) = self.gate_opinion(candle, gate).filter(|view| *view != 0) else {
            return false;
        };
        let wanted = match side {
            Side::Long => 1,
            Side::Short => -1,
        };
        match polarity {
            Polarity::Confirm => view == wanted,
            Polarity::Oppose => view == -wanted,
        }
    }

    /// The OPENING-RANGE break, taken only when a second reading agrees.
    ///
    /// THE RANGE COMES FROM THE PRECOMPUTED BLOCK rather than from signal
    /// state, and that is what separates this from `orb`. The engine calls a
    /// signal only on bars where no position is open, so a rule accumulating its
    /// own opening range measures a SHORT range on exactly the days it was
    /// already busy -- and the two families therefore do not produce identical
    /// trade sets on a symbol that trades early.
    ///
    /// NO `direction` AXIS. The break already produces a side, and the grid
    /// spells the follow-or-fade question as `polarity` on the GATE instead --
    /// which is a stronger claim, because it says the confirmer is contrary
    /// rather than that the breakout is.
    ///
    /// THE FIRST CANDLE OF THE SESSION CANNOT TRADE. Its own high and low ARE
    /// the range, so a close outside them is impossible and a close inside is
    /// not a break; Python refuses the whole bar rather than relying on the
    /// arithmetic to come out that way.
    pub(super) fn gated_orb(
        &self,
        candle: &Candle,
        minute: i64,
        gate: Gate,
        polarity: Polarity,
    ) -> Option<Side> {
        let (top, bottom) = self.opening.as_ref()?.value()?;
        if self.late(minute) || minute < self.spec.session.0 + BAR_SECONDS / 60 {
            return None;
        }
        let side = if candle.close > top {
            Side::Long
        } else if candle.close < bottom {
            Side::Short
        } else {
            return None;
        };
        self.gate_accepts(candle, gate, side, polarity)
            .then_some(side)
    }

    /// A coil arms the trade; the break of the coil's own range takes it.
    ///
    /// `squeeze` and `nr` both enter ON the compression bar, because the engine
    /// fills at the next open and cannot rest a stop order on a level -- so they
    /// trade the pause and guess the direction from where price came from. This
    /// one WAITS: the coil is a setup that expires, the entry is the close
    /// beyond the range built since the coil, and the direction is whichever
    /// side actually broke. On a coil that breaks downward after an uptrend the
    /// two take OPPOSITE sides of the same setup.
    ///
    /// The window is bars, not sessions, and is allowed to span the overnight
    /// gap for the same reason a Donchian channel is: the coil is a shape in the
    /// bar series, and cutting it at the close would make the family's meaning
    /// depend on where in the day the compression happened.
    pub(super) fn two_stage(
        &self,
        candle: &Candle,
        minute: i64,
        coil: Coil,
        window: usize,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        // `if index < window + 1: return None`. The ring holds the previous
        // `window + 1` candles once it is full, so its length is the same test.
        if self.levels.len() < window + 1 {
            return None;
        }
        // `range(index - window, index)`: the last `window` candles, oldest
        // first, and the ring's newest entry is `index - 1`.
        let scan = self.levels.len() - window;
        let armed = self.levels.iter().skip(scan).position(|past| match coil {
            Coil::Squeeze => match (past.width, past.width_min) {
                (Some(width), Some(floor)) => width <= 1.1 * floor,
                _ => false,
            },
            // `+ 1e-12` is Python's own tolerance: the bar IS in its own
            // inclusive minimum, so an exact equality is the ordinary case and
            // floating point must not be allowed to miss it.
            Coil::Nr => past
                .span_min
                .is_some_and(|floor| past.high - past.low <= floor + 1e-12),
        })?;
        // `max(bars[k][H] ...)` and `min(bars[k][L] ...)` for
        // `range(armed, index)` -- from the armed bar to the one before this.
        let (top, bottom) = self
            .levels
            .iter()
            .skip(scan + armed)
            .fold((f64::NEG_INFINITY, f64::INFINITY), |(top, bottom), past| {
                (top.max(past.high), bottom.min(past.low))
            });
        let raw = if candle.close > top {
            Side::Long
        } else if candle.close < bottom {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// A level breaks, price comes back to it, and it holds.
    ///
    /// The entry every breakout family is structurally unable to take. `pdr` and
    /// `donchian` fire on the bar that closes through the level and are then in
    /// a position, so the pullback that follows is something they sit through
    /// rather than something they can act on -- and the engine allows one entry
    /// a day, so even a flat rule could not re-enter. This DECLINES the break
    /// itself and waits for the retest, a strictly later bar and, on any break
    /// that pulls back at all, a better price.
    ///
    /// Tolerance is fixed at a quarter of ATR rather than swept. The axis that
    /// matters is how long the break is allowed to take to come back; sweeping
    /// both would turn one hypothesis into a grid over what "retest" means.
    pub(super) fn break_retest(
        &self,
        candle: &Candle,
        minute: i64,
        window: usize,
        level: BreakLevel,
        retest: Retest,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let atr = self.atr.value().filter(|atr| *atr != 0.0)?;
        let day = self.day_of(candle.ts);
        // `range(max(1, index - window), index)`: the last `window` candles,
        // oldest first, and same-day only.
        let scan = self.levels.len().saturating_sub(window);
        let mut broke = None;
        for past in self.levels.iter().skip(scan) {
            if past.day != day {
                continue;
            }
            let (top, bottom) = match level {
                BreakLevel::Pdr => match past.prior_range {
                    Some((high, low)) => (Some(high), Some(low)),
                    None => (None, None),
                },
                BreakLevel::Donchian => (past.top, past.bottom),
            };
            let (Some(top), Some(bottom)) = (top, bottom) else {
                continue;
            };
            if past.close > top {
                broke = Some((Side::Long, top));
                break;
            }
            if past.close < bottom {
                broke = Some((Side::Short, bottom));
                break;
            }
        }
        let (side, level_price) = broke?;
        let reference = match retest {
            Retest::Vwap => self.session.vwap()?,
            Retest::Level => level_price,
        };
        let edge = 0.25 * atr;
        match side {
            Side::Long => {
                (candle.low <= reference + edge && candle.close > reference).then_some(Side::Long)
            }
            Side::Short => {
                (candle.high >= reference - edge && candle.close < reference).then_some(Side::Short)
            }
        }
    }

    /// Two independent level systems landing on the same price.
    ///
    /// `floor_pivot` and `pdr` each trade their own level, and each is a
    /// single-source claim: R1 matters because floor traders used it,
    /// yesterday's high matters because it traded. This only fires where two
    /// systems derived DIFFERENTLY agree to within a fraction of ATR -- a
    /// coincidence, and one that says nothing about either system's own merit.
    /// The hypothesis is that a level several methods find is a level more
    /// people are watching.
    ///
    /// It is also a natural rarity filter, which is why its trade count has to
    /// be read before its return is.
    pub(super) fn level_confluence(
        &self,
        candle: &Candle,
        minute: i64,
        pair: LevelPair,
        tolerance: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let atr = self.atr.value().filter(|atr| *atr != 0.0)?;
        let (first, second) = match pair {
            LevelPair::PivotPdr => (self.pivot_levels()?, self.prior_range_levels()?),
            LevelPair::PivotVwap => (self.pivot_levels()?, self.vwap_levels()?),
            LevelPair::PdrVwap => (self.prior_range_levels()?, self.vwap_levels()?),
        };
        let (up_a, down_a) = first;
        let (up_b, down_b) = second;
        let edge = tolerance * atr;
        let follow = self.params.direction == Direction::Follow;
        // THE UPPER CLUSTER IS TESTED FIRST AND RETURNS, which is Python's own
        // order: on a day where both clusters exist and both are touched, the
        // resistance side wins.
        if (up_a - up_b).abs() <= edge {
            let cluster = 0.5 * (up_a + up_b);
            if candle.high >= cluster - edge {
                if follow && candle.close > cluster {
                    return Some(Side::Long);
                }
                if !follow && candle.close < cluster {
                    return Some(Side::Short);
                }
            }
        }
        if (down_a - down_b).abs() <= edge {
            let cluster = 0.5 * (down_a + down_b);
            if candle.low <= cluster + edge {
                if follow && candle.close < cluster {
                    return Some(Side::Short);
                }
                if !follow && candle.close > cluster {
                    return Some(Side::Long);
                }
            }
        }
        None
    }

    /// `_level_pair("pivot")`: the floor-trader R1 and S1 for today, derived
    /// from YESTERDAY's bar.
    fn pivot_levels(&self) -> Option<(f64, f64)> {
        let (_, r1, s1, _, _) = self.prior.pivots?;
        Some((r1, s1))
    }

    /// `_level_pair("pdr")`: yesterday's high and low.
    fn prior_range_levels(&self) -> Option<(f64, f64)> {
        self.prior.range
    }

    /// `_level_pair("vwap")`: the session VWAP, which is ONE price acting as
    /// BOTH sides.
    ///
    /// Above it, the VWAP is the level that has to hold; below it, the one that
    /// has to be reclaimed. Returning it twice is Python's own construction and
    /// not a placeholder -- it makes the "two systems agree" test degenerate to
    /// "the other system's level is where today's average trade is".
    fn vwap_levels(&self) -> Option<(f64, f64)> {
        let vwap = self.session.vwap()?;
        Some((vwap, vwap))
    }

    /// A directional bar on unusual volume, read as PARTICIPATION.
    ///
    /// `climax` reads the same volume as exhaustion. They fire on overlapping
    /// bars and disagree about the sign, which is the point.
    pub(super) fn volume_thrust(
        &self,
        candle: &Candle,
        minute: i64,
        volume_mult: f64,
        threshold_atr: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let average = self.volume_mean.as_ref()?.value()?;
        let atr = self.atr.value()?;
        if average <= 0.0 || atr <= 0.0 || candle.volume < volume_mult * average {
            return None;
        }
        let move_ = candle.close - candle.open;
        if move_.abs() < threshold_atr * atr {
            return None;
        }
        let raw = if move_ > 0.0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// A close stretched past `threshold_z` sigma of its own rolling
    /// distribution.
    pub(super) fn zscore_signal(
        &self,
        candle: &Candle,
        minute: i64,
        threshold_z: f64,
    ) -> Option<Side> {
        if minute > self.params.last_entry_minute {
            return None;
        }
        let (mean, sigma) = self.zscore.as_ref()?.value()?;
        if sigma <= 0.0 || self.atr.value().is_none() {
            return None;
        }
        let z = (candle.close - mean) / sigma;
        let raw = if z > threshold_z {
            Side::Short
        } else if z < -threshold_z {
            Side::Long
        } else {
            return None;
        };
        // `zscore` is written the other way round from the rest: its raw side is
        // already the FADE, so `follow` is what inverts it.
        Some(match self.params.direction {
            Direction::Fade => raw,
            Direction::Follow => Direction::Fade.apply(raw),
        })
    }

    /// Today's OPEN plus or minus a fraction of yesterday's range.
    ///
    /// Larry Williams' construction, and it is neither `orb` nor `pdr`. `orb`
    /// measures the first bars of today and extends by ATR; `pdr` uses
    /// yesterday's actual high and low as levels. This anchors on today's open --
    /// a price from this session -- and scales by yesterday's range, so its
    /// trigger moves with the gap. On a day that opens beyond yesterday's high,
    /// `pdr` is already triggered at the bell and this still requires a further
    /// push.
    pub(super) fn volatility_breakout(
        &self,
        candle: &Candle,
        minute: i64,
        fraction: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let (high, low) = self.prior.range?;
        let span = high - low;
        if span <= 0.0 {
            return None;
        }
        let opening = self.session.open;
        let edge = fraction * span;
        let raw = if candle.close > opening + edge {
            Side::Long
        } else if candle.close < opening - edge {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// Five exhaustion readings vote; trade the margin when it is wide enough.
    ///
    /// THE ONE FAMILY HERE THAT CANNOT BE WRITTEN AS A FILTER. Every gated
    /// family is `A and B`, so its trades are a subset of A's. A vote of three
    /// out of five fires on bars where NO single member is at an extreme, and it
    /// also refuses bars where one member is screaming and the other four are
    /// flat -- which is exactly where `rsi`, `zscore` and `wick` all enter.
    ///
    /// Three READABLE ballots are required, so a pool that has half degraded
    /// fails to a refusal rather than to a two-member vote wearing a five-member
    /// name.
    pub(super) fn confluence(&self, candle: &Candle, minute: i64, votes: i32) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let ballots = [
            self.vote_rsi_extreme(),
            self.vote_zscore(candle),
            self.vote_vwap_deviation(candle),
            self.vote_wick_rejection(candle),
            self.vote_range_position(candle),
        ];
        let counted: Vec<i32> = ballots.into_iter().flatten().collect();
        if counted.len() < 3 {
            return None;
        }
        let net: i32 = counted.iter().sum();
        if net.abs() < votes {
            return None;
        }
        let raw = if net > 0 { Side::Long } else { Side::Short };
        // `mode` rather than `direction`: the confluence grid spells the
        // crowding question as follow-or-fade of the ballot itself.
        Some(match self.params.direction {
            Direction::Follow => raw,
            Direction::Fade => Direction::Fade.apply(raw),
        })
    }

    /// `fade_side("rsi_extreme")`: the side a mean-reversion rule would take.
    pub(super) fn vote_rsi_extreme(&self) -> Option<i32> {
        let value = self.rsi.as_ref()?.value()?;
        Some(if value > 75.0 {
            -1
        } else if value < 25.0 {
            1
        } else {
            0
        })
    }

    /// `fade_side("zscore")`, at the pool's fixed 2.0 rather than the `zscore`
    /// family's swept threshold.
    pub(super) fn vote_zscore(&self, candle: &Candle) -> Option<i32> {
        self.atr.value().filter(|atr| *atr > 0.0)?;
        let (mean, sigma) = self.zscore.as_ref()?.value()?;
        if sigma <= 0.0 {
            return None;
        }
        let z = (candle.close - mean) / sigma;
        Some(if z > 2.0 {
            -1
        } else if z < -2.0 {
            1
        } else {
            0
        })
    }

    /// `fade_side("vwap_dev")`: distance from the session VWAP in ATRs.
    pub(super) fn vote_vwap_deviation(&self, candle: &Candle) -> Option<i32> {
        let atr = self.atr.value().filter(|atr| *atr > 0.0)?;
        let vwap = self.session.vwap()?;
        let distance = (candle.close - vwap) / atr;
        Some(if distance > 1.0 {
            -1
        } else if distance < -1.0 {
            1
        } else {
            0
        })
    }

    /// `pool_view("wick_rej")`: bar anatomy, which nothing else in the pool
    /// reads. A long lower wick is a price the market visited and rejected
    /// inside one bar.
    pub(super) fn vote_wick_rejection(&self, candle: &Candle) -> Option<i32> {
        let span = candle.high - candle.low;
        if span <= 0.0 {
            return None;
        }
        let top = candle.open.max(candle.close);
        let bottom = candle.open.min(candle.close);
        let upper = (candle.high - top) / span;
        let lower = (bottom - candle.low) / span;
        Some(if upper >= 0.5 && upper > lower {
            -1
        } else if lower >= 0.5 && lower > upper {
            1
        } else {
            0
        })
    }

    /// `pool_view("range_pos")`: where the close sits inside the day's path so
    /// far.
    pub(super) fn vote_range_position(&self, candle: &Candle) -> Option<i32> {
        let (top, bottom) = (self.session.high, self.session.low);
        if !top.is_finite() || !bottom.is_finite() || top <= bottom {
            return None;
        }
        let where_ = (candle.close - bottom) / (top - bottom);
        Some(if where_ > 0.8 {
            -1
        } else if where_ < 0.2 {
            1
        } else {
            0
        })
    }

    /// A breakout that CLOSED through its level and then closed back inside.
    ///
    /// `failed_break` is the one-bar version and this is not it. There, price
    /// pokes through and closes back inside WITHIN THE SAME BAR, so no breakout
    /// family ever entered -- it is a rejection wick with a level attached. Here
    /// the market actually closed beyond the level, which means a breakout rule
    /// took the trade and is now positioned, and the fade is taken against a
    /// position the study's own families are holding. That is a materially
    /// stronger claim and a much rarer bar.
    ///
    /// The level is read AT THE BREAK BAR, so a channel that has since widened
    /// past the failure cannot make the trap look like a level still holding.
    pub(super) fn trap(&self, candle: &Candle, minute: i64, window: usize) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let skip = self.levels.len().saturating_sub(window);
        for past in self.levels.iter().skip(skip) {
            let (Some(top), Some(bottom)) = (past.top, past.bottom) else {
                continue;
            };
            if past.close > top {
                return (candle.close < top).then_some(Side::Short);
            }
            if past.close < bottom {
                return (candle.close > bottom).then_some(Side::Long);
            }
        }
        None
    }

    /// On-balance volume breaking its own channel, price ignored.
    ///
    /// `volume_thrust` reads ONE bar's volume, so it sees events. This
    /// accumulates, so it sees whether the events have been one-sided over a
    /// stretch -- and it can break out while price has not, which is the whole
    /// reason to run it. The channel is on the OBV line itself, so no price
    /// level enters the trigger at all.
    pub(super) fn obv_break(&self, _candle: &Candle, minute: i64) -> Option<Side> {
        let top = self.obv_high.as_ref()?.value()?;
        let bottom = self.obv_low.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let value = self.obv.as_ref()?.total;
        let raw = if value > top {
            Side::Long
        } else if value < bottom {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// A fast TEMA crossing a slow one.
    ///
    /// Not `ma_cross` with more settings: `tema` adds its own smoothing error
    /// back twice, so it crosses BEFORE an SMA does. A cross that fires here and
    /// not there is early rather than different.
    pub(super) fn xma_cross(&self, _candle: &Candle, minute: i64) -> Option<Side> {
        let fast = self.fast_tema.as_ref()?.value()?;
        let slow = self.slow_tema.as_ref()?.value()?;
        let before = self.previous_cross?;
        if self.late(minute) {
            return None;
        }
        let now = fast - slow;
        let raw = if before <= 0.0 && now > 0.0 {
            Side::Long
        } else if before >= 0.0 && now < 0.0 {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// Short-horizon volatility crossing a multiple of its own long baseline.
    ///
    /// THE SAME TWO READINGS `vol_mode` FILTERS ON, ASKED THE OPPOSITE QUESTION.
    /// The filter asks whether a cell only works in the quiet half; this asks
    /// whether the TRANSITION is tradeable and in which direction. A filter can
    /// never answer that, because it never fires on anything -- it only removes
    /// bars.
    ///
    /// The DIRECTION comes from a plain one-session momentum, so the family is
    /// "the regime says trade, the recent move says which way". A zero move is
    /// refused rather than broken to a side.
    pub(super) fn vol_regime(
        &self,
        minute: i64,
        ratio: f64,
        regime: Regime,
        lookback: usize,
    ) -> Option<Side> {
        let short = self.short_volatility.value()?;
        let long = self.long_volatility.value().filter(|long| *long > 0.0)?;
        if self.late(minute) {
            return None;
        }
        let expanding = short / long >= ratio;
        if expanding != (regime == Regime::Expanding) {
            return None;
        }
        let closes = self.closes.as_ref()?;
        let move_ = closes.back(0)? - closes.back(lookback)?;
        let raw = if move_ > 0.0 {
            Side::Long
        } else if move_ < 0.0 {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// Three horizons of momentum, each weighted by how UNUSUAL it is.
    ///
    /// STANDARDISING IS WHAT MAKES SUMMING LEGAL. A three-session return and a
    /// twenty-session return have different variances, so adding them raw would
    /// make the longest horizon the only one that mattered and would quietly
    /// reduce this to a one-horizon rule.
    ///
    /// The dispersion is the dispersion of CLOSES over TWICE the horizon, not of
    /// returns -- which is what the prefix sums already tabulate, and is the
    /// scale the move has to be read against.
    pub(super) fn momentum_stack(
        &self,
        candle: &Candle,
        minute: i64,
        threshold: f64,
        session: usize,
    ) -> Option<Side> {
        if self.late(minute) || candle.close <= 0.0 {
            return None;
        }
        let closes = self.closes.as_ref()?;
        let mut score = 0.0;
        for span in [session, 5 * session, 20 * session] {
            // `index < 2 * span` in Python, which is one bar MORE than the
            // variance window needs: the guard is on the index and the window
            // runs `index + 1 - 2 * span ..= index`.
            let before = closes.back(span).filter(|value| *value > 0.0)?;
            closes.back(2 * span)?;
            let variance = closes.variance(2 * span)?;
            if variance <= 1e-12 {
                return None;
            }
            score += (candle.close - before) / variance.sqrt();
        }
        let raw = if score >= threshold {
            Side::Long
        } else if score <= -threshold {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// Mann-Kendall's monotone-trend z past a threshold in SIGMA.
    ///
    /// `xma_cross` and every other trend reading here is a weighted mean of the
    /// window and is dominated by its largest term. This counts only the SIGN of
    /// each pairwise comparison, so one violent bar contributes at most its
    /// share of the count -- measured at a 2.0% loss against a least-squares
    /// slope's 6.9% on the same contaminated window.
    pub(super) fn kendall_signal(&self, minute: i64, threshold: f64) -> Option<Side> {
        let value = self.kendall.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let raw = if value >= threshold {
            Side::Long
        } else if value <= -threshold {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// The CUSUM filter's event, which is already a side.
    ///
    /// NOTHING TO READ AT THIS INDEX BEYOND THE EVENT. The accumulator and its
    /// reset live in the indicator, because the filter is stateful and the same
    /// drift must not be able to fire it twice -- so a signal that recomputed
    /// the sum from a window would be a different family with the same name.
    pub(super) fn cusum_signal(&self, minute: i64) -> Option<Side> {
        let raw = match self.cusum.as_ref()?.value() {
            1 => Side::Long,
            -1 => Side::Short,
            _ => return None,
        };
        if self.late(minute) {
            return None;
        }
        Some(self.params.direction.apply(raw))
    }

    /// Price makes a new channel extreme; cumulative volume does not follow.
    ///
    /// THE POLARITY IS INVERTED RELATIVE TO EVERY OTHER FAMILY HERE, and it is
    /// the Python's: `-side if direction == "follow" else side`. The raw side is
    /// already the FADE -- a break the flow did not pay for is a break to sell
    /// -- so "following" the divergence means going with the break instead, and
    /// `jp225:obv_divergence` is a `follow` cell. Routing this through
    /// `Direction::apply` would silently take the wrong side of every trade.
    pub(super) fn obv_divergence(&self, candle: &Candle, minute: i64) -> Option<Side> {
        let top = self.channel_high.as_ref()?.value()?;
        let bottom = self.channel_low.as_ref()?.value()?;
        let obv_top = self.obv_high.as_ref()?.value()?;
        let obv_bottom = self.obv_low.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let line = self.obv.as_ref()?.total;
        let raw = if candle.close > top && line < obv_top {
            Side::Short
        } else if candle.close < bottom && line > obv_bottom {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Follow => Direction::Fade.apply(raw),
            Direction::Fade => raw,
        })
    }

    /// The Kalman state, read as a RATE or as a RESIDUAL.
    ///
    /// The threshold is dimensionless in both modes and the denominator is what
    /// makes it so: the slope is a per-bar log return, so it is divided by
    /// `atr / close` -- the bar's own typical log move -- while the residual is
    /// a price distance and is divided by the ATR itself. Comparing either
    /// against a raw number would make the cell mean something different on
    /// every market.
    pub(super) fn kalman_signal(
        &self,
        candle: &Candle,
        minute: i64,
        mode: KalmanMode,
        threshold: f64,
    ) -> Option<Side> {
        let filter = self.kalman.as_ref()?;
        let level = filter.level()?;
        let slope = filter.slope()?;
        if self.late(minute) {
            return None;
        }
        let atr = self
            .atr
            .value()
            .filter(|atr| *atr != 0.0 && atr.is_finite())?;
        let raw = match mode {
            KalmanMode::Slope => {
                if candle.close <= 0.0 {
                    return None;
                }
                let unit = atr / candle.close;
                if unit <= 0.0 {
                    return None;
                }
                let strength = slope / unit;
                if strength >= threshold {
                    Side::Long
                } else if strength <= -threshold {
                    Side::Short
                } else {
                    return None;
                }
            }
            KalmanMode::Residual => {
                // THE SIGN IS THE OTHER WAY ROUND, because this leg is a
                // mean-reversion statement: price far BELOW the filtered level
                // is the long.
                let gap = (candle.close - level) / atr;
                if gap <= -threshold {
                    Side::Long
                } else if gap >= threshold {
                    Side::Short
                } else {
                    return None;
                }
            }
        };
        Some(self.params.direction.apply(raw))
    }

    /// The fractionally differenced series, z-scored against its own window.
    ///
    /// INVERTED ON `follow` LIKE `obv_divergence`, and again it is the Python's:
    /// `-side if direction == "follow" else side`. The raw side is the FADE of
    /// the deviation, so `usdjpy:fracdiff` -- a `follow` cell -- buys a series
    /// that has stretched UPWARD away from its own trailing mean.
    pub(super) fn fracdiff_signal(&self, minute: i64, threshold_z: f64) -> Option<Side> {
        let value = self.fracdiff.as_ref()?.value()?;
        let (mean, sigma) = self.fracdiff_stats.as_ref()?.value()?;
        if sigma <= 0.0 || self.late(minute) {
            return None;
        }
        let z = (value - mean) / sigma;
        let raw = if z >= threshold_z {
            Side::Short
        } else if z <= -threshold_z {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Follow => Direction::Fade.apply(raw),
            Direction::Fade => raw,
        })
    }

    /// A z-score over a window the OU fit measured, not one the search chose.
    ///
    /// THE INDEX GUARD IS PYTHON'S, NOT A BOUNDS CHECK. Python refuses at
    /// `index < span`, which is one bar MORE than the window needs -- the mean
    /// runs `index + 1 - span ..= index` and would be computable a bar earlier.
    /// `closes.back(span)?` is that guard and not the weaker one, the same way
    /// `momentum_stack` spells it.
    pub(super) fn half_life_signal(
        &self,
        candle: &Candle,
        minute: i64,
        multiple: f64,
        threshold_z: f64,
    ) -> Option<Side> {
        let life = self.half_life.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        // `int(round(...))`, AND PYTHON'S `round` IS HALF-TO-EVEN. Rust's
        // `f64::round` is half-away-from-zero, so the two disagree on an exact
        // `.5` -- reachable here because `multiple` is 2.0 and a life ending in
        // exactly `.25` doubles onto the tie. One bar of window is one different
        // z-score, which is a different trade.
        let span = (round_half_even(life * multiple) as i64).max(2) as usize;
        let closes = self.closes.as_ref()?;
        closes.back(span)?;
        let (mean, variance) = closes.mean_variance(span)?;
        if variance <= 1e-12 {
            return None;
        }
        let z = (candle.close - mean) / variance.sqrt();
        let raw = if z >= threshold_z {
            Side::Short
        } else if z <= -threshold_z {
            Side::Long
        } else {
            return None;
        };
        // Written like `fracdiff` and `zscore`: the raw side is already the
        // FADE, so `follow` is what inverts it.
        Some(match self.params.direction {
            Direction::Follow => Direction::Fade.apply(raw),
            Direction::Fade => raw,
        })
    }
}

// -------------------------------------------------------------------------- //
// the candle loop
// -------------------------------------------------------------------------- //

impl FamilyEngine {
    /// One completed in-session candle, in the order `cfd_families.backtest`
    /// walks its bar list.
    pub(super) fn on_candle(
        &mut self,
        candle: Candle,
        equity: f64,
        last_of_day: bool,
    ) -> Vec<Action> {
        let day = self.day_of(candle.ts);
        let minute = self.minute_of(candle.ts);
        let (_, close_minute) = self.spec.session;
        // BEFORE ANYTHING READS IT, so `candles` is this candle's own index.
        // Python's `index` is the loop variable, already advanced when the exit
        // block runs and still advanced when the entry records it.
        self.candles += 1;
        // This candle's entry already went out on its first minute; see
        // `enable_early_fills`. Taken, so it can apply to this candle only.
        let filled_early = self.early_filled_candle.take() == Some(self.candles);

        // Inclusive readings first: at this point every one of them holds what
        // the Python array holds at this index. The channels deliberately do
        // not, and are pushed at the end.
        let day_changed = self.session.day != Some(day);
        self.session.push(&candle, day);
        self.prior.push(&candle, day);
        self.push_inclusive(&candle, day);
        // IN-SESSION CANDLE CLOSES, WHICH IS WHAT A NATIVE SLEEVE READS.
        // `replay` builds `vol_mult` from `price_at[symbol]`, and for a native
        // market that dict is `{bar[TS]: bar[C]}` over the CONTEXT's bars --
        // session-filtered 30-minute closes, exactly these.
        //
        // UNCONDITIONAL SINCE 2026-09-04. `price_at` used to be OVERWRITTEN for
        // any market an external sleeve traded, which put every NQ cell on the
        // raw minute series whether it was external or not
        // ([[vol-throttle-is-per-market-not-per-sleeve]]). `EXTERNAL` reaches no
        // market this book trades any more, so every sleeve is on its own
        // context bars.
        self.volatility_multiplier.push(candle.close, day_changed);

        let mut actions = Vec::new();

        // 1. EXIT. Session flatten, then stop, then target, then the trail
        //    ratchet -- and the ratchet only runs on a candle that did not exit,
        //    which is why it is an `else` in the Python rather than a step.
        if !filled_early && let Some(position) = &mut self.position {
            let mut price = None;
            // UNCONDITIONAL, because `family_hold` is "session" for all
            // twenty-two families this book selects. Python's `swing` branch
            // is `daily or (hold in ("swing", "overnight") and not
            // SESSION_ONLY)`, and both halves are false here whatever the flag
            // says -- the one swing cell the book carried, `jp225:swing_donchian`,
            // went non-positive the moment `ef.SESSION_ONLY` took its hold away
            // and left the book with it.
            //
            // IT FIRES ON THE LAST IN-SESSION CANDLE, NOT ON A FIXED MINUTE.
            // `minute >= close_minute` needs a candle to actually PRINT at or
            // after the configured close, and on a native 30-minute market it
            // usually does not: `xniusd_30m` stops at 13:30 while `INSTRUMENTS`
            // closes it at 14:00, so that candle existed on 34 of 411 days. On
            // the other 92% the flatten never fired and a session cell ran on
            // its trailing stop instead, holding a median of twenty hours and a
            // maximum of thirty-five days -- which is what killed
            // `xalusd:gated_fade`.
            //
            // Where the close candle does print -- uk100 on 99.8% of days,
            // usdjpy 99.8%, ukoil 97.4% -- the last in-session candle IS the
            // candle at the close, so this can only ADD flattens that were meant
            // to happen. It never moves one.
            if minute >= close_minute || last_of_day {
                price = Some(candle.open);
            } else {
                let stop = position.stop;
                let breached = match position.side {
                    Side::Long => candle.low <= stop,
                    Side::Short => candle.high >= stop,
                };
                if breached {
                    price = Some(match position.side {
                        Side::Long => candle.open.min(stop),
                        Side::Short => candle.open.max(stop),
                    });
                } else if let Some(target) = position.target {
                    let reached = match position.side {
                        Side::Long => candle.high >= target,
                        Side::Short => candle.low <= target,
                    };
                    if reached {
                        price = Some(match position.side {
                            Side::Long => candle.open.max(target),
                            Side::Short => candle.open.min(target),
                        });
                    }
                }
                // LAST OF THE THREE, and the order is the Python's: a candle
                // that both reaches the bar count and trades through the stop
                // books the STOP. Checking the clock first would hand those
                // candles an exit at the open, which is a better price than the
                // position was entitled to on exactly the bars that went wrong.
                if price.is_none()
                    && let Some(limit) = position.max_bars
                    && self.candles - position.entry_candle >= limit as u64
                {
                    price = Some(candle.open);
                }
            }
            match price {
                Some(idealised) => {
                    let price = self.exit_fill(candle.ts, candle.open, idealised);
                    let closed = self.position.take().expect("checked just above");
                    self.settle_shadow(&closed, price);
                    actions.push(Action::Close {
                        price,
                        fraction: 1.0,
                    });
                }
                None => {
                    // Trailed on the DAILY range for the same reason the stop is
                    // set from it: a trail in bar ATR would tighten every time
                    // the bar got finer.
                    if let (Some(trail), Some(risk)) = (position.trail, self.risk.value())
                        && risk > 0.0
                    {
                        match position.side {
                            Side::Long => {
                                position.best = position.best.max(candle.close);
                                position.stop = position.stop.max(position.best - trail * risk);
                            }
                            Side::Short => {
                                position.best = position.best.min(candle.close);
                                position.stop = position.stop.min(position.best + trail * risk);
                            }
                        }
                    }
                }
            }
        }

        // 2. FILL. A signal read on the previous candle enters at THIS candle's
        //    open -- unless live already filled it on this candle's first
        //    minute (`fill_early`), in which case there is nothing left pending.
        if let Some(enter) = self.fill_pending(candle.ts, candle.open, equity, self.candles) {
            actions.push(enter);
        }

        // 3. SIGNAL. At most one position and one new entry a day, and the
        //    volatility filter is checked BEFORE the signal is called -- which
        //    is why anything reading the day's path has to read it from
        //    `SessionPath` rather than accumulate its own.
        // 1970-01-01, day 0, was a Thursday -- so Monday is `(day + 3) % 7 == 0`.
        let weekday_allowed = self
            .entry_days
            .is_none_or(|mask| mask & (1 << (day + 3).rem_euclid(7)) != 0);
        let tradeable = (self.spec.session.0..close_minute).contains(&minute) && weekday_allowed;
        if self.position.is_none()
            && !filled_early
            && self.pending.is_none()
            && self.traded_day != Some(day)
            && tradeable
            && self.accepts_vol()
            && let Some(side) = self.signal(&candle, minute)
        {
            // `present` on every one of these. A missing reading that reaches
            // `distance` stops being recoverable, and this is the guard that
            // matters most in the whole module.
            let readings = (
                self.atr.value(),
                self.risk.value(),
                self.short_volatility.value(),
            );
            if let (Some(atr), Some(risk), Some(_)) = readings
                && atr > 0.0
                && atr.is_finite()
                && risk > 0.0
                && risk.is_finite()
                && self.accepts_trend(candle.close, side)
            {
                let realized = self.short_volatility.value().unwrap_or(0.0);
                self.pending = Some(Pending {
                    side,
                    day,
                    distance: self.params.stop_day * risk,
                    realized,
                });
            }
        }

        // 4. Advance the exclusive readings, now that everything has read them
        //    at this index.
        let level = LevelHistory {
            day,
            high: candle.high,
            low: candle.low,
            close: candle.close,
            top: self.channel_high.as_ref().and_then(RollingExtreme::value),
            bottom: self.channel_low.as_ref().and_then(RollingExtreme::value),
            prior_range: self.prior.range,
            width: self.current_width,
            width_min: self.current_width_min,
            span_min: self.current_span_min,
        };
        self.push_channels(&candle);
        if self.level_window > 0 {
            self.levels.push_back(level);
            while self.levels.len() > self.level_window {
                self.levels.pop_front();
            }
        }
        if let (Some(fast), Some(slow)) = (
            self.fast_tema.as_ref().and_then(Tema::value),
            self.slow_tema.as_ref().and_then(Tema::value),
        ) {
            self.previous_cross = Some(fast - slow);
        }
        if let (Some(fast), Some(slow)) = (
            self.fast_ema.as_ref().and_then(Ema::value),
            self.slow_ema.as_ref().and_then(Ema::value),
        ) {
            self.previous_cross = Some(fast - slow);
        }

        actions
    }
}

impl Strategy for FamilyEngine {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        self.update_all(bar, equity)
            .into_iter()
            .next()
            .unwrap_or(Action::Hold)
    }

    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.action_timestamp = None;
        // The shift is applied here, once, so every day and minute below sits on
        // the clock the Python study selected this cell on.
        let ts = bar.ts + self.spec.shift_hours * 3_600;
        let slot = ts.div_euclid(BAR_SECONDS);
        if let Some(previous) = self.last_bar_ts {
            let gap = ts - previous;
            if gap > 0 && self.source_step.is_none_or(|step| gap < step) {
                self.source_step = Some(gap);
            }
        }
        self.last_bar_ts = Some(ts);
        let mut actions = Vec::new();
        if self.slot != Some(slot) {
            if let Some(candle) = self.building.take() {
                let minute = self.minute_of(candle.ts);
                if self.in_session(minute) {
                    // WHETHER THAT CANDLE WAS THE DAY'S LAST, ANSWERED BY THE
                    // SLOT THAT ENDED IT.
                    //
                    // Python asks `day_of[index + 1] != day` over a
                    // session-filtered array, which is a look-ahead a streaming
                    // engine cannot make -- except that a slot is only closed
                    // when a bar of the NEXT one arrives, so the successor is in
                    // hand at exactly this moment. It is the last in-session
                    // candle of its day when that successor falls on a different
                    // day, or outside the session at all: the session is one
                    // contiguous window a day, so a candle outside it means the
                    // day's session is over rather than merely interrupted.
                    //
                    // THE SUCCESSOR'S SLOT, NOT THE ARRIVING BAR'S OWN MINUTE,
                    // and the difference is a whole extra exit. `context`
                    // session-filters the AGGREGATED bar, so a candle is in
                    // session when its SLOT is -- a slot that opens at 10:00
                    // qualifies even if the only minute in it printed at 10:15,
                    // which is outside a window closing at 10:00. HK50 gaps
                    // 03:29-04:15 real on some days, so the candle after its
                    // 09:00 one is built entirely from out-of-window minutes;
                    // reading the bar's own minute called that the end of the
                    // session and flattened an hour early at a worse price.
                    let opening = slot * BAR_SECONDS;
                    let last_of_day = !self.in_session(self.minute_of(opening))
                        || self.day_of(opening) != self.day_of(candle.ts);
                    actions = self.on_candle(candle, equity, last_of_day);
                    if !actions.is_empty() {
                        self.action_timestamp = Some(candle.ts - self.spec.shift_hours * 3_600);
                    }
                }
            }
            self.slot = Some(slot);
            self.building = Some(Candle {
                ts: slot * BAR_SECONDS,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume: bar.volume,
            });
            // THE ENTRY GOES OUT NOW, on the candle's first minute, whose open
            // IS the candle's open -- the price the backtest books. See
            // `enable_early_fills`. Only for a candle `on_candle` will later
            // step, which is an in-session one.
            let opening = slot * BAR_SECONDS;
            if self.fill_early
                && self.in_session(self.minute_of(opening))
                && let Some(enter) = self.fill_pending(opening, bar.open, equity, self.candles + 1)
            {
                self.early_filled_candle = Some(self.candles + 1);
                if self.action_timestamp.is_none() {
                    self.action_timestamp = Some(opening - self.spec.shift_hours * 3_600);
                }
                actions.push(enter);
            }
        } else if let Some(candle) = &mut self.building {
            candle.high = candle.high.max(bar.high);
            candle.low = candle.low.min(bar.low);
            candle.close = bar.close;
            candle.volume += bar.volume;
        }
        // ACT WHEN THE CANDLE IS FINISHED, NOT WHEN THE NEXT ONE STARTS.
        //
        // The block above can only close a slot when a bar of the FOLLOWING one
        // turns up, and that bar cannot exist until its own period has closed and
        // published. Live, every market here arrives as `<symbol>_1m`, so a
        // 30-minute candle whose last minute landed at T+3 sat unevaluated until
        // T+63 -- with nothing missing, on a decision whose every input was
        // already in memory. Measured on `mt5_execution_commands` for 2026-09-07,
        // with the watermark hold-back on top: usdjpy and ukoil +125s, ethusd
        // +95s, against the 1.6s `fill_models.exness` charges.
        //
        // The final source bar identifies itself -- `ts + source_step` lands
        // exactly on the slot's end -- so the candle is closed here instead, and
        // the block above then finds `building` already taken and does nothing
        // when the next bar eventually arrives.
        //
        // `last_of_day` IS THE ONE THING THIS CANNOT READ FROM A BAR, and the
        // difference is deliberate. Above it is answered by WHICH slot the next
        // bar fell in; here there is no next bar, so it is answered from the
        // session and the clock. The two agree wherever the feed is contiguous.
        // They part on a market that stops mid-session and does not print again
        // until the next day: the arrival rule then reads the day as over and
        // flattens, and this one does not.
        //
        // THAT PARTING COSTS NOTHING LIVE, which is why it is accepted. At the
        // moment the hole opens, nothing can tell "the next slot has not
        // published yet" from "it never will" -- so the runtime does not try. It
        // carries `session_overdue`, which flattens on its own clock half an hour
        // past the session close needing no bar at all, and that is what closes
        // such a position today as well. A backtest replaying a session-filtered
        // array does know, which is why the parity fixtures see the arrival rule
        // and only the live minute feed sees this one.
        if let (Some(step), true) = (self.source_step, self.building.is_some()) {
            let slot_end = (slot + 1) * BAR_SECONDS;
            // EXACTLY the slot's end, never merely past it. `source_step` is the
            // smallest gap seen so far, so early in a stream it can still be an
            // over-estimate, and `>=` would let one of those close a candle that
            // is not finished. An equality can only be wrong by being missed, and
            // a miss costs nothing: the block above still closes the slot when
            // the next bar arrives, exactly as before. The same fall-back covers
            // a slot whose final period never printed.
            if step < BAR_SECONDS && ts + step == slot_end {
                let candle = self.building.take().expect("checked just above");
                let candle_ts = candle.ts;
                if self.in_session(self.minute_of(candle_ts)) {
                    let last_of_day = !self.in_session(self.minute_of(slot_end))
                        || self.day_of(slot_end) != self.day_of(candle_ts);
                    let closed = self.on_candle(candle, equity, last_of_day);
                    if !closed.is_empty() {
                        // The SAME stamp the arrival path records: the closed
                        // candle's own opening, unshifted. Only the moment of the
                        // decision has moved, not the bar it is attributed to.
                        self.action_timestamp = Some(candle_ts - self.spec.shift_hours * 3_600);
                    }
                    actions.extend(closed);
                }
            }
        }
        actions
    }

    fn action_timestamp(&self, default: i64) -> i64 {
        self.action_timestamp.unwrap_or(default)
    }

    /// `size` again, against the balance the closes at this instant left behind.
    ///
    /// Nothing the position holds depends on the book's lot count -- the stop,
    /// the target, the trail and the shadow account are all set from the price
    /// and the distance -- so re-answering here changes only what the engine
    /// fills, which is exactly the number `replay` computes after settling.
    fn resize_entry(&self, price: f64, quantity: f64, _shown: f64, actual: f64) -> f64 {
        self.position
            .as_ref()
            .and_then(|position| self.quantity(actual, price, position.distance))
            .unwrap_or(quantity)
    }

    fn discard(&mut self, action: Action) {
        if matches!(action, Action::Enter { .. }) {
            self.position = None;
            self.traded_day = None;
        }
    }

    /// Called by the engine at the backtest boundary, after the preroll has
    /// warmed the indicators and before the reported window opens.
    ///
    /// THE SHADOW ACCOUNT RESETS HERE TOO, and it has to. `cfd_families`
    /// starts its standalone run AT `lo` with `initial` in hand -- it never
    /// trades the warm-up -- so a shadow balance that had been compounding
    /// through the preroll would answer the admission question against an
    /// equity Python never had. On `jp225:swing_donchian` that alone was five
    /// extra trades: Python refused an entry at $940.70 of standalone equity
    /// that Rust admitted at a balance inflated by a year of preroll P&L.
    fn reset_trading_state(&mut self) {
        self.pending = None;
        self.position = None;
        self.traded_day = None;
        self.shadow_equity = ADMISSION_BALANCE * self.sleeve.shown_equity();
        self.coverage = FillCoverage::default();
    }

    fn entry_cost_bp(&self) -> Option<f64> {
        self.entry_cost_bp
    }

    /// Only `position`, and deliberately nothing else.
    ///
    /// `pending` is an entry the strategy has SIGNALLED and not yet filled --
    /// live will fill it on the next bar, which is a trade it should take.
    /// `traded_day` stays set so a once-a-day sleeve does not get a second
    /// attempt today: "the next signal" is tomorrow's, and that is what the
    /// backtest would do too. `shadow_equity` stays where the replay left it,
    /// because it answers the admission question and rewinding it would admit
    /// trades Python refused.
    fn abandon_open_position(&mut self) {
        self.position = None;
    }

    /// Real New York minutes, because the engine's flattener does not know about
    /// the shifted clock.
    fn session_end_minute(&self) -> Option<usize> {
        let real = (self.spec.session.1 - self.spec.shift_hours * 60).rem_euclid(1_440);
        Some(real as usize)
    }

    /// The same un-shift applied to the session OPEN. JP225's 01:00 shifted open
    /// is 19:00 New York, which is ABOVE its 02:00 close -- the wrap the live
    /// watchdog has to read correctly.
    fn session_start_minute(&self) -> Option<usize> {
        let real = (self.spec.session.0 - self.spec.shift_hours * 60).rem_euclid(1_440);
        Some(real as usize)
    }

    /// The Python cell exits at the session-close candle's OPEN; the engine's
    /// generic flattener closes on the LAST in-session bar at its CLOSE. Running
    /// both means the engine fires first and this strategy's own exit never
    /// happens, which is what made `XALUSD PDR` return -2.08% against Python's
    /// +31.17% ([[double-session-flatten-cut-targets-short]]).
    ///
    /// `session_end_minute` is still reported, because the LIVE runtime drives
    /// its own flatten from it and must keep doing so
    /// ([[live-vs-backtest-parity]]).
    fn flattens_itself(&self) -> bool {
        true
    }

    // `entry_risk_fraction` AND `entry_stop_price` ARE DELIBERATELY NOT
    // IMPLEMENTED, and implementing them silently deletes most of the book.
    //
    // The engine reads that pair as "size this entry for me": when BOTH are
    // present it discards the strategy's own `quantity` and substitutes
    // `risk_limited_quantity`, which knows nothing about the market's contract
    // spec, the sleeve's scale, its shown equity or the volatility multiplier.
    // On JP225 that asked for `400 * 0.0031 / 200` = 0.006 lots, `risk_size`
    // floored it to zero, and the entry was dropped WITHOUT even reaching the
    // broker-minimum counter -- 204 entries became 1, and the book reported a
    // 0.00% return that looked like a strategy with no signals rather than a
    // sizing hook that had eaten them.
    //
    // `exness_combined` sizes itself, exactly as the commodity and index books
    // did. Leaving both hooks at their `None` default is what lets `quantity`
    // through to the broker-minimum check.
}
