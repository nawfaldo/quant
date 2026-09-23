//! Streaming indicators, one bar at a time.
//!
//! Every one of these is a bar-at-a-time rewrite of an `exness_families` helper
//! that builds a whole array up front. The contract each keeps is the value the
//! Python array holds AT THE CURRENT INDEX once this candle has been folded in,
//! so a signal reading them here sees exactly what the sealed cell saw.
//!
//! WARM-UP IS NOT UNIFORM AND THAT IS FAITHFUL. `rolling_extreme` answers from
//! bar 1 with a partial window; `rolling_mean_sigma` refuses until it has a full
//! one; `ema` is seeded at the first value and never refuses at all. Making them
//! agree would change what the cells trade.

use super::*;

/// `es.rolling_extreme`: the extreme of the `period` bars *before* this one, the
/// current bar excluded.
///
/// Excluding the current bar is what makes a breakout test meaningful -- a close
/// can never exceed a channel that already counted it. Monotonic deque, so the
/// answer is O(1) amortised rather than a scan of a 300-bar window.
pub(super) struct RollingExtreme {
    period: usize,
    maximum: bool,
    /// `(index, value)`, decreasing for a maximum and increasing for a minimum.
    queue: VecDeque<(usize, f64)>,
    index: usize,
}

impl RollingExtreme {
    pub(super) fn new(period: usize, maximum: bool) -> Self {
        Self {
            period,
            maximum,
            queue: VecDeque::new(),
            index: 0,
        }
    }

    /// The reading at this bar, taken BEFORE the bar is pushed.
    pub(super) fn value(&self) -> Option<f64> {
        self.queue.front().map(|(_, value)| *value)
    }

    pub(super) fn push(&mut self, value: f64) {
        while self.queue.back().is_some_and(|(_, held)| {
            if self.maximum {
                value >= *held
            } else {
                value <= *held
            }
        }) {
            self.queue.pop_back();
        }
        self.queue.push_back((self.index, value));
        self.index += 1;
        // Pruned for the NEXT read: the window then covers indices
        // `[next - period, next - 1]`.
        let cutoff = self.index as i64 - 1 - self.period as i64;
        while self
            .queue
            .front()
            .is_some_and(|(at, _)| (*at as i64) <= cutoff)
        {
            self.queue.pop_front();
        }
    }
}

/// `rolling_mean_sigma`: mean and population sigma of the `period` values
/// ending at this index, inclusive. `None` until a full window exists.
pub(super) struct MeanSigma {
    period: usize,
    values: VecDeque<f64>,
    total: f64,
    total_sq: f64,
}

impl MeanSigma {
    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            total: 0.0,
            total_sq: 0.0,
        }
    }

    pub(super) fn push(&mut self, value: f64) {
        self.values.push_back(value);
        self.total += value;
        self.total_sq += value * value;
        if self.values.len() > self.period
            && let Some(old) = self.values.pop_front()
        {
            self.total -= old;
            self.total_sq -= old * old;
        }
    }

    pub(super) fn value(&self) -> Option<(f64, f64)> {
        if self.values.len() < self.period {
            return None;
        }
        let n = self.period as f64;
        let mean = self.total / n;
        Some((mean, (self.total_sq / n - mean * mean).max(0.0).sqrt()))
    }
}

/// `rolling_min`: the minimum of the `period` values ending at this index, THE
/// BAR INCLUDED, and `None` until a full window exists.
///
/// NOT `RollingExtreme::new(period, false)`, and the difference decides whether
/// `two_stage` ever arms. That one answers from bar 1 with a partial window and
/// EXCLUDES the bar it is asked about, because it exists for breakout tests. A
/// compression test asks whether THIS bar's width is the smallest of the last
/// `period` INCLUDING itself, so a squeeze read against the exclusive channel
/// would compare a bar with a window it is not part of and arm on the wrong bars.
pub(super) struct RollingMinInclusive {
    period: usize,
    /// `(index, value)`, increasing, so the front is the window minimum.
    queue: VecDeque<(usize, f64)>,
    index: usize,
    seen: usize,
}

impl RollingMinInclusive {
    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            queue: VecDeque::new(),
            index: 0,
            seen: 0,
        }
    }

    /// Folds this bar in and returns the reading AT it.
    pub(super) fn push(&mut self, value: f64) -> Option<f64> {
        while self.queue.back().is_some_and(|(_, held)| *held >= value) {
            self.queue.pop_back();
        }
        self.queue.push_back((self.index, value));
        let cutoff = self.index as i64 - self.period as i64;
        while self
            .queue
            .front()
            .is_some_and(|(at, _)| (*at as i64) <= cutoff)
        {
            self.queue.pop_front();
        }
        self.index += 1;
        self.seen += 1;
        (self.seen >= self.period).then(|| self.queue.front().expect("just pushed").1)
    }
}

/// `rolling_mean`: the causal mean of the `period` values ending here.
pub(super) struct RollingMean {
    period: usize,
    values: VecDeque<f64>,
    total: f64,
}

impl RollingMean {
    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            total: 0.0,
        }
    }

    pub(super) fn push(&mut self, value: f64) {
        self.values.push_back(value);
        self.total += value;
        if self.values.len() > self.period
            && let Some(old) = self.values.pop_front()
        {
            self.total -= old;
        }
    }

    pub(super) fn value(&self) -> Option<f64> {
        (self.values.len() >= self.period).then(|| self.total / self.period as f64)
    }
}

/// `es.ema`: seeded at the first value, so it never refuses to answer.
#[derive(Clone, Copy)]
pub(super) struct Ema {
    alpha: f64,
    level: Option<f64>,
}

impl Ema {
    pub(super) fn new(period: usize) -> Self {
        Self {
            alpha: 2.0 / (period as f64 + 1.0),
            level: None,
        }
    }

    pub(super) fn push(&mut self, value: f64) -> f64 {
        let level = match self.level {
            Some(previous) => previous + self.alpha * (value - previous),
            None => value,
        };
        self.level = Some(level);
        level
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.level
    }
}

/// `exness_indicators.tema`: `3*e1 - 3*e2 + e3`, an EMA with two rounds of its
/// own error added back. Refuses for the first `3 * period` bars, which is the
/// Python guard and not a warm-up convention -- the chained seeds make the early
/// values meaningless rather than merely noisy.
pub(super) struct Tema {
    period: usize,
    first: Ema,
    second: Ema,
    third: Ema,
    seen: usize,
    level: Option<f64>,
}

impl Tema {
    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            first: Ema::new(period),
            second: Ema::new(period),
            third: Ema::new(period),
            seen: 0,
            level: None,
        }
    }

    pub(super) fn push(&mut self, value: f64) {
        let one = self.first.push(value);
        let two = self.second.push(one);
        let three = self.third.push(two);
        self.level = (self.seen >= 3 * self.period).then_some(3.0 * one - 3.0 * two + three);
        self.seen += 1;
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.level
    }
}

/// `es.average_true_range`: a simple mean of the last `period` true ranges. The
/// first bar has no previous close, so its own open stands in.
pub(super) struct AverageTrueRange {
    mean: RollingMean,
    previous_close: Option<f64>,
}

impl AverageTrueRange {
    pub(super) fn new(period: usize) -> Self {
        Self {
            mean: RollingMean::new(period),
            previous_close: None,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle) {
        let previous = self.previous_close.unwrap_or(candle.open);
        let range = (candle.high - candle.low)
            .max((candle.high - previous).abs())
            .max((candle.low - previous).abs());
        self.mean.push(range);
        self.previous_close = Some(candle.close);
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.mean.value()
    }
}

/// `trailing_volatility`: annualised standard deviation of the last `period`
/// log returns, inclusive of this bar's.
pub(super) struct TrailingVolatility {
    stats: MeanSigma,
    annual: f64,
    previous_close: Option<f64>,
}

impl TrailingVolatility {
    pub(super) fn new(period: usize, annual: f64) -> Self {
        Self {
            stats: MeanSigma::new(period),
            annual,
            previous_close: None,
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        // Python prepends a zero return for the first bar rather than skipping
        // it, so the window is the same length as the bar list.
        let value = match self.previous_close {
            Some(previous) if previous > 0.0 && close > 0.0 => (close / previous).ln(),
            _ => 0.0,
        };
        self.stats.push(value);
        self.previous_close = Some(close);
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.stats
            .value()
            .map(|(_, sigma)| (sigma * sigma * self.annual).sqrt())
    }
}

/// `wilder_rsi`: Wilder's smoothing, which saturates. An extreme reading means
/// "one-sided for a while" rather than "far from the mean", which is a different
/// claim about the same price path from the one a z-score makes.
pub(super) struct WilderRsi {
    period: usize,
    previous_close: Option<f64>,
    seen: usize,
    seed_gains: f64,
    seed_losses: f64,
    gains: f64,
    losses: f64,
    level: Option<f64>,
}

impl WilderRsi {
    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            previous_close: None,
            seen: 0,
            seed_gains: 0.0,
            seed_losses: 0.0,
            gains: 0.0,
            losses: 0.0,
            level: None,
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        let Some(previous) = self.previous_close.replace(close) else {
            return;
        };
        let change = close - previous;
        self.seen += 1;
        if self.seen <= self.period {
            self.seed_gains += change.max(0.0);
            self.seed_losses += (-change).max(0.0);
            if self.seen == self.period {
                self.gains = self.seed_gains / self.period as f64;
                self.losses = self.seed_losses / self.period as f64;
                self.level = Some(Self::rsi(self.gains, self.losses));
            }
            return;
        }
        let n = self.period as f64;
        self.gains = (self.gains * (n - 1.0) + change.max(0.0)) / n;
        self.losses = (self.losses * (n - 1.0) + (-change).max(0.0)) / n;
        self.level = Some(Self::rsi(self.gains, self.losses));
    }

    pub(super) fn rsi(gains: f64, losses: f64) -> f64 {
        if losses > 0.0 {
            100.0 - 100.0 / (1.0 + gains / losses)
        } else {
            100.0
        }
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.level
    }
}

/// `daily_risk`: the average true DAILY range over the previous
/// `DAILY_RANGE_DAYS` completed days, carried onto every bar of the next one.
///
/// Only days strictly BEFORE a bar's own day contribute, so nothing leaks from
/// the day being traded into the size of the risk taken on it. The daily range
/// is measured over the SESSION bars alone, because that is the array the Python
/// builds it from.
pub(super) struct DailyRisk {
    day: Option<i64>,
    high: f64,
    low: f64,
    close: f64,
    previous_close: Option<f64>,
    ranges: VecDeque<f64>,
    total: f64,
    /// The reading offered for the day currently being accumulated.
    current: Option<f64>,
}

impl DailyRisk {
    pub(super) fn new() -> Self {
        Self {
            day: None,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            close: 0.0,
            previous_close: None,
            ranges: VecDeque::with_capacity(DAILY_RANGE_DAYS + 1),
            total: 0.0,
            current: None,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            if self.day.is_some() {
                self.complete();
            }
            self.day = Some(day);
            self.high = candle.high;
            self.low = candle.low;
        } else {
            self.high = self.high.max(candle.high);
            self.low = self.low.min(candle.low);
        }
        self.close = candle.close;
    }

    /// Seals the day that just ended and republishes the reading the NEXT day
    /// will trade on.
    pub(super) fn complete(&mut self) {
        let mut span = self.high - self.low;
        if let Some(previous) = self.previous_close {
            span = span
                .max((self.high - previous).abs())
                .max((self.low - previous).abs());
        }
        self.previous_close = Some(self.close);
        self.ranges.push_back(span);
        self.total += span;
        if self.ranges.len() > DAILY_RANGE_DAYS
            && let Some(old) = self.ranges.pop_front()
        {
            self.total -= old;
        }
        self.current =
            (self.ranges.len() >= DAILY_RANGE_DAYS).then(|| self.total / DAILY_RANGE_DAYS as f64);
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.on_balance_volume`: cumulative signed volume. The level is
/// meaningless; the SHAPE is the point, which is why it is read through a
/// channel on itself rather than against a price level.
pub(super) struct OnBalanceVolume {
    pub(super) total: f64,
    previous_close: Option<f64>,
}

impl OnBalanceVolume {
    pub(super) fn new() -> Self {
        Self {
            total: 0.0,
            previous_close: None,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle) {
        if let Some(previous) = self.previous_close {
            if candle.close > previous {
                self.total += candle.volume;
            } else if candle.close < previous {
                self.total -= candle.volume;
            }
        }
        self.previous_close = Some(candle.close);
    }
}

/// `exness_indicators.floor_pivots`, and `prior_ranges` alongside it: both are
/// the previous day's shape read from the same accumulator.
///
/// The pivot is `(H + L + C) / 3` and R/S are reflections of the range around
/// it -- levels almost nobody transacted at. Whether such a level works is a
/// question about self-fulfilment, and it can only be asked with a price that
/// has no trade behind it.
pub(super) struct PriorDay {
    day: Option<i64>,
    high: f64,
    low: f64,
    close: f64,
    /// `(high, low)` of the previous day.
    pub(super) range: Option<(f64, f64)>,
    /// `(pivot, r1, s1, r2, s2)` from the previous day.
    pub(super) pivots: Option<(f64, f64, f64, f64, f64)>,
}

impl PriorDay {
    pub(super) fn new() -> Self {
        Self {
            day: None,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            close: 0.0,
            range: None,
            pivots: None,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            if self.day.is_some() {
                self.range = Some((self.high, self.low));
                let pivot = (self.high + self.low + self.close) / 3.0;
                let span = self.high - self.low;
                self.pivots = Some((
                    pivot,
                    2.0 * pivot - self.low,
                    2.0 * pivot - self.high,
                    pivot + span,
                    pivot - span,
                ));
            }
            self.day = Some(day);
            self.high = candle.high;
            self.low = candle.low;
        } else {
            self.high = self.high.max(candle.high);
            self.low = self.low.min(candle.low);
        }
        self.close = candle.close;
    }
}

/// `session_running` and `session_vwap`: the day's path so far, inclusive of
/// this candle.
///
/// Precomputed in Python rather than accumulated inside a signal, because the
/// engine only calls a signal on bars where no position is open and the
/// volatility filter accepts -- so a rule building its own running range would
/// silently skip bars and measure a short day. The same reasoning is why this is
/// updated on EVERY in-session candle here and not inside `signal`.
pub(super) struct SessionPath {
    pub(super) day: Option<i64>,
    pub(super) open: f64,
    pub(super) high: f64,
    pub(super) low: f64,
    notional: f64,
    volume: f64,
}

impl SessionPath {
    pub(super) fn new() -> Self {
        Self {
            day: None,
            open: 0.0,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            notional: 0.0,
            volume: 0.0,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            self.day = Some(day);
            self.open = candle.open;
            self.high = candle.high;
            self.low = candle.low;
            self.notional = 0.0;
            self.volume = 0.0;
        } else {
            self.high = self.high.max(candle.high);
            self.low = self.low.min(candle.low);
        }
        self.notional += (candle.high + candle.low + candle.close) / 3.0 * candle.volume;
        self.volume += candle.volume;
    }

    pub(super) fn vwap(&self) -> Option<f64> {
        (self.volume > 0.0).then(|| self.notional / self.volume)
    }
}

/// `daily_multipliers`, the book's two-sided volatility throttle.
///
/// NO LOOKAHEAD. The multiplier offered for a day is computed from days STRICTLY
/// BEFORE it -- the day's own return is folded in only after the multiplier for
/// that day has been recorded. Reading it the other way would let a sleeve size
/// down on the morning of a crash it has not seen yet, which is the most
/// flattering bug available in a risk model.
pub(super) struct VolatilityMultiplier {
    variance: f64,
    days_seen: u32,
    previous_close: Option<f64>,
    latest_close: Option<f64>,
}

impl VolatilityMultiplier {
    pub(super) fn new() -> Self {
        Self {
            variance: 0.0,
            days_seen: 0,
            previous_close: None,
            latest_close: None,
        }
    }

    pub(super) fn push(&mut self, close: f64, day_changed: bool) {
        if day_changed {
            self.complete_day();
        }
        self.latest_close = Some(close);
    }

    pub(super) fn complete_day(&mut self) {
        let Some(latest) = self.latest_close else {
            return;
        };
        let Some(previous) = self.previous_close.replace(latest) else {
            return;
        };
        if previous <= 0.0 || latest <= 0.0 {
            return;
        }
        // A SIMPLE return, matching `daily_multipliers`. The log return the
        // realised-volatility gates use is a different series and the two are
        // deliberately not shared.
        let change = latest / previous - 1.0;
        if self.days_seen == 0 {
            self.variance = change * change;
        } else {
            let lambda = 0.5f64.powf(1.0 / VOL_HALFLIFE);
            self.variance = lambda * self.variance + (1.0 - lambda) * change * change;
        }
        self.days_seen += 1;
    }

    pub(super) fn value(&self) -> f64 {
        if self.days_seen < VOL_MIN_DAYS || self.variance <= 0.0 {
            return 1.0;
        }
        let annual = (self.variance * 252.0).sqrt();
        if annual > 0.0 {
            (VOL_TARGET / annual).min(VOL_MAX_MULTIPLIER)
        } else {
            1.0
        }
    }
}

/// `exness_indicators.mann_kendall_z`: the nonparametric monotone-trend test.
///
/// COMPUTED BY SLIDING, NOT BY RECOMPUTING, exactly as the Python does. S is a
/// sum over every pair in the window, so when the window advances the only
/// pairs that change are those involving the departing and the arriving value:
/// subtract the one, add the other. That is O(period) a bar against the naive
/// O(period^2), and it is the EXACT statistic rather than a subsample -- which
/// matters here because `usdjpy:kendall` reads a 270-bar window on every one of
/// a hundred thousand candles.
///
/// THE CONTINUITY CORRECTION IS PART OF THE READING. `(S - 1)` above zero and
/// `(S + 1)` below it, never `S / root`: at the threshold this cell trades on,
/// the correction is worth about a hundredth of a sigma, which is a different
/// set of bars.
pub(super) struct MannKendall {
    period: usize,
    window: VecDeque<f64>,
    score: i64,
    root: f64,
    value: Option<f64>,
}

impl MannKendall {
    pub(super) fn new(period: usize) -> Self {
        let n = period as f64;
        let variance = n * (n - 1.0) * (2.0 * n + 5.0) / 18.0;
        Self {
            period,
            window: VecDeque::with_capacity(period + 1),
            score: 0,
            root: variance.sqrt(),
            value: None,
        }
    }

    /// `1`, `-1` or `0` on the sign of a comparison, which is the whole
    /// statistic: a violent bar contributes one count like every other.
    fn sign(left: f64, right: f64) -> i64 {
        if left > right {
            1
        } else if left < right {
            -1
        } else {
            0
        }
    }

    pub(super) fn push(&mut self, value: f64) {
        // Python refuses the whole series below ten observations, because the
        // normal approximation the z-score rests on is not one at that size.
        if self.period < 10 || self.root <= 0.0 {
            self.value = None;
            return;
        }
        let arriving: i64 = self
            .window
            .iter()
            .map(|other| Self::sign(value, *other))
            .sum();
        self.score += arriving;
        self.window.push_back(value);
        if self.window.len() > self.period {
            let gone = self.window.pop_front().expect("just pushed");
            let departing: i64 = self
                .window
                .iter()
                .map(|other| Self::sign(*other, gone))
                .sum();
            self.score -= departing;
        }
        self.value = if self.window.len() < self.period {
            None
        } else if self.score > 0 {
            Some((self.score as f64 - 1.0) / self.root)
        } else if self.score < 0 {
            Some((self.score as f64 + 1.0) / self.root)
        } else {
            Some(0.0)
        };
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.value
    }
}

/// `exness_indicators.kalman_trend`: a local-linear-trend filter on LOG price.
///
/// THE SLOPE IS A STATE, not a difference of two smoothed points. A differenced
/// slope is the slope of the past window and keeps the old sign for as long as
/// the window is wide; this is the filter's current estimate of the rate and
/// turns with the data.
///
/// The two variances are FIXED rather than swept -- they matter only through
/// their ratio, and sweeping them would be sweeping how much to smooth, which
/// the `trend` axis already asks.
pub(super) struct KalmanTrend {
    level: Option<f64>,
    slope: f64,
    /// The state covariance, symmetric 2x2 as `(p00, p01, p11)`.
    p00: f64,
    p01: f64,
    p11: f64,
    level_out: Option<f64>,
    slope_out: Option<f64>,
}

impl KalmanTrend {
    const PROCESS: f64 = 1e-4;
    const MEASUREMENT: f64 = 1e-2;

    pub(super) fn new() -> Self {
        Self {
            level: None,
            slope: 0.0,
            p00: 1.0,
            p01: 1.0,
            p11: 1.0,
            level_out: None,
            slope_out: None,
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        // Python `continue`s on an unusable close, which leaves BOTH outputs at
        // `None` for this bar while every piece of state carries over untouched.
        self.level_out = None;
        self.slope_out = None;
        // A NaN close is unusable too, which is why the test is written as
        // "is a positive number" rather than as `close <= 0.0`.
        if close.is_nan() || close <= 0.0 {
            return;
        }
        let observed = close.ln();
        let Some(level) = self.level else {
            self.level = Some(observed);
            self.slope = 0.0;
            return;
        };
        // Predict: the level advances by the slope, the slope persists, and the
        // covariance grows by the process noise.
        let mut level = level + self.slope;
        self.p00 = self.p00 + 2.0 * self.p01 + self.p11 + Self::PROCESS;
        self.p01 += self.p11;
        self.p11 += Self::PROCESS;
        let innovation = observed - level;
        let s = self.p00 + Self::MEASUREMENT;
        let (k0, k1) = (self.p00 / s, self.p01 / s);
        level += k0 * innovation;
        self.slope += k1 * innovation;
        // `p01` is read twice on the right, so `p11` must consume the
        // PRE-update value.
        let new_p00 = self.p00 - k0 * self.p00;
        let new_p01 = self.p01 - k0 * self.p01;
        self.p11 -= k1 * self.p01;
        self.p00 = new_p00;
        self.p01 = new_p01;
        self.level = Some(level);
        self.level_out = Some(level.exp());
        self.slope_out = Some(self.slope);
    }

    /// `ctx["kalman_level"]`, back in PRICE units.
    ///
    /// NOT `self.level`, which is the running LOG state and is carried across a
    /// bar the filter could not read. This is the value the Python array holds
    /// at this index, which is `None` on such a bar.
    #[allow(clippy::misnamed_getters, reason = "the state and the reading differ")]
    pub(super) fn level(&self) -> Option<f64> {
        self.level_out
    }

    /// `ctx["kalman_slope"]`, a per-bar LOG return -- so comparable against
    /// `atr / close` and against nothing else.
    pub(super) fn slope(&self) -> Option<f64> {
        self.slope_out
    }
}

/// `exness_indicators.frac_diff`: fractionally differenced LOG price.
///
/// The weights decay as a POWER LAW and are truncated once one falls below
/// `THRESHOLD`, which is the standard construction: the reading keeps the same
/// memory length at every bar rather than growing it, so an early reading and a
/// late one are comparable.
///
/// `weights[0]` multiplies the MOST RECENT observation. Consuming the window the
/// other way round produces a perfectly plausible series that is a smoothing
/// rather than a differencing.
pub(super) struct FracDiff {
    weights: Vec<f64>,
    logs: VecDeque<f64>,
    value: Option<f64>,
}

impl FracDiff {
    const WIDTH: usize = 64;
    const THRESHOLD: f64 = 1e-4;

    pub(super) fn new(order: f64) -> Self {
        let mut weights = Vec::new();
        // An order outside (0, 1) is not a fractional difference at all, and
        // Python answers `None` for the whole series rather than falling through
        // to a one-weight identity.
        if order > 0.0 && order < 1.0 {
            weights.push(1.0f64);
            for k in 1..Self::WIDTH {
                let weight = -weights[weights.len() - 1] * (order - k as f64 + 1.0) / k as f64;
                if weight.abs() < Self::THRESHOLD {
                    break;
                }
                weights.push(weight);
            }
        }
        Self {
            weights,
            logs: VecDeque::new(),
            value: None,
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        self.value = None;
        if self.weights.is_empty() {
            return;
        }
        // A non-positive close is a HOLE, not a zero: Python leaves its log as
        // `None` and skips every window that still contains it, so the reading
        // resumes only once the hole has rolled out of the span.
        if close.is_nan() || close <= 0.0 {
            self.logs.clear();
            return;
        }
        self.logs.push_back(close.ln());
        while self.logs.len() > self.weights.len() {
            self.logs.pop_front();
        }
        if self.logs.len() < self.weights.len() {
            return;
        }
        let mut total = 0.0;
        for (k, weight) in self.weights.iter().enumerate() {
            total += weight * self.logs[self.logs.len() - 1 - k];
        }
        self.value = Some(total);
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.value
    }
}

/// `exness_indicators.cusum_events`: Lopez de Prado's symmetric CUSUM filter.
///
/// THE RESET IS WHAT MAKES IT AN EVENT SAMPLER rather than another oscillator.
/// The same drift cannot fire it twice without an intervening retracement, so
/// its trades are spaced by market structure instead of by a bar count.
///
/// THE THRESHOLD IS PER BAR AND IT IS `multiple * atr / close`, NOT the
/// annualised volatility. The accumulator sums one-bar log returns; thresholding
/// those with an annualised fraction is out by `sqrt(annual_periods)` -- about
/// 90 at 30 minutes -- and the filter then fires a couple of dozen times in
/// seven years instead of a few hundred.
pub(super) struct CusumFilter {
    multiple: f64,
    positive: f64,
    negative: f64,
    previous: Option<f64>,
    event: i32,
}

impl CusumFilter {
    pub(super) fn new(multiple: f64) -> Self {
        Self {
            multiple,
            positive: 0.0,
            negative: 0.0,
            previous: None,
            event: 0,
        }
    }

    /// `atr` is this candle's INCLUSIVE reading, which is what `ctx["atr"]`
    /// holds at this index.
    pub(super) fn push(&mut self, close: f64, atr: Option<f64>) {
        self.event = 0;
        let previous = self.previous;
        // Recorded whatever happens below: Python indexes `closes[index - 1]`
        // rather than tracking a cursor, so a bar the filter skipped is still
        // the next bar's predecessor.
        self.previous = Some(close);
        let Some(previous) = previous.filter(|value| *value > 0.0) else {
            return;
        };
        if close.is_nan() || close <= 0.0 {
            return;
        }
        // A bar with no threshold reading passes through WITHOUT accumulating,
        // rather than accumulating against a stale level.
        let Some(threshold) = atr
            .map(|value| self.multiple * value / close)
            .filter(|value| value.is_finite() && *value > 0.0)
        else {
            return;
        };
        let step = (close / previous).ln();
        self.positive = (self.positive + step).max(0.0);
        self.negative = (self.negative + step).min(0.0);
        if self.positive > threshold {
            self.event = 1;
            self.positive = 0.0;
            self.negative = 0.0;
        } else if self.negative < -threshold {
            self.event = -1;
            self.positive = 0.0;
            self.negative = 0.0;
        }
    }

    /// `ctx["cusum"][multiple][index]`: `1`, `-1`, or `0` for no event.
    pub(super) fn value(&self) -> i32 {
        self.event
    }
}

/// `ctx["prefix_sum"]` and `ctx["prefix_square"]`, plus the closes themselves.
///
/// PREFIX SUMS, so a mean and a variance over ANY window ending at this bar are
/// two subtractions. `momentum_stack` standardises three horizons on every bar
/// -- `session`, `5 * session` and `20 * session` -- and a rolling accumulator
/// for each would be three more indicators serving one family.
///
/// The closes are kept beside them because both families that read this block
/// also want the close a fixed number of bars BACK, which a prefix sum cannot
/// answer.
pub(super) struct CloseHistory {
    closes: Vec<f64>,
    total: Vec<f64>,
    square: Vec<f64>,
}

impl CloseHistory {
    pub(super) fn new() -> Self {
        Self {
            closes: Vec::new(),
            total: vec![0.0],
            square: vec![0.0],
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        let last = self.total.len() - 1;
        self.total.push(self.total[last] + close);
        self.square.push(self.square[last] + close * close);
        self.closes.push(close);
    }

    /// `bars[index - back][C]`, or `None` when the history is shorter than
    /// that -- which is Python's `if index < lookback: return None`.
    pub(super) fn back(&self, back: usize) -> Option<f64> {
        self.closes
            .len()
            .checked_sub(back + 1)
            .map(|index| self.closes[index])
    }

    /// The POPULATION variance of the `count` closes ending at this bar, read
    /// off the prefix sums. `None` when the history is shorter than the window.
    pub(super) fn variance(&self, count: usize) -> Option<f64> {
        let end = self.closes.len();
        let start = end.checked_sub(count)?;
        let n = count as f64;
        let mean = (self.total[end] - self.total[start]) / n;
        Some((self.square[end] - self.square[start]) / n - mean * mean)
    }

    /// Both moments over the same window, in one pair of subtractions.
    ///
    /// `half_life` needs the MEAN as well, and asking `variance` for it would
    /// mean computing it twice over a window whose length changes every bar.
    /// Python reads both off `ctx["prefix_sum"]` and `ctx["prefix_square"]` in
    /// the same four lines, so this is the same arithmetic in the same order.
    pub(super) fn mean_variance(&self, count: usize) -> Option<(f64, f64)> {
        let end = self.closes.len();
        let start = end.checked_sub(count)?;
        let n = count as f64;
        let mean = (self.total[end] - self.total[start]) / n;
        Some((mean, (self.square[end] - self.square[start]) / n - mean * mean))
    }
}

/// `ou_half_life(closes, period)`: the mean-reversion half-life, in BARS.
///
/// The Ornstein-Uhlenbeck fit, streamed. Python regresses the one-bar change of
/// the LOG close on the previous log close over a rolling `period` window --
/// `dp = lambda*p + c` -- and converts the fitted lambda into the time a
/// deviation takes to decay by half. `half_life` then uses that as its own
/// z-score lookback, so this is a horizon that is MEASURED every bar rather
/// than chosen once.
///
/// THREE REFUSALS, AND EACH IS A REFUSAL RATHER THAN A NUMBER:
///
///   * a non-negative fitted lambda means the series is not reverting at all
///   * a degenerate window has no slope to fit
///   * a half-life longer than the sample it came from is an extrapolation
///
/// On a trending series the fitted lambda sits a hair below zero by chance and
/// the implied half-life is thousands of bars; clamping it to the ceiling would
/// report that as a confident reading indistinguishable from a genuine slow
/// reverter, so it is dropped instead.
///
/// `count` IS LITERALLY `period`, NOT THE NUMBER OF PAIRS ACCUMULATED, which is
/// what Python does and therefore what parity requires: a window containing a
/// non-positive close contributes no pair but still divides by `period`.
pub(super) struct OuHalfLife {
    period: usize,
    /// `(log close, one-bar log change)` for the last `period + 2` bars, which
    /// is every entry either end of the rolling window can still reach.
    history: VecDeque<(Option<f64>, f64)>,
    sx: f64,
    sy: f64,
    sxy: f64,
    sxx: f64,
    /// Python's `index`: how many closes have been pushed, minus one.
    index: usize,
    seen: bool,
    value: Option<f64>,
}

impl OuHalfLife {
    /// The floor Python applies to a fitted life before returning it.
    const FLOOR: f64 = 2.0;

    pub(super) fn new(period: usize) -> Self {
        Self {
            period,
            history: VecDeque::with_capacity(period + 3),
            sx: 0.0,
            sy: 0.0,
            sxy: 0.0,
            sxx: 0.0,
            index: 0,
            seen: false,
            value: None,
        }
    }

    pub(super) fn push(&mut self, close: f64) {
        if self.seen {
            self.index += 1;
        }
        self.seen = true;
        let index = self.index;
        let log = if close > 0.0 { Some(close.ln()) } else { None };
        let previous = self.history.back().and_then(|(log, _)| *log);
        let diff = match (log, previous) {
            (Some(now), Some(before)) => now - before,
            _ => 0.0,
        };
        self.history.push_back((log, diff));
        while self.history.len() > self.period + 2 {
            self.history.pop_front();
        }

        // ENTERING: the pair `(logs[index - 1], diffs[index])`, so the
        // regressor is the level the change was measured FROM.
        if index >= 1 {
            if let Some(x) = previous {
                let y = diff;
                self.sx += x;
                self.sy += y;
                self.sxy += x * y;
                self.sxx += x * x;
            }
        }
        // LEAVING: `(logs[gone - 1], diffs[gone])` at `gone = index - period`.
        // After the trim the deque holds `index - period - 1 ..= index`, so
        // those two live at offsets 0 and 1.
        if let Some(gone) = index.checked_sub(self.period).filter(|gone| *gone >= 1) {
            let _ = gone;
            if self.history.len() == self.period + 2 {
                if let Some(x) = self.history[0].0 {
                    let y = self.history[1].1;
                    self.sx -= x;
                    self.sy -= y;
                    self.sxy -= x * y;
                    self.sxx -= x * x;
                }
            }
        }

        self.value = self.fit(index);
    }

    fn fit(&self, index: usize) -> Option<f64> {
        if self.period < 20 || index < self.period + 1 {
            return None;
        }
        let count = self.period as f64;
        let mean_x = self.sx / count;
        let variance = self.sxx / count - mean_x * mean_x;
        if variance <= 1e-24 {
            return None;
        }
        let slope = (self.sxy / count - mean_x * (self.sy / count)) / variance;
        if slope >= -1e-9 {
            return None;
        }
        let life = std::f64::consts::LN_2 / -slope;
        // The ceiling is `period`: a half-life longer than its own sample is an
        // extrapolation, and the one value that means "not reverting on any
        // timescale I can see".
        if life > self.period as f64 {
            return None;
        }
        Some(life.max(Self::FLOOR))
    }

    pub(super) fn value(&self) -> Option<f64> {
        self.value
    }
}

/// `opening_ranges(bars, (1,))[1]`: the FIRST in-session candle's high and low,
/// day-keyed.
///
/// Precomputed in Python rather than accumulated inside a signal, and that is
/// the whole difference between `gated_orb` and `orb`. The engine calls a signal
/// only on bars where no position is open, so a rule building its own opening
/// range measures a SHORT range on exactly the days it was already busy. This is
/// fed on EVERY in-session candle for the same reason.
pub(super) struct OpeningRange {
    day: Option<i64>,
    window: Option<(f64, f64)>,
}

impl OpeningRange {
    pub(super) fn new() -> Self {
        Self {
            day: None,
            window: None,
        }
    }

    pub(super) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            self.day = Some(day);
            self.window = Some((candle.high, candle.low));
        }
    }

    /// `(high, low)` of today's opening candle.
    pub(super) fn value(&self) -> Option<(f64, f64)> {
        self.window
    }
}
