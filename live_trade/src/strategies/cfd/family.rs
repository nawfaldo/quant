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
pub(crate) enum Direction {
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
pub(crate) fn round_half_even(value: f64) -> f64 {
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
    pub(crate) fn apply(self, side: Side) -> Side {
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
pub(crate) enum Trend {
    None,
    Ema20d,
    Ema50d,
}

/// The `vol_mode` axis, a boolean on short-against-long realised volatility.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum VolMode {
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
pub(crate) enum Exit {
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
    /// `signal`: the family's own exit rule, read on each held candle's close
    /// and acted on at the next candle's open (`EXIT_SIGNALS`).
    Signal,
    /// `days_N`: leave at the open once `N * per_session` candles have passed.
    Days(usize),
    /// `level`: a target at the price level the SIGNAL named, carried as a
    /// distance from the signal bar's close and applied from the fill. A
    /// signal that names none trades with no target.
    Level,
}

#[derive(Clone, Copy, Debug)]
pub(crate) enum Family {
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
        level: BreakLevel,
    },
    ObvBreak {
        channel: usize,
    },
    /// A fast average crossing a slow one, where the AVERAGE TYPE is the axis
    /// (`kind`): TEMA, WMA or HMA here.
    XmaCross {
        fast: usize,
        slow: usize,
        kind: MaKind,
    },
    /// The move over one session, taken only when the variance behind it was
    /// (`jumpy`) or was not a JUMP: bipower variation's share of realised
    /// variance over `period` bars against `share`. Seated 2026-09-30 as
    /// `btc:jump`.
    Jump {
        period: usize,
        share: f64,
        jumpy: bool,
        /// `ctx["periods"]["session"]`, the horizon the move is read over.
        lookback: usize,
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
    /// `pullback` with the retrace depth scaled by the Hurst exponent over
    /// `period` closes: `depth * clamp(1 + response * (H - 0.5) * 2, 0.5, 2)`.
    /// Seated 2026-10-12 as `usdjpy:adaptive_pullback@60m-barlb`.
    AdaptivePullback {
        channel: usize,
        depth: f64,
        /// The trend EMA the pullback is measured against, in bars.
        reference: usize,
        period: usize,
        response: f64,
    },
    /// Trades through yesterday's extreme by `buffer_atr` ATR and closes back
    /// inside it. Python's raw side is the FADE of the break and `follow`
    /// inverts it. Seated 2026-10-12 as `usdjpy:failed_break@5m-barlb`.
    FailedBreak {
        buffer_atr: f64,
    },
    /// The bar Wilder's parabolic SAR (0.02 / 0.2) flips. Seated 2026-10-12
    /// as `usdjpy:sar@30m-barlb-off`.
    Sar,
    /// `exness_fastbar`'s `bollinger` (`fb_`/`sb_`): the close through the
    /// `period`-bar mean +- `k` standard deviations (`reenter`: back inside).
    /// Seated 2026-10-12 as the multi-day `ethusd:sb_bollinger@240m-nat`.
    FbBollinger {
        period: usize,
        k: f64,
        reenter: bool,
    },
    // ---- the TikTok-creator families (`cfd_tt_families`), see `tt` ----
    //
    // Each returns its OWN stop distance with its side, so `stop_day` is
    // inert for them, and they run on the sleeve's own bar size.
    /// `qp_ma_cross`: a fast EMA crossing a slow one, stop `stop_atr` x ATR(14).
    QpMaCross {
        fast: usize,
        slow: usize,
        both: bool,
        stop_atr: f64,
    },
    /// `lux_body_momentum`: two same-colour bodies averaging `mult` x the
    /// 20-bar average body. `widen` is the sealed `stop_widen` (1.0 when the
    /// cell was sealed without the wide-stop protocol).
    LuxBodyMomentum {
        mult: f64,
        stop: TtStop,
        widen: f64,
    },
    /// `lux_sweep_reclaim` on the last `swing`-bar pivot: a sweep of the level
    /// closed back through within `confirm_bars`; the target is the opposite
    /// pivot (`level` exit).
    LuxSweepReclaim {
        swing: usize,
        confirm_bars: usize,
    },
    /// `lux_ny_vwap_pullback`: after the `or_min` opening range, inside the next
    /// `window` minutes, a new extreme of the day past the range, a retrace to
    /// the session VWAP and a candle closing back away from it.
    LuxNyVwapPullback {
        or_min: usize,
        window: usize,
        stop_mult: f64,
    },
    /// `lux_supply_demand`: the first return into a zone left by an impulse bar
    /// `impulse` times the `base_len`-bar base, closing back out of it.
    LuxSupplyDemand {
        base_len: usize,
        impulse: f64,
    },
    /// `lux_htf_manipulation`: the manipulation candle on an `htf_m`-bar candle.
    LuxHtfManipulation {
        htf_m: usize,
        close_above: TtHtfClose,
        prior: bool,
        ema: usize,
        stop: TtStop,
    },
    /// `lux_swing_sweep_mss`: a swing sweep, then a market-structure shift.
    LuxSwingSweepMss {
        k: usize,
        w: usize,
        mss_entry: bool,
        ema: usize,
    },
    /// `luxalgo_manipulation`: a candle sweeping the previous one and engulfing it.
    LuxalgoManipulation {
        prior_bars: usize,
        ema: usize,
        engulf: TtEngulf,
        stop: TtStop,
        fade: bool,
    },
    /// `td_ema_macd`: close over EMA200, MACD above zero, crossed its signal
    /// within `within` bars.
    TdEmaMacd {
        within: usize,
        both: bool,
    },
    /// `yf_alphatrend` (`tt_yoforex3`): AlphaTrend -- SMA(14) true range x
    /// `coeff` off the bar's low/high, ratcheting while the oscillator (RSI(14)
    /// when `use_rsi`, else MFI(14)) is >= 50 -- crossing its own 2-bar lag,
    /// alternating sides. Stop at the lagged line, floored at 0.2 ATR.
    YfAlphatrend {
        coeff: f64,
        use_rsi: bool,
    },
    /// `mql_big_move_cont` (`tt_marlon_quantlab`): a close-to-close move over
    /// `big` x the PREVIOUS bar's ATR(14), traded with the move (`vol` adds a
    /// volume > 2x its 20-bar mean condition). Stop `stop_atr` x ATR.
    MqlBigMoveCont {
        big: f64,
        vol: bool,
        stop_atr: f64,
    },
    /// `qc_ema_cross` (`tt_quantcrawler`): EMA(`fast`) crossing EMA(21); `both`
    /// takes the short cross too. Stop `stop_atr` x ATR(14).
    QcEmaCross {
        fast: usize,
        both: bool,
        stop_atr: f64,
    },
    /// `dt_rsi_dip_vol` (`tt_deltatrendtrading`): RSI(`n`) under `lo` with the
    /// close under its 20-bar SMA -> long; `vol_high` keeps only bars whose
    /// 20-bar realised vol is above its 100-bar median. Stop `stop_atr` x ATR.
    DtRsiDipVol {
        n: usize,
        lo: f64,
        vol_high: bool,
        stop_atr: f64,
    },
    /// `qp_rsi2_pullback` (`tt_quantpad`): RSI(2) under `lo` above the 200-bar
    /// SMA -> long, over `100 - lo` below it -> short. Stop `stop_atr` x ATR.
    QpRsi2Pullback {
        lo: f64,
        stop_atr: f64,
    },
    // ---- the FundedNext book's `cfd_families` cells (`fundednext/oct-18-2026`) ----
    /// `skew`: the rolling skewness of `period` one-bar log returns past
    /// `threshold` (`positive`: above it, else below minus it), traded on a
    /// DECLARED side (`long`). A swing family run `SESSION_ONLY`; no `_late`.
    Skew {
        period: usize,
        positive: bool,
        threshold: f64,
        long: bool,
    },
    /// `value_area`: the close against yesterday's volume value area -- the
    /// POC as a position (`poc`), or a rejection at an edge. The raw side is
    /// the FADE and `follow` inverts it.
    ValueArea {
        buffer_atr: f64,
        poc: bool,
    },
    /// `supertrend`: the bar the `mult` x ATR ratcheting band flips.
    Supertrend {
        mult: f64,
    },
    /// `range_expansion`: from `signal_minute`, the day has travelled
    /// `expansion` daily ranges; the side is the close against the day's open.
    RangeExpansion {
        signal_minute: i64,
        expansion: f64,
    },
    /// `gated_swing` on the `ma` trigger: EMA(`fast`) crossing EMA(`slow`),
    /// passed by the slow-RSI gate. A swing family run `SESSION_ONLY`; no
    /// `_late`. The `donchian` trigger is not ported.
    GatedSwing {
        /// `trigger="donchian"`: the `20 * unit` channel; `None` is the MA cross.
        donchian: Option<usize>,
        fast: usize,
        slow: usize,
        gate: Gate,
        polarity: Polarity,
        gate_period: usize,
    },
    /// `stochastic`: %K crossing %D inside the outer `threshold` band of the
    /// `period`-bar range. The raw side is the fade; `follow` inverts it.
    Stochastic {
        period: usize,
        smooth: usize,
        threshold: f64,
        /// Python's %D running sum at day starts (`RangeStochastic::seeds`).
        sum_seeds: &'static [(i64, u64)],
    },
    /// `lux_htf_stoch_bucket`: a 14x`htf_m`-bar stochastic in its `zone` while
    /// RSI(`rsi_n`) turns out of 70/30. Stop 1 ATR (`Atr(1.0)`) or the swing.
    LuxHtfStochBucket {
        htf_m: usize,
        zone: f64,
        rsi_n: usize,
        stop: TtStop,
    },
    /// `av_donchian_mid`: the close crossing the lagged `n`-bar channel's
    /// midline; target the band (`level`), stop `stop_atr` ATR.
    AvDonchianMid {
        n: usize,
        stop_atr: f64,
    },
    /// `qtt_triple_ema`: EMA34 crossing EMA55 on the side of EMA55 and EMA200;
    /// out when the 34 crosses back (`signal`). Stop `stop_atr` ATR.
    QttTripleEma {
        stop_atr: f64,
    },
    /// `tmb_ha_stoch_doji`: a Heikin Ashi doji with %K(14,3) under `os` (over
    /// `100 - os`), then the next HA candle turning; stop past the two bars.
    TmbHaStochDoji {
        doji: f64,
        os: f64,
    },
    /// `nt_profile_levels`: a close crossing a peak of the 168-close profile
    /// (bins `bw` ATR wide), refreshed every 4 bars ON PYTHON'S GLOBAL INDEX
    /// -- `phase_anchors` are `(candle ts, index % 4)` to realign on.
    NtProfileLevels {
        bw: f64,
        stop_atr: f64,
        phase_anchors: &'static [(i64, u8)],
    },
    /// `cf_prevday_continuation`: yesterday closed in its top (bottom) `zone`
    /// and up (down) -> the session's first bar goes with it.
    CfPrevdayContinuation {
        zone: f64,
        stop_atr: f64,
    },
    /// `qp_gap_fade`: an open `min_gap` ATR away from yesterday's close, faded
    /// on the first bar; target that close (`level`).
    QpGapFade {
        min_gap: f64,
        stop_atr: f64,
    },
    /// `nt_donchian_close`: a close through the previous `n` closes' channel,
    /// alternating sides (`after_loss`: only after a losing breakout).
    NtDonchianClose {
        n: usize,
        after_loss: bool,
        stop_atr: f64,
    },
    /// `tr_supertrend`: SuperTrend(10, `mult`) flipping on the side of
    /// EMA(`ema`); stop at the line.
    TrSupertrend {
        mult: f64,
        ema: usize,
    },
    /// `lux_rsi_divergence` (`confirm=none`, `regime=any`): a confirmed `k`-bar
    /// swing low below the previous one with RSI(`rsi_n`) above its reading
    /// there -> long within 20 bars unless the swing breaks (mirror); stop
    /// under the swing.
    LuxRsiDivergence {
        rsi_n: usize,
        k: usize,
    },
    /// `lux_session_open_reaction` at `london_open`: a candle wicks through
    /// the 03:00 New York open and closes back with a body the other way.
    /// The level is read off the source bars, outside the session.
    LuxSessionOpenReaction,
    // ---- the 2026-10-18 FundedNext book's `cfd_families` cells ----
    /// `day_of_week`: a fixed side on one weekday of the (shifted) day.
    DayOfWeek {
        weekday: i64,
        long: bool,
    },
    /// `key_reversal`: a new channel extreme that closes back through the
    /// previous close.
    KeyReversal {
        channel: usize,
    },
    /// `swing_break`: a close through the last confirmed fractal pivot.
    SwingBreak {
        wing: usize,
    },
    /// `structure`: the last two pivots set a direction; a close through
    /// the opposite pivot (`reversal`) or the same one (`continuation`).
    Structure {
        wing: usize,
        continuation: bool,
    },
    /// `momentum` (`usoil_families_research`): the move over `lookback`
    /// candles, read on the candle at `signal_minute` only.
    Momentum {
        signal_minute: i64,
        lookback: usize,
        threshold_atr: f64,
    },
    /// `macd_hist`: the histogram crossing zero, or turning on one side.
    MacdHist {
        fast: usize,
        slow: usize,
        signal: usize,
        zero: bool,
    },
    /// `quantile_break`: a close beyond a rolling quantile channel.
    QuantileBreak {
        channel: usize,
        share: f64,
        buffer_atr: f64,
    },
    /// `vol_of_vol`: the dispersion of the volatility ratio sets the state,
    /// the `unit`-candle move sets the side.
    VolOfVol {
        period: usize,
        dispersion: f64,
        unsteady: bool,
        lookback: usize,
    },
    /// `regime_breakout`: Williams' breakout with the trigger scaled by the
    /// short/long volatility ratio.
    RegimeBreakout {
        fraction: f64,
        response: f64,
    },
    /// `estimator`: path variance over close-to-close variance.
    Estimator {
        period: usize,
        parkinson: bool,
        ratio: f64,
        whipping: bool,
        lookback: usize,
    },
    /// `gated_donchian`: the channel break, taken when the gate agrees.
    GatedDonchian {
        channel: usize,
        gate: Gate,
        polarity: Polarity,
        gate_period: usize,
    },
    /// `gated_fade`: a mean-reversion trigger, taken when the gate agrees.
    GatedFade {
        trigger: FadeTrigger,
        gate: Gate,
        polarity: Polarity,
        gate_period: usize,
    },
    // ---- the 2026-10-18 book's TikTok cells (`tt`) ----
    AvFirstHourFib {
        entry: f64,
        both: bool,
    },
    BnqIfvgModel {
        k: usize,
        disp: f64,
    },
    PtlOrbCandle {
        or_min: usize,
    },
    QttRsiEmaRetest {
        retest: bool,
    },
    TrOscillatorRebound {
        ema: usize,
    },
    TrRvi {
        below: bool,
        ema: usize,
    },
    TwpAsiaLondonNy {
        tight: f64,
        win: i64,
    },
}

/// `FADE_TRIGGERS`: the mean-reversion entries `gated_fade` chooses between.
#[allow(dead_code, reason = "the unreached arms are what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum FadeTrigger {
    VwapDev,
}

impl Family {
    /// Whether this is a TikTok-creator family, which sets its own stop.
    #[allow(dead_code, reason = "read by the tests and the warm-up guard")]
    pub(crate) fn is_tt(self) -> bool {
        matches!(
            self,
            Self::QpMaCross { .. }
                | Self::LuxBodyMomentum { .. }
                | Self::LuxHtfManipulation { .. }
                | Self::LuxSwingSweepMss { .. }
                | Self::LuxalgoManipulation { .. }
                | Self::TdEmaMacd { .. }
                | Self::LuxSweepReclaim { .. }
                | Self::LuxNyVwapPullback { .. }
                | Self::LuxSupplyDemand { .. }
                | Self::YfAlphatrend { .. }
                | Self::MqlBigMoveCont { .. }
                | Self::QcEmaCross { .. }
                | Self::DtRsiDipVol { .. }
                | Self::QpRsi2Pullback { .. }
                | Self::LuxHtfStochBucket { .. }
                | Self::AvDonchianMid { .. }
                | Self::QttTripleEma { .. }
                | Self::TmbHaStochDoji { .. }
                | Self::NtProfileLevels { .. }
                | Self::CfPrevdayContinuation { .. }
                | Self::QpGapFade { .. }
                | Self::NtDonchianClose { .. }
                | Self::TrSupertrend { .. }
                | Self::LuxRsiDivergence { .. }
                | Self::LuxSessionOpenReaction
                | Self::AvFirstHourFib { .. }
                | Self::BnqIfvgModel { .. }
                | Self::PtlOrbCandle { .. }
                | Self::QttRsiEmaRetest { .. }
                | Self::TrOscillatorRebound { .. }
                | Self::TrRvi { .. }
                | Self::TwpAsiaLondonNy { .. }
        )
    }
}

/// A TikTok family's `stop` axis.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum TtStop {
    /// The last 3-bar swing on the losing side, floored at 0.2 ATR.
    Swing,
    /// The just-closed higher-timeframe candle's far extreme, floored.
    Candle,
    /// The setup candle's far extreme (`luxalgo_manipulation`).
    Sweep,
    /// `atr_K`: K x ATR. No seated cell uses it; kept so `atr_2`/`atr_5`
    /// manipulation cells port without a new arm.
    #[allow(dead_code, reason = "no seated cell uses an ATR manipulation stop")]
    Atr(f64),
}

/// `luxalgo_manipulation`'s `engulf` axis.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TtEngulf {
    Range,
    Body,
}

/// `lux_htf_manipulation`'s `close_above` axis.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TtHtfClose {
    High,
    Close,
}

/// `efficiency`'s `regime` axis: trade a clean path, or a noisy one.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ErRegime {
    Clean,
    Noisy,
}

/// `LEVEL_PAIRS`: which two level systems `level_confluence` asks to agree.
///
/// The unreached combination is kept, because a two-of-three axis is only
/// legible next to the one the book did not take.
#[allow(dead_code, reason = "the unreached arm is what the reached ones mean")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum LevelPair {
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
pub(crate) enum Gate {
    /// Close against the SESSION VWAP.
    Vwap,
    /// THE SLOW RSI, at 50. `rsi` the family trades the FAST one at an extreme;
    /// this reads the same construct as a standing bias, which is the opposite
    /// use of it and is why the two do not collide.
    Rsi,
    /// The wide (3.0) supertrend's direction.
    Supertrend,
    /// OBV against the middle of its own `10 * unit` channel.
    Obv,
}

/// `vol_regime`'s `regime` axis: which side of the ratio is the event.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Regime {
    /// Short-horizon volatility at or above `ratio` times its own baseline.
    Expanding,
    /// Below it. `jp225:vol_regime` takes this one, so the cell is a breakout
    /// entered while the market is QUIETENING rather than while it erupts.
    Contracting,
}

/// `kalman`'s `mode` axis: which of the two claims the filter's state makes.
#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum KalmanMode {
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
pub(crate) enum Polarity {
    Confirm,
    Oppose,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// `two_stage`'s coil: what counts as compression.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Coil {
    /// Bollinger bandwidth within 10% of its own recent floor.
    Squeeze,
    /// A narrow-range bar: this bar's span at the window's minimum.
    Nr,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// `_breakout_levels`: which level system `break_retest` watches break.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BreakLevel {
    /// Yesterday's high and low.
    Pdr,
    /// The `4 * session` Donchian channel, read AS IT STOOD when it broke.
    Donchian,
}

#[allow(dead_code, reason = "the unreached arm is what the reached one means")]
/// What price has to come back TO for the retest to count.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Retest {
    /// The broken level itself -- the textbook version.
    Level,
    /// Session VWAP, which after a wide break can sit a long way under the
    /// level, so the two ask for different pullback depths on the same day.
    Vwap,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Params {
    pub(crate) family: Family,
    pub(crate) direction: Direction,
    pub(crate) exit: Exit,
    pub(crate) last_entry_minute: i64,
    pub(crate) stop_day: f64,
    pub(crate) trend: Trend,
    pub(crate) vol_mode: VolMode,
}

/// A LIVE entry sent at its signal, before its fill candle's first minute
/// exists, waiting to be re-based onto that candle's real open.
#[derive(Clone, Copy)]
pub(crate) struct Provisional {
    /// The fill candle's opening, on the SHIFTED clock.
    pub(crate) slot_ts: i64,
    /// Realised volatility at the signal, for the admission re-check.
    pub(crate) realized: f64,
    /// The target DISTANCE the fill planned, so the target is re-based exactly.
    pub(crate) target_distance: Option<f64>,
}

/// A signal read on a closed candle, waiting for the next candle's OPEN.
#[derive(Clone, Copy)]
pub(crate) struct Pending {
    pub(crate) side: Side,
    pub(crate) day: i64,
    /// `stop_day * daily_risk`, resolved at the signal bar.
    pub(crate) distance: f64,
    /// The signal's own target distance, read only by `Exit::Level`.
    pub(crate) target: Option<f64>,
    /// 20-session realised volatility at the SIGNAL bar, which is where Python
    /// reads it. Only the admission test uses it -- the book's own sizing
    /// replaced the one-sided throttle with the two-sided EWMA one.
    pub(crate) realized: f64,
}

pub(crate) struct Position {
    pub(crate) side: Side,
    /// The fill price, kept for the shadow account's own P&L. The live book's
    /// accounting is the engine's and never reads this.
    pub(crate) entry: f64,
    /// Lots the STANDALONE rule would have taken, which is what settles into
    /// `shadow_equity` when this position closes.
    pub(crate) shadow_lots: f64,
    pub(crate) stop: f64,
    /// `stop_day * daily_risk` as it stood at the SIGNAL bar.
    ///
    /// Kept rather than recovered from `entry - stop`, which is the same number
    /// only up to a floating-point subtraction, and `resize_entry` divides by
    /// it: a stop that has since RATCHETED under a trailing exit would give a
    /// different answer altogether.
    pub(crate) distance: f64,
    /// The sizing throttle at the fill (`replay`'s `vol_mult` for the day).
    pub(crate) mult: f64,
    pub(crate) target: Option<f64>,
    /// `trail_N`'s multiple, in daily ranges behind the best close.
    pub(crate) trail: Option<f64>,
    /// `time_N`'s bar count, or `None` for the two price-driven shapes.
    pub(crate) max_bars: Option<usize>,
    /// `position["index"]`, as `FamilyEngine::candles` counted it.
    ///
    /// Python compares `index - position["index"]` over the SESSION-FILTERED bar
    /// list, so the unit is in-session candles rather than wall-clock time -- an
    /// overnight gap costs a `time_4` position nothing at all. `candles` counts
    /// the same bars in the same order, which is what makes the two agree.
    pub(crate) entry_candle: u64,
    pub(crate) best: f64,
    /// The spread this position crossed at entry, in bp WITHOUT the slippage
    /// allowance: the broker's quote in the entry minute where the fill model
    /// reaches it, the sealed constant where it does not. RESOLVED AT ENTRY --
    /// `cfd_families.backtest` stores it on the position for the same reason:
    /// a quote read at exit would be hours later, on the wrong side of the move.
    pub(crate) spread_bp: f64,
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
pub(crate) struct LevelHistory {
    pub(crate) day: i64,
    pub(crate) high: f64,
    pub(crate) low: f64,
    pub(crate) close: f64,
    pub(crate) top: Option<f64>,
    pub(crate) bottom: Option<f64>,
    /// `(high, low)` of the day BEFORE this candle's, for `_breakout_levels`
    /// under `pdr`.
    pub(crate) prior_range: Option<(f64, f64)>,
    /// `ctx["width"][index]`: Bollinger bandwidth, `sigma / mean`.
    pub(crate) width: Option<f64>,
    /// `ctx["width_min"][compress][index]`, inclusive of this bar.
    pub(crate) width_min: Option<f64>,
    /// `ctx["span_min"][compress][index]`, inclusive of this bar.
    pub(crate) span_min: Option<f64>,
}

/// The streaming port of `cfd_families.backtest` for one sealed cell.
///
/// ORDERING MATCHES THE PYTHON AND THE RUST ENGINE: session flatten first, then
/// the stop, then the target, then the trail ratchet. An ambiguous candle is
/// read as the unfavourable sequence throughout.
pub(crate) struct FamilyEngine {
    pub(crate) sleeve: Sleeve,
    pub(crate) params: Params,
    pub(crate) spec: Instrument,
    pub(crate) quantity_step: f64,

    // ---- candle assembly ----
    pub(crate) slot: Option<i64>,
    pub(crate) building: Option<Candle>,
    /// Seconds between one SOURCE bar and the next, learned from the stream.
    ///
    /// Read rather than configured so it cannot disagree with what the runtime
    /// is feeding: `ohlcv_step` is 60 for every market in this book and 1,800
    /// for a 30-minute source, and a constant transcribed here would be a second
    /// copy of that rule. Held as the SMALLEST positive gap seen, because every
    /// larger one is a session break, a weekend or a hole.
    pub(crate) source_step: Option<i64>,
    /// The previous source bar's timestamp, on the shifted clock, for the above.
    pub(crate) last_bar_ts: Option<i64>,
    /// The next bar of this market as the BACKTEST knows it (`set_next_bar`):
    /// `None` when not told (live), `Some(None)` at the end of the data.
    pub(crate) next_bar: Option<Option<i64>>,
    /// With no next bar: whether the store has more. Unread by the signal --
    /// Python's `index + 1 >= n_bars` ends the day at the end of its array
    /// either way -- and kept for the end-of-run rule in the engine.
    #[allow(dead_code)]
    pub(crate) data_continues: bool,
    /// Canon replay (`RunRequest::canon_replay`, the FundedNext parity
    /// fixtures): Python's own look-ahead day end. Off -- every live run and
    /// every ordinary backtest -- a FundedNext sleeve ends its day on the
    /// scheduled last print instead (`last_print_minute`).
    pub(crate) canon_replay: bool,

    // ---- indicators, in the order `context` builds them ----
    pub(crate) atr: AverageTrueRange,
    pub(crate) risk: DailyRisk,
    pub(crate) trend: Option<Ema>,
    pub(crate) short_volatility: TrailingVolatility,
    pub(crate) long_volatility: TrailingVolatility,
    pub(crate) session: SessionPath,
    pub(crate) prior: PriorDay,
    pub(crate) volatility_multiplier: VolatilityMultiplier,
    /// `ctx["vol_target"]`, the ADMISSION throttle's target.
    ///
    /// Seeded from the frozen per-instrument figure and overridden to
    /// `LEVEL_TWO_SEGMENT_VOL_TARGET` when the sleeve is rebuilt at the
    /// level-two handover, because Python recomputes it there from a series
    /// with no in-sample rows. Carried on the engine rather than read off
    /// `spec` for exactly that reason: it is a property of the SEGMENT, not of
    /// the instrument.
    pub(crate) vol_target: f64,

    // ---- per-family state, built only for the cell that reads it ----
    pub(crate) channel_high: Option<RollingExtreme>,
    pub(crate) channel_low: Option<RollingExtreme>,
    pub(crate) volume_mean: Option<RollingMean>,
    pub(crate) zscore: Option<MeanSigma>,
    pub(crate) rsi: Option<WilderRsi>,
    pub(crate) obv: Option<OnBalanceVolume>,
    pub(crate) obv_high: Option<RollingExtreme>,
    pub(crate) obv_low: Option<RollingExtreme>,
    pub(crate) fast_tema: Option<MovingAverage>,
    pub(crate) slow_tema: Option<MovingAverage>,
    /// The previous candle's `fast - slow`, which is what makes a CROSS
    /// distinguishable from a standing difference.
    pub(crate) previous_cross: Option<f64>,
    /// The backward lookback `trap`, `break_retest` and `two_stage` walk. Length
    /// is set by whichever of them this cell is; every other family leaves it
    /// empty and pays nothing.
    pub(crate) levels: VecDeque<LevelHistory>,
    /// How many candles `levels` retains. Zero for a family that never looks
    /// back, which is what keeps the ring off the hot path for eleven of them.
    pub(crate) level_window: usize,
    /// `pullback`'s own trend anchor. SEPARATE FROM `trend`: that one is the
    /// shared admission gate this book's cells leave at `none`, and conflating
    /// them would apply one axis twice.
    pub(crate) reference_ema: Option<Ema>,
    /// `ctx["width"]`: the Bollinger stats `two_stage`'s squeeze is measured
    /// from, at `p["session"]` rather than at the `zscore` periods.
    pub(crate) width_stats: Option<MeanSigma>,
    pub(crate) width_min: Option<RollingMinInclusive>,
    pub(crate) span_min: Option<RollingMinInclusive>,
    /// `ctx["kendall"][period]`, the Mann-Kendall z.
    pub(crate) kendall: Option<MannKendall>,
    /// `ctx["kalman_level"]` and `ctx["kalman_slope"]`, from one filter.
    pub(crate) kalman: Option<KalmanTrend>,
    /// `ctx["fracdiff"][order]` and the trailing statistics it is standardised
    /// against. TWO PIECES, because the raw differenced series is not zero-mean
    /// and its scale depends on the order, so a threshold on it would mean a
    /// different thing at each `d`.
    pub(crate) fracdiff: Option<FracDiff>,
    pub(crate) fracdiff_stats: Option<MeanSigma>,
    /// `ctx["cusum"][multiple]`, an event series rather than a level: the filter
    /// is STATEFUL and resets after every event, so it cannot be evaluated at a
    /// threshold the signal picks.
    pub(crate) cusum: Option<CusumFilter>,
    /// `ctx["prefix_sum"]` / `ctx["prefix_square"]`, and the closes themselves.
    /// Shared by the two families that read a close a fixed number of bars back.
    pub(crate) closes: Option<CloseHistory>,
    /// `ctx["halflife"][period]`, the OU fit `half_life` reads its own lookback
    /// from. It is a SECOND pass over the same closes rather than a method on
    /// `CloseHistory`, because it regresses the LOG series and that history has
    /// to be kept at log scale to stay exact.
    pub(crate) half_life: Option<OuHalfLife>,
    /// `ctx["opening"][1]`, today's first in-session candle.
    pub(crate) opening: Option<OpeningRange>,
    /// The three inclusive readings `two_stage` records into `levels`, captured
    /// as this candle is folded in.
    pub(crate) current_width: Option<f64>,
    pub(crate) current_width_min: Option<f64>,
    pub(crate) current_span_min: Option<f64>,
    /// `ctx["aroon_up"]` / `ctx["aroon_down"]`, current and previous.
    pub(crate) aroon: Option<Aroon>,
    /// `ctx["fast"][n]` / `ctx["slow"][n]`: plain seeded EMAs, NOT the TEMAs
    /// `xma_cross` reads. The cross itself goes through `previous_cross`.
    pub(crate) fast_ema: Option<Ema>,
    pub(crate) slow_ema: Option<Ema>,
    /// `ctx["roofing"][band]`.
    pub(crate) roofing: Option<Roofing>,
    /// `ctx["rvol"]`.
    pub(crate) relative_volume: Option<RelativeVolume>,
    /// `ctx["er"][period]`, with the net move it was measured over.
    pub(crate) efficiency: Option<EfficiencyRatio>,
    /// `ctx["cci"][period]`.
    pub(crate) cci: Option<Cci>,
    /// `ctx["slope"][period]` / `ctx["fit"][period]`.
    pub(crate) linreg: Option<LinearRegression>,
    /// `ctx["jump"][period]`, the bipower jump share.
    pub(crate) jump: Option<BipowerJump>,
    /// `ctx["hurst"][period]`, read by `adaptive_pullback`.
    pub(crate) hurst: Option<HurstExponent>,
    /// `ctx["sar_flip"]`, read by `sar`.
    pub(crate) sar: Option<ParabolicSar>,
    /// `ctx["fb"]` sma/std, read by `fb_bollinger`.
    pub(crate) band: Option<BollingerBand>,
    /// `ctx["skew"][period]`.
    pub(crate) skew: Option<RollingSkew>,
    /// `ctx["value_area"]`, yesterday's value area for today.
    pub(crate) value_area: Option<SessionValueArea>,
    /// `ctx["supertrend"][mult]`, on the engine's ATR.
    pub(crate) supertrend: Option<Supertrend>,
    /// `ctx["stoch_fast"]` / `ctx["stoch_slow"]` at one period.
    pub(crate) stochastic: Option<RangeStochastic>,
    /// `ctx["pivot_high"]` and friends at one wing.
    pub(crate) pivots: Option<SwingPivots>,
    /// `ctx["macd_line"]` / `ctx["macd_hist"]` at one setting.
    pub(crate) macd: Option<MacdHistogram>,
    /// `ctx["parkinson"]` / `["garman_klass"]` / `["close_var"]`.
    pub(crate) estimators: Option<VarianceEstimators>,
    /// `ctx["quantile_high"]` / `["quantile_low"]` at one key.
    pub(crate) quantile: Option<QuantileChannel>,
    /// `_vol_of_vol_series`.
    pub(crate) vol_of_vol: Option<VolOfVol>,
    /// The previous candle's close, for `key_reversal`.
    pub(crate) previous_close: Option<f64>,

    // ---- trading state ----
    /// How many in-session candles this engine has stepped, which is Python's
    /// `index` into the context's bar list. Only `time_N` reads it.
    pub(crate) candles: u64,
    pub(crate) traded_day: Option<i64>,
    pub(crate) pending: Option<Pending>,
    pub(crate) position: Option<Position>,
    pub(crate) action_timestamp: Option<i64>,
    /// The standalone account that decides which trades EXIST. See
    /// `ADMISSION_BALANCE`; it never touches the live book's balance.
    pub(crate) shadow_equity: f64,

    // ---- the Exness live-fill model (`backtest::fills`) ----
    /// The broker's minute series for this sleeve's market. Installed by the
    /// backtest and never by the live runtime, which is filled by the broker
    /// itself and has no future minute to read.
    pub(crate) fills: Option<Arc<MarketFills>>,
    /// The parity fixtures' FILLS_ONLY gate when no broker table is
    /// installed: shifted candle stamps that had no broker entry price.
    pub(crate) unfillable: Option<Arc<std::collections::HashSet<i64>>>,
    /// Live `CFD_BROKER_HOURS` (`BrokerQuotes`); `None` in every backtest,
    /// whose bars arrive already filtered.
    pub(crate) quotes: Option<Arc<dyn super::BrokerQuotes>>,
    /// The whole entry charge, bp of the fill, for the fill just emitted --
    /// `Some` only when the broker table priced its spread, so the engine keeps
    /// its constant whenever this sleeve fell back to it.
    pub(crate) entry_cost_bp: Option<f64>,
    pub(crate) coverage: FillCoverage,

    // ---- live execution ----
    /// Fill a pending signal on the FIRST MINUTE of its fill candle instead of
    /// once that candle has closed. LIVE ONLY: see `enable_early_fills`.
    pub(crate) fill_early: bool,
    /// The `candles` index of a candle whose entry was filled early, so its own
    /// `on_candle` can skip what the ordinary path never saw that position do.
    pub(crate) early_filled_candle: Option<u64>,
    /// A live entry already SENT at its signal, to be re-based onto the fill
    /// candle's real open when that candle's first minute arrives. See
    /// `send_at_signal`.
    pub(crate) provisional: Option<Provisional>,
    /// The target distance the most recent fill planned.
    pub(crate) last_target_distance: Option<f64>,
    /// Weekdays a new signal may be taken on, Monday = bit 0; `None` for every
    /// day. RESEARCH ONLY -- set by the backtest from `EXNESS_ENTRY_DAYS`, the
    /// same switch `cfd_families.ENTRY_DAYS` reads, and never by the live
    /// runtime.
    pub(crate) entry_days: Option<u8>,

    // ---- the sleeve's own timeframe ----
    /// Seconds in one candle: 1,800 for every `cfd_families` cell, the sealed
    /// bar for a TikTok cell.
    pub(crate) bar_seconds: i64,
    /// `periods(symbol, bar)["session"]`: candles in one session at this bar.
    pub(crate) per_session: usize,
    /// A TikTok family's state (`tt`), `None` for every other family.
    pub(crate) tt: Option<super::tt::TtState>,
    /// `exit_signal(index - 1, ...)`: the family's own exit rule as read on the
    /// PREVIOUS candle's close, for a position held through it.
    pub(crate) exit_flag: bool,
    /// The day of the last source bar folded into the sizing throttle, for a
    /// sleeve whose candles are NOT the 30-minute grid `replay` marks on.
    pub(crate) vol_day: Option<i64>,
}

impl FamilyEngine {
    pub(crate) fn new(sleeve: Sleeve, quantity_step: f64) -> Self {
        let spec = sleeve.contract();
        let params = sleeve
            .params()
            .expect("FamilyEngine built for an imported sleeve");
        let session_bars = sleeve.per_session();
        let annual = spec.calendar * session_bars as f64;
        // `periods`: `unit` is one session in bars, or ONE BAR under
        // `EXNESS_BAR_LOOKBACKS` -- which also fixes the ATR at 14 bars.
        // Annualising stays in sessions either way.
        let unit = if sleeve.bar_lookbacks() {
            1
        } else {
            session_bars
        };
        let atr_bars = if sleeve.bar_lookbacks() {
            14
        } else {
            2 * session_bars
        };
        let trend = match params.trend {
            Trend::None => None,
            Trend::Ema20d => Some(Ema::new(20 * unit)),
            Trend::Ema50d => Some(Ema::new(50 * unit)),
        };

        let mut engine = Self {
            sleeve,
            params,
            spec,
            quantity_step,
            slot: None,
            building: None,
            source_step: None,
            next_bar: None,
            data_continues: true,
            canon_replay: false,
            last_bar_ts: None,
            atr: AverageTrueRange::new(atr_bars),
            risk: DailyRisk::new(),
            trend,
            short_volatility: TrailingVolatility::new(20 * unit, annual),
            long_volatility: TrailingVolatility::new(100 * unit, annual),
            session: SessionPath::new(),
            prior: PriorDay::new(),
            volatility_multiplier: VolatilityMultiplier::new(),
            vol_target: sleeve.vol_target(),
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
            jump: None,
            hurst: None,
            sar: None,
            band: None,
            skew: None,
            value_area: None,
            supertrend: None,
            stochastic: None,
            pivots: None,
            macd: None,
            estimators: None,
            quantile: None,
            vol_of_vol: None,
            previous_close: None,
            candles: 0,
            traded_day: None,
            pending: None,
            position: None,
            action_timestamp: None,
            shadow_equity: sleeve.book().admission_balance() * sleeve.shown_equity(),
            fills: None,
            unfillable: None,
            quotes: None,
            entry_cost_bp: None,
            coverage: FillCoverage::default(),
            fill_early: false,
            early_filled_candle: None,
            provisional: None,
            last_target_distance: None,
            entry_days: sleeve.entry_days(),
            bar_seconds: sleeve.bar_seconds(),
            per_session: session_bars,
            tt: None,
            exit_flag: false,
            vol_day: None,
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
        // `daily or multiday` fills whatever day the next candle is on.
        if !self.sleeve.multiday() && (pending.day != day || minute >= self.spec.session.1) {
            return None;
        }
        // The `off` window: the fill must land OUTSIDE the cash session too, and
        // never on the broker day's last candle, which would carry the position
        // through the rollover (`backtest`'s fill block).
        if self.sleeve.broker_day()
            && (self.in_cash_session(candle_ts)
                || minute + self.bar_seconds / 60 > self.spec.session.1)
        {
            return None;
        }
        let broker_entry = self
            .fills
            .as_ref()
            .and_then(|fills| fills.entry(candle_ts, candle_open));
        // `EXNESS_FILLS_ONLY`: Python refuses the SIGNAL when its fill candle has
        // no broker entry price, so nothing is pending and the fill candle is
        // free to signal again -- which is what consuming the pending entry
        // here, before this candle's own signal step, reproduces. Live has no
        // broker table: the broker itself refuses an order in a closed market.
        if self.sleeve.fills_only() {
            let fillable = match (&self.fills, &self.unfillable) {
                (Some(_), _) => broker_entry.is_some(),
                (None, Some(gate)) => !gate.contains(&candle_ts),
                (None, None) => true,
            };
            if !fillable {
                return None;
            }
        }
        let entry = broker_entry.unwrap_or(candle_open);
        // Stage one: does this trade exist at all? See `ADMISSION_BALANCE`.
        let shadow_lots = self.admission_lots(entry, pending.distance, pending.realized)?;
        // Stage two: how large is it against the shared balance?
        if std::env::var("FN_SIZE_DBG").is_ok_and(|want| want == self.sleeve.python_key()) {
            eprintln!("SIZEDBG {} ts {} equity {:?} mult {:?} distance {:?}", self.sleeve.python_key(), candle_ts, equity, self.volatility_multiplier.value(), pending.distance);
        }
        let quantity = self.quantity(equity, entry, pending.distance)?;
        let broker_spread = self
            .fills
            .as_ref()
            .and_then(|fills| fills.spread_bp(candle_ts));
        self.coverage.entries += 1;
        if broker_entry.is_some() && broker_spread.is_some() {
            self.coverage.entries_priced += 1;
        }
        // The whole entry charge in bp of the fill: the broker's quote where the
        // fill model priced it, plus the commission. Without a commission and a
        // broker quote this stays `None` and the engine bills its constant; WITH
        // a commission it never may, because the engine's constant knows nothing
        // of it -- so the sealed spread is named here instead.
        let commission_bp = self.spec.commission_bp(entry);
        self.entry_cost_bp = match broker_spread {
            Some(spread) => Some(spread + SLIPPAGE_BP + commission_bp),
            None if commission_bp != 0.0 => {
                Some(self.spec.spread_bp + SLIPPAGE_BP + commission_bp)
            }
            None => None,
        };
        let (target, trail, max_bars) = match self.params.exit {
            // `backtest`: a `level*` exit takes the signal's own target, when
            // present and positive, and nothing else.
            Exit::Level => (
                pending
                    .target
                    .filter(|target| target.is_finite() && *target > 0.0),
                None,
                None,
            ),
            _ => self.exit_plan(pending.distance),
        };
        self.last_target_distance = target;
        self.position = Some(Position {
            side: pending.side,
            entry,
            shadow_lots,
            stop: match pending.side {
                Side::Long => entry - pending.distance,
                Side::Short => entry + pending.distance,
            },
            distance: pending.distance,
            mult: self.volatility_multiplier.value(),
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

    pub(crate) fn install_fills(&mut self, fills: Arc<MarketFills>) {
        self.fills = Some(fills);
    }

    /// LIVE ONLY: send the entry the moment its signal is known, at the close
    /// of the signal candle, instead of a minute later.
    ///
    /// THE MINUTE WAS REAL MONEY AND NOISE. The backtest fills at the fill
    /// candle's open and the fill model charges the broker's price two seconds
    /// after it. Live used to wait for that candle's first MINUTE BAR to know
    /// its open, and a minute bar exists only once its minute has closed -- so
    /// every entry went out 60s after the price the backtest books. Compared
    /// on 2026-10-01: ETHUSD Kalman +$0.35 backtest, +$1.30 live; three more
    /// ETHUSD entries $0.34-0.35 apart, every one of them that minute.
    ///
    /// Everything the fill decides is known at the signal candle's close except
    /// the open itself, and the open of the next minute is that close to within
    /// a tick. So the order goes out now, sized off the close, and the
    /// position's entry, stop, target and shadow lots are re-based onto the real
    /// open when it prints (`rebase_provisional`) -- the engine then holds
    /// EXACTLY what the backtest holds, and only the order left a minute
    /// sooner, at the moment the fill model already assumes.
    ///
    /// Only where the backtest will fill at the very next candle: same day,
    /// inside the session, before the close. A signal on the day's last candle
    /// is dropped by the backtest and is never sent here either.
    pub(crate) fn send_at_signal(
        &mut self,
        next_slot: i64,
        estimate: f64,
        equity: f64,
    ) -> Option<Action> {
        if !self.fill_early || self.position.is_some() || self.provisional.is_some() {
            return None;
        }
        let pending = self.pending?;
        let minute = self.minute_of(next_slot);
        if !self.in_session(minute)
            || pending.day != self.day_of(next_slot)
            || minute >= self.spec.session.1
        {
            return None;
        }
        match self.fill_pending(next_slot, estimate, equity, self.candles + 1) {
            Some(enter) => {
                self.provisional = Some(Provisional {
                    slot_ts: next_slot,
                    realized: pending.realized,
                    target_distance: self.last_target_distance,
                });
                self.early_filled_candle = Some(self.candles + 1);
                Some(enter)
            }
            None => {
                // Refused at the estimate. Put the signal back so the ordinary
                // path decides at the real open, as the backtest does.
                self.pending = Some(pending);
                None
            }
        }
    }

    /// Moves a provisional position onto the fill candle's real `open`, or
    /// unwinds it where the backtest would not have filled there. Returns the
    /// unwinding close, if any.
    pub(crate) fn rebase_provisional(
        &mut self,
        provisional: Provisional,
        opening: i64,
        open: f64,
    ) -> Option<Action> {
        let (side, distance) = {
            let position = self.position.as_ref()?;
            (position.side, position.distance)
        };
        let minute = self.minute_of(opening);
        let fillable = self.in_session(minute)
            && self.day_of(opening) == self.day_of(provisional.slot_ts)
            && minute < self.spec.session.1;
        let shadow = if fillable {
            self.admission_lots(open, distance, provisional.realized)
        } else {
            None
        };
        match shadow {
            Some(lots) => {
                let position = self.position.as_mut().expect("checked above");
                position.entry = open;
                position.best = open;
                position.shadow_lots = lots;
                position.stop = match side {
                    Side::Long => open - distance,
                    Side::Short => open + distance,
                };
                position.target = provisional.target_distance.map(|target| match side {
                    Side::Long => open + target,
                    Side::Short => open - target,
                });
                None
            }
            None => {
                // The backtest never held this: the next bar was another day,
                // outside the session, or the admission refuses at the real
                // open. Close what was sent and stand where the backtest stands.
                self.position = None;
                self.traded_day = None;
                self.early_filled_candle = None;
                Some(Action::Close {
                    price: open,
                    fraction: 1.0,
                })
            }
        }
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
    pub(crate) fn restrict_entry_days(&mut self, mask: u8) {
        self.entry_days = Some(mask);
    }

    pub(crate) fn enable_early_fills(&mut self) {
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
            .and_then(|fills| fills.exit_after(candle_ts, candle_open, self.bar_seconds))
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
    pub(crate) fn arm_family_state(&mut self) {
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
            Family::Trap {
                channel, window, ..
            } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.level_window = window;
            }
            Family::ObvBreak { channel } => {
                self.obv = Some(OnBalanceVolume::new());
                self.obv_high = Some(RollingExtreme::new(channel, true));
                self.obv_low = Some(RollingExtreme::new(channel, false));
            }
            Family::XmaCross { fast, slow, kind } => {
                self.fast_tema = Some(MovingAverage::new(kind, fast));
                self.slow_tema = Some(MovingAverage::new(kind, slow));
            }
            Family::Jump { period, .. } => {
                self.jump = Some(BipowerJump::new(period));
                self.closes = Some(CloseHistory::new());
            }
            Family::Pullback {
                channel, reference, ..
            } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.reference_ema = Some(Ema::new(reference));
            }
            Family::AdaptivePullback {
                channel,
                reference,
                period,
                ..
            } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.reference_ema = Some(Ema::new(reference));
                self.hurst = Some(HurstExponent::new(period));
            }
            Family::FailedBreak { .. } => {}
            Family::Sar => {
                self.sar = Some(ParabolicSar::new());
            }
            Family::FbBollinger { period, k, .. } => {
                self.band = Some(BollingerBand::new(period, k));
            }
            Family::GatedOrb {
                gate, gate_period, ..
            } => {
                self.opening = Some(OpeningRange::new());
                self.arm_gate(gate, gate_period);
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
            Family::Skew { period, .. } => {
                self.skew = Some(RollingSkew::new(period));
            }
            Family::ValueArea { .. } => {
                self.value_area = Some(SessionValueArea::new());
            }
            Family::Supertrend { mult } => {
                self.supertrend = Some(Supertrend::new(mult));
            }
            // The day's running range and open: `SessionPath`, on every cell.
            Family::RangeExpansion { .. } => {}
            Family::GatedSwing {
                donchian,
                fast,
                slow,
                gate,
                gate_period,
                ..
            } => {
                match donchian {
                    Some(channel) => {
                        self.channel_high = Some(RollingExtreme::new(channel, true));
                        self.channel_low = Some(RollingExtreme::new(channel, false));
                    }
                    None => {
                        self.fast_ema = Some(Ema::new(fast));
                        self.slow_ema = Some(Ema::new(slow));
                    }
                }
                self.arm_gate(gate, gate_period);
            }
            Family::DayOfWeek { .. } | Family::RegimeBreakout { .. } => {}
            Family::KeyReversal { channel } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
            }
            Family::SwingBreak { wing } | Family::Structure { wing, .. } => {
                self.pivots = Some(SwingPivots::new(wing));
            }
            Family::Momentum { .. } => {
                self.closes = Some(CloseHistory::new());
            }
            Family::MacdHist {
                fast, slow, signal, ..
            } => {
                self.macd = Some(MacdHistogram::new(fast, slow, signal));
            }
            Family::QuantileBreak { channel, share, .. } => {
                self.quantile = Some(QuantileChannel::new(channel, share));
            }
            Family::VolOfVol { period, .. } => {
                self.vol_of_vol = Some(VolOfVol::new(period));
                self.closes = Some(CloseHistory::new());
            }
            Family::Estimator { period, .. } => {
                self.estimators = Some(VarianceEstimators::new(period));
                self.closes = Some(CloseHistory::new());
            }
            Family::GatedDonchian {
                channel,
                gate,
                gate_period,
                ..
            } => {
                self.channel_high = Some(RollingExtreme::new(channel, true));
                self.channel_low = Some(RollingExtreme::new(channel, false));
                self.arm_gate(gate, gate_period);
            }
            Family::GatedFade {
                gate, gate_period, ..
            } => {
                self.arm_gate(gate, gate_period);
            }
            Family::Stochastic {
                period,
                smooth,
                sum_seeds,
                ..
            } => {
                self.stochastic = Some(RangeStochastic::new(period, smooth, sum_seeds));
            }
            Family::QpMaCross { .. }
            | Family::LuxBodyMomentum { .. }
            | Family::LuxHtfManipulation { .. }
            | Family::LuxSwingSweepMss { .. }
            | Family::LuxalgoManipulation { .. }
            | Family::TdEmaMacd { .. }
            | Family::LuxSweepReclaim { .. }
            | Family::LuxNyVwapPullback { .. }
            | Family::LuxSupplyDemand { .. }
            | Family::YfAlphatrend { .. }
            | Family::MqlBigMoveCont { .. }
            | Family::QcEmaCross { .. }
            | Family::DtRsiDipVol { .. }
            | Family::QpRsi2Pullback { .. }
            | Family::LuxHtfStochBucket { .. }
            | Family::AvDonchianMid { .. }
            | Family::QttTripleEma { .. }
            | Family::TmbHaStochDoji { .. }
            | Family::NtProfileLevels { .. }
            | Family::CfPrevdayContinuation { .. }
            | Family::QpGapFade { .. }
            | Family::NtDonchianClose { .. }
            | Family::TrSupertrend { .. }
            | Family::LuxRsiDivergence { .. }
            | Family::LuxSessionOpenReaction | Family::AvFirstHourFib { .. }
            | Family::BnqIfvgModel { .. }
            | Family::PtlOrbCandle { .. }
            | Family::QttRsiEmaRetest { .. }
            | Family::TrOscillatorRebound { .. }
            | Family::TrRvi { .. }
            | Family::TwpAsiaLondonNy { .. } => {
                self.tt = super::tt::TtState::new(self.params.family, self.bar_seconds / 60);
                if let Some(tt) = &mut self.tt {
                    tt.set_shift(self.spec.shift_hours);
                }
            }
        }
    }

    /// The state a gate reads. `gate_period` is the slow RSI's period for
    /// `rsi` and the OBV channel for `obv`.
    fn arm_gate(&mut self, gate: Gate, gate_period: usize) {
        match gate {
            Gate::Rsi => self.rsi = Some(WilderRsi::new(gate_period)),
            Gate::Vwap => {}
            Gate::Supertrend => self.supertrend = Some(Supertrend::new(3.0)),
            Gate::Obv => {
                self.obv = Some(OnBalanceVolume::new());
                self.obv_high = Some(RollingExtreme::new(gate_period, true));
                self.obv_low = Some(RollingExtreme::new(gate_period, false));
            }
        }
    }

    pub(crate) fn day_of(&self, ts: i64) -> i64 {
        ts.div_euclid(86_400)
    }

    pub(crate) fn minute_of(&self, ts: i64) -> i64 {
        ts.rem_euclid(86_400) / 60
    }

    /// The scheduled last-print minute of the shifted day holding `ts`, for a
    /// FundedNext sleeve on live rules; `None` otherwise.
    fn last_print(&self, ts: i64) -> Option<i64> {
        if self.canon_replay || self.sleeve.book() != Book::FundedNext {
            return None;
        }
        crate::strategies::fundednext::oct_18_2026::contracts::last_print_minute(
            self.sleeve.market(),
            self.spec.shift_hours,
            self.spec.session.1,
            self.day_of(ts),
        )
    }

    /// Whether the candle opening at `candle_ts` holds the scheduled last print.
    fn holds_last_print(&self, candle_ts: i64) -> bool {
        let start = self.minute_of(candle_ts);
        let width = self.bar_seconds / 60;
        self.last_print(candle_ts)
            .is_some_and(|last| (start..start + width).contains(&last))
    }

    /// Whether the source bar stamped `ts` IS the scheduled last print, which
    /// closes its candle on that bar rather than at the slot's end.
    fn is_last_print(&self, ts: i64) -> bool {
        self.building.is_some()
            && self.last_print(ts).is_some_and(|last| self.minute_of(ts) == last)
    }

    pub(crate) fn in_session(&self, minute: i64) -> bool {
        let (open, close) = self.spec.session;
        (open..=close).contains(&minute)
    }

    /// `rth_flags` for an `off`-window sleeve: whether the candle stamped `ts`
    /// (SHIFTED clock) opens while the market's cash session is open, in REAL
    /// New York minutes. Always false for an in-session sleeve.
    pub(crate) fn in_cash_session(&self, ts: i64) -> bool {
        let Some((open, close)) = self.sleeve.off_hours() else {
            return false;
        };
        let minute = (ts.div_euclid(60) - self.spec.shift_hours * 60).rem_euclid(1_440);
        if open < close {
            open <= minute && minute < close
        } else {
            minute >= open || minute < close
        }
    }

    /// `_late`: true once the session is past this cell's entry cutoff. A swing
    /// cell has no cutoff, and its `last_entry_minute` is set past the close so
    /// this can never fire.
    pub(crate) fn late(&self, minute: i64) -> bool {
        minute > self.params.last_entry_minute
    }

    /// `accepts_vol`, resolved once per candle.
    pub(crate) fn accepts_vol(&self) -> bool {
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
    pub(crate) fn accepts_trend(&self, close: f64, side: Side) -> bool {
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
    pub(crate) fn push_inclusive(&mut self, candle: &Candle, day: i64) {
        // Every candle, held or flat: the TikTok families' state machines
        // advance on every bar of the session array.
        // `tt_features.base`'s `since`: minutes past the session open.
        let since = (self.minute_of(candle.ts) - self.spec.session.0).rem_euclid(1_440);
        if let Some(tt) = &mut self.tt {
            tt.push(candle, day, since);
        }
        self.atr.push(candle);
        if let Some(hurst) = &mut self.hurst {
            hurst.push(candle.close);
        }
        if let Some(sar) = &mut self.sar {
            sar.push(candle);
        }
        if let Some(band) = &mut self.band {
            band.push(candle.close);
        }
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
        if let Some(jump) = &mut self.jump {
            jump.push(candle.close);
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
        // Inclusive too: each is defined at this index through this bar, and
        // the supertrend reads THIS bar's `ctx["atr"]`, folded in above.
        if let Some(skew) = &mut self.skew {
            skew.push(candle.close);
        }
        if let Some(areas) = &mut self.value_area {
            areas.push(candle, day);
        }
        if let Some(supertrend) = &mut self.supertrend {
            supertrend.push(candle, self.atr.value());
        }
        if let Some(stochastic) = &mut self.stochastic {
            stochastic.push(candle);
        }
        if let Some(pivots) = &mut self.pivots {
            pivots.push(candle);
        }
        if let Some(macd) = &mut self.macd {
            macd.push(candle.close);
        }
        if let Some(estimators) = &mut self.estimators {
            estimators.push(candle);
        }
        if let Some(quantile) = &mut self.quantile {
            quantile.push(candle.close);
        }
        if let Some(vol_of_vol) = &mut self.vol_of_vol {
            vol_of_vol.push(self.short_volatility.value(), self.long_volatility.value());
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
    pub(crate) fn push_channels(&mut self, candle: &Candle) {
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
    pub(crate) fn quantity(&self, equity: f64, entry: f64, distance: f64) -> Option<f64> {
        book_lots(
            self.sleeve,
            &self.spec,
            self.quantity_step,
            equity,
            entry,
            distance,
            self.volatility_multiplier.value(),
        )
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
    pub(crate) fn admission_lots(&self, entry: f64, distance: f64, realized: f64) -> Option<f64> {
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
    pub(crate) fn settle_shadow(&mut self, position: &Position, exit: f64) {
        let gross = match position.side {
            Side::Long => exit - position.entry,
            Side::Short => position.entry - exit,
        };
        // `cost_price`: the spread and slippage in bp of the fill, plus the
        // broker's commission in price units (zero on Exness Pro).
        let cost = position.entry * (position.spread_bp + SLIPPAGE_BP) / 10_000.0
            + self.spec.commission_price(position.entry);
        self.shadow_equity += (gross - cost) * position.shadow_lots * self.spec.multiplier;
    }

    /// `exit_plan_for`: `(target distance, trail multiple, bar count)`.
    ///
    /// Exactly one leg is ever set. The Python returns a three-tuple with two
    /// `None`s for the same reason: the axis picks one shape, and a cell that
    /// carried both a target and a trail would be two exits racing.
    pub(crate) fn exit_plan(&self, distance: f64) -> (Option<f64>, Option<f64>, Option<usize>) {
        match self.params.exit {
            Exit::RewardMultiple(reward) => (Some(reward * distance), None, None),
            Exit::Trail(multiple) => (None, Some(multiple), None),
            Exit::Bars(bars) => (None, None, Some(bars)),
            // `exit_plan_for`: the signal exit carries no target, trail or
            // count; `days_N` is N sessions of THIS bar size.
            Exit::Signal => (None, None, None),
            Exit::Days(days) => (None, None, Some(days * self.per_session)),
            // Resolved from the signal's own target in `fill_pending`.
            Exit::Level => (None, None, None),
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
    pub(crate) fn signal(&self, candle: &Candle, minute: i64) -> Option<Side> {
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
            Family::Trap { window, level, .. } => self.trap(candle, minute, window, level),
            Family::ObvBreak { .. } => self.obv_break(candle, minute),
            Family::XmaCross { .. } => self.xma_cross(candle, minute),
            Family::Jump {
                share,
                jumpy,
                lookback,
                ..
            } => self.jump_signal(candle, minute, share, jumpy, lookback),
            Family::Pullback { depth, .. } => self.pullback(candle, minute, depth),
            Family::AdaptivePullback {
                depth, response, ..
            } => self.adaptive_pullback(candle, minute, depth, response),
            Family::FailedBreak { buffer_atr } => self.failed_break(candle, minute, buffer_atr),
            Family::Sar => self.sar_signal(minute),
            Family::FbBollinger { reenter, .. } => self.fb_bollinger(minute, reenter),
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
            Family::Skew {
                positive,
                threshold,
                long,
                ..
            } => self.skew_signal(positive, threshold, long),
            Family::ValueArea { buffer_atr, poc } => {
                self.value_area_signal(candle, minute, buffer_atr, poc)
            }
            Family::Supertrend { .. } => self.supertrend_signal(minute),
            Family::RangeExpansion {
                signal_minute,
                expansion,
            } => self.range_expansion(candle, minute, signal_minute, expansion),
            Family::GatedSwing { gate, polarity, .. } => {
                self.gated_swing(candle, gate, polarity)
            }
            Family::Stochastic { threshold, .. } => self.stochastic_signal(minute, threshold),
            Family::DayOfWeek { weekday, long } => self.day_of_week(candle, weekday, long),
            Family::KeyReversal { .. } => self.key_reversal(candle, minute),
            Family::SwingBreak { .. } => self.swing_break(candle, minute),
            Family::Structure { continuation, .. } => self.structure(candle, minute, continuation),
            Family::Momentum {
                signal_minute,
                lookback,
                threshold_atr,
            } => self.momentum(candle, minute, signal_minute, lookback, threshold_atr),
            Family::MacdHist { zero, .. } => self.macd_hist(minute, zero),
            Family::QuantileBreak { buffer_atr, .. } => {
                self.quantile_break(candle, minute, buffer_atr)
            }
            Family::VolOfVol {
                period,
                dispersion,
                unsteady,
                lookback,
            } => self.vol_of_vol_signal(candle, minute, period, dispersion, unsteady, lookback),
            Family::RegimeBreakout { fraction, response } => {
                self.regime_breakout(candle, minute, fraction, response)
            }
            Family::Estimator {
                parkinson,
                ratio,
                whipping,
                lookback,
                ..
            } => self.estimator(candle, minute, parkinson, ratio, whipping, lookback),
            Family::GatedDonchian { gate, polarity, .. } => {
                self.gated_donchian(candle, minute, gate, polarity)
            }
            Family::GatedFade {
                trigger,
                gate,
                polarity,
                ..
            } => self.gated_fade(candle, minute, trigger, gate, polarity),
            // Carried with their own stop distance: see `signal_with_distance`.
            Family::QpMaCross { .. }
            | Family::LuxBodyMomentum { .. }
            | Family::LuxHtfManipulation { .. }
            | Family::LuxSwingSweepMss { .. }
            | Family::LuxalgoManipulation { .. }
            | Family::TdEmaMacd { .. }
            | Family::LuxSweepReclaim { .. }
            | Family::LuxNyVwapPullback { .. }
            | Family::LuxSupplyDemand { .. }
            | Family::YfAlphatrend { .. }
            | Family::MqlBigMoveCont { .. }
            | Family::QcEmaCross { .. }
            | Family::DtRsiDipVol { .. }
            | Family::QpRsi2Pullback { .. }
            | Family::LuxHtfStochBucket { .. }
            | Family::AvDonchianMid { .. }
            | Family::QttTripleEma { .. }
            | Family::TmbHaStochDoji { .. }
            | Family::NtProfileLevels { .. }
            | Family::CfPrevdayContinuation { .. }
            | Family::QpGapFade { .. }
            | Family::NtDonchianClose { .. }
            | Family::TrSupertrend { .. }
            | Family::LuxRsiDivergence { .. }
            | Family::LuxSessionOpenReaction | Family::AvFirstHourFib { .. }
            | Family::BnqIfvgModel { .. }
            | Family::PtlOrbCandle { .. }
            | Family::QttRsiEmaRetest { .. }
            | Family::TrOscillatorRebound { .. }
            | Family::TrRvi { .. }
            | Family::TwpAsiaLondonNy { .. } => None,
        }
    }

    /// The side, and the signal's OWN stop distance where it names one.
    ///
    /// `backtest`: a signal returning `(side, distance)` places its own stop,
    /// and lots are sized on that distance. Every `cfd_families` family
    /// returns a bare side and gets `stop_day * risk`. A TikTok event is taken
    /// only inside the entry cutoff, applied here as `_event_signal` applies it.
    pub(crate) fn signal_with_distance(
        &self,
        candle: &Candle,
        minute: i64,
    ) -> Option<(Side, Option<f64>, Option<f64>)> {
        if let Some(tt) = &self.tt {
            if self.late(minute) {
                return None;
            }
            return tt
                .event()
                .map(|(side, distance, target)| (side, Some(distance), target));
        }
        self.signal(candle, minute).map(|side| (side, None, None))
    }

    /// `skew_signal`: the regime, traded on its declared side. No `_late`.
    pub(crate) fn skew_signal(&self, positive: bool, threshold: f64, long: bool) -> Option<Side> {
        let value = self.skew.as_ref()?.value()?;
        if !positive && value > -threshold {
            return None;
        }
        if positive && value < threshold {
            return None;
        }
        Some(if long { Side::Long } else { Side::Short })
    }

    /// `value_area_signal`. The raw side is the fade; `follow` inverts it.
    pub(crate) fn value_area_signal(
        &self,
        candle: &Candle,
        minute: i64,
        buffer_atr: f64,
        poc: bool,
    ) -> Option<Side> {
        let (high, low, point) = self.value_area.as_ref()?.levels?;
        let atr = self
            .atr
            .value()
            .filter(|atr| *atr != 0.0 && !atr.is_nan())?;
        if self.late(minute) {
            return None;
        }
        let edge = buffer_atr * atr;
        let raw = if poc {
            if candle.close > point + edge {
                Side::Long
            } else if candle.close < point - edge {
                Side::Short
            } else {
                return None;
            }
        } else if candle.high >= high - edge && candle.close < high {
            Side::Short
        } else if candle.low <= low + edge && candle.close > low {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Fade => raw,
            Direction::Follow => Direction::Fade.apply(raw),
        })
    }

    /// `supertrend_signal`: the bar the band flips; `fade` takes the other side.
    pub(crate) fn supertrend_signal(&self, minute: i64) -> Option<Side> {
        let (now, before) = self.supertrend.as_ref()?.readings()?;
        if self.late(minute) || now == before {
            return None;
        }
        let raw = if now > 0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// `range_expansion_signal`: the day has already travelled far enough.
    pub(crate) fn range_expansion(
        &self,
        candle: &Candle,
        minute: i64,
        signal_minute: i64,
        expansion: f64,
    ) -> Option<Side> {
        if minute < signal_minute || self.late(minute) {
            return None;
        }
        let risk = self.risk.value().filter(|risk| *risk != 0.0)?;
        let travelled = self.session.high - self.session.low;
        if travelled < expansion * risk {
            return None;
        }
        let raw = if candle.close > self.session.open {
            Side::Long
        } else {
            Side::Short
        };
        Some(self.params.direction.apply(raw))
    }

    /// `gated_swing_signal` on the `ma` trigger. No `_late`, no direction.
    pub(crate) fn gated_swing(&self, candle: &Candle, gate: Gate, polarity: Polarity) -> Option<Side> {
        if let (Some(high), Some(low)) = (&self.channel_high, &self.channel_low) {
            let (top, bottom) = (high.value()?, low.value()?);
            let side = if candle.close > top {
                Side::Long
            } else if candle.close < bottom {
                Side::Short
            } else {
                return None;
            };
            return self.gate_accepts(candle, gate, side, polarity).then_some(side);
        }
        let fast = self.fast_ema.as_ref()?.value()?;
        let slow = self.slow_ema.as_ref()?.value()?;
        let before = self.previous_cross?;
        let now = fast - slow;
        let side = if before <= 0.0 && now > 0.0 {
            Side::Long
        } else if before >= 0.0 && now < 0.0 {
            Side::Short
        } else {
            return None;
        };
        self.gate_accepts(candle, gate, side, polarity)
            .then_some(side)
    }

    /// `stochastic_signal`. The raw side is the fade; `follow` inverts it.
    pub(crate) fn stochastic_signal(&self, minute: i64, threshold: f64) -> Option<Side> {
        let ((fast, slow), (fast_before, slow_before)) = self.stochastic.as_ref()?.readings()?;
        if self.late(minute) {
            return None;
        }
        let now = fast - slow;
        let before = fast_before - slow_before;
        let raw = if before <= 0.0 && now > 0.0 && fast < 100.0 - threshold {
            Side::Long
        } else if before >= 0.0 && now < 0.0 && fast > threshold {
            Side::Short
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Fade => raw,
            Direction::Follow => Direction::Fade.apply(raw),
        })
    }


    /// `efficiency_signal`: the move over `period`, if its path was the kind
    /// of path this cell trades.
    pub(crate) fn efficiency_signal(
        &self,
        minute: i64,
        min_er: f64,
        regime: ErRegime,
    ) -> Option<Side> {
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
    pub(crate) fn cci_signal(&self, minute: i64, threshold: f64) -> Option<Side> {
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
    pub(crate) fn linreg_trend_signal(
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
    pub(crate) fn aroon_signal(&self, minute: i64, min_strength: f64) -> Option<Side> {
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
    pub(crate) fn swing_ma_signal(&self) -> Option<Side> {
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
    pub(crate) fn roofing_signal(&self, minute: i64, amplitude_atr: f64) -> Option<Side> {
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
    pub(crate) fn rvol_signal(
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
    pub(crate) fn pullback(&self, candle: &Candle, minute: i64, depth: f64) -> Option<Side> {
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

    /// `adaptive_pullback_signal`: `pullback` with the depth scaled by Hurst.
    pub(crate) fn adaptive_pullback(
        &self,
        candle: &Candle,
        minute: i64,
        depth: f64,
        response: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let top = self.channel_high.as_ref()?.value()?;
        let bottom = self.channel_low.as_ref()?.value()?;
        let reference = self.reference_ema.as_ref()?.value()?;
        let risk = self
            .risk
            .value()
            .filter(|risk| *risk != 0.0 && !risk.is_nan())?;
        let exponent = self.hurst.as_ref()?.value()?;
        let scale = (1.0 + response * (exponent - 0.5) * 2.0).max(0.5).min(2.0);
        let depth = depth * scale;
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

    /// `failed_break_signal`. The raw side FADES the failed break, and
    /// `direction == "follow"` inverts it -- the opposite polarity to
    /// `Direction::apply`.
    pub(crate) fn failed_break(
        &self,
        candle: &Candle,
        minute: i64,
        buffer_atr: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let (high, low) = self.prior.range?;
        let atr = self.atr.value().filter(|atr| *atr != 0.0)?;
        let edge = buffer_atr * atr;
        let raw = if candle.high > high + edge && candle.close < high {
            Side::Short
        } else if candle.low < low - edge && candle.close > low {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Follow => Direction::Fade.apply(raw),
            Direction::Fade => raw,
        })
    }

    /// `sar_signal`: the flip, then the cutoff.
    pub(crate) fn sar_signal(&self, minute: i64) -> Option<Side> {
        let flip = self.sar.as_ref()?.flip();
        if flip == 0 || self.late(minute) {
            return None;
        }
        let raw = if flip > 0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// `fastbar._signal(_bollinger)`: the cutoff, then the band event, then
    /// `_out` (`follow` keeps the side).
    pub(crate) fn fb_bollinger(&self, minute: i64, reenter: bool) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let raw = match self.band.as_ref()?.event(reenter) {
            1 => Side::Long,
            -1 => Side::Short,
            _ => return None,
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
    pub(crate) fn gate_opinion(&self, candle: &Candle, gate: Gate) -> Option<i32> {
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
            Gate::Supertrend => {
                let (now, _) = self.supertrend_now()?;
                Some(if now == 1 || now == -1 { now } else { 0 })
            }
            Gate::Obv => {
                let line = self.obv.as_ref()?.total;
                let top = self.obv_high.as_ref()?.value()?;
                let bottom = self.obv_low.as_ref()?.value()?;
                if top <= bottom {
                    return None;
                }
                let middle = 0.5 * (top + bottom);
                Some(if line > middle {
                    1
                } else if line < middle {
                    -1
                } else {
                    0
                })
            }
        }
    }

    /// The supertrend direction at this candle, without requiring the
    /// previous one (a gate reads the state, not the flip).
    fn supertrend_now(&self) -> Option<(i32, Option<i32>)> {
        let supertrend = self.supertrend.as_ref()?;
        supertrend.current().map(|now| (now, supertrend.previous_reading()))
    }

    /// `gate_accepts`: whether the gate lets `side` through.
    pub(crate) fn gate_accepts(
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
    pub(crate) fn gated_orb(
        &self,
        candle: &Candle,
        minute: i64,
        gate: Gate,
        polarity: Polarity,
    ) -> Option<Side> {
        let (top, bottom) = self.opening.as_ref()?.value()?;
        if self.late(minute) || minute < self.spec.session.0 + self.bar_seconds / 60 {
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
    pub(crate) fn two_stage(
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
    pub(crate) fn break_retest(
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
    pub(crate) fn level_confluence(
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
    pub(crate) fn volume_thrust(
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
    pub(crate) fn zscore_signal(
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
    pub(crate) fn volatility_breakout(
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
    pub(crate) fn confluence(&self, candle: &Candle, minute: i64, votes: i32) -> Option<Side> {
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
    pub(crate) fn vote_rsi_extreme(&self) -> Option<i32> {
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
    pub(crate) fn vote_zscore(&self, candle: &Candle) -> Option<i32> {
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
    pub(crate) fn vote_vwap_deviation(&self, candle: &Candle) -> Option<i32> {
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
    pub(crate) fn vote_wick_rejection(&self, candle: &Candle) -> Option<i32> {
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
    pub(crate) fn vote_range_position(&self, candle: &Candle) -> Option<i32> {
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
    pub(crate) fn trap(
        &self,
        candle: &Candle,
        minute: i64,
        window: usize,
        level: BreakLevel,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let skip = self.levels.len().saturating_sub(window);
        for past in self.levels.iter().skip(skip) {
            let (top, bottom) = match level {
                BreakLevel::Donchian => (past.top, past.bottom),
                BreakLevel::Pdr => match past.prior_range {
                    Some((high, low)) => (Some(high), Some(low)),
                    None => (None, None),
                },
            };
            let (Some(top), Some(bottom)) = (top, bottom) else {
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
    pub(crate) fn obv_break(&self, _candle: &Candle, minute: i64) -> Option<Side> {
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
    pub(crate) fn xma_cross(&self, _candle: &Candle, minute: i64) -> Option<Side> {
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

    /// `jump_signal`: the one-session move, in the regime the cell trades.
    pub(crate) fn jump_signal(
        &self,
        candle: &Candle,
        minute: i64,
        share: f64,
        jumpy: bool,
        lookback: usize,
    ) -> Option<Side> {
        let value = self.jump.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        if (value >= share) != jumpy {
            return None;
        }
        let before = self.closes.as_ref()?.back(lookback)?;
        let move_ = candle.close - before;
        if move_ == 0.0 {
            return None;
        }
        let raw = if move_ > 0.0 { Side::Long } else { Side::Short };
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
    pub(crate) fn vol_regime(
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
    pub(crate) fn momentum_stack(
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
            let variance = closes.window_variance(2 * span)?;
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
    /// `day_of_week_signal`: `es.weekday` of the candle's own (shifted) stamp.
    pub(crate) fn day_of_week(&self, candle: &Candle, weekday: i64, long: bool) -> Option<Side> {
        if (self.day_of(candle.ts) + 3).rem_euclid(7) != weekday {
            return None;
        }
        Some(if long { Side::Long } else { Side::Short })
    }

    /// `key_reversal_signal`. The raw side is the fade; `follow` inverts it.
    pub(crate) fn key_reversal(&self, candle: &Candle, minute: i64) -> Option<Side> {
        let previous = self.previous_close?;
        if self.late(minute) {
            return None;
        }
        let top = self.channel_high.as_ref()?.value()?;
        let bottom = self.channel_low.as_ref()?.value()?;
        let raw = if candle.high > top && candle.close < previous {
            Side::Short
        } else if candle.low < bottom && candle.close > previous {
            Side::Long
        } else {
            return None;
        };
        Some(match self.params.direction {
            Direction::Fade => raw,
            Direction::Follow => Direction::Fade.apply(raw),
        })
    }

    /// `swing_break_signal`.
    pub(crate) fn swing_break(&self, candle: &Candle, minute: i64) -> Option<Side> {
        let pivots = self.pivots.as_ref()?;
        let (top, bottom) = (pivots.high?, pivots.low?);
        if self.late(minute) {
            return None;
        }
        let raw = if candle.close > top {
            Side::Long
        } else if candle.close < bottom {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `structure_signal`.
    pub(crate) fn structure(&self, candle: &Candle, minute: i64, continuation: bool) -> Option<Side> {
        let pivots = self.pivots.as_ref()?;
        let (high, low) = (pivots.high?, pivots.low?);
        let (previous_high, previous_low) = (pivots.prior_high?, pivots.prior_low?);
        if self.late(minute) {
            return None;
        }
        let up = high > previous_high && low > previous_low;
        let down = high < previous_high && low < previous_low;
        let raw = if continuation {
            if up && candle.close > high {
                Side::Long
            } else if down && candle.close < low {
                Side::Short
            } else {
                return None;
            }
        } else if up && candle.close < low {
            Side::Short
        } else if down && candle.close > high {
            Side::Long
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `usoil_families_research.momentum_signal`: no `_late`, a clock minute.
    pub(crate) fn momentum(
        &self,
        candle: &Candle,
        minute: i64,
        signal_minute: i64,
        lookback: usize,
        threshold_atr: f64,
    ) -> Option<Side> {
        if minute != signal_minute {
            return None;
        }
        let before = self.closes.as_ref()?.back(lookback)?;
        let atr = self.atr.value().filter(|atr| *atr > 0.0)?;
        let change = candle.close - before;
        let threshold = threshold_atr * atr;
        let raw = if change > threshold {
            Side::Long
        } else if change < -threshold {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `macd_hist_signal`.
    pub(crate) fn macd_hist(&self, minute: i64, zero: bool) -> Option<Side> {
        if self.candles < 3 {
            return None;
        }
        let macd = self.macd.as_ref()?;
        let (now, before) = (macd.histogram?, macd.previous?);
        if self.late(minute) {
            return None;
        }
        let line = macd.line?;
        let raw = if zero {
            if before <= 0.0 && 0.0 < now {
                Side::Long
            } else if before >= 0.0 && 0.0 > now {
                Side::Short
            } else {
                return None;
            }
        } else {
            let earlier = macd.earlier?;
            if earlier < before && before > now && line > 0.0 {
                Side::Short
            } else if earlier > before && before < now && line < 0.0 {
                Side::Long
            } else {
                return None;
            }
        };
        Some(self.params.direction.apply(raw))
    }

    /// `quantile_break_signal`.
    pub(crate) fn quantile_break(&self, candle: &Candle, minute: i64, buffer_atr: f64) -> Option<Side> {
        let quantile = self.quantile.as_ref()?;
        let (top, bottom) = (quantile.upper?, quantile.lower?);
        if self.late(minute) {
            return None;
        }
        let atr = self.atr.value().filter(|atr| *atr != 0.0 && !atr.is_nan())?;
        let buffer = buffer_atr * atr;
        let raw = if candle.close > top + buffer {
            Side::Long
        } else if candle.close < bottom - buffer {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// The `unit`-candle move both `vol_of_vol` and `estimator` trade.
    fn unit_move(&self, candle: &Candle, lookback: usize) -> Option<Side> {
        let before = self.closes.as_ref()?.back(lookback)?;
        let change = candle.close - before;
        if change == 0.0 {
            return None;
        }
        let raw = if change > 0.0 { Side::Long } else { Side::Short };
        Some(self.params.direction.apply(raw))
    }

    /// `vol_of_vol_signal`.
    pub(crate) fn vol_of_vol_signal(
        &self,
        candle: &Candle,
        minute: i64,
        period: usize,
        dispersion: f64,
        unsteady: bool,
        lookback: usize,
    ) -> Option<Side> {
        if (self.candles as usize) < period + 1 || self.late(minute) {
            return None;
        }
        let value = self.vol_of_vol.as_ref()?.value()?;
        if (value >= dispersion) != unsteady {
            return None;
        }
        self.unit_move(candle, lookback)
    }

    /// `regime_breakout_signal`.
    pub(crate) fn regime_breakout(
        &self,
        candle: &Candle,
        minute: i64,
        fraction: f64,
        response: f64,
    ) -> Option<Side> {
        if self.late(minute) {
            return None;
        }
        let (high, low) = self.prior.range?;
        let opening = self.session.open;
        if !opening.is_finite() {
            return None;
        }
        let span = high - low;
        if span <= 0.0 {
            return None;
        }
        let short = self.short_volatility.value()?;
        let long = self.long_volatility.value().filter(|long| *long > 0.0)?;
        let ratio = short / long;
        let scale = 1.0 + response * (ratio.max(0.33).min(3.0) - 1.0);
        let edge = fraction * span * scale;
        let raw = if candle.close > opening + edge {
            Side::Long
        } else if candle.close < opening - edge {
            Side::Short
        } else {
            return None;
        };
        Some(self.params.direction.apply(raw))
    }

    /// `estimator_signal`.
    pub(crate) fn estimator(
        &self,
        candle: &Candle,
        minute: i64,
        parkinson: bool,
        ratio: f64,
        whipping: bool,
        lookback: usize,
    ) -> Option<Side> {
        let estimators = self.estimators.as_ref()?;
        let path = if parkinson {
            estimators.parkinson?
        } else {
            estimators.garman_klass?
        };
        let close = estimators.close_to_close.filter(|close| *close > 0.0)?;
        if self.late(minute) {
            return None;
        }
        if (path / close >= ratio) != whipping {
            return None;
        }
        self.unit_move(candle, lookback)
    }

    /// `gated_donchian_signal`.
    pub(crate) fn gated_donchian(
        &self,
        candle: &Candle,
        minute: i64,
        gate: Gate,
        polarity: Polarity,
    ) -> Option<Side> {
        let top = self.channel_high.as_ref()?.value()?;
        let bottom = self.channel_low.as_ref()?.value()?;
        if self.late(minute) {
            return None;
        }
        let side = if candle.close > top {
            Side::Long
        } else if candle.close < bottom {
            Side::Short
        } else {
            return None;
        };
        self.gate_accepts(candle, gate, side, polarity).then_some(side)
    }

    /// `gated_fade_signal`.
    pub(crate) fn gated_fade(
        &self,
        candle: &Candle,
        minute: i64,
        trigger: FadeTrigger,
        gate: Gate,
        polarity: Polarity,
    ) -> Option<Side> {
        let side = match trigger {
            FadeTrigger::VwapDev => match self.vote_vwap_deviation(candle)? {
                1 => Side::Long,
                -1 => Side::Short,
                _ => return None,
            },
        };
        if self.late(minute) {
            return None;
        }
        self.gate_accepts(candle, gate, side, polarity).then_some(side)
    }

    pub(crate) fn kendall_signal(&self, minute: i64, threshold: f64) -> Option<Side> {
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
    pub(crate) fn cusum_signal(&self, minute: i64) -> Option<Side> {
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
    pub(crate) fn obv_divergence(&self, candle: &Candle, minute: i64) -> Option<Side> {
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
    pub(crate) fn kalman_signal(
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
    pub(crate) fn fracdiff_signal(&self, minute: i64, threshold_z: f64) -> Option<Side> {
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
    pub(crate) fn half_life_signal(
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
    pub(crate) fn on_candle(
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
        //
        // A SLEEVE ON ANOTHER BAR SIZE READS THE SAME 30-MINUTE GRID. `replay`
        // keys the throttle per MARKET on the context bars of the 30m cells, so
        // a 5m candle's close is not the day's close it uses; `update_all` feeds
        // those sleeves from the source bars instead (`fold_throttle`).
        if !self.throttle_from_source() && self.throttle_counts(candle.ts) {
            self.volatility_multiplier.push(candle.close, day_changed);
        }

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
            // A `multiday` position is not flattened at the close: it holds
            // until its own stop, target, trail or clock (`swing` in Python).
            if !self.sleeve.multiday() && (minute >= close_minute || last_of_day) {
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
                // The family's own exit, read on the PREVIOUS candle's close
                // (`exit_signal(index - 1, ...)`) and taken at this open.
                if price.is_none() && self.exit_flag {
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
        //    Outside `rth` a fill on the day's LAST candle is dropped: nothing may
        //    be carried through the rollover.
        if self.sleeve.broker_day() && last_of_day {
            self.pending = None;
        }
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
        // The `off` window takes no new position while the cash session is open.
        let tradeable = (self.spec.session.0..close_minute).contains(&minute)
            && weekday_allowed
            && !self.in_cash_session(candle.ts);
        if self.position.is_none()
            && !filled_early
            && self.pending.is_none()
            && self.traded_day != Some(day)
            && tradeable
            && self.accepts_vol()
            && let Some((side, own_distance, own_target)) =
                self.signal_with_distance(&candle, minute)
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
                let distance = own_distance
                    .filter(|distance| distance.is_finite() && *distance > 0.0)
                    .unwrap_or(self.params.stop_day * risk);
                self.pending = Some(Pending {
                    side,
                    day,
                    distance,
                    target: own_target,
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
            self.fast_tema.as_ref().and_then(MovingAverage::value),
            self.slow_tema.as_ref().and_then(MovingAverage::value),
        ) {
            self.previous_cross = Some(fast - slow);
        }
        if let (Some(fast), Some(slow)) = (
            self.fast_ema.as_ref().and_then(Ema::value),
            self.slow_ema.as_ref().and_then(Ema::value),
        ) {
            self.previous_cross = Some(fast - slow);
        }

        self.previous_close = Some(candle.close);

        // 5. The family's own exit rule at THIS close, for the position held
        //    through it -- acted on at the next candle's open.
        self.exit_flag = match (&self.position, &self.tt, self.params.exit) {
            (Some(position), Some(tt), Exit::Signal) => tt.exit_now(position.side),
            _ => false,
        };

        actions
    }

    /// Folds one SOURCE bar into the sizing throttle for a sleeve whose candles
    /// are not the 30-minute grid.
    ///
    /// `daily_multipliers` takes each day's LAST close among the market's
    /// in-session 30-minute bars. A bar belongs to one when its 30-minute SLOT
    /// is in session, which is `context`'s rule for the aggregated bar.
    /// Whether the sizing throttle is fed from the source bars (`fold_throttle`)
    /// rather than from this sleeve's own in-session candles.
    ///
    /// A sleeve on another bar size always is. So is a FundedNext OFF-HOURS
    /// sleeve on any bar size: `fundednext_combined_strategies` marks and
    /// throttles an `<market>~off` alias on EVERY 30m bar of the broker day,
    /// the 00:00-01:00 slot the session leaves out included, and that slot is
    /// where a Friday-evening print becomes a day of its own. On
    /// `de40:stochastic@60m` it moved the multiplier 1.9187 -> 1.9080 and the
    /// lot under the 0.02 floor. The Exness book's off-hours cells keep their
    /// sealed (in-session) grid.
    /// Canon replay of the FundedNext book: Python's replay marks, and so
    /// throttles, only on bars from `WARMUP_DAYS` before `LOG_START`
    /// (`fundednext_combined_strategies.load_state`), whatever the window.
    fn throttle_counts(&self, ts: i64) -> bool {
        !(self.canon_replay && self.sleeve.book() == Book::FundedNext)
            || ts >= crate::strategies::fundednext::oct_18_2026::CANON_THROTTLE_FROM
    }

    fn throttle_from_source(&self) -> bool {
        self.bar_seconds != BAR_SECONDS || self.whole_day_throttle()
    }

    fn whole_day_throttle(&self) -> bool {
        self.sleeve.book() == Book::FundedNext && self.sleeve.broker_day()
    }

    /// Live `CFD_BROKER_HOURS`: the candle opening at `ts` (shifted) survives
    /// only if the broker printed inside it. Always true without a source.
    fn candle_quoted(&self, ts: i64, width: i64) -> bool {
        let Some(quotes) = self.quotes.as_ref().filter(|_| self.sleeve.broker_hours()) else {
            return true;
        };
        let start = ts - self.spec.shift_hours * 3_600;
        quotes.quoted_real(start, start + width)
    }

    fn fold_throttle(&mut self, ts: i64, close: f64) {
        if !self.throttle_from_source() {
            return;
        }
        let slot = ts.div_euclid(BAR_SECONDS) * BAR_SECONDS;
        // The throttle reads the market's 30-minute bars, which the broker's
        // hours filter at THAT width.
        if !self.candle_quoted(slot, BAR_SECONDS) {
            return;
        }
        if !self.whole_day_throttle() && !self.in_session(self.minute_of(slot)) {
            return;
        }
        if !self.throttle_counts(ts) {
            return;
        }
        let day = self.day_of(ts);
        let changed = self.vol_day != Some(day);
        self.vol_day = Some(day);
        self.volatility_multiplier.push(close, changed);
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
        let width = self.bar_seconds;
        let slot = ts.div_euclid(width);
        if let Some(previous) = self.last_bar_ts {
            let gap = ts - previous;
            if gap > 0 && self.source_step.is_none_or(|step| gap < step) {
                self.source_step = Some(gap);
            }
        }
        self.last_bar_ts = Some(ts);
        if let Some(tt) = &mut self.tt {
            tt.observe_source(bar.ts, bar.open, bar.high, bar.low);
        }
        let mut actions = Vec::new();
        if self.slot != Some(slot) {
            if let Some(candle) = self.building.take() {
                let minute = self.minute_of(candle.ts);
                if self.in_session(minute) && self.candle_quoted(candle.ts, width) {
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
                    let opening = slot * width;
                    let last_of_day = !self.in_session(self.minute_of(opening))
                        || self.day_of(opening) != self.day_of(candle.ts)
                        || self.holds_last_print(candle.ts);
                    actions = self.on_candle(candle, equity, last_of_day);
                    if !actions.is_empty() {
                        self.action_timestamp = Some(candle.ts - self.spec.shift_hours * 3_600);
                    }
                }
            }
            // After the closed candle has been stepped on the day it belongs to,
            // before anything is filled on the new one.
            self.fold_throttle(ts, bar.close);
            self.slot = Some(slot);
            self.building = Some(Candle {
                ts: slot * width,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume: bar.volume,
            });
            let opening = slot * width;
            // AN ENTRY ALREADY SENT AT ITS SIGNAL is re-based onto this, the
            // real open the backtest fills at -- or unwound if the backtest
            // would not have filled it here at all.
            if let Some(provisional) = self.provisional.take()
                && let Some(unwind) = self.rebase_provisional(provisional, opening, bar.open)
            {
                if self.action_timestamp.is_none() {
                    self.action_timestamp = Some(opening - self.spec.shift_hours * 3_600);
                }
                actions.push(unwind);
            }
            // THE ENTRY GOES OUT NOW, on the candle's first minute, whose open
            // IS the candle's open -- the price the backtest books. See
            // `enable_early_fills`. Only for a candle `on_candle` will later
            // step, which is an in-session one. Reached only when the signal
            // could not be sent at its own close (`send_at_signal`).
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
            self.fold_throttle(ts, bar.close);
        } else {
            self.fold_throttle(ts, bar.close);
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
            let slot_end = (slot + 1) * width;
            // EXACTLY the slot's end, never merely past it. `source_step` is the
            // smallest gap seen so far, so early in a stream it can still be an
            // over-estimate, and `>=` would let one of those close a candle that
            // is not finished. An equality can only be wrong by being missed, and
            // a miss costs nothing: the block above still closes the slot when
            // the next bar arrives, exactly as before. The same fall-back covers
            // a slot whose final period never printed.
            // A FUNDEDNEXT SLEEVE ALSO CLOSES ON THE SLOT'S LAST BAR THE FEED
            // HAS, which a backtest knows (`set_next_bar`): Python settles a
            // decision at its candle's stamp, and a candle the market stopped
            // printing in -- a Friday-17:00 minute alone in the broker day's
            // 00:00 slot -- would otherwise wait for Monday's first bar while
            // the rest of the book trades past it.
            let last_known = self.sleeve.book() == Book::FundedNext
                && self.next_bar.is_some_and(|next| {
                    next.is_none_or(|next| {
                        (next + self.spec.shift_hours * 3_600).div_euclid(width) != slot
                    })
                });
            if (step < width && ts + step == slot_end) || last_known || self.is_last_print(ts) {
                let candle = self.building.take().expect("checked just above");
                let candle_ts = candle.ts;
                let candle_close = candle.close;
                if self.in_session(self.minute_of(candle_ts)) && self.candle_quoted(candle_ts, width) {
                    // THE FUNDEDNEXT BOOK READS THE SUCCESSOR, as Python does.
                    // Its markets stop printing before their configured close
                    // (DE40's vendor feed an hour before the broker day ends,
                    // HK50 an hour before its window in winter), and the clock
                    // then never calls a candle the day's last, so a session
                    // position sleeps for days. A backtest knows the next bar
                    // (`set_next_bar`); the arrival rule above is applied to it.
                    // The Exness book keeps the clock, which its live runtime
                    // shares.
                    let arrival = match (self.sleeve.book(), self.next_bar) {
                        (Book::FundedNext, Some(next)) => Some(next.is_none_or(|next| {
                            let opening = (next + self.spec.shift_hours * 3_600).div_euclid(width) * width;
                            !self.in_session(self.minute_of(opening))
                                || self.day_of(opening) != self.day_of(candle_ts)
                        })),
                        _ => None,
                    };
                    let last_of_day = arrival.unwrap_or_else(|| {
                        !self.in_session(self.minute_of(slot_end))
                            || self.day_of(slot_end) != self.day_of(candle_ts)
                            || self.holds_last_print(candle_ts)
                    });
                    let mut closed = self.on_candle(candle, equity, last_of_day);
                    if let Some(enter) = self.send_at_signal(slot_end, candle_close, equity) {
                        closed.push(enter);
                    }
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
        self.provisional = None;
        self.position = None;
        self.traded_day = None;
        self.shadow_equity = self.sleeve.book().admission_balance() * self.sleeve.shown_equity();
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
        self.provisional = None;
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

    /// `multiday` (`ethusd:sb_bollinger@240m-nat`): held through the close.
    fn holds_overnight(&self) -> bool {
        self.sleeve.multiday()
    }

    fn entry_sizing(&self) -> Option<(f64, f64)> {
        self.position.as_ref().map(|position| (position.distance, position.mult))
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
    // `oct_12_2026` sizes itself, exactly as the commodity and index books
    // did. Leaving both hooks at their `None` default is what lets `quantity`
    // through to the broker-minimum check.
}

/// The book's sizing rule (`exness_combined_strategies.size`): lots for an entry
/// at `entry` with stop `distance`, sized off `equity` at throttle `mult`.
/// `FamilyEngine::quantity` and the challenge replay both call this.
pub(crate) fn book_lots(
    sleeve: Sleeve,
    spec: &Instrument,
    quantity_step: f64,
    equity: f64,
    entry: f64,
    distance: f64,
    mult: f64,
) -> Option<f64> {
        let money = spec.multiplier;
        if money <= 0.0 || distance <= 0.0 || equity <= 0.0 || entry <= 0.0 {
            return None;
        }
        let factor = sleeve.scale() * sleeve.book().risk_scale();
        if factor <= 0.0 {
            return None;
        }
        // Uncapped: the canon run records an empty `sizing_equity_cap`, so a
        // sleeve compounds against the whole shared balance.
        let sizing_equity = equity * sleeve.shown_equity();
        let risk = sizing_equity * RISK_FRACTION * factor * mult;
        let raw = risk / (distance * money);
        let step = quantity_step.max(spec.volume_step);
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
        if sleeve.sized_as_import() {
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
            entry * spec.contract_size * spec.fx_to_usd * MARGIN_FRACTION;
        let ceiling = if margin_per_lot > 0.0 {
            spec.volume_max.min(equity / margin_per_lot)
        } else {
            0.0
        };
        let floor = spec.volume_min;
        let mut lots = (raw.min(ceiling) / step + 1e-10).floor() * step;
        if FORCE_MINIMUM_LOT && raw > 0.0 && lots + 1e-10 < floor && ceiling + 1e-10 >= floor {
            lots = floor;
        }
        (lots + 1e-10 >= floor && lots.is_finite()).then_some(lots)
    }
