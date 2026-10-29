//! Streaming indicators, one bar at a time.
//!
//! Every one of these is a bar-at-a-time rewrite of an `cfd_families` helper
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
pub(crate) struct RollingExtreme {
    period: usize,
    maximum: bool,
    /// `(index, value)`, decreasing for a maximum and increasing for a minimum.
    queue: VecDeque<(usize, f64)>,
    index: usize,
}

impl RollingExtreme {
    pub(crate) fn new(period: usize, maximum: bool) -> Self {
        Self {
            period,
            maximum,
            queue: VecDeque::new(),
            index: 0,
        }
    }

    /// The reading at this bar, taken BEFORE the bar is pushed.
    pub(crate) fn value(&self) -> Option<f64> {
        self.queue.front().map(|(_, value)| *value)
    }

    pub(crate) fn push(&mut self, value: f64) {
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
pub(crate) struct MeanSigma {
    period: usize,
    values: VecDeque<f64>,
    total: f64,
    total_sq: f64,
}

impl MeanSigma {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            total: 0.0,
            total_sq: 0.0,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
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

    pub(crate) fn value(&self) -> Option<(f64, f64)> {
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
pub(crate) struct RollingMinInclusive {
    period: usize,
    /// `(index, value)`, increasing, so the front is the window minimum.
    queue: VecDeque<(usize, f64)>,
    index: usize,
    seen: usize,
}

impl RollingMinInclusive {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            queue: VecDeque::new(),
            index: 0,
            seen: 0,
        }
    }

    /// Folds this bar in and returns the reading AT it.
    pub(crate) fn push(&mut self, value: f64) -> Option<f64> {
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
pub(crate) struct RollingMean {
    period: usize,
    values: VecDeque<f64>,
    total: f64,
}

impl RollingMean {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            total: 0.0,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        self.values.push_back(value);
        self.total += value;
        if self.values.len() > self.period
            && let Some(old) = self.values.pop_front()
        {
            self.total -= old;
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        (self.values.len() >= self.period).then(|| self.total / self.period as f64)
    }
}

/// `es.ema`: seeded at the first value, so it never refuses to answer.
#[derive(Clone, Copy)]
pub(crate) struct Ema {
    alpha: f64,
    level: Option<f64>,
}

impl Ema {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            alpha: 2.0 / (period as f64 + 1.0),
            level: None,
        }
    }

    pub(crate) fn push(&mut self, value: f64) -> f64 {
        let level = match self.level {
            Some(previous) => previous + self.alpha * (value - previous),
            None => value,
        };
        self.level = Some(level);
        level
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.level
    }
}

/// `exness_indicators.tema`: `3*e1 - 3*e2 + e3`, an EMA with two rounds of its
/// own error added back. Refuses for the first `3 * period` bars, which is the
/// Python guard and not a warm-up convention -- the chained seeds make the early
/// values meaningless rather than merely noisy.
pub(crate) struct Tema {
    period: usize,
    first: Ema,
    second: Ema,
    third: Ema,
    seen: usize,
    level: Option<f64>,
}

impl Tema {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            first: Ema::new(period),
            second: Ema::new(period),
            third: Ema::new(period),
            seen: 0,
            level: None,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        let one = self.first.push(value);
        let two = self.second.push(one);
        let three = self.third.push(two);
        self.level = (self.seen >= 3 * self.period).then_some(3.0 * one - 3.0 * two + three);
        self.seen += 1;
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.level
    }
}

/// `es.average_true_range`: a simple mean of the last `period` true ranges. The
/// first bar has no previous close, so its own open stands in.
pub(crate) struct AverageTrueRange {
    mean: RollingMean,
    previous_close: Option<f64>,
}

impl AverageTrueRange {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            mean: RollingMean::new(period),
            previous_close: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
        let previous = self.previous_close.unwrap_or(candle.open);
        let range = (candle.high - candle.low)
            .max((candle.high - previous).abs())
            .max((candle.low - previous).abs());
        self.mean.push(range);
        self.previous_close = Some(candle.close);
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.mean.value()
    }
}

/// `trailing_volatility`: annualised standard deviation of the last `period`
/// log returns, inclusive of this bar's.
pub(crate) struct TrailingVolatility {
    stats: MeanSigma,
    annual: f64,
    previous_close: Option<f64>,
}

impl TrailingVolatility {
    pub(crate) fn new(period: usize, annual: f64) -> Self {
        Self {
            stats: MeanSigma::new(period),
            annual,
            previous_close: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        // Python prepends a zero return for the first bar rather than skipping
        // it, so the window is the same length as the bar list.
        let value = match self.previous_close {
            Some(previous) if previous > 0.0 && close > 0.0 => (close / previous).ln(),
            _ => 0.0,
        };
        self.stats.push(value);
        self.previous_close = Some(close);
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.stats
            .value()
            .map(|(_, sigma)| (sigma * sigma * self.annual).sqrt())
    }
}

/// `wilder_rsi`: Wilder's smoothing, which saturates. An extreme reading means
/// "one-sided for a while" rather than "far from the mean", which is a different
/// claim about the same price path from the one a z-score makes.
pub(crate) struct WilderRsi {
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
    pub(crate) fn new(period: usize) -> Self {
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

    pub(crate) fn push(&mut self, close: f64) {
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

    pub(crate) fn rsi(gains: f64, losses: f64) -> f64 {
        if losses > 0.0 {
            100.0 - 100.0 / (1.0 + gains / losses)
        } else {
            100.0
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
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
pub(crate) struct DailyRisk {
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
    pub(crate) fn new() -> Self {
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

    pub(crate) fn push(&mut self, candle: &Candle, day: i64) {
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
    pub(crate) fn complete(&mut self) {
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

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.on_balance_volume`: cumulative signed volume. The level is
/// meaningless; the SHAPE is the point, which is why it is read through a
/// channel on itself rather than against a price level.
pub(crate) struct OnBalanceVolume {
    pub(crate) total: f64,
    previous_close: Option<f64>,
}

impl OnBalanceVolume {
    pub(crate) fn new() -> Self {
        Self {
            total: 0.0,
            previous_close: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
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
pub(crate) struct PriorDay {
    day: Option<i64>,
    high: f64,
    low: f64,
    close: f64,
    /// `(high, low)` of the previous day.
    pub(crate) range: Option<(f64, f64)>,
    /// `(pivot, r1, s1, r2, s2)` from the previous day.
    pub(crate) pivots: Option<(f64, f64, f64, f64, f64)>,
}

impl PriorDay {
    pub(crate) fn new() -> Self {
        Self {
            day: None,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            close: 0.0,
            range: None,
            pivots: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle, day: i64) {
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
pub(crate) struct SessionPath {
    pub(crate) day: Option<i64>,
    pub(crate) open: f64,
    pub(crate) high: f64,
    pub(crate) low: f64,
    notional: f64,
    volume: f64,
}

impl SessionPath {
    pub(crate) fn new() -> Self {
        Self {
            day: None,
            open: 0.0,
            high: f64::NEG_INFINITY,
            low: f64::INFINITY,
            notional: 0.0,
            volume: 0.0,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle, day: i64) {
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

    pub(crate) fn vwap(&self) -> Option<f64> {
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
pub(crate) struct VolatilityMultiplier {
    variance: f64,
    days_seen: u32,
    previous_close: Option<f64>,
    latest_close: Option<f64>,
}

impl VolatilityMultiplier {
    pub(crate) fn new() -> Self {
        Self {
            variance: 0.0,
            days_seen: 0,
            previous_close: None,
            latest_close: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64, day_changed: bool) {
        if day_changed {
            self.complete_day();
        }
        self.latest_close = Some(close);
    }

    pub(crate) fn complete_day(&mut self) {
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

    pub(crate) fn value(&self) -> f64 {
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
pub(crate) struct MannKendall {
    period: usize,
    window: VecDeque<f64>,
    score: i64,
    root: f64,
    value: Option<f64>,
}

impl MannKendall {
    pub(crate) fn new(period: usize) -> Self {
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

    pub(crate) fn push(&mut self, value: f64) {
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

    pub(crate) fn value(&self) -> Option<f64> {
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
pub(crate) struct KalmanTrend {
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

    pub(crate) fn new() -> Self {
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

    pub(crate) fn push(&mut self, close: f64) {
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
    pub(crate) fn level(&self) -> Option<f64> {
        self.level_out
    }

    /// `ctx["kalman_slope"]`, a per-bar LOG return -- so comparable against
    /// `atr / close` and against nothing else.
    pub(crate) fn slope(&self) -> Option<f64> {
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
pub(crate) struct FracDiff {
    weights: Vec<f64>,
    logs: VecDeque<f64>,
    value: Option<f64>,
}

impl FracDiff {
    const WIDTH: usize = 64;
    const THRESHOLD: f64 = 1e-4;

    pub(crate) fn new(order: f64) -> Self {
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

    pub(crate) fn push(&mut self, close: f64) {
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

    pub(crate) fn value(&self) -> Option<f64> {
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
pub(crate) struct CusumFilter {
    multiple: f64,
    positive: f64,
    negative: f64,
    previous: Option<f64>,
    event: i32,
}

impl CusumFilter {
    pub(crate) fn new(multiple: f64) -> Self {
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
    pub(crate) fn push(&mut self, close: f64, atr: Option<f64>) {
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
    pub(crate) fn value(&self) -> i32 {
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
pub(crate) struct CloseHistory {
    closes: Vec<f64>,
    total: Vec<f64>,
    square: Vec<f64>,
}

impl CloseHistory {
    pub(crate) fn new() -> Self {
        Self {
            closes: Vec::new(),
            total: vec![0.0],
            square: vec![0.0],
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        let last = self.total.len() - 1;
        self.total.push(self.total[last] + close);
        self.square.push(self.square[last] + close * close);
        self.closes.push(close);
    }

    /// `bars[index - back][C]`, or `None` when the history is shorter than
    /// that -- which is Python's `if index < lookback: return None`.
    pub(crate) fn back(&self, back: usize) -> Option<f64> {
        self.closes
            .len()
            .checked_sub(back + 1)
            .map(|index| self.closes[index])
    }

    /// The POPULATION variance of the `count` closes ending at this bar, read
    /// off the prefix sums. `None` when the history is shorter than the window.
    pub(crate) fn variance(&self, count: usize) -> Option<f64> {
        let end = self.closes.len();
        let start = end.checked_sub(count)?;
        let n = count as f64;
        let mean = (self.total[end] - self.total[start]) / n;
        Some((self.square[end] - self.square[start]) / n - mean * mean)
    }

    /// The POPULATION variance of the `count` closes ending at this bar,
    /// TWO-PASS over the window itself -- `momentum_stack_signal` since
    /// 2026-10-12. The prefix-sum form cancels catastrophically once the squared
    /// sums are large, and its answer then depends on where the history began.
    pub(crate) fn window_variance(&self, count: usize) -> Option<f64> {
        let end = self.closes.len();
        let start = end.checked_sub(count)?;
        let window = &self.closes[start..end];
        let mut total = 0.0;
        for close in window {
            total += close;
        }
        let mean = total / count as f64;
        let mut variance = 0.0;
        for close in window {
            variance += (close - mean) * (close - mean);
        }
        Some(variance / count as f64)
    }

    /// Both moments over the same window, in one pair of subtractions.
    ///
    /// `half_life` needs the MEAN as well, and asking `variance` for it would
    /// mean computing it twice over a window whose length changes every bar.
    /// Python reads both off `ctx["prefix_sum"]` and `ctx["prefix_square"]` in
    /// the same four lines, so this is the same arithmetic in the same order.
    pub(crate) fn mean_variance(&self, count: usize) -> Option<(f64, f64)> {
        let end = self.closes.len();
        let start = end.checked_sub(count)?;
        let n = count as f64;
        let mean = (self.total[end] - self.total[start]) / n;
        Some((
            mean,
            (self.square[end] - self.square[start]) / n - mean * mean,
        ))
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
pub(crate) struct OuHalfLife {
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

    pub(crate) fn new(period: usize) -> Self {
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

    pub(crate) fn push(&mut self, close: f64) {
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

    pub(crate) fn value(&self) -> Option<f64> {
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
pub(crate) struct OpeningRange {
    day: Option<i64>,
    window: Option<(f64, f64)>,
}

impl OpeningRange {
    pub(crate) fn new() -> Self {
        Self {
            day: None,
            window: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            self.day = Some(day);
            self.window = Some((candle.high, candle.low));
        }
    }

    /// `(high, low)` of today's opening candle.
    pub(crate) fn value(&self) -> Option<(f64, f64)> {
        self.window
    }
}

/// `ind.aroon`: how RECENTLY the `period`-bar extreme was set, in [0, 100].
///
/// `rolling_argextreme` INCLUDES the current bar (unlike `RollingExtreme`), and
/// on a tie it keeps the LATER index -- the deque pops while the held value is
/// `<=` (max) or `>=` (min) the new one -- so an equal high set again today
/// reads as brand new. It answers only once `period` bars exist.
///
/// Both the current and the previous reading are held, because the family
/// trades the CROSS of up over down and Python reads index and index - 1.
pub(crate) struct Aroon {
    period: usize,
    highs: VecDeque<(usize, f64)>,
    lows: VecDeque<(usize, f64)>,
    index: usize,
    current: Option<(f64, f64)>,
    previous: Option<(f64, f64)>,
}

impl Aroon {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            highs: VecDeque::new(),
            lows: VecDeque::new(),
            index: 0,
            current: None,
            previous: None,
        }
    }

    fn age(
        queue: &mut VecDeque<(usize, f64)>,
        index: usize,
        period: usize,
        value: f64,
        maximum: bool,
    ) {
        while queue
            .front()
            .is_some_and(|(at, _)| (*at as i64) <= index as i64 - period as i64)
        {
            queue.pop_front();
        }
        while queue.back().is_some_and(|(_, held)| {
            if maximum {
                *held <= value
            } else {
                *held >= value
            }
        }) {
            queue.pop_back();
        }
        queue.push_back((index, value));
    }

    pub(crate) fn push(&mut self, high: f64, low: f64) {
        let index = self.index;
        Self::age(&mut self.highs, index, self.period, high, true);
        Self::age(&mut self.lows, index, self.period, low, false);
        self.previous = self.current;
        self.current = if index + 1 >= self.period {
            let period = self.period as f64;
            let up_age = (index - self.highs.front().map_or(index, |(at, _)| *at)) as f64;
            let down_age = (index - self.lows.front().map_or(index, |(at, _)| *at)) as f64;
            Some((
                100.0 * (period - up_age) / period,
                100.0 * (period - down_age) / period,
            ))
        } else {
            None
        };
        self.index += 1;
    }

    /// `((up, down) now, (up, down) one bar ago)`.
    pub(crate) fn readings(&self) -> Option<((f64, f64), (f64, f64))> {
        Some((self.current?, self.previous?))
    }
}

/// `ind.roofing_filter`: Ehlers' two-pole Butterworth HIGHPASS at `high_period`,
/// then his SuperSmoother LOWPASS at `low_period`. A bandpass with zero mean.
///
/// RECURSIVE FROM THE FIRST CLOSE, so it is streamed exactly as Python loops:
/// `highpass` and `smooth` are 0.0 at indices 0 and 1 and the recursions start
/// at 2. Output is withheld until `3 * high_period` bars, Python's allowance
/// for the transient. The last 21 outputs are kept for the amplitude gate,
/// which reads `line[index - 20 ..= index]`.
pub(crate) struct Roofing {
    alpha: f64,
    gain: f64,
    c1: f64,
    c2: f64,
    c3: f64,
    warm: usize,
    index: usize,
    closes: [f64; 2],
    highpass: [f64; 2],
    smooth: [f64; 2],
    recent: VecDeque<Option<f64>>,
}

impl Roofing {
    pub(crate) fn new(high_period: usize, low_period: usize) -> Self {
        let radians = 0.707 * 2.0 * std::f64::consts::PI / high_period as f64;
        let alpha = (radians.cos() + radians.sin() - 1.0) / radians.cos();
        let gain = (1.0 - alpha / 2.0).powi(2);
        let a1 = (-1.414 * std::f64::consts::PI / low_period as f64).exp();
        let b1 = 2.0 * a1 * (1.414 * std::f64::consts::PI / low_period as f64).cos();
        let (c2, c3) = (b1, -a1 * a1);
        Self {
            alpha,
            gain,
            c1: 1.0 - c2 - c3,
            c2,
            c3,
            warm: 3 * high_period,
            index: 0,
            closes: [0.0; 2],
            highpass: [0.0; 2],
            smooth: [0.0; 2],
            recent: VecDeque::new(),
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        let (mut hp, mut sm) = (0.0, 0.0);
        if self.index >= 2 {
            let (b, c) = (self.closes[1], self.closes[0]);
            hp = self.gain * (close - 2.0 * b + c) + 2.0 * (1.0 - self.alpha) * self.highpass[1]
                - (1.0 - self.alpha).powi(2) * self.highpass[0];
            sm = self.c1 * (hp + self.highpass[1]) / 2.0
                + self.c2 * self.smooth[1]
                + self.c3 * self.smooth[0];
        }
        self.closes = [self.closes[1], close];
        self.highpass = [self.highpass[1], hp];
        self.smooth = [self.smooth[1], sm];
        let out = (self.index >= self.warm).then_some(sm);
        self.recent.push_back(out);
        while self.recent.len() > 21 {
            self.recent.pop_front();
        }
        self.index += 1;
    }

    /// `(line[index], line[index - 1])`.
    pub(crate) fn readings(&self) -> Option<(f64, f64)> {
        let n = self.recent.len();
        if n < 2 {
            return None;
        }
        Some((self.recent[n - 1]?, self.recent[n - 2]?))
    }

    /// `max(|line[i]| for i in index-20 ..= index if present)`.
    pub(crate) fn recent_amplitude(&self) -> Option<f64> {
        self.recent
            .iter()
            .flatten()
            .map(|v| v.abs())
            .fold(None, |m, v| Some(m.map_or(v, |m: f64| m.max(v))))
    }
}

/// `ind.relative_volume(bars, days=20)`: this candle's volume over the mean of
/// the SAME CLOCK MINUTE on up to 20 earlier sessions.
///
/// Causal exactly as Python is: the reading is taken from the window BEFORE this
/// candle is appended, and needs at least `max(5, days // 4)` = 5 earlier
/// observations. A zero average reads as missing, not infinite.
pub(crate) struct RelativeVolume {
    by_minute: std::collections::HashMap<i64, VecDeque<f64>>,
    current: Option<f64>,
}

impl RelativeVolume {
    const DAYS: usize = 20;

    pub(crate) fn new() -> Self {
        Self {
            by_minute: std::collections::HashMap::new(),
            current: None,
        }
    }

    pub(crate) fn push(&mut self, minute: i64, volume: f64) {
        let window = self.by_minute.entry(minute).or_default();
        self.current = if window.len() >= 5.max(Self::DAYS / 4) {
            let average = window.iter().sum::<f64>() / window.len() as f64;
            (average > 0.0).then(|| volume / average)
        } else {
            None
        };
        window.push_back(volume);
        while window.len() > Self::DAYS {
            window.pop_front();
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.efficiency_ratio`: Kaufman's net travel over gross travel.
///
/// The running gross sum is updated in Python's exact order -- add this step,
/// then drop the one `period` steps back -- so the float it holds is the same.
/// `net_move` is `close[i] - close[i - period]`, the direction the family trades.
pub(crate) struct EfficiencyRatio {
    period: usize,
    closes: VecDeque<f64>,
    steps: VecDeque<f64>,
    running: f64,
    index: usize,
    current: Option<(f64, f64)>,
}

impl EfficiencyRatio {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            closes: VecDeque::with_capacity(period + 2),
            steps: VecDeque::with_capacity(period + 2),
            running: 0.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        let step = self
            .closes
            .back()
            .map_or(0.0, |previous| (close - previous).abs());
        self.closes.push_back(close);
        // Python's `steps[0]` is a literal 0.0 that is added and never removed,
        // so only steps from index 1 are queued: the front is then always
        // `steps[index - period]` when Python subtracts it.
        if self.index > 0 {
            self.steps.push_back(step);
        }
        self.running += step;
        if self.index > self.period {
            let old = self.steps.pop_front().unwrap_or(0.0);
            self.running -= old;
        }
        if self.closes.len() > self.period + 1 {
            self.closes.pop_front();
        }
        self.current = if self.index >= self.period {
            let first = *self.closes.front().unwrap_or(&close);
            let net = (close - first).abs();
            let ratio = if self.running > 0.0 {
                net / self.running
            } else {
                0.0
            };
            Some((ratio, close - first))
        } else {
            None
        };
        self.index += 1;
    }

    /// `(ratio, close[i] - close[i - period])`.
    pub(crate) fn value(&self) -> Option<(f64, f64)> {
        self.current
    }
}

/// `exness_indicators.cci`: typical-price deviation over MEAN ABSOLUTE deviation.
///
/// The mean is `sma`'s running sum, in its order; the deviation is summed over
/// the window afresh each bar, as Python does.
pub(crate) struct Cci {
    period: usize,
    typical: VecDeque<f64>,
    running: f64,
    index: usize,
    current: Option<f64>,
}

impl Cci {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            typical: VecDeque::with_capacity(period + 1),
            running: 0.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, high: f64, low: f64, close: f64) {
        let typical = (high + low + close) / 3.0;
        self.typical.push_back(typical);
        self.running += typical;
        if self.index >= self.period {
            let old = self.typical.pop_front().unwrap_or(0.0);
            self.running -= old;
        }
        self.current = if self.index + 1 >= self.period {
            let mean = self.running / self.period as f64;
            let deviation = self
                .typical
                .iter()
                .fold(0.0, |total, value| total + (value - mean).abs())
                / self.period as f64;
            (deviation > 1e-15).then(|| (typical - mean) / (0.015 * deviation))
        } else {
            None
        };
        self.index += 1;
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.linreg`: `(slope per bar, r squared)` of the least-squares
/// line over the last `period` closes, from Python's three running sums in its
/// exact update order.
pub(crate) struct LinearRegression {
    period: usize,
    values: VecDeque<f64>,
    running: f64,
    weight: f64,
    squares: f64,
    index: usize,
    current: Option<(f64, Option<f64>)>,
}

impl LinearRegression {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            running: 0.0,
            weight: 0.0,
            squares: 0.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        let period = self.period as f64;
        let x_sum = period * (period - 1.0) / 2.0;
        let xx_sum = ((self.period - 1) * self.period * (2 * self.period - 1)) as f64 / 6.0;
        let denominator = period * xx_sum - x_sum * x_sum;
        self.weight += period * value - self.running;
        self.running += value;
        self.squares += value * value;
        self.values.push_back(value);
        if self.index >= self.period {
            let old = self.values.pop_front().unwrap_or(0.0);
            self.running -= old;
            self.squares -= old * old;
        }
        self.current = if denominator != 0.0 && self.index + 1 >= self.period {
            let slope =
                (period * (self.weight - self.running) - x_sum * self.running) / denominator;
            let variance = self.squares - self.running * self.running / period;
            let explained = slope * slope * (xx_sum - x_sum * x_sum / period);
            let fit = (variance > 1e-15).then(|| (explained / variance).clamp(0.0, 1.0));
            Some((slope, fit))
        } else {
            None
        };
        self.index += 1;
    }

    /// `(slope, fit)`; the fit is `None` on a flat window, as in Python.
    pub(crate) fn value(&self) -> Option<(f64, Option<f64>)> {
        self.current
    }
}

/// `exness_indicators.wma`: the linear-weighted mean in O(n), from Python's two
/// running sums in its exact update order. `None` until `period` values.
pub(crate) struct Wma {
    period: usize,
    values: VecDeque<f64>,
    running: f64,
    weight: f64,
    denominator: f64,
    index: usize,
    current: Option<f64>,
}

impl Wma {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            running: 0.0,
            weight: 0.0,
            denominator: (period * (period + 1)) as f64 / 2.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        self.weight += self.period as f64 * value - self.running;
        self.running += value;
        self.values.push_back(value);
        if self.index >= self.period {
            let old = self.values.pop_front().unwrap_or(0.0);
            self.running -= old;
        }
        self.current = (self.index + 1 >= self.period).then(|| self.weight / self.denominator);
        self.index += 1;
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.hma`: `wma(2*wma(p/2) - wma(p), round(sqrt(p)))`, the
/// outer average started at the first bar both inner ones are defined.
pub(crate) struct Hma {
    fast: Wma,
    slow: Wma,
    outer: Wma,
}

impl Hma {
    pub(crate) fn new(period: usize) -> Self {
        let half = (period / 2).max(1);
        let root = (super::family::round_half_even((period as f64).sqrt()) as usize).max(1);
        Self {
            fast: Wma::new(half),
            slow: Wma::new(period),
            outer: Wma::new(root),
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        self.fast.push(value);
        self.slow.push(value);
        if let (Some(a), Some(b)) = (self.fast.value(), self.slow.value()) {
            self.outer.push(2.0 * a - b);
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.outer.value()
    }
}

/// `xma_cross`'s `kind` axis: which member of `ind.MA_KINDS` is crossed.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MaKind {
    Tema,
    Wma,
    Hma,
    /// `ind.sma`, a running sum: the FundedNext book's `gbpusd:xma_cross`.
    Sma,
    /// `ind.lsma`, the least-squares endpoint: `de40:xma_cross`.
    Lsma,
}

/// One average of the zoo, streamed.
pub(crate) enum MovingAverage {
    Tema(Tema),
    Wma(Wma),
    Hma(Hma),
    Sma(Sma),
    Lsma(Lsma),
}

impl MovingAverage {
    pub(crate) fn new(kind: MaKind, period: usize) -> Self {
        match kind {
            MaKind::Tema => Self::Tema(Tema::new(period)),
            MaKind::Wma => Self::Wma(Wma::new(period)),
            MaKind::Hma => Self::Hma(Hma::new(period)),
            MaKind::Sma => Self::Sma(Sma::new(period)),
            MaKind::Lsma => Self::Lsma(Lsma::new(period)),
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        match self {
            Self::Tema(average) => average.push(value),
            Self::Wma(average) => average.push(value),
            Self::Hma(average) => average.push(value),
            Self::Sma(average) => {
                average.push(value);
            }
            Self::Lsma(average) => average.push(value),
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        match self {
            Self::Tema(average) => average.value(),
            Self::Wma(average) => average.value(),
            Self::Hma(average) => average.value(),
            Self::Sma(average) => average.value(),
            Self::Lsma(average) => average.value(),
        }
    }
}

/// `exness_indicators.bipower_jump`: the share of realised variance over the
/// last `period` one-bar log returns that bipower variation calls a JUMP.
///
/// The two running sums are updated in Python's order -- add the new bar, then
/// drop the one `period` back -- so they drift exactly as the Python's do.
pub(crate) struct BipowerJump {
    period: usize,
    previous_close: Option<f64>,
    previous_absolute: f64,
    /// `(square, product)` for the last `period + 1` bars.
    window: VecDeque<(f64, f64)>,
    rv: f64,
    bv: f64,
    index: usize,
    current: Option<f64>,
}

impl BipowerJump {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            previous_close: None,
            previous_absolute: 0.0,
            window: VecDeque::with_capacity(period + 2),
            rv: 0.0,
            bv: 0.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        // `_log_returns`: 0.0 on the first bar and wherever the ratio is undefined.
        let r = match self.previous_close {
            Some(a) if a > 0.0 && close > 0.0 => (close / a).ln(),
            _ => 0.0,
        };
        self.previous_close = Some(close);
        let absolute = r.abs();
        let square = r * r;
        let product = if self.index >= 1 {
            absolute * self.previous_absolute
        } else {
            0.0
        };
        self.previous_absolute = absolute;
        self.rv += square;
        self.bv += product;
        self.window.push_back((square, product));
        if self.index >= self.period {
            let (old_square, old_product) = self.window.pop_front().unwrap_or((0.0, 0.0));
            self.rv -= old_square;
            self.bv -= old_product;
        }
        self.current = if self.period < 10 || self.index < self.period || self.rv <= 1e-24 {
            None
        } else {
            let mu = std::f64::consts::PI / 2.0;
            Some((self.rv - mu * self.bv).max(0.0) / self.rv)
        };
        self.index += 1;
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `ind.hurst_exponent(closes, period)`: the slope of log(sd of k-bar log
/// moves) on log(k) over k = 1, 2, 4, 8, 16, from running sums over a trailing
/// `period`.
///
/// THE PYTHON'S OWN ARITHMETIC, NOT A CLEANER ONE. The k-bar moves are read off
/// ONE prefix sum of one-bar log returns (zero where a ratio is undefined), the
/// window sums are maintained by adding the new move and subtracting the one
/// that left, and the means and covariance are plain left-to-right sums -- so
/// the reading rounds as the sealed cell's did.
pub(crate) struct HurstExponent {
    period: usize,
    prev_close: Option<f64>,
    /// `prefix[j]` for the last `period + 18` values of j, keyed by absolute j.
    prefix: VecDeque<f64>,
    /// Absolute index of `prefix[0]`.
    prefix_start: usize,
    /// `index` of the bar last pushed (Python's loop variable).
    index: Option<usize>,
    sums: [(f64, f64); 5],
    value: Option<f64>,
}

const HURST_LAGS: [usize; 5] = [1, 2, 4, 8, 16];

impl HurstExponent {
    pub(crate) fn new(period: usize) -> Self {
        let mut prefix = VecDeque::with_capacity(period + 20);
        prefix.push_back(0.0);
        Self {
            period,
            prev_close: None,
            prefix,
            prefix_start: 0,
            index: None,
            sums: [(0.0, 0.0); 5],
            value: None,
        }
    }

    fn prefix_at(&self, j: usize) -> f64 {
        self.prefix[j - self.prefix_start]
    }

    /// The k-bar log move ENDING at `index`.
    fn move_at(&self, k: usize, index: usize) -> f64 {
        self.prefix_at(index + 1) - self.prefix_at(index + 1 - k)
    }

    pub(crate) fn push(&mut self, close: f64) {
        let r = match self.prev_close {
            Some(a) if a > 0.0 && close > 0.0 => (close / a).ln(),
            _ => 0.0,
        };
        self.prev_close = Some(close);
        let index = self.index.map_or(0, |i| i + 1);
        self.index = Some(index);
        let last = *self.prefix.back().expect("seeded with prefix[0]");
        self.prefix.push_back(last + r);
        while self.prefix.len() > self.period + 18 {
            self.prefix.pop_front();
            self.prefix_start += 1;
        }
        self.value = None;
        if self.period < 4 * 16 {
            return;
        }
        for (slot, &k) in HURST_LAGS.iter().enumerate() {
            if index + 1 < k {
                continue;
            }
            let value = self.move_at(k, index);
            self.sums[slot].0 += value;
            self.sums[slot].1 += value * value;
            if index >= self.period {
                let gone = index - self.period;
                if gone + 1 >= k {
                    let old = self.move_at(k, gone);
                    self.sums[slot].0 -= old;
                    self.sums[slot].1 -= old * old;
                }
            }
        }
        if index < self.period + 16 {
            return;
        }
        let period = self.period as f64;
        let mut ys = [0.0; 5];
        for (slot, y) in ys.iter_mut().enumerate() {
            let (total, square) = self.sums[slot];
            let variance = square / period - (total / period) * (total / period);
            if variance <= 1e-24 {
                return;
            }
            *y = 0.5 * variance.ln();
        }
        let xs = HURST_LAGS.map(|k| (k as f64).ln());
        let mut sum_x = 0.0;
        for x in xs {
            sum_x += x;
        }
        let mean_x = sum_x / 5.0;
        let mut denominator = 0.0;
        for x in xs {
            denominator += (x - mean_x) * (x - mean_x);
        }
        if denominator <= 0.0 {
            return;
        }
        let mut sum_y = 0.0;
        for y in ys {
            sum_y += y;
        }
        let mean_y = sum_y / 5.0;
        let mut covariance = 0.0;
        for (x, y) in xs.iter().zip(ys) {
            covariance += (x - mean_x) * (y - mean_y);
        }
        self.value = Some(covariance / denominator);
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.value
    }
}

/// `ind.parabolic_sar(bars, 0.02, 0.2)[1]`: the side Wilder's SAR flipped to
/// on this bar, 0 otherwise. The SAR is clamped to the PREVIOUS TWO bars'
/// extremes (one at the second bar), and a flip resets it to the extreme.
pub(crate) struct ParabolicSar {
    /// Bars seen, Python's `index + 1`.
    seen: usize,
    first: Option<(f64, f64, f64)>,
    /// `(high, low)` of the previous two bars, newest first.
    back: [(f64, f64); 2],
    state: i8,
    sar: f64,
    extreme: f64,
    speed: f64,
    flip: i8,
}

const SAR_STEP: f64 = 0.02;
const SAR_CAP: f64 = 0.2;

impl ParabolicSar {
    pub(crate) fn new() -> Self {
        Self {
            seen: 0,
            first: None,
            back: [(f64::NAN, f64::NAN); 2],
            state: 0,
            sar: 0.0,
            extreme: 0.0,
            speed: SAR_STEP,
            flip: 0,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
        self.flip = 0;
        let index = self.seen;
        self.seen += 1;
        if index == 0 {
            self.first = Some((candle.high, candle.low, candle.close));
            self.back = [(candle.high, candle.low), (f64::NAN, f64::NAN)];
            return;
        }
        if index == 1 {
            let (h0, l0, c0) = self.first.expect("first bar recorded");
            self.state = if candle.close >= c0 { 1 } else { -1 };
            self.sar = if self.state == 1 { l0 } else { h0 };
            self.extreme = if self.state == 1 { h0 } else { l0 };
            self.speed = SAR_STEP;
        }
        self.sar += self.speed * (self.extreme - self.sar);
        let (h1, l1) = self.back[0];
        let (floor_low, ceiling_high) = if index > 1 {
            let (h2, l2) = self.back[1];
            (l1.min(l2), h1.max(h2))
        } else {
            (l1, h1)
        };
        if self.state == 1 {
            self.sar = self.sar.min(floor_low);
            if candle.low < self.sar {
                self.state = -1;
                self.sar = self.extreme;
                self.extreme = candle.low;
                self.speed = SAR_STEP;
                self.flip = -1;
            } else if candle.high > self.extreme {
                self.extreme = candle.high;
                self.speed = SAR_CAP.min(self.speed + SAR_STEP);
            }
        } else {
            self.sar = self.sar.max(ceiling_high);
            if candle.high > self.sar {
                self.state = 1;
                self.sar = self.extreme;
                self.extreme = candle.high;
                self.speed = SAR_STEP;
                self.flip = 1;
            } else if candle.low < self.extreme {
                self.extreme = candle.low;
                self.speed = SAR_CAP.min(self.speed + SAR_STEP);
            }
        }
        self.back = [(candle.high, candle.low), self.back[0]];
    }

    pub(crate) fn flip(&self) -> i8 {
        self.flip
    }
}

/// `exness_fastbar`'s Bollinger band at `period` closes: `numpy.mean` and
/// `numpy.std` (population, two-pass) over a sliding window, both with numpy's
/// pairwise summation, so the band rounds exactly as the sealed cell's did.
pub(crate) struct BollingerBand {
    period: usize,
    k: f64,
    window: VecDeque<f64>,
    /// `(close, upper, lower)` at the previous bar, then at this one.
    previous: Option<(f64, f64, f64)>,
    current: Option<(f64, f64, f64)>,
}

impl BollingerBand {
    pub(crate) fn new(period: usize, k: f64) -> Self {
        Self {
            period,
            k,
            window: VecDeque::with_capacity(period + 1),
            previous: None,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        self.window.push_back(close);
        if self.window.len() > self.period {
            self.window.pop_front();
        }
        self.previous = self.current;
        let (upper, lower) = if self.window.len() == self.period {
            let values: Vec<f64> = self.window.iter().copied().collect();
            let n = self.period as f64;
            let mean = super::tt::numpy_sum(&values) / n;
            let squares: Vec<f64> = values.iter().map(|x| (x - mean) * (x - mean)).collect();
            let sd = (super::tt::numpy_sum(&squares) / n).sqrt();
            (mean + self.k * sd, mean + (-self.k) * sd)
        } else {
            (f64::NAN, f64::NAN)
        };
        self.current = Some((close, upper, lower));
    }

    /// `_band_break`: +1 on the close crossing above the upper band, -1 below
    /// the lower. `_band_reenter` when `reenter`.
    pub(crate) fn event(&self, reenter: bool) -> i8 {
        let (Some((c0, u0, l0)), Some((c1, u1, l1))) = (self.previous, self.current) else {
            return 0;
        };
        if reenter {
            if c0 < l0 && c1 >= l1 {
                1
            } else if c0 > u0 && c1 <= u1 {
                -1
            } else {
                0
            }
        } else if c0 <= u0 && c1 > u1 {
            1
        } else if c0 >= l0 && c1 < l1 {
            -1
        } else {
            0
        }
    }
}

// -------------------------------------------------------------------------- //
// the FundedNext book's readings (`fundednext/oct-21-2026`)
// -------------------------------------------------------------------------- //

/// `exness_indicators.sma`: ONE running sum, added to and then subtracted from,
/// exactly as the Python walks it -- so it drifts the way the sealed series did
/// rather than being re-summed per window.
pub(crate) struct Sma {
    period: usize,
    values: VecDeque<f64>,
    running: f64,
}

impl Sma {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            running: 0.0,
        }
    }

    /// Folds `value` in and returns the reading AT it.
    pub(crate) fn push(&mut self, value: f64) -> Option<f64> {
        self.running += value;
        self.values.push_back(value);
        if self.values.len() > self.period
            && let Some(old) = self.values.pop_front()
        {
            self.running -= old;
        }
        self.value()
    }

    pub(crate) fn value(&self) -> Option<f64> {
        (self.values.len() >= self.period).then(|| self.running / self.period as f64)
    }

    /// Replaces the running sum with the one Python held at this point, so
    /// the rounding drift it accumulated before this engine's first bar is
    /// carried forward bit for bit. Only meaningful with a full window.
    pub(crate) fn seed(&mut self, running: f64) {
        if self.values.len() >= self.period {
            self.running = running;
        }
    }
}

/// `exness_indicators.lsma`: the endpoint of the least-squares line through the
/// last `period` values, recovered from the same two running sums `wma` keeps.
pub(crate) struct Lsma {
    period: usize,
    values: VecDeque<f64>,
    running: f64,
    weight: f64,
    x_sum: f64,
    denominator: f64,
    current: Option<f64>,
}

impl Lsma {
    pub(crate) fn new(period: usize) -> Self {
        let p = period as f64;
        let x_sum = (period * period.saturating_sub(1)) as f64 / 2.0;
        let xx_sum = (period.saturating_sub(1) * period * (2 * period).saturating_sub(1)) as f64 / 6.0;
        Self {
            period,
            values: VecDeque::with_capacity(period + 1),
            running: 0.0,
            weight: 0.0,
            x_sum,
            denominator: p * xx_sum - x_sum * x_sum,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, value: f64) {
        let p = self.period as f64;
        self.weight += p * value - self.running;
        self.running += value;
        self.values.push_back(value);
        if self.values.len() > self.period
            && let Some(old) = self.values.pop_front()
        {
            self.running -= old;
        }
        // `if denominator == 0: return out` -- every reading stays `None`.
        self.current = (self.values.len() >= self.period && self.denominator != 0.0).then(|| {
            let slope =
                (p * (self.weight - self.running) - self.x_sum * self.running) / self.denominator;
            self.running / p + slope * (p - 1.0) / 2.0
        });
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.rolling_extreme_inclusive`: the extreme of the `period`
/// values ENDING here, this one included.
pub(crate) struct RollingExtremeInclusive {
    period: usize,
    maximum: bool,
    queue: VecDeque<(usize, f64)>,
    index: usize,
}

impl RollingExtremeInclusive {
    pub(crate) fn new(period: usize, maximum: bool) -> Self {
        Self {
            period,
            maximum,
            queue: VecDeque::new(),
            index: 0,
        }
    }

    pub(crate) fn push(&mut self, value: f64) -> Option<f64> {
        let index = self.index;
        self.index += 1;
        while self
            .queue
            .front()
            .is_some_and(|(at, _)| *at as i64 <= index as i64 - self.period as i64)
        {
            self.queue.pop_front();
        }
        while self.queue.back().is_some_and(|(_, held)| {
            if self.maximum {
                *held <= value
            } else {
                *held >= value
            }
        }) {
            self.queue.pop_back();
        }
        self.queue.push_back((index, value));
        (index + 1 >= self.period).then(|| self.queue.front().expect("just pushed").1)
    }
}

/// `exness_indicators.rolling_skew`: the skewness of the last `period` one-bar
/// log returns, from three running power sums. The first bar's return is 0.0
/// and IS a member of the sums, as in the Python.
pub(crate) struct RollingSkew {
    period: usize,
    previous: Option<f64>,
    returns: VecDeque<f64>,
    s1: f64,
    s2: f64,
    s3: f64,
    index: usize,
    current: Option<f64>,
}

impl RollingSkew {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            previous: None,
            returns: VecDeque::with_capacity(period + 1),
            s1: 0.0,
            s2: 0.0,
            s3: 0.0,
            index: 0,
            current: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        let r = match self.previous {
            Some(a) if a > 0.0 && close > 0.0 => (close / a).ln(),
            _ => 0.0,
        };
        self.previous = Some(close);
        self.s1 += r;
        self.s2 += r * r;
        self.s3 += r * r * r;
        self.returns.push_back(r);
        let index = self.index;
        self.index += 1;
        self.current = None;
        if index >= self.period {
            let old = self.returns.pop_front().unwrap_or(0.0);
            self.s1 -= old;
            self.s2 -= old * old;
            self.s3 -= old * old * old;
            let p = self.period as f64;
            let mean = self.s1 / p;
            let variance = self.s2 / p - mean * mean;
            if variance > 1e-18 {
                let third = self.s3 / p - 3.0 * mean * (self.s2 / p) + 2.0 * mean.powf(3.0);
                self.current = Some(third / variance.powf(1.5));
            }
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        self.current
    }
}

/// `exness_indicators.stochastic`: `(%K, %D)` over an INCLUSIVE `period`-bar
/// range. A flat window leaves a `None` %K; the smoothing input carries the
/// last reading across it (50.0 before any), from the first defined %K on.
pub(crate) struct RangeStochastic {
    /// `(candle ts, running-sum bits)`: Python's %D sum at day starts, applied
    /// before the candle is folded in. Empty for no seeding.
    seeds: &'static [(i64, u64)],
    highs: RollingExtremeInclusive,
    lows: RollingExtremeInclusive,
    started: bool,
    carried: f64,
    smooth: Sma,
    current: (Option<f64>, Option<f64>),
    previous: (Option<f64>, Option<f64>),
}

impl RangeStochastic {
    pub(crate) fn new(period: usize, smooth: usize, seeds: &'static [(i64, u64)]) -> Self {
        Self {
            seeds,
            highs: RollingExtremeInclusive::new(period, true),
            lows: RollingExtremeInclusive::new(period, false),
            started: false,
            carried: 50.0,
            smooth: Sma::new(smooth),
            current: (None, None),
            previous: (None, None),
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
        let top = self.highs.push(candle.high);
        let bottom = self.lows.push(candle.low);
        let fast = match (top, bottom) {
            (Some(top), Some(bottom)) if top - bottom > 0.0 => {
                Some(100.0 * (candle.close - bottom) / (top - bottom))
            }
            _ => None,
        };
        self.started |= fast.is_some();
        let slow = if self.started {
            if let Some(value) = fast {
                self.carried = value;
            }
            if let Ok(at) = self.seeds.binary_search_by_key(&candle.ts, |seed| seed.0) {
                self.smooth.seed(f64::from_bits(self.seeds[at].1));
            }
            self.smooth.push(self.carried)
        } else {
            None
        };
        self.previous = self.current;
        self.current = (fast, slow);
    }

    /// `((fast, slow) now, (fast, slow) one bar back)`, all four present.
    pub(crate) fn readings(&self) -> Option<((f64, f64), (f64, f64))> {
        Some((
            (self.current.0?, self.current.1?),
            (self.previous.0?, self.previous.1?),
        ))
    }
}

/// `exness_indicators.supertrend`, on the engine's own ATR (`ctx["atr"]`): an
/// envelope that only ratchets towards price, and the side it last flipped to.
/// A bar with no ATR reading has no direction and moves nothing.
pub(crate) struct Supertrend {
    multiple: f64,
    state: i32,
    bands: Option<(f64, f64)>,
    previous_close: Option<f64>,
    current: Option<i32>,
    previous: Option<i32>,
}

impl Supertrend {
    pub(crate) fn new(multiple: f64) -> Self {
        Self {
            multiple,
            state: 1,
            bands: None,
            previous_close: None,
            current: None,
            previous: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle, atr: Option<f64>) {
        self.previous = self.current;
        let previous_close = self.previous_close.replace(candle.close);
        self.current = None;
        let Some(width) = atr.filter(|width| *width != 0.0) else {
            return;
        };
        let middle = (candle.high + candle.low) / 2.0;
        let top = middle + self.multiple * width;
        let bottom = middle - self.multiple * width;
        let Some((upper, lower)) = self.bands else {
            self.bands = Some((top, bottom));
            self.current = Some(self.state);
            return;
        };
        let previous = previous_close.unwrap_or(candle.close);
        let upper = if top < upper || previous > upper {
            top
        } else {
            upper
        };
        let lower = if bottom > lower || previous < lower {
            bottom
        } else {
            lower
        };
        self.bands = Some((upper, lower));
        if self.state == 1 && candle.close < lower {
            self.state = -1;
        } else if self.state == -1 && candle.close > upper {
            self.state = 1;
        }
        self.current = Some(self.state);
    }

    /// `(direction now, direction one bar back)`.
    pub(crate) fn readings(&self) -> Option<(i32, i32)> {
        Some((self.current?, self.previous?))
    }

    /// The direction at this bar alone, for a gate.
    pub(crate) fn current(&self) -> Option<i32> {
        self.current
    }

    /// The direction one bar back, if any.
    pub(crate) fn previous_reading(&self) -> Option<i32> {
        self.previous
    }
}

/// CPython's `sum()` over floats (3.12+): Neumaier-compensated, from 0.
pub(crate) fn python_fsum_like(values: impl Iterator<Item = f64>) -> f64 {
    let mut total = 0.0_f64;
    let mut compensation = 0.0_f64;
    for x in values {
        let t = total + x;
        if total.abs() >= x.abs() {
            compensation += (total - t) + x;
        } else {
            compensation += (x - t) + total;
        }
        total = t;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total
}

/// `cfd_families.session_value_areas`: the PRIOR session's
/// `(value_high, value_low, point_of_control)`, from a 50-bin volume profile
/// of its candles with each candle's volume spread evenly over the bins its
/// range touches (`volume_profile.profile`), 70% of the volume around the POC.
pub(crate) struct SessionValueArea {
    day: Option<i64>,
    /// `(high, low, volume)` of today's candles so far.
    candles: Vec<(f64, f64, f64)>,
    /// Today's reading, from yesterday's candles.
    pub(crate) levels: Option<(f64, f64, f64)>,
}

/// `PROFILE_BINS` and `VALUE_AREA_SHARE`.
const PROFILE_BINS: f64 = 50.0;
const VALUE_AREA_SHARE: f64 = 0.70;

impl SessionValueArea {
    pub(crate) fn new() -> Self {
        Self {
            day: None,
            candles: Vec::new(),
            levels: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle, day: i64) {
        if self.day != Some(day) {
            self.levels = if self.day.is_some() {
                Self::profile_levels(&self.candles)
            } else {
                None
            };
            self.day = Some(day);
            self.candles.clear();
        }
        self.candles.push((candle.high, candle.low, candle.volume));
    }

    fn profile_levels(session: &[(f64, f64, f64)]) -> Option<(f64, f64, f64)> {
        let top = session
            .iter()
            .map(|c| c.0)
            .fold(f64::NEG_INFINITY, f64::max);
        let bottom = session.iter().map(|c| c.1).fold(f64::INFINITY, f64::min);
        if top <= bottom {
            return None;
        }
        let size = (top - bottom) / PROFILE_BINS;
        if size <= 0.0 {
            return None;
        }
        // `{bin: volume}` in INSERTION order, which is the order `sum` reads.
        let mut keys: Vec<i64> = Vec::new();
        let mut volumes: Vec<f64> = Vec::new();
        let mut slot: std::collections::HashMap<i64, usize> = std::collections::HashMap::new();
        for &(high, low, volume) in session {
            if volume <= 0.0 {
                continue;
            }
            let first = (low / size).floor() as i64;
            let last = (high / size).floor() as i64;
            let share = volume / (last - first + 1) as f64;
            for index in first..=last {
                match slot.get(&index) {
                    Some(&at) => volumes[at] += share,
                    None => {
                        slot.insert(index, keys.len());
                        keys.push(index);
                        volumes.push(0.0 + share);
                    }
                }
            }
        }
        if keys.is_empty() {
            return None;
        }
        let bin = |key: i64| volumes[slot[&key]];
        let mut ordered = keys.clone();
        ordered.sort_unstable();
        // `max(sorted(bins), key=...)`: the FIRST of equal maxima.
        let mut poc_at = 0usize;
        for (at, key) in ordered.iter().enumerate() {
            if bin(*key) > bin(ordered[poc_at]) {
                poc_at = at;
            }
        }
        let poc = ordered[poc_at] as f64 * size;
        let total = python_fsum_like(volumes.iter().copied());
        if total <= 0.0 {
            return None;
        }
        let (mut low, mut high) = (poc_at, poc_at);
        let mut covered = bin(ordered[poc_at]);
        let target = total * VALUE_AREA_SHARE;
        let last = ordered.len() - 1;
        while covered < target && (low > 0 || high < last) {
            let below = if low > 0 { bin(ordered[low - 1]) } else { -1.0 };
            let above = if high < last { bin(ordered[high + 1]) } else { -1.0 };
            if above >= below {
                high += 1;
                covered += bin(ordered[high]);
            } else {
                low -= 1;
                covered += bin(ordered[low]);
            }
        }
        let area_low = ordered[low] as f64 * size;
        let area_high = (ordered[high] + 1) as f64 * size;
        Some((area_high, area_low, poc))
    }
}

/// `exness_indicators.swing_pivots`: the last two CONFIRMED fractal pivots of
/// each kind. A pivot at bar `c` is the extreme of `c - wing ..= c + wing`
/// (ties allowed) and is recorded at `c + wing`, never earlier.
pub(crate) struct SwingPivots {
    wing: usize,
    highs: VecDeque<f64>,
    lows: VecDeque<f64>,
    pub(crate) high: Option<f64>,
    pub(crate) prior_high: Option<f64>,
    pub(crate) low: Option<f64>,
    pub(crate) prior_low: Option<f64>,
}

impl SwingPivots {
    pub(crate) fn new(wing: usize) -> Self {
        Self {
            wing,
            highs: VecDeque::new(),
            lows: VecDeque::new(),
            high: None,
            prior_high: None,
            low: None,
            prior_low: None,
        }
    }

    /// Inclusive: the readings after this push are Python's at this index.
    pub(crate) fn push(&mut self, candle: &Candle) {
        let width = 2 * self.wing + 1;
        self.highs.push_back(candle.high);
        self.lows.push_back(candle.low);
        if self.highs.len() > width {
            self.highs.pop_front();
            self.lows.pop_front();
        }
        if self.highs.len() < width {
            return;
        }
        let top = self.highs[self.wing];
        let bottom = self.lows[self.wing];
        if self.highs.iter().all(|high| *high <= top) {
            self.prior_high = self.high;
            self.high = Some(top);
        }
        if self.lows.iter().all(|low| *low >= bottom) {
            self.prior_low = self.low;
            self.low = Some(bottom);
        }
    }
}

/// `exness_indicators.macd`: raw EMAs seeded at the first close, line, trigger
/// and histogram, all `None` for the first `slow + signal` bars.
pub(crate) struct MacdHistogram {
    fast: f64,
    slow: f64,
    signal: f64,
    blank: usize,
    quick: Option<f64>,
    slack: Option<f64>,
    trigger: Option<f64>,
    seen: usize,
    pub(crate) line: Option<f64>,
    pub(crate) histogram: Option<f64>,
    pub(crate) previous: Option<f64>,
    pub(crate) earlier: Option<f64>,
}

impl MacdHistogram {
    pub(crate) fn new(fast: usize, slow: usize, signal: usize) -> Self {
        let alpha = |n: usize| 2.0 / (n as f64 + 1.0);
        Self {
            fast: alpha(fast),
            slow: alpha(slow),
            signal: alpha(signal),
            blank: slow + signal,
            quick: None,
            slack: None,
            trigger: None,
            seen: 0,
            line: None,
            histogram: None,
            previous: None,
            earlier: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        let step = |level: Option<f64>, value: f64, alpha: f64| match level {
            None => value,
            Some(level) => level + alpha * (value - level),
        };
        let quick = step(self.quick, close, self.fast);
        let slack = step(self.slack, close, self.slow);
        self.quick = Some(quick);
        self.slack = Some(slack);
        let line = quick - slack;
        let trigger = step(self.trigger, line, self.signal);
        self.trigger = Some(trigger);
        self.seen += 1;
        self.earlier = self.previous;
        self.previous = self.histogram;
        if self.seen <= self.blank {
            self.line = None;
            self.histogram = None;
        } else {
            self.line = Some(line);
            self.histogram = Some(line - trigger);
        }
    }
}

/// `exness_indicators.variance_estimators`: Parkinson, Garman-Klass and
/// close-to-close variances over the `period` bars ending here.
pub(crate) struct VarianceEstimators {
    period: usize,
    terms: VecDeque<(f64, f64, f64)>,
    sums: (f64, f64, f64),
    previous_close: Option<f64>,
    index: usize,
    pub(crate) parkinson: Option<f64>,
    pub(crate) garman_klass: Option<f64>,
    pub(crate) close_to_close: Option<f64>,
}

impl VarianceEstimators {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            terms: VecDeque::new(),
            sums: (0.0, 0.0, 0.0),
            previous_close: None,
            index: 0,
            parkinson: None,
            garman_klass: None,
            close_to_close: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
        let (high, low, open, close) = (candle.high, candle.low, candle.open, candle.close);
        let mut range = 0.0;
        if high > 0.0 && low > 0.0 && high >= low {
            let span = (high / low).ln();
            range = span * span;
        }
        let mut body = 0.0;
        if open > 0.0 && close > 0.0 {
            let b = (close / open).ln();
            body = b * b;
        }
        let mut move_term = 0.0;
        if let Some(previous) = self.previous_close
            && previous > 0.0
            && close > 0.0
        {
            let m = (close / previous).ln();
            move_term = m * m;
        }
        self.previous_close = Some(close);
        self.sums.0 += range;
        self.sums.1 += body;
        self.sums.2 += move_term;
        self.terms.push_back((range, body, move_term));
        if self.index >= self.period {
            let (r, b, c) = self.terms.pop_front().expect("window is full");
            self.sums.0 -= r;
            self.sums.1 -= b;
            self.sums.2 -= c;
        }
        let index = self.index;
        self.index += 1;
        if self.period < 5 || index < self.period {
            return;
        }
        let n = self.period as f64;
        let log4 = 4.0 * 2f64.ln();
        let body_scale = 2.0 * 2f64.ln() - 1.0;
        self.parkinson = Some(self.sums.0 / n / log4);
        self.garman_klass = Some((0.5 * self.sums.0 / n - body_scale * self.sums.1 / n).max(0.0));
        self.close_to_close = Some(self.sums.2 / n);
    }
}

/// `exness_indicators.quantile_channel` on closes: the `share` and
/// `1 - share` quantiles of the `period` closes ending HERE, inclusive.
pub(crate) struct QuantileChannel {
    period: usize,
    share: f64,
    values: VecDeque<f64>,
    pub(crate) upper: Option<f64>,
    pub(crate) lower: Option<f64>,
}

impl QuantileChannel {
    pub(crate) fn new(period: usize, share: f64) -> Self {
        Self {
            period,
            share,
            values: VecDeque::new(),
            upper: None,
            lower: None,
        }
    }

    pub(crate) fn push(&mut self, close: f64) {
        self.values.push_back(close);
        if self.values.len() > self.period {
            self.values.pop_front();
        }
        self.upper = None;
        self.lower = None;
        if self.period < 5 || !(0.5 < self.share && self.share < 1.0) {
            return;
        }
        if self.values.len() < self.period || self.values.iter().any(|v| v.is_nan()) {
            return;
        }
        let mut window: Vec<f64> = self.values.iter().copied().collect();
        window.sort_by(|a, b| a.total_cmp(b));
        let pick = |share: f64| {
            let position = share * (window.len() - 1) as f64;
            let low = position.floor() as usize;
            let high = (low + 1).min(window.len() - 1);
            let weight = position - low as f64;
            window[low] * (1.0 - weight) + window[high] * weight
        };
        self.upper = Some(pick(self.share));
        self.lower = Some(pick(1.0 - self.share));
    }
}

/// `cfd_families._vol_of_vol_series`: the coefficient of variation of the
/// short/long volatility ratio over the trailing `period` bars, summed oldest
/// first exactly as the numpy offsets add it.
pub(crate) struct VolOfVol {
    period: usize,
    ratios: VecDeque<Option<f64>>,
}

impl VolOfVol {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            ratios: VecDeque::new(),
        }
    }

    pub(crate) fn push(&mut self, short: Option<f64>, long: Option<f64>) {
        let ratio = match (short, long) {
            (Some(s), Some(l)) if l > 0.0 && !s.is_nan() => Some(s / l),
            _ => None,
        };
        self.ratios.push_back(ratio);
        if self.ratios.len() > self.period {
            self.ratios.pop_front();
        }
    }

    pub(crate) fn value(&self) -> Option<f64> {
        if self.period < 2 || self.ratios.len() < self.period {
            return None;
        }
        let mut total = 0.0;
        let mut squares = 0.0;
        for ratio in &self.ratios {
            let value = (*ratio)?;
            total += value;
            squares += value * value;
        }
        let count = self.period as f64;
        let mean = total / count;
        let variance = squares / count - mean * mean;
        (variance > 1e-18 && mean > 0.0).then(|| variance.sqrt() / mean)
    }
}

/// `exness_indicators.amihud`: the mean |log return| per unit volume (x 1e6)
/// over the `period` candles ending here, once at least `max(5, period / 2)`
/// of them have a reading. Summed and un-summed in Python's order.
pub(crate) struct Amihud {
    period: usize,
    ratios: VecDeque<Option<f64>>,
    total: f64,
    live: usize,
    previous: Option<f64>,
    pub(crate) value: Option<f64>,
}

impl Amihud {
    pub(crate) fn new(period: usize) -> Self {
        Self {
            period,
            ratios: VecDeque::new(),
            total: 0.0,
            live: 0,
            previous: None,
            value: None,
        }
    }

    pub(crate) fn push(&mut self, candle: &Candle) {
        let ratio = match self.previous {
            Some(previous) if previous > 0.0 && candle.close > 0.0 && candle.volume > 0.0 => {
                Some((candle.close / previous).ln().abs() / candle.volume * 1e6)
            }
            _ => None,
        };
        self.previous = Some(candle.close);
        self.value = None;
        if self.period < 5 {
            return;
        }
        if let Some(ratio) = ratio {
            self.total += ratio;
            self.live += 1;
        }
        self.ratios.push_back(ratio);
        if self.ratios.len() > self.period
            && let Some(Some(gone)) = self.ratios.pop_front()
        {
            self.total -= gone;
            self.live -= 1;
        }
        if self.live >= (self.period / 2).max(5) {
            self.value = Some(self.total / self.live as f64);
        }
    }
}
