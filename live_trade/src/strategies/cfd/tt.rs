//! The TikTok-creator families (`cfd_tt_families`), streamed.
//!
//! A PORT OF `tt_features` AND THE CREATOR MODULES, NOT A REINTERPRETATION.
//! Each Python family computes `{bar: (side, stop_distance, target)}` over the
//! whole session-bar array; every one of them is CAUSAL (an entry at `i` reads
//! bars `0..=i` only), so walking the bars one at a time and asking "is there an
//! event at this bar" gives the same answers. `TtState::push` is that walk: it
//! is called on EVERY in-session candle, holding or flat, because the Python
//! state machines (`swing_sweep_mss`, the MACD cross memory, the HTF candle
//! grouping) advance on every bar whatever the engine is doing.
//!
//! THE ENTRY CUTOFF IS NOT APPLIED HERE. `cfd_tt_families` strips
//! `last_entry_minute` before calling `compute` and applies it in the signal
//! wrapper, so the event SET is independent of it; `FamilyEngine::signal`
//! applies it the same way.
//!
//! ONE KNOWN LOOK-AHEAD IN THE PYTHON, AND WHY IT CANNOT MATTER. `F.htf` marks
//! a higher-timeframe candle closed when the NEXT bar starts a new group, which
//! at a day's end means knowing no more bars are coming today. Here a partial
//! day-end group is closed when the next day's first bar arrives, before that
//! bar is evaluated. The only difference is that an event can no longer fire ON
//! the day's last bar -- and such an event could never be filled, because its
//! fill bar would be tomorrow's and `backtest` drops a pending entry that
//! crosses the day. The HTF values every later bar reads are identical.

use std::collections::VecDeque;

use super::family::{Family, TtEngulf, TtHtfClose, TtStop};
use super::{Candle, Side};

/// `F.ema`: seeded at the first value, NaN for the first `n` bars.
#[derive(Clone, Copy)]
struct SeededEma {
    alpha: f64,
    level: f64,
    seen: usize,
    blank: usize,
}

impl SeededEma {
    fn new(n: usize, blank: bool) -> Self {
        Self {
            alpha: 2.0 / (n as f64 + 1.0),
            level: f64::NAN,
            seen: 0,
            blank: if blank { n } else { 0 },
        }
    }

    /// `_ema_arr` skips non-finite inputs and starts at the first finite one.
    fn push(&mut self, value: f64) {
        if !value.is_finite() {
            return;
        }
        self.level = if self.level.is_finite() {
            self.level + self.alpha * (value - self.level)
        } else {
            value
        };
        self.seen += 1;
    }

    fn value(&self) -> f64 {
        if self.seen <= self.blank {
            f64::NAN
        } else {
            self.level
        }
    }
}

/// `F.atr(14)`: Wilder, seeded with the mean of the first `n` true ranges; the
/// first bar's "previous close" is its own close.
struct WilderAtr {
    n: usize,
    prev_close: Option<f64>,
    seed: f64,
    seen: usize,
    level: f64,
}

impl WilderAtr {
    fn new(n: usize) -> Self {
        Self {
            n,
            prev_close: None,
            seed: 0.0,
            seen: 0,
            level: f64::NAN,
        }
    }

    fn push(&mut self, c: &Candle) {
        let prev = self.prev_close.unwrap_or(c.close);
        let tr = (c.high - c.low)
            .max((c.high - prev).abs())
            .max((c.low - prev).abs());
        self.prev_close = Some(c.close);
        self.seen += 1;
        if self.seen < self.n {
            self.seed += tr;
        } else if self.seen == self.n {
            self.seed += tr;
            self.level = self.seed / self.n as f64;
        } else {
            self.level += (1.0 / self.n as f64) * (tr - self.level);
        }
    }

    fn value(&self) -> f64 {
        self.level
    }
}

/// `F.pivots(k)`: the most recent CONFIRMED swing high and low. A pivot high at
/// `j` is strictly above the `k` bars after it and at least the `k` before;
/// it becomes known at `j + k`.
struct Pivots {
    k: usize,
    highs: VecDeque<f64>,
    lows: VecDeque<f64>,
    last_high: f64,
    last_low: f64,
}

impl Pivots {
    fn new(k: usize) -> Self {
        Self {
            k,
            highs: VecDeque::with_capacity(2 * k + 2),
            lows: VecDeque::with_capacity(2 * k + 2),
            last_high: f64::NAN,
            last_low: f64::NAN,
        }
    }

    fn push(&mut self, c: &Candle) {
        self.highs.push_back(c.high);
        self.lows.push_back(c.low);
        let width = 2 * self.k + 1;
        if self.highs.len() > width {
            self.highs.pop_front();
            self.lows.pop_front();
        }
        if self.highs.len() < width {
            return;
        }
        let k = self.k;
        let centre_h = self.highs[k];
        let centre_l = self.lows[k];
        let right_h = (k + 1..width)
            .map(|j| self.highs[j])
            .fold(f64::NEG_INFINITY, f64::max);
        let right_l = (k + 1..width)
            .map(|j| self.lows[j])
            .fold(f64::INFINITY, f64::min);
        let left_h = (0..k)
            .map(|j| self.highs[j])
            .fold(f64::NEG_INFINITY, f64::max);
        let left_l = (0..k).map(|j| self.lows[j]).fold(f64::INFINITY, f64::min);
        if centre_h > right_h && centre_h >= left_h {
            self.last_high = centre_h;
        }
        if centre_l < right_l && centre_l <= left_l {
            self.last_low = centre_l;
        }
    }
}

/// `F.stop_dist`: distance to a structural level on the losing side, floored
/// at 0.2 ATR; NaN when the ATR or the level is missing.
fn stop_dist(side: f64, price: f64, level: f64, atr: f64) -> f64 {
    if !(atr > 0.0) || !level.is_finite() {
        return f64::NAN;
    }
    (side * (price - level)).max(0.2 * atr)
}

/// `F.pivots(k)`'s confirmation test alone: whether the bar `k` back is a swing
/// high and/or low, known now. `Pivots` keeps only prices; `rsi_divergence`
/// needs the pivot's bar as well.
struct IndexedPivots {
    k: usize,
    highs: VecDeque<f64>,
    lows: VecDeque<f64>,
}

impl IndexedPivots {
    fn new(k: usize) -> Self {
        Self {
            k,
            highs: VecDeque::with_capacity(2 * k + 2),
            lows: VecDeque::with_capacity(2 * k + 2),
        }
    }

    /// `(is a pivot high, is a pivot low)` for the bar `k` before this one.
    fn push(&mut self, c: &Candle) -> (bool, bool) {
        self.highs.push_back(c.high);
        self.lows.push_back(c.low);
        let width = 2 * self.k + 1;
        if self.highs.len() > width {
            self.highs.pop_front();
            self.lows.pop_front();
        }
        if self.highs.len() < width {
            return (false, false);
        }
        let k = self.k;
        let (ch, cl) = (self.highs[k], self.lows[k]);
        let rh = (k + 1..width).map(|j| self.highs[j]).fold(f64::NEG_INFINITY, f64::max);
        let rl = (k + 1..width).map(|j| self.lows[j]).fold(f64::INFINITY, f64::min);
        let lh = (0..k).map(|j| self.highs[j]).fold(f64::NEG_INFINITY, f64::max);
        let ll = (0..k).map(|j| self.lows[j]).fold(f64::INFINITY, f64::min);
        (ch > rh && ch >= lh, cl < rl && cl <= ll)
    }
}

/// `_emit`'s acceptance test for a distance with no target.
fn valid(distance: f64) -> bool {
    distance > 0.0 && distance.is_finite()
}

/// `_emit`'s acceptance test for a distance WITH a level target: both must be
/// positive and finite, so the firing set does not depend on the exit axis.
fn valid_target(distance: f64, target: f64) -> bool {
    valid(distance) && target > 0.0 && target.is_finite()
}

/// `F.tgt`: distance to a target level in the trade's direction, or NaN.
fn tgt(side: f64, price: f64, level: f64) -> f64 {
    if !level.is_finite() {
        return f64::NAN;
    }
    let d = side * (level - price);
    if d > 0.0 { d } else { f64::NAN }
}

fn side_of(sign: f64) -> Side {
    if sign > 0.0 { Side::Long } else { Side::Short }
}

/// A swing-sweep setup in progress (`swing_sweep_mss`'s `st[side]`).
#[derive(Clone, Copy)]
struct Sweep {
    t: i64,
    ext: f64,
    mss: Option<(i64, f64)>,
}

/// A rolling max (or min) over the `n` values ending here; NaN until full
/// (`F.rolling_max` / `F.rolling_min`).
struct RollingWindow {
    n: usize,
    maximum: bool,
    values: VecDeque<f64>,
}

impl RollingWindow {
    fn new(n: usize, maximum: bool) -> Self {
        Self {
            n,
            maximum,
            values: VecDeque::with_capacity(n + 1),
        }
    }

    fn push(&mut self, value: f64) -> f64 {
        self.values.push_back(value);
        if self.values.len() > self.n {
            self.values.pop_front();
        }
        if self.values.len() < self.n {
            return f64::NAN;
        }
        if self.maximum {
            self.values.iter().copied().fold(f64::NEG_INFINITY, f64::max)
        } else {
            self.values.iter().copied().fold(f64::INFINITY, f64::min)
        }
    }
}

/// `tradingrush._trend_events`' shared state: the trend EMA and the
/// `SWING`-bar extremes the stop sits at.
struct TrendFrame {
    ema: SeededEma,
    swing_low: RollingWindow,
    swing_high: RollingWindow,
}

impl TrendFrame {
    fn new(ema: usize) -> Self {
        Self {
            ema: SeededEma::new(ema, true),
            swing_low: RollingWindow::new(10, false),
            swing_high: RollingWindow::new(10, true),
        }
    }

    /// `_trend_events` for one bar: long where `long_sig` fires above the EMA,
    /// short where `short_sig` fires below it.
    fn event(&mut self, c: &Candle, a: f64, long_sig: bool, short_sig: bool) -> Option<(Side, f64)> {
        self.ema.push(c.close);
        let low = self.swing_low.push(c.low);
        let high = self.swing_high.push(c.high);
        let e = self.ema.value();
        if !(long_sig || short_sig) || !(a > 0.0) || !e.is_finite() {
            return None;
        }
        if long_sig && c.close > e {
            let d = stop_dist(1.0, c.close, low, a);
            return valid(d).then_some((Side::Long, d));
        }
        if short_sig && c.close < e {
            let d = stop_dist(-1.0, c.close, high, a);
            return valid(d).then_some((Side::Short, d));
        }
        None
    }
}

/// `x + 2 x[-1] + 2 x[-2] + x[-3]` over six, NaN until three lags exist.
struct Weighted4 {
    lags: VecDeque<f64>,
}

impl Weighted4 {
    fn new() -> Self {
        Self {
            lags: VecDeque::with_capacity(4),
        }
    }

    fn push(&mut self, x: f64) -> f64 {
        self.lags.push_back(x);
        if self.lags.len() > 4 {
            self.lags.pop_front();
        }
        if self.lags.len() < 4 {
            return f64::NAN;
        }
        (self.lags[3] + 2.0 * self.lags[2] + 2.0 * self.lags[1] + self.lags[0]) / 6.0
    }
}

fn zero_if_nan(x: f64) -> f64 {
    if x.is_nan() { 0.0 } else { x }
}

enum Kind {
    /// `av_first_hour_fib`.
    FirstHourFib {
        entry: f64,
        both: bool,
        day: Option<i64>,
        levels: Option<Option<(f64, f64)>>,
        hi: f64,
        lo: f64,
        last_close: f64,
        fired: bool,
    },
    /// `bnq_ifvg_model` with `pda = "off"`.
    IfvgModel {
        disp: f64,
        pivots: Pivots,
        recent: VecDeque<(f64, f64)>,
        day: Option<i64>,
        sweep_long: Option<(i64, f64, f64)>,
        sweep_short: Option<(i64, f64, f64)>,
        gaps_long: VecDeque<(i64, f64, f64)>,
        gaps_short: VecDeque<(i64, f64, f64)>,
    },
    /// `ptl_orb_candle`.
    OrbCandle {
        threshold: i64,
        day: Option<i64>,
        day_open: f64,
        range: Option<(f64, f64)>,
        building: bool,
        inside_seen: bool,
        done: bool,
        fired: bool,
        prev_close: f64,
    },
    /// `qtt_rsi_ema_retest`, exit `ema20_50_exit`.
    RsiEmaRetest {
        retest: bool,
        rsi: TtRsi,
        rsi_ema: SeededEma,
        e200: SeededEma,
        e20: SeededEma,
        e50: SeededEma,
        prev: Option<(f64, f64)>,
    },
    /// `tr_oscillator_rebound` with `osc = "willr"`.
    WillrRebound {
        hh: RollingWindow,
        ll: RollingWindow,
        prev: f64,
        frame: TrendFrame,
    },
    /// `tr_rvi`.
    Rvi {
        below: bool,
        body: Weighted4,
        span: Weighted4,
        num: CumSma,
        den: CumSma,
        signal: Weighted4,
        seen: usize,
        prev: Option<(f64, f64)>,
        frame: TrendFrame,
    },
    /// `twp_asia_london_ny`.
    AsiaLondonNy {
        tight: f64,
        win: i64,
        widths: VecDeque<f64>,
        last_day: Option<i64>,
        tight_today: bool,
        done: Option<i64>,
    },
    MaCross {
        fast: SeededEma,
        slow: SeededEma,
        prev: Option<(f64, f64)>,
        both: bool,
        stop_atr: f64,
    },
    BodyMomentum {
        bodies: VecDeque<f64>,
        opens: VecDeque<(f64, f64)>,
        pivots: Pivots,
        mult: f64,
        swing: bool,
    },
    HtfManipulation {
        m: i64,
        close_above: TtHtfClose,
        prior: bool,
        ema: Option<SeededEma>,
        swing_candle: bool,
        pos: i64,
        day: Option<i64>,
        cur: Option<(f64, f64, f64, f64)>,
        last: Option<(f64, f64, f64, f64)>,
        prev2: Option<(f64, f64, f64, f64)>,
    },
    SwingSweepMss {
        major: Pivots,
        minor: Pivots,
        ema: Option<SeededEma>,
        w: i64,
        mss_entry: bool,
        long: Option<Sweep>,
        short: Option<Sweep>,
    },
    Manipulation {
        prior_bars: usize,
        ema: Option<SeededEma>,
        engulf: TtEngulf,
        sweep_stop: bool,
        stop_atr: f64,
        fade: bool,
        history: VecDeque<(Candle, i64)>,
        atr2: SmaAtr,
    },
    /// `sweep_reclaim` on a `swing<k>` level.
    SweepReclaim {
        pivots: Pivots,
        confirm: i64,
        /// `(bar swept, extreme since, level)` for the low and the high side.
        low_sweep: Option<(i64, f64, f64)>,
        high_sweep: Option<(i64, f64, f64)>,
    },
    /// `ny_vwap_pullback`.
    NyVwapPullback {
        or_min: i64,
        end: i64,
        stop_mult: f64,
        bar_minutes: i64,
        day: Option<i64>,
        vwap_pv: f64,
        vwap_v: f64,
        /// The opening range being built, and whether it can still grow.
        range: Option<(f64, f64)>,
        building: bool,
        /// The finished range, known on the bars after it.
        done: Option<(f64, f64)>,
        run_high: f64,
        run_low: f64,
    },
    /// `supply_demand`.
    SupplyDemand {
        base_len: usize,
        impulse: f64,
        pivots: Pivots,
        /// The last `base_len` bars' `(high, low)`, before this one.
        base: VecDeque<(f64, f64)>,
        /// `(side, lo, hi, born)`.
        zones: Vec<(f64, f64, f64, i64)>,
    },
    EmaMacd {
        fast: SeededEma,
        slow: SeededEma,
        signal: SeededEma,
        ema200: SeededEma,
        prev: Option<(f64, f64)>,
        last_up: i64,
        last_dn: i64,
        within: i64,
        both: bool,
    },
    /// `yf_alphatrend`.
    AlphaTrend {
        coeff: f64,
        /// `F.sma(tr, 14)`, the line's own range (not the Wilder stop ATR).
        range_sma: CumSma,
        osc: TtRsi,
        prev_close: Option<f64>,
        /// The ratcheting line's accumulator (`acc`, 0.0 until first set).
        acc: f64,
        /// `at[i]`, `at[i-1]`, `at[i-2]`, `at[i-3]` (NaN where unset).
        line: [f64; 4],
        /// The last side the alternating filter emitted.
        last: i8,
        /// `sig[i]` at the current bar.
        sig: i8,
    },
    /// `mql_big_move_cont`.
    BigMove {
        big: f64,
        vol: bool,
        stop_atr: f64,
        prev_close: Option<f64>,
        /// ATR(14) at the previous bar.
        prev_atr: f64,
        volume_sma: CumSma,
        /// SMA(volume, 20) at the previous bar.
        prev_volume_mean: f64,
    },
    /// `dt_rsi_dip_vol`.
    RsiDipVol {
        lo: f64,
        stop_atr: f64,
        rsi: TtRsi,
        mean: CumSma,
        vol: Option<VolHigh>,
    },
    /// `qp_rsi2_pullback`.
    Rsi2Pullback {
        lo: f64,
        stop_atr: f64,
        rsi: TtRsi,
        mean: CumSma,
    },
    // ---- the FundedNext book's creator families (`fundednext/oct-18-2026`) ----
    /// `lux_htf_stoch_bucket`.
    HtfStochBucket {
        stoch: TtStochastic,
        rsi: TtRsi,
        prev_rsi: f64,
        zone: f64,
        /// `None` for the `atr_1` stop, the 3-bar pivots for `swing`.
        pivots: Option<Pivots>,
    },
    /// `av_donchian_mid`.
    DonchianMid {
        highs: VecDeque<f64>,
        lows: VecDeque<f64>,
        n: usize,
        stop_atr: f64,
        /// `(close, mid)` at the previous bar.
        prev: Option<(f64, f64)>,
    },
    /// `qtt_triple_ema`.
    TripleEma {
        e34: SeededEma,
        e55: SeededEma,
        e200: SeededEma,
        prev: Option<(f64, f64)>,
        stop_atr: f64,
    },
    /// `tmb_ha_stoch_doji`.
    HaStochDoji {
        doji: f64,
        os: f64,
        stoch: TtStochastic,
        /// `(ha_open, ha_close)` of the previous bar, for the next HA open.
        ha_prev: Option<(f64, f64)>,
        /// The previous bar's `(ha_o, ha_c, ha_h, ha_l, %K, low, high)`.
        prev: Option<[f64; 7]>,
    },
    /// `nt_profile_levels`.
    ProfileLevels {
        bw: f64,
        stop_atr: f64,
        closes: VecDeque<f64>,
        levels: Option<Vec<f64>>,
        prev_close: Option<f64>,
        /// Python's GLOBAL bar index modulo 4, which decides the refresh bars,
        /// minus this engine's own: realigned at every anchor.
        phase_anchors: &'static [(i64, u8)],
        shift: i64,
    },
    /// `cf_prevday_continuation`.
    PrevdayContinuation {
        zone: f64,
        stop_atr: f64,
        day: Option<i64>,
        /// Today's `(open, high, low, close)` so far, and yesterday's.
        today: (f64, f64, f64, f64),
        yesterday: Option<(f64, f64, f64, f64)>,
    },
    /// `lux_rsi_divergence` (`confirm=none`, `regime=any`).
    RsiDivergence {
        k: usize,
        rsi: TtRsi,
        /// The last `k + 1` candles' `(low, high, rsi)`, so a pivot confirmed
        /// now can read its own bar's RSI.
        recent: VecDeque<(f64, f64, f64)>,
        pivots: IndexedPivots,
        /// `(index, price, rsi)` of the latest and previous confirmed swing,
        /// as `F.pivots` keeps `l_i` / `l_prev_i`.
        low: (Option<(i64, f64, f64)>, Option<(i64, f64, f64)>),
        high: (Option<(i64, f64, f64)>, Option<(i64, f64, f64)>),
        seen_low: i64,
        seen_high: i64,
        /// `(side, bar index it was found on, swing extreme)`.
        pending: Vec<(f64, i64, f64)>,
    },
    /// `lux_session_open_reaction` at `london_open`.
    SessionOpenReaction,
    /// `qp_gap_fade`.
    GapFade {
        min_gap: f64,
        stop_atr: f64,
        day: Option<i64>,
        close: f64,
        /// Yesterday's last close.
        previous_close: Option<f64>,
    },
    /// `nt_donchian_close`.
    DonchianClose {
        n: usize,
        after_loss: bool,
        stop_atr: f64,
        closes: VecDeque<f64>,
        pos: i8,
        entry: f64,
        /// `(hh, ll)` at the current bar, for the exit rule.
        channel: (f64, f64),
        close: f64,
    },
    /// `tr_supertrend`.
    TrSupertrend {
        mult: f64,
        atr10: WilderAtr,
        ema: SeededEma,
        up: f64,
        dn: f64,
        t: i8,
        prev_close: Option<f64>,
    },
}

/// numpy's pairwise summation (`add.reduce` on a contiguous float64 block),
/// which is what `.mean()` and `.std()` use. Reproduced so a mean over 14 or 20
/// values rounds exactly as the Python's does.
pub(crate) fn numpy_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for x in a {
            res += x;
        }
        res
    } else if n <= 128 {
        let mut r = [0.0; 8];
        r.copy_from_slice(&a[..8]);
        let mut i = 8;
        while i < n - (n % 8) {
            for j in 0..8 {
                r[j] += a[i + j];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        numpy_sum(&a[..n2]) + numpy_sum(&a[n2..])
    }
}

/// `F.sma(values, n)`: `(csum[i+1] - csum[i+1-n]) / n` off ONE running
/// cumulative sum, as numpy computes it -- not a rolling window, whose
/// rounding differs.
struct CumSma {
    n: usize,
    cum: f64,
    past: VecDeque<f64>,
}

impl CumSma {
    fn new(n: usize) -> Self {
        let mut past = VecDeque::with_capacity(n + 2);
        past.push_back(0.0);
        Self { n, cum: 0.0, past }
    }

    fn push(&mut self, value: f64) -> f64 {
        self.cum += value;
        self.past.push_back(self.cum);
        if self.past.len() > self.n + 1 {
            self.past.pop_front();
        }
        if self.past.len() == self.n + 1 {
            (self.cum - self.past[0]) / self.n as f64
        } else {
            f64::NAN
        }
    }
}

/// `tt_features.stochastic(n, smooth)`: %K over the `n`-bar range -- 50.0
/// wherever the window is not yet full or is flat (`numpy.where(hh > ll, ...)`
/// with NaN comparing false) -- smoothed by `F.sma`'s cumulative sum.
struct TtStochastic {
    n: usize,
    highs: VecDeque<f64>,
    lows: VecDeque<f64>,
    sma: CumSma,
}

impl TtStochastic {
    fn new(n: usize, smooth: usize) -> Self {
        Self {
            n,
            highs: VecDeque::with_capacity(n + 1),
            lows: VecDeque::with_capacity(n + 1),
            sma: CumSma::new(smooth),
        }
    }

    fn push(&mut self, c: &Candle) -> f64 {
        self.highs.push_back(c.high);
        self.lows.push_back(c.low);
        if self.highs.len() > self.n {
            self.highs.pop_front();
            self.lows.pop_front();
        }
        let k = if self.highs.len() == self.n {
            let hh = self.highs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let ll = self.lows.iter().copied().fold(f64::INFINITY, f64::min);
            if hh > ll {
                100.0 * (c.close - ll) / (hh - ll)
            } else {
                50.0
            }
        } else {
            50.0
        };
        self.sma.push(k)
    }
}

/// `nt_profile_levels`' `look`: the closes each profile is built from.
const PROFILE_LOOK: usize = 168;

/// One refresh of `nt_profile_levels`: a histogram of `closes` with bins about
/// `width` wide (5 to 80 of them), smoothed `[1, 2, 1] / 4`, and the centre of
/// every interior local peak at least a quarter of the tallest.
///
/// `numpy.histogram(w, bins=nb, range=(lo, hi))` exactly: edges from
/// `linspace`, the index from the scaled offset and then corrected against the
/// edges, the top edge closing the last bin.
fn profile_peaks(closes: &VecDeque<f64>, width: f64) -> Vec<f64> {
    let lo = closes.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = closes.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    // `int(max(5, min(80, x)))`.
    let x = (hi - lo) / width;
    let capped = if x < 80.0 { x } else { 80.0 };
    let nb = (if capped > 5.0 { capped } else { 5.0 }) as usize;
    let (first, last) = if lo == hi {
        (lo - 0.5, hi + 0.5)
    } else {
        (lo, hi)
    };
    let delta = last - first;
    let div = nb as f64;
    let step = delta / div;
    let mut edges: Vec<f64> = (0..=nb)
        .map(|k| {
            if step == 0.0 {
                k as f64 / div * delta + first
            } else {
                k as f64 * step + first
            }
        })
        .collect();
    edges[nb] = last;
    let mut hist = vec![0i64; nb];
    for &value in closes {
        if !(value >= first && value <= last) {
            continue;
        }
        let mut index = (((value - first) / delta) * div) as i64;
        if index == nb as i64 {
            index -= 1;
        }
        if value < edges[index as usize] {
            index -= 1;
        }
        if value >= edges[index as usize + 1] && index != nb as i64 - 1 {
            index += 1;
        }
        hist[index as usize] += 1;
    }
    // `numpy.convolve(hist, [1, 2, 1], "same") / 4.0`.
    let smooth: Vec<f64> = (0..nb)
        .map(|k| {
            let left = if k > 0 { hist[k - 1] } else { 0 };
            let right = if k + 1 < nb { hist[k + 1] } else { 0 };
            (left + 2 * hist[k] + right) as f64 / 4.0
        })
        .collect();
    let tallest = smooth.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    (1..nb - 1)
        .filter(|&k| {
            smooth[k] > smooth[k - 1] && smooth[k] >= smooth[k + 1] && smooth[k] >= 0.25 * tallest
        })
        .map(|k| (edges[k] + edges[k + 1]) / 2.0)
        .collect()
}

/// `F.rsi(n)`: the first move is 0 (`diff` with the first close prepended),
/// the averages are seeded with the numpy mean of moves `1..=n`, and the first
/// reading is at bar `n + 1`.
struct TtRsi {
    n: usize,
    prev: Option<f64>,
    count: usize,
    ups: Vec<f64>,
    downs: Vec<f64>,
    au: f64,
    ad: f64,
    value: f64,
}

impl TtRsi {
    fn new(n: usize) -> Self {
        Self {
            n,
            prev: None,
            count: 0,
            ups: Vec::with_capacity(n),
            downs: Vec::with_capacity(n),
            au: 0.0,
            ad: 0.0,
            value: f64::NAN,
        }
    }

    fn push(&mut self, close: f64) -> f64 {
        let d = self.prev.map_or(0.0, |p| close - p);
        self.prev = Some(close);
        let (up, dn) = (d.max(0.0), (-d).max(0.0));
        let i = self.count;
        self.count += 1;
        if i >= 1 && i <= self.n {
            self.ups.push(up);
            self.downs.push(dn);
            if i == self.n {
                self.au = numpy_sum(&self.ups) / self.n as f64;
                self.ad = numpy_sum(&self.downs) / self.n as f64;
            }
        } else if i > self.n {
            self.au += (up - self.au) / self.n as f64;
            self.ad += (dn - self.ad) / self.n as f64;
            self.value = if self.ad == 0.0 {
                100.0
            } else {
                100.0 - 100.0 / (1.0 + self.au / self.ad)
            };
        }
        self.value
    }
}

/// `tt_deltatrendtrading._vol_high`: the population std of the last 20 log
/// returns above the median of the last 100 such readings.
struct VolHigh {
    prev: Option<f64>,
    returns: VecDeque<f64>,
    readings: VecDeque<f64>,
}

impl VolHigh {
    fn new() -> Self {
        Self {
            prev: None,
            returns: VecDeque::with_capacity(21),
            readings: VecDeque::with_capacity(101),
        }
    }

    fn push(&mut self, close: f64) -> bool {
        let log = close.ln();
        let r = self.prev.map_or(0.0, |p| log - p);
        self.prev = Some(log);
        self.returns.push_back(r);
        if self.returns.len() > 20 {
            self.returns.pop_front();
        }
        if self.returns.len() < 20 {
            return false;
        }
        let window: Vec<f64> = self.returns.iter().copied().collect();
        let mean = numpy_sum(&window) / 20.0;
        let squares: Vec<f64> = window.iter().map(|x| (x - mean) * (x - mean)).collect();
        let rv = (numpy_sum(&squares) / 20.0).sqrt();
        self.readings.push_back(rv);
        if self.readings.len() > 100 {
            self.readings.pop_front();
        }
        if self.readings.len() < 100 {
            return false;
        }
        let mut sorted: Vec<f64> = self.readings.iter().copied().collect();
        sorted.sort_by(f64::total_cmp);
        let median = (sorted[49] + sorted[50]) / 2.0;
        rv > median
    }
}

/// `es.average_true_range(bars, 14)` for `luxalgo_manipulation`'s `atr_K`
/// stops: a running-sum mean with the first bar's open as its previous close.
struct SmaAtr {
    ranges: VecDeque<f64>,
    total: f64,
    prev_close: Option<f64>,
}

impl SmaAtr {
    fn push(&mut self, c: &Candle) -> Option<f64> {
        let prev = self.prev_close.unwrap_or(c.open);
        let tr = (c.high - c.low)
            .max((c.high - prev).abs())
            .max((c.low - prev).abs());
        self.prev_close = Some(c.close);
        self.ranges.push_back(tr);
        self.total += tr;
        if self.ranges.len() > 14 {
            let old = self.ranges.pop_front().unwrap_or(0.0);
            self.total -= old;
        }
        (self.ranges.len() >= 14).then(|| self.total / 14.0)
    }
}

pub(crate) struct TtState {
    kind: Kind,
    atr: WilderAtr,
    /// Index of the current bar in the session-bar series.
    index: i64,
    /// The event at the current bar, before the entry cutoff: side, stop
    /// distance and the signal's own target distance (`level` exits).
    event: Option<(Side, f64, Option<f64>)>,
    manip_atr: Option<f64>,
    /// `stop_widen`: the stop distance a signal returns is multiplied by it.
    widen: f64,
    /// `F.clock_window(..., "london_open")["op"]`: `(New York date, open)`
    /// of that date's 03:00 candle, from EVERY source bar (`observe_source`).
    london_open: Option<(i64, f64)>,
    /// `F.clock_window("asia")` / `("london")`: `(high, low)` per New York
    /// date the window ENDS on, from every source bar (`observe_source`).
    asia: std::collections::HashMap<i64, (f64, f64)>,
    london: std::collections::HashMap<i64, (f64, f64)>,
    bar_minutes: i64,
    shift_seconds: i64,
}

impl TtState {
    pub(crate) fn new(family: Family, bar_minutes: i64) -> Option<Self> {
        let mut widen = 1.0;
        let kind = match family {
            Family::QpMaCross {
                fast,
                slow,
                both,
                stop_atr,
            } => Kind::MaCross {
                fast: SeededEma::new(fast, true),
                slow: SeededEma::new(slow, true),
                prev: None,
                both,
                stop_atr,
            },
            Family::LuxBodyMomentum {
                mult,
                stop,
                widen: k,
            } => {
                widen = k;
                Kind::BodyMomentum {
                    bodies: VecDeque::with_capacity(23),
                    opens: VecDeque::with_capacity(3),
                    pivots: Pivots::new(3),
                    mult,
                    swing: stop == TtStop::Swing,
                }
            }
            Family::LuxSweepReclaim {
                swing,
                confirm_bars,
            } => Kind::SweepReclaim {
                pivots: Pivots::new(swing),
                confirm: confirm_bars as i64,
                low_sweep: None,
                high_sweep: None,
            },
            Family::LuxNyVwapPullback {
                or_min,
                window,
                stop_mult,
            } => Kind::NyVwapPullback {
                or_min: or_min as i64,
                end: (or_min + window) as i64,
                stop_mult,
                bar_minutes,
                day: None,
                vwap_pv: 0.0,
                vwap_v: 0.0,
                range: None,
                building: false,
                done: None,
                run_high: f64::NAN,
                run_low: f64::NAN,
            },
            Family::LuxSupplyDemand { base_len, impulse } => Kind::SupplyDemand {
                base_len,
                impulse,
                pivots: Pivots::new(5),
                base: VecDeque::with_capacity(base_len + 1),
                zones: Vec::new(),
            },
            Family::LuxHtfManipulation {
                htf_m,
                close_above,
                prior,
                ema,
                stop,
            } => Kind::HtfManipulation {
                m: htf_m as i64,
                close_above,
                prior,
                ema: (ema > 0).then(|| SeededEma::new(ema, true)),
                swing_candle: stop == TtStop::Candle,
                pos: 0,
                day: None,
                cur: None,
                last: None,
                prev2: None,
            },
            Family::LuxSwingSweepMss {
                k,
                w,
                mss_entry,
                ema,
            } => Kind::SwingSweepMss {
                major: Pivots::new(k),
                minor: Pivots::new(2),
                ema: (ema > 0).then(|| SeededEma::new(ema, true)),
                w: w as i64,
                mss_entry,
                long: None,
                short: None,
            },
            Family::LuxalgoManipulation {
                prior_bars,
                ema,
                engulf,
                stop,
                fade,
            } => {
                Kind::Manipulation {
                    prior_bars,
                    // `_bar_ema` is `es.ema`: seeded, never blank.
                    ema: (ema > 0).then(|| SeededEma::new(ema, false)),
                    engulf,
                    sweep_stop: stop == TtStop::Sweep,
                    stop_atr: match stop {
                        TtStop::Atr(k) => k,
                        _ => 0.0,
                    },
                    fade,
                    history: VecDeque::with_capacity(prior_bars + 3),
                    atr2: SmaAtr {
                        ranges: VecDeque::with_capacity(15),
                        total: 0.0,
                        prev_close: None,
                    },
                }
            }
            Family::TdEmaMacd { within, both } => Kind::EmaMacd {
                fast: SeededEma::new(12, false),
                slow: SeededEma::new(26, false),
                signal: SeededEma::new(9, false),
                ema200: SeededEma::new(200, true),
                prev: None,
                last_up: -1_000_000_000,
                last_dn: -1_000_000_000,
                within: within as i64,
                both,
            },
            Family::YfAlphatrend { coeff, use_rsi } => {
                // Only the RSI arm (`rsi` 1, the script's no-volume option) is
                // seated; the MFI arm is not ported.
                if !use_rsi {
                    return None;
                }
                Kind::AlphaTrend {
                    coeff,
                    range_sma: CumSma::new(14),
                    osc: TtRsi::new(14),
                    prev_close: None,
                    acc: 0.0,
                    line: [f64::NAN; 4],
                    last: 0,
                    sig: 0,
                }
            }
            Family::MqlBigMoveCont { big, vol, stop_atr } => Kind::BigMove {
                big,
                vol,
                stop_atr,
                prev_close: None,
                prev_atr: f64::NAN,
                volume_sma: CumSma::new(20),
                prev_volume_mean: f64::NAN,
            },
            // `qc_ema_cross` IS `qp_ma_cross` with the slow EMA fixed at 21: both
            // read `tt_features.ema` and the same two cross conditions.
            Family::QcEmaCross {
                fast,
                both,
                stop_atr,
            } => Kind::MaCross {
                fast: SeededEma::new(fast, true),
                slow: SeededEma::new(21, true),
                prev: None,
                both,
                stop_atr,
            },
            Family::DtRsiDipVol {
                n,
                lo,
                vol_high,
                stop_atr,
            } => Kind::RsiDipVol {
                lo,
                stop_atr,
                rsi: TtRsi::new(n),
                mean: CumSma::new(20),
                vol: vol_high.then(VolHigh::new),
            },
            Family::QpRsi2Pullback { lo, stop_atr } => Kind::Rsi2Pullback {
                lo,
                stop_atr,
                rsi: TtRsi::new(2),
                mean: CumSma::new(200),
            },
            Family::LuxHtfStochBucket {
                htf_m,
                zone,
                rsi_n,
                stop,
            } => Kind::HtfStochBucket {
                stoch: TtStochastic::new(14 * htf_m, 3 * htf_m),
                rsi: TtRsi::new(rsi_n),
                prev_rsi: f64::NAN,
                zone,
                pivots: (stop == TtStop::Swing).then(|| Pivots::new(3)),
            },
            Family::AvDonchianMid { n, stop_atr } => Kind::DonchianMid {
                highs: VecDeque::with_capacity(n + 1),
                lows: VecDeque::with_capacity(n + 1),
                n,
                stop_atr,
                prev: None,
            },
            Family::QttTripleEma { stop_atr } => Kind::TripleEma {
                e34: SeededEma::new(34, true),
                e55: SeededEma::new(55, true),
                e200: SeededEma::new(200, true),
                prev: None,
                stop_atr,
            },
            Family::TmbHaStochDoji { doji, os } => Kind::HaStochDoji {
                doji,
                os,
                stoch: TtStochastic::new(14, 3),
                ha_prev: None,
                prev: None,
            },
            Family::NtProfileLevels {
                bw,
                stop_atr,
                phase_anchors,
            } => Kind::ProfileLevels {
                bw,
                stop_atr,
                closes: VecDeque::with_capacity(PROFILE_LOOK + 1),
                levels: None,
                prev_close: None,
                phase_anchors,
                shift: 0,
            },
            Family::CfPrevdayContinuation { zone, stop_atr } => Kind::PrevdayContinuation {
                zone,
                stop_atr,
                day: None,
                today: (f64::NAN, f64::NAN, f64::NAN, f64::NAN),
                yesterday: None,
            },
            Family::LuxRsiDivergence { rsi_n, k } => Kind::RsiDivergence {
                k,
                rsi: TtRsi::new(rsi_n),
                recent: VecDeque::with_capacity(k + 2),
                pivots: IndexedPivots::new(k),
                low: (None, None),
                high: (None, None),
                seen_low: -1,
                seen_high: -1,
                pending: Vec::new(),
            },
            Family::LuxSessionOpenReaction => Kind::SessionOpenReaction,
            Family::AvFirstHourFib { entry, both } => Kind::FirstHourFib {
                entry,
                both,
                day: None,
                levels: None,
                hi: f64::NEG_INFINITY,
                lo: f64::INFINITY,
                last_close: f64::NAN,
                fired: false,
            },
            Family::BnqIfvgModel { k, disp } => Kind::IfvgModel {
                disp,
                pivots: Pivots::new(k),
                recent: VecDeque::with_capacity(3),
                day: None,
                sweep_long: None,
                sweep_short: None,
                gaps_long: VecDeque::new(),
                gaps_short: VecDeque::new(),
            },
            Family::PtlOrbCandle { or_min } => Kind::OrbCandle {
                threshold: (or_min as i64).max(bar_minutes),
                day: None,
                day_open: f64::NAN,
                range: None,
                building: false,
                inside_seen: false,
                done: false,
                fired: false,
                prev_close: f64::NAN,
            },
            Family::QttRsiEmaRetest { retest } => Kind::RsiEmaRetest {
                retest,
                rsi: TtRsi::new(14),
                rsi_ema: SeededEma::new(9, false),
                e200: SeededEma::new(200, true),
                e20: SeededEma::new(20, true),
                e50: SeededEma::new(50, true),
                prev: None,
            },
            Family::TrOscillatorRebound { ema } => Kind::WillrRebound {
                hh: RollingWindow::new(14, true),
                ll: RollingWindow::new(14, false),
                prev: f64::NAN,
                frame: TrendFrame::new(ema),
            },
            Family::TrRvi { below, ema } => Kind::Rvi {
                below,
                body: Weighted4::new(),
                span: Weighted4::new(),
                num: CumSma::new(10),
                den: CumSma::new(10),
                signal: Weighted4::new(),
                seen: 0,
                prev: None,
                frame: TrendFrame::new(ema),
            },
            Family::TwpAsiaLondonNy { tight, win } => Kind::AsiaLondonNy {
                tight,
                win,
                widths: VecDeque::with_capacity(21),
                last_day: None,
                tight_today: false,
                done: None,
            },
            Family::QpGapFade { min_gap, stop_atr } => Kind::GapFade {
                min_gap,
                stop_atr,
                day: None,
                close: f64::NAN,
                previous_close: None,
            },
            Family::NtDonchianClose {
                n,
                after_loss,
                stop_atr,
            } => Kind::DonchianClose {
                n,
                after_loss,
                stop_atr,
                closes: VecDeque::with_capacity(n + 1),
                pos: 0,
                entry: f64::NAN,
                channel: (f64::NAN, f64::NAN),
                close: f64::NAN,
            },
            Family::TrSupertrend { mult, ema } => Kind::TrSupertrend {
                mult,
                atr10: WilderAtr::new(10),
                ema: SeededEma::new(ema, true),
                up: f64::NAN,
                dn: f64::NAN,
                t: 1,
                prev_close: None,
            },
            _ => return None,
        };
        Some(Self {
            kind,
            atr: WilderAtr::new(14),
            index: -1,
            event: None,
            manip_atr: None,
            widen,
            london_open: None,
            asia: std::collections::HashMap::new(),
            london: std::collections::HashMap::new(),
            bar_minutes,
            shift_seconds: 0,
        })
    }

    /// The sleeve's clock shift, so a candle's New York date can be read.
    pub(crate) fn set_shift(&mut self, shift_hours: i64) {
        self.shift_seconds = shift_hours * 3_600;
    }

    /// Every SOURCE bar, in or out of session, with its REAL New York stamp.
    /// Only `session_open_reaction` reads it: Python's clock windows come off
    /// the full bar series (`ctx["tt_full"]`), so the 03:00 candle exists for a
    /// market whose session opens at 09:30. That candle is the cell's own bar
    /// size, so its open is the first source bar in the slot starting 03:00.
    pub(crate) fn observe_source(&mut self, real_ts: i64, open: f64, high: f64, low: f64) {
        let width = self.bar_minutes.max(1);
        let minute = real_ts.rem_euclid(86_400) / 60;
        let slot = minute / width * width;
        let date = real_ts.div_euclid(86_400);
        if matches!(self.kind, Kind::AsiaLondonNy { .. }) {
            // Membership by the CANDLE's start, as `clock_window` reads the
            // cell-size buckets of `tt_full`.
            let fold = |table: &mut std::collections::HashMap<i64, (f64, f64)>, key: i64| {
                let entry = table.entry(key).or_insert((f64::NEG_INFINITY, f64::INFINITY));
                entry.0 = entry.0.max(high);
                entry.1 = entry.1.min(low);
            };
            if !(180..1_140).contains(&slot) {
                fold(&mut self.asia, date + i64::from(slot >= 1_140));
            }
            if (180..480).contains(&slot) {
                fold(&mut self.london, date);
            }
            return;
        }
        if !matches!(self.kind, Kind::SessionOpenReaction) {
            return;
        }
        if (180..185).contains(&slot) && self.london_open.is_none_or(|(day, _)| day != date) {
            self.london_open = Some((date, open));
        }
    }

    /// Folds one in-session candle in and records the event at it, if any.
    ///
    /// `since` is minutes past the session open on the sleeve's own clock,
    /// `tt_features.base`'s `since`.
    pub(crate) fn push(&mut self, c: &Candle, day: i64, since: i64) {
        self.index += 1;
        let i = self.index;
        self.atr.push(c);
        let a = self.atr.value();
        self.event = None;
        let mut event = None;
        // A level target, for the families that name one.
        let mut target: Option<f64> = None;
        match &mut self.kind {
            Kind::MaCross {
                fast,
                slow,
                prev,
                both,
                stop_atr,
            } => {
                fast.push(c.close);
                slow.push(c.close);
                let (f, s) = (fast.value(), slow.value());
                if let Some((pf, ps)) = *prev
                    && pf.is_finite()
                    && ps.is_finite()
                {
                    let distance = *stop_atr * a;
                    if pf <= ps && f > s {
                        if valid(distance) {
                            event = Some((Side::Long, distance));
                        }
                    } else if *both && pf >= ps && f < s && valid(distance) {
                        event = Some((Side::Short, distance));
                    }
                }
                *prev = Some((f, s));
            }
            Kind::BodyMomentum {
                bodies,
                opens,
                pivots,
                mult,
                swing,
            } => {
                bodies.push_back((c.close - c.open).abs());
                if bodies.len() > 22 {
                    bodies.pop_front();
                }
                opens.push_back((c.open, c.close));
                if opens.len() > 2 {
                    opens.pop_front();
                }
                pivots.push(c);
                // avg[i] = mean(body[i-21 ..= i-2]).
                if bodies.len() == 22 && opens.len() == 2 {
                    let avg = bodies.iter().take(20).sum::<f64>() / 20.0;
                    let (o1, c1) = opens[0];
                    let up = c.close > c.open && c1 > o1;
                    let dn = c.close < c.open && c1 < o1;
                    let pair = (bodies[21] + bodies[20]) / 2.0;
                    if avg.is_finite() && (up || dn) && pair >= *mult * avg {
                        let side = if up { 1.0 } else { -1.0 };
                        let distance = if *swing {
                            let reference = if up {
                                pivots.last_low
                            } else {
                                pivots.last_high
                            };
                            stop_dist(side, c.close, reference, a)
                        } else {
                            a
                        };
                        if valid(distance) {
                            event = Some((side_of(side), distance));
                        }
                    }
                }
            }
            Kind::HtfManipulation {
                m,
                close_above,
                prior,
                ema,
                swing_candle,
                pos,
                day: current_day,
                cur,
                last,
                prev2,
            } => {
                if let Some(e) = ema {
                    e.push(c.close);
                }
                if *current_day != Some(day) {
                    // A partial group from the previous day closes now.
                    if current_day.is_some()
                        && let Some(open) = cur.take()
                    {
                        *prev2 = *last;
                        *last = Some(open);
                    }
                    *current_day = Some(day);
                    *pos = 0;
                } else {
                    *pos += 1;
                }
                if *pos % *m == 0 {
                    *cur = Some((c.open, c.high, c.low, c.close));
                } else if let Some((o, h, l, _)) = *cur {
                    *cur = Some((o, h.max(c.high), l.min(c.low), c.close));
                }
                if *pos % *m == *m - 1 {
                    *prev2 = *last;
                    *last = cur.take();
                    if let (Some((po, _ph, pl, pc)), Some((qo, qh, ql, qc))) = (*last, *prev2)
                        && qh.is_finite()
                    {
                        let ph = last.map(|x| x.1).unwrap_or(f64::NAN);
                        let (ref_up, ref_dn) = match close_above {
                            TtHtfClose::High => (qh, ql),
                            TtHtfClose::Close => (qc, qc),
                        };
                        let mut bull = pl < ql && pc > ref_up && pc > po;
                        let mut bear = ph > qh && pc < ref_dn && pc < po;
                        if *prior {
                            bull = bull && qc > qo;
                            bear = bear && qc < qo;
                        }
                        if let Some(e) = ema {
                            let v = e.value();
                            bull = bull && v.is_finite() && c.close > v;
                            bear = bear && v.is_finite() && c.close < v;
                        }
                        if bull {
                            let d = if *swing_candle {
                                stop_dist(1.0, c.close, pl, a)
                            } else {
                                2.0 * a
                            };
                            if valid(d) {
                                event = Some((Side::Long, d));
                            }
                        } else if bear {
                            let d = if *swing_candle {
                                stop_dist(-1.0, c.close, ph, a)
                            } else {
                                2.0 * a
                            };
                            if valid(d) {
                                event = Some((Side::Short, d));
                            }
                        }
                    }
                }
            }
            Kind::SwingSweepMss {
                major,
                minor,
                ema,
                w,
                mss_entry,
                long,
                short,
            } => {
                major.push(c);
                minor.push(c);
                if let Some(e) = ema {
                    e.push(c.close);
                }
                let (ph, pl) = (major.last_high, major.last_low);
                let (mh, ml) = (minor.last_high, minor.last_low);
                if ph.is_finite() && c.high > ph && short.is_none_or(|s| i - s.t > *w) {
                    *short = Some(Sweep {
                        t: i,
                        ext: c.high,
                        mss: None,
                    });
                }
                if pl.is_finite() && c.low < pl && long.is_none_or(|s| i - s.t > *w) {
                    *long = Some(Sweep {
                        t: i,
                        ext: c.low,
                        mss: None,
                    });
                }
                let ema_value = ema.as_ref().map(SeededEma::value);
                for side in [1.0f64, -1.0] {
                    let slot = if side > 0.0 { &mut *long } else { &mut *short };
                    let Some(s) = slot.as_mut() else { continue };
                    if i == s.t {
                        continue;
                    }
                    if s.mss.is_none() {
                        s.ext = if side > 0.0 {
                            s.ext.min(c.low)
                        } else {
                            s.ext.max(c.high)
                        };
                        if i - s.t > *w {
                            *slot = None;
                            continue;
                        }
                        let shift = (side > 0.0 && mh.is_finite() && c.close > mh)
                            || (side < 0.0 && ml.is_finite() && c.close < ml);
                        if !shift {
                            continue;
                        }
                        s.mss = Some((i, if side > 0.0 { c.high } else { c.low }));
                        if !*mss_entry {
                            continue;
                        }
                    } else if *mss_entry || s.mss.is_some_and(|(t, _)| i - t > 20) {
                        *slot = None;
                        continue;
                    }
                    if let Some(v) = ema_value
                        && !(v.is_finite() && side * (c.close - v) > 0.0)
                    {
                        continue;
                    }
                    let fire = if *mss_entry {
                        true
                    } else {
                        let level = (s.ext + s.mss.map_or(f64::NAN, |x| x.1)) / 2.0;
                        (side > 0.0 && c.low <= level && level < c.close)
                            || (side < 0.0 && c.high >= level && level > c.close)
                    };
                    let distance = stop_dist(side, c.close, s.ext, a);
                    if fire && valid(distance) {
                        if event.is_none() {
                            event = Some((side_of(side), distance));
                        }
                        *slot = None;
                    }
                }
            }
            Kind::Manipulation {
                prior_bars,
                ema,
                engulf,
                sweep_stop,
                stop_atr,
                fade,
                history,
                atr2,
            } => {
                self.manip_atr = atr2.push(c);
                if let Some(e) = ema {
                    e.push(c.close);
                }
                history.push_back((*c, day));
                while history.len() > *prior_bars + 2 {
                    history.pop_front();
                }
                let n = *prior_bars;
                let back = n.max(1);
                let len = history.len();
                if len > back && history[len - 1 - back].1 == day {
                    let prev = history[len - 2].0;
                    let (top, bottom) = match engulf {
                        TtEngulf::Range => (prev.high, prev.low),
                        TtEngulf::Body => (prev.open.max(prev.close), prev.open.min(prev.close)),
                    };
                    let side = if c.low < prev.low && c.close > top && c.close > c.open {
                        1.0
                    } else if c.high > prev.high && c.close < bottom && c.close < c.open {
                        -1.0
                    } else {
                        0.0
                    };
                    let mut ok = side != 0.0;
                    for k in 1..=n {
                        if !ok {
                            break;
                        }
                        let before = history[len - 1 - k].0;
                        if side * (before.close - before.open) <= 0.0 {
                            ok = false;
                        }
                    }
                    if ok && let Some(e) = ema {
                        if side * (c.close - e.value()) <= 0.0 {
                            ok = false;
                        }
                    }
                    if ok {
                        let distance = if *sweep_stop {
                            if *fade {
                                c.high - c.low
                            } else if side > 0.0 {
                                c.close - c.low
                            } else {
                                c.high - c.close
                            }
                        } else {
                            self.manip_atr.map_or(f64::NAN, |v| *stop_atr * v)
                        };
                        if distance > 0.0 {
                            let final_side = if *fade { -side } else { side };
                            event = Some((side_of(final_side), distance));
                        }
                    }
                }
            }
            Kind::SweepReclaim {
                pivots,
                confirm,
                low_sweep,
                high_sweep,
            } => {
                pivots.push(c);
                let (lo, hi) = (pivots.last_low, pivots.last_high);
                if lo.is_finite() && c.low < lo {
                    *low_sweep = match *low_sweep {
                        Some((t, ext, level)) if level == lo => Some((t, ext.min(c.low), lo)),
                        _ => Some((i, c.low, lo)),
                    };
                }
                if hi.is_finite() && c.high > hi {
                    *high_sweep = match *high_sweep {
                        Some((t, ext, level)) if level == hi => Some((t, ext.max(c.high), hi)),
                        _ => Some((i, c.high, hi)),
                    };
                }
                if let Some((t, ext, level)) = *low_sweep
                    && i - t < *confirm
                    && c.close > level
                {
                    let d = stop_dist(1.0, c.close, ext, a);
                    let g = tgt(1.0, c.close, hi);
                    if valid_target(d, g) {
                        if event.is_none() {
                            event = Some((Side::Long, d));
                            target = Some(g);
                        }
                        *low_sweep = None;
                    }
                }
                if let Some((t, ext, level)) = *high_sweep
                    && i - t < *confirm
                    && c.close < level
                {
                    let d = stop_dist(-1.0, c.close, ext, a);
                    let g = tgt(-1.0, c.close, lo);
                    if valid_target(d, g) {
                        if event.is_none() {
                            event = Some((Side::Short, d));
                            target = Some(g);
                        }
                        *high_sweep = None;
                    }
                }
            }
            Kind::NyVwapPullback {
                or_min,
                end,
                stop_mult,
                bar_minutes,
                day: current_day,
                vwap_pv,
                vwap_v,
                range,
                building,
                done,
                run_high,
                run_low,
            } => {
                let (prev_high, prev_low) = (*run_high, *run_low);
                let inside = since < (*or_min).max(*bar_minutes);
                if *current_day != Some(day) {
                    *current_day = Some(day);
                    *vwap_pv = 0.0;
                    *vwap_v = 0.0;
                    *done = None;
                    *range = None;
                    // `opening_range` needs the day's FIRST bar inside the
                    // window; a day that opens past it has no range at all.
                    *building = inside;
                    *run_high = c.high;
                    *run_low = c.low;
                } else {
                    *run_high = run_high.max(c.high);
                    *run_low = run_low.min(c.low);
                }
                if *building {
                    if inside {
                        *range = Some(match *range {
                            Some((h, l)) => (h.max(c.high), l.min(c.low)),
                            None => (c.high, c.low),
                        });
                    } else {
                        *building = false;
                        *done = *range;
                    }
                }
                let volume = if c.volume.is_finite() && c.volume > 0.0 {
                    c.volume
                } else {
                    1.0
                };
                let typical = (c.high + c.low + c.close) / 3.0;
                *vwap_pv += typical * volume;
                *vwap_v += volume;
                let vw = *vwap_pv / *vwap_v;
                if i >= 1
                    && let Some((range_high, range_low)) = *done
                    && since < *end
                {
                    let d = *stop_mult * a;
                    if prev_low < range_low && c.high >= vw && vw > c.close && c.close < c.open {
                        let g = tgt(-1.0, c.close, prev_low);
                        if valid_target(d, g) {
                            event = Some((Side::Short, d));
                            target = Some(g);
                        }
                    } else if prev_high > range_high
                        && c.low <= vw
                        && vw < c.close
                        && c.close > c.open
                    {
                        let g = tgt(1.0, c.close, prev_high);
                        if valid_target(d, g) {
                            event = Some((Side::Long, d));
                            target = Some(g);
                        }
                    }
                }
            }
            Kind::SupplyDemand {
                base_len,
                impulse,
                pivots,
                base,
                zones,
            } => {
                pivots.push(c);
                let n = *base_len;
                if i > n as i64 {
                    let mut keep = Vec::with_capacity(zones.len());
                    for zone in zones.iter() {
                        let (side, lo, hi, t) = *zone;
                        if i - t > 100
                            || (side > 0.0 && c.close < lo)
                            || (side < 0.0 && c.close > hi)
                        {
                            continue;
                        }
                        if i > t
                            && ((side > 0.0 && c.low <= hi && c.close > hi)
                                || (side < 0.0 && c.high >= lo && c.close < lo))
                        {
                            let level = if side > 0.0 { lo } else { hi };
                            let d = stop_dist(side, c.close, level, a);
                            if valid(d) && event.is_none() {
                                event = Some((side_of(side), d));
                            }
                            // A zone trades once, emitted or not.
                            continue;
                        }
                        keep.push(*zone);
                    }
                    let start = keep.len().saturating_sub(20);
                    *zones = keep.split_off(start);
                    if base.len() == n {
                        let bhi = base.iter().map(|x| x.0).fold(f64::NEG_INFINITY, f64::max);
                        let blo = base.iter().map(|x| x.1).fold(f64::INFINITY, f64::min);
                        let brng = bhi - blo;
                        if brng > 0.0 && c.high - c.low >= *impulse * brng {
                            let (ph, pl) = (pivots.last_high, pivots.last_low);
                            if c.close > c.open && ph.is_finite() && c.close > ph {
                                zones.push((1.0, blo, bhi, i));
                            } else if c.close < c.open && pl.is_finite() && c.close < pl {
                                zones.push((-1.0, blo, bhi, i));
                            }
                        }
                    }
                }
                base.push_back((c.high, c.low));
                while base.len() > n {
                    base.pop_front();
                }
            }
            Kind::EmaMacd {
                fast,
                slow,
                signal,
                ema200,
                prev,
                last_up,
                last_dn,
                within,
                both,
            } => {
                fast.push(c.close);
                slow.push(c.close);
                ema200.push(c.close);
                // `line[:slow] = NaN`: the first 26 bars carry no line.
                let line = if slow.seen > 26 {
                    fast.value() - slow.value()
                } else {
                    f64::NAN
                };
                signal.push(line);
                let sg = if line.is_finite() {
                    signal.value()
                } else {
                    f64::NAN
                };
                if let Some((pl, ps)) = *prev
                    && pl.is_finite()
                    && ps.is_finite()
                    && line.is_finite()
                    && sg.is_finite()
                {
                    if pl <= ps && line > sg {
                        *last_up = i;
                    } else if pl >= ps && line < sg {
                        *last_dn = i;
                    }
                }
                *prev = Some((line, sg));
                let e = ema200.value();
                if e.is_finite() && line.is_finite() && sg.is_finite() {
                    let distance = 1.5 * a;
                    if c.close > e && line > 0.0 && line > sg && i - *last_up < *within {
                        if valid(distance) {
                            event = Some((Side::Long, distance));
                        }
                    } else if *both
                        && c.close < e
                        && line < 0.0
                        && line < sg
                        && i - *last_dn < *within
                        && valid(distance)
                    {
                        event = Some((Side::Short, distance));
                    }
                }
            }
            Kind::AlphaTrend {
                coeff,
                range_sma,
                osc,
                prev_close,
                acc,
                line,
                last,
                sig,
            } => {
                let prev = prev_close.unwrap_or(c.close);
                *prev_close = Some(c.close);
                let tr = (c.high - c.low)
                    .max((c.high - prev).abs())
                    .max((c.low - prev).abs());
                let range = range_sma.push(tr);
                let o = osc.push(c.close);
                let mut at = f64::NAN;
                if range.is_finite() && o.is_finite() {
                    let upt = c.low - range * *coeff;
                    let dnt = c.high + range * *coeff;
                    if o >= 50.0 {
                        if !(*acc != 0.0 && upt < *acc) {
                            *acc = upt;
                        }
                    } else if !(*acc != 0.0 && dnt > *acc) {
                        *acc = dnt;
                    }
                    at = *acc;
                }
                line.rotate_right(1);
                line[0] = at;
                // `ref[i] = at[i-2]`.
                let (at0, at1, ref0, ref1) = (line[0], line[1], line[2], line[3]);
                *sig = 0;
                if at0.is_finite() && ref0.is_finite() && at1.is_finite() && ref1.is_finite() {
                    if at0 > ref0 && at1 <= ref1 && *last != 1 {
                        *sig = 1;
                        *last = 1;
                    } else if at0 < ref0 && at1 >= ref1 && *last != -1 {
                        *sig = -1;
                        *last = -1;
                    }
                }
                if *sig != 0 && a > 0.0 {
                    let side = f64::from(*sig);
                    // `_emit_signals`: the lagged line when it is on the losing
                    // side of the close, else the default 1.5 ATR.
                    let distance = if ref0.is_finite() && side * (c.close - ref0) > 0.0 {
                        stop_dist(side, c.close, ref0, a)
                    } else {
                        1.5 * a
                    };
                    if valid(distance) {
                        event = Some((side_of(side), distance));
                    }
                }
            }
            Kind::BigMove {
                big,
                vol,
                stop_atr,
                prev_close,
                prev_atr,
                volume_sma,
                prev_volume_mean,
            } => {
                let mv = prev_close.map_or(0.0, |p| c.close - p);
                *prev_close = Some(c.close);
                let mut fire = mv.abs() > *big * *prev_atr;
                if *vol {
                    fire &= c.volume > 2.0 * *prev_volume_mean;
                }
                if fire && mv != 0.0 && a > 0.0 {
                    let distance = *stop_atr * a;
                    event = Some((if mv > 0.0 { Side::Long } else { Side::Short }, distance));
                }
                *prev_atr = a;
                *prev_volume_mean = volume_sma.push(c.volume);
            }
            Kind::RsiDipVol {
                lo,
                stop_atr,
                rsi,
                mean,
                vol,
            } => {
                let r = rsi.push(c.close);
                let m = mean.push(c.close);
                let high = vol.as_mut().is_none_or(|v| v.push(c.close));
                if r.is_finite() && m.is_finite() && r < *lo && c.close < m && high {
                    let distance = *stop_atr * a;
                    if valid(distance) {
                        event = Some((Side::Long, distance));
                    }
                }
            }
            Kind::Rsi2Pullback {
                lo,
                stop_atr,
                rsi,
                mean,
            } => {
                let r = rsi.push(c.close);
                let m = mean.push(c.close);
                if r.is_finite() && m.is_finite() {
                    let distance = *stop_atr * a;
                    if c.close > m && r < *lo {
                        if valid(distance) {
                            event = Some((Side::Long, distance));
                        }
                    } else if c.close < m && r > 100.0 - *lo && valid(distance) {
                        event = Some((Side::Short, distance));
                    }
                }
            }
            Kind::HtfStochBucket {
                stoch,
                rsi,
                prev_rsi,
                zone,
                pivots,
            } => {
                let st = stoch.push(c);
                let r = rsi.push(c.close);
                if let Some(pivots) = pivots.as_mut() {
                    pivots.push(c);
                }
                let before = std::mem::replace(prev_rsi, r);
                if i >= 1 && st.is_finite() {
                    if st >= *zone && before >= 70.0 && 70.0 > r {
                        let d = match pivots {
                            None => a,
                            Some(pivots) => stop_dist(-1.0, c.close, pivots.last_high, a),
                        };
                        if valid(d) {
                            event = Some((Side::Short, d));
                        }
                    } else if st <= 100.0 - *zone && before <= 30.0 && 30.0 < r {
                        let d = match pivots {
                            None => a,
                            Some(pivots) => stop_dist(1.0, c.close, pivots.last_low, a),
                        };
                        if valid(d) {
                            event = Some((Side::Long, d));
                        }
                    }
                }
            }
            Kind::DonchianMid {
                highs,
                lows,
                n,
                stop_atr,
                prev,
            } => {
                // `rolling_max(h, n)` lagged one bar: the `n` bars BEFORE this.
                let (up, dn) = if highs.len() == *n {
                    (
                        highs.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                        lows.iter().copied().fold(f64::INFINITY, f64::min),
                    )
                } else {
                    (f64::NAN, f64::NAN)
                };
                let mid = (up + dn) / 2.0;
                if let Some((pc, pm)) = *prev {
                    let lu = pc <= pm && c.close > mid;
                    let ld = pc >= pm && c.close < mid;
                    if (lu || ld) && a > 0.0 {
                        let side = if lu { 1.0 } else { -1.0 };
                        let d = *stop_atr * a;
                        let t = tgt(side, c.close, if lu { up } else { dn });
                        if valid_target(d, t) {
                            event = Some((side_of(side), d));
                            target = Some(t);
                        }
                    }
                }
                *prev = Some((c.close, mid));
                highs.push_back(c.high);
                lows.push_back(c.low);
                if highs.len() > *n {
                    highs.pop_front();
                    lows.pop_front();
                }
            }
            Kind::TripleEma {
                e34,
                e55,
                e200,
                prev,
                stop_atr,
            } => {
                e34.push(c.close);
                e55.push(c.close);
                e200.push(c.close);
                let (f, s, l) = (e34.value(), e55.value(), e200.value());
                if let Some((pf, ps)) = *prev
                    && a > 0.0
                    && l.is_finite()
                {
                    let crossed = pf.is_finite() && ps.is_finite();
                    let distance = *stop_atr * a;
                    if crossed && f > s && pf <= ps && c.close > s && c.close > l {
                        if valid(distance) {
                            event = Some((Side::Long, distance));
                        }
                    } else if crossed
                        && f < s
                        && pf >= ps
                        && c.close < s
                        && c.close < l
                        && valid(distance)
                    {
                        event = Some((Side::Short, distance));
                    }
                }
                *prev = Some((f, s));
            }
            Kind::HaStochDoji {
                doji,
                os,
                stoch,
                ha_prev,
                prev,
            } => {
                let k = stoch.push(c);
                let hc = (c.open + c.high + c.low + c.close) / 4.0;
                let ho = match *ha_prev {
                    None => (c.open + c.close) / 2.0,
                    Some((po, pc)) => (po + pc) / 2.0,
                };
                let hh = c.high.max(ho.max(hc));
                let hl = c.low.min(ho.min(hc));
                if let Some([jo, jc, jh, jl, kj, lj, hj]) = *prev {
                    let rng = jh - jl;
                    if a > 0.0 && rng > 0.0 && kj.is_finite() && (jc - jo).abs() <= *doji * rng {
                        if kj <= *os && hc > ho {
                            let d = stop_dist(1.0, c.close, lj.min(c.low), a);
                            if valid(d) {
                                event = Some((Side::Long, d));
                            }
                        } else if kj >= 100.0 - *os && hc < ho {
                            let d = stop_dist(-1.0, c.close, hj.max(c.high), a);
                            if valid(d) {
                                event = Some((Side::Short, d));
                            }
                        }
                    }
                }
                *ha_prev = Some((ho, hc));
                *prev = Some([ho, hc, hh, hl, k, c.low, c.high]);
            }
            Kind::ProfileLevels {
                bw,
                stop_atr,
                closes,
                levels,
                prev_close,
                phase_anchors,
                shift,
            } => {
                if let Ok(at) = phase_anchors.binary_search_by_key(&c.ts, |anchor| anchor.0) {
                    *shift = (i64::from(phase_anchors[at].1) - i).rem_euclid(4);
                }
                // `(i - look) % 4 == 0` on PYTHON's index, `i + shift` mod 4.
                if closes.len() == PROFILE_LOOK
                    && (i + *shift - PROFILE_LOOK as i64).rem_euclid(4) == 0
                    && a > 0.0
                {
                    *levels = Some(profile_peaks(closes, *bw * a));
                }
                if let (Some(before), Some(found)) = (*prev_close, levels.as_ref())
                    && !found.is_empty()
                    && a > 0.0
                {
                    let up = found.iter().any(|x| before <= *x && *x < c.close);
                    let dn = found.iter().any(|x| c.close < *x && *x <= before);
                    let distance = *stop_atr * a;
                    if up && !dn {
                        event = Some((Side::Long, distance));
                    } else if dn && !up {
                        event = Some((Side::Short, distance));
                    }
                }
                *prev_close = Some(c.close);
                closes.push_back(c.close);
                if closes.len() > PROFILE_LOOK {
                    closes.pop_front();
                }
            }
            Kind::PrevdayContinuation {
                zone,
                stop_atr,
                day: today_of,
                today,
                yesterday,
            } => {
                if *today_of != Some(day) {
                    if today_of.is_some() {
                        *yesterday = Some(*today);
                    }
                    *today_of = Some(day);
                    *today = (c.open, c.high, c.low, c.close);
                    // `first[i]`: the session's first bar reads yesterday.
                    if let Some((d_open, d_high, d_low, d_close)) = *yesterday
                        && a > 0.0
                    {
                        let rng = d_high - d_low;
                        if rng > 0.0 {
                            let pos = (d_close - d_low) / rng;
                            let distance = *stop_atr * a;
                            if pos >= 1.0 - *zone && d_close > d_open {
                                if valid(distance) {
                                    event = Some((Side::Long, distance));
                                }
                            } else if pos <= *zone && d_close < d_open && valid(distance) {
                                event = Some((Side::Short, distance));
                            }
                        }
                    }
                } else {
                    today.1 = today.1.max(c.high);
                    today.2 = today.2.min(c.low);
                    today.3 = c.close;
                }
            }
            Kind::RsiDivergence {
                k,
                rsi,
                recent,
                pivots,
                low,
                high,
                seen_low,
                seen_high,
                pending,
            } => {
                let r = rsi.push(c.close);
                recent.push_back((c.low, c.high, r));
                if recent.len() > *k + 1 {
                    recent.pop_front();
                }
                let (is_high, is_low) = pivots.push(c);
                if recent.len() == *k + 1 {
                    let (pl, ph, pr) = recent[0];
                    let at = i - *k as i64;
                    if is_low {
                        low.1 = low.0;
                        low.0 = Some((at, pl, pr));
                    }
                    if is_high {
                        high.1 = high.0;
                        high.0 = Some((at, ph, pr));
                    }
                }
                // `for i in range(20, n)`, and a bar without an ATR is skipped
                // whole: a pivot it would have noticed is noticed on the next
                // bar that is not, and the pending list does not move.
                if i >= 20 && a > 0.0 {
                    if let (Some((j, lj, rj)), Some((_, ljp, rjp))) = (low.0, low.1)
                        && j != *seen_low
                    {
                        *seen_low = j;
                        if lj < ljp && rj > rjp {
                            pending.push((1.0, i, lj));
                        }
                    }
                    if let (Some((j, hj, rj)), Some((_, hjp, rjp))) = (high.0, high.1)
                        && j != *seen_high
                    {
                        *seen_high = j;
                        if hj > hjp && rj < rjp {
                            pending.push((-1.0, i, hj));
                        }
                    }
                    let mut keep = Vec::with_capacity(pending.len());
                    for &(side, found, ext) in pending.iter() {
                        if i - found > 20 {
                            continue;
                        }
                        if (side > 0.0 && c.low < ext) || (side < 0.0 && c.high > ext) {
                            continue;
                        }
                        let distance = stop_dist(side, c.close, ext, a);
                        if valid(distance) {
                            if event.is_none() {
                                event = Some((side_of(side), distance));
                            }
                            continue;
                        }
                        keep.push((side, found, ext));
                    }
                    *pending = keep;
                }
            }
            Kind::SessionOpenReaction => {
                let real = c.ts - self.shift_seconds;
                let date = real.div_euclid(86_400);
                if let Some((day, z)) = self.london_open
                    && day == date
                    && real.rem_euclid(86_400) >= 185 * 60
                {
                    if c.low < z && z < c.close && c.close > c.open {
                        let distance = stop_dist(1.0, c.close, c.low, a);
                        if valid(distance) {
                            event = Some((Side::Long, distance));
                        }
                    } else if c.high > z && z > c.close && c.close < c.open {
                        let distance = stop_dist(-1.0, c.close, c.high, a);
                        if valid(distance) {
                            event = Some((Side::Short, distance));
                        }
                    }
                }
            }
            Kind::GapFade {
                min_gap,
                stop_atr,
                day: today_of,
                close,
                previous_close,
            } => {
                if *today_of != Some(day) {
                    *previous_close = today_of.map(|_| *close);
                    *today_of = Some(day);
                    if let Some(pdc) = *previous_close
                        && a > 0.0
                    {
                        let gap = c.open - pdc;
                        if gap.abs() >= *min_gap * a {
                            let side = if gap > 0.0 { -1.0 } else { 1.0 };
                            let far = tgt(side, c.close, pdc);
                            let distance = *stop_atr * a;
                            if valid_target(distance, far) {
                                event = Some((side_of(side), distance));
                                target = Some(far);
                            }
                        }
                    }
                }
                *close = c.close;
            }
            Kind::DonchianClose {
                n,
                after_loss,
                stop_atr,
                closes,
                pos,
                entry,
                channel,
                close,
            } => {
                let (hh, ll) = if closes.len() == *n {
                    (
                        closes.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                        closes.iter().copied().fold(f64::INFINITY, f64::min),
                    )
                } else {
                    (f64::NAN, f64::NAN)
                };
                *channel = (hh, ll);
                *close = c.close;
                if hh.is_finite() {
                    let side: i8 = if c.close > hh {
                        1
                    } else if c.close < ll {
                        -1
                    } else {
                        0
                    };
                    if side != 0 && side != *pos {
                        let lost =
                            *pos != 0 && entry.is_finite() && f64::from(*pos) * (c.close - *entry) < 0.0;
                        *pos = side;
                        *entry = c.close;
                        if (!*after_loss || lost) && a > 0.0 {
                            event = Some((side_of(f64::from(side)), *stop_atr * a));
                        }
                    }
                }
                closes.push_back(c.close);
                if closes.len() > *n {
                    closes.pop_front();
                }
            }
            Kind::TrSupertrend {
                mult,
                atr10,
                ema,
                up,
                dn,
                t,
                prev_close,
            } => {
                atr10.push(c);
                let width = atr10.value();
                ema.push(c.close);
                let e = ema.value();
                let before = prev_close.replace(c.close).unwrap_or(c.close);
                let mut flip = 0i8;
                let mut line = f64::NAN;
                if width.is_finite() {
                    let mid = (c.high + c.low) / 2.0;
                    let mut bu = mid - *mult * width;
                    let mut bd = mid + *mult * width;
                    if up.is_finite() && before > *up {
                        bu = bu.max(*up);
                    }
                    if dn.is_finite() && before < *dn {
                        bd = bd.min(*dn);
                    }
                    let previous = *t;
                    if dn.is_finite() && *t == -1 && c.close > *dn {
                        *t = 1;
                    } else if up.is_finite() && *t == 1 && c.close < *up {
                        *t = -1;
                    }
                    *up = bu;
                    *dn = bd;
                    line = if *t == 1 { *up } else { *dn };
                    if *t != previous {
                        flip = *t;
                    }
                }
                if flip != 0 && a > 0.0 && e.is_finite() {
                    if flip == 1 && c.close > e {
                        let d = stop_dist(1.0, c.close, line, a);
                        if valid(d) {
                            event = Some((Side::Long, d));
                        }
                    } else if flip == -1 && c.close < e {
                        let d = stop_dist(-1.0, c.close, line, a);
                        if valid(d) {
                            event = Some((Side::Short, d));
                        }
                    }
                }
            }
            // The 2026-10-18 kinds, handled below.
            _ => {}
        }
        let real_ts = c.ts - self.shift_seconds;
        let real = real_ts.rem_euclid(86_400) / 60;
        let date = real_ts.div_euclid(86_400);
        let known = |table: &std::collections::HashMap<i64, (f64, f64)>, end: i64| {
            table
                .get(&date)
                .filter(|_| real_ts >= date * 86_400 + end * 60)
                .copied()
        };
        let asia = known(&self.asia, 180);
        let london = known(&self.london, 480);
        match &mut self.kind {
            Kind::FirstHourFib {
                entry,
                both,
                day: current,
                levels,
                hi,
                lo,
                last_close,
                fired,
            } => 'fib: {
                if *current != Some(day) {
                    *current = Some(day);
                    *levels = None;
                    *hi = f64::NEG_INFINITY;
                    *lo = f64::INFINITY;
                    *fired = false;
                }
                if since < 60 {
                    *hi = hi.max(c.high);
                    *lo = lo.min(c.low);
                    *last_close = c.close;
                    break 'fib;
                }
                if levels.is_none() {
                    let span = *hi - *lo;
                    if !(span > 0.0) {
                        *levels = Some(None);
                        break 'fib;
                    }
                    *levels = Some(Some((*last_close, span)));
                }
                let Some(Some((base, span))) = *levels else {
                    break 'fib;
                };
                if *fired || !(a > 0.0) {
                    break 'fib;
                }
                let (up, down) = (base + *entry * span, base - *entry * span);
                if c.close > up {
                    *fired = true;
                    let far = tgt(1.0, c.close, base + 1.618 * span);
                    if far > 0.0 && far.is_finite() {
                        event = Some((Side::Long, (c.close - (base - *entry * span)).max(0.2 * a)));
                        target = Some(far);
                    }
                } else if *both && c.close < down {
                    *fired = true;
                    let far = tgt(-1.0, c.close, base - 1.618 * span);
                    if far > 0.0 && far.is_finite() {
                        event = Some((Side::Short, ((base + *entry * span) - c.close).max(0.2 * a)));
                        target = Some(far);
                    }
                }
            }
            Kind::IfvgModel {
                disp,
                pivots,
                recent,
                day: current,
                sweep_long,
                sweep_short,
                gaps_long,
                gaps_short,
            } => 'ifvg: {
                pivots.push(c);
                recent.push_back((c.high, c.low));
                if recent.len() > 3 {
                    recent.pop_front();
                }
                // `F.fvg` at this bar: against the bar two back.
                let (bull, bear, gap_lo, gap_hi) = if recent.len() == 3 {
                    let (h0, l0) = recent[0];
                    let bull = c.low > h0;
                    let bear = c.high < l0;
                    let gap_lo = if bull { h0 } else if bear { c.high } else { f64::NAN };
                    let gap_hi = if bull { c.low } else if bear { l0 } else { f64::NAN };
                    (bull, bear, gap_lo, gap_hi)
                } else {
                    (false, false, f64::NAN, f64::NAN)
                };
                if !(a > 0.0) {
                    break 'ifvg;
                }
                if current.is_some_and(|d| d != day) {
                    *sweep_long = None;
                    *sweep_short = None;
                    gaps_long.clear();
                    gaps_short.clear();
                }
                *current = Some(day);
                if bear {
                    gaps_long.push_back((i, gap_lo, gap_hi));
                    if gaps_long.len() > 4 {
                        gaps_long.pop_front();
                    }
                }
                if bull {
                    gaps_short.push_back((i, gap_lo, gap_hi));
                    if gaps_short.len() > 4 {
                        gaps_short.pop_front();
                    }
                }
                let (pl, ph) = (pivots.last_low, pivots.last_high);
                if pl.is_finite() && c.low < pl && ph.is_finite() {
                    *sweep_long = Some((i, c.low, ph));
                }
                if ph.is_finite() && c.high > ph && pl.is_finite() {
                    *sweep_short = Some((i, c.high, pl));
                }
                for side in [1.0_f64, -1.0] {
                    let (slot, gaps) = if side > 0.0 {
                        (&mut *sweep_long, &*gaps_long)
                    } else {
                        (&mut *sweep_short, &*gaps_short)
                    };
                    let Some((j, ext, goal)) = *slot else {
                        continue;
                    };
                    let ext = if side > 0.0 { ext.min(c.low) } else { ext.max(c.high) };
                    *slot = Some((j, ext, goal));
                    if i - j > 8 {
                        *slot = None;
                        continue;
                    }
                    if side * (c.close - c.open) < *disp * a {
                        continue;
                    }
                    let hit = gaps.iter().any(|&(g, glo, ghi)| {
                        (i - g).min(i - j) <= 4
                            && i > g
                            && if side > 0.0 { c.close > ghi } else { c.close < glo }
                    });
                    if !hit {
                        continue;
                    }
                    *slot = None;
                    let far = tgt(side, c.close, goal);
                    if far > 0.0 && far.is_finite() && event.is_none() {
                        event = Some((side_of(side), stop_dist(side, c.close, ext, a)));
                        target = Some(far);
                    }
                }
            }
            Kind::OrbCandle {
                threshold,
                day: current,
                day_open,
                range,
                building,
                inside_seen,
                done,
                fired,
                prev_close,
            } => {
                let previous = *prev_close;
                *prev_close = c.close;
                if *current != Some(day) {
                    *current = Some(day);
                    *day_open = c.open;
                    *range = None;
                    *building = false;
                    *inside_seen = false;
                    *done = false;
                    *fired = false;
                }
                if since < *threshold {
                    *inside_seen = true;
                    *building = true;
                    *range = Some(match *range {
                        None => (c.high, c.low),
                        Some((h, l)) => (h.max(c.high), l.min(c.low)),
                    });
                } else if *inside_seen && *building {
                    // The first bar after the range closed: known from here on.
                    *building = false;
                    *done = true;
                }
                if *done && !*fired && a > 0.0 && i >= 1 {
                    *fired = true;
                    let side = if previous > *day_open {
                        1.0
                    } else if previous < *day_open {
                        -1.0
                    } else {
                        0.0
                    };
                    if side != 0.0
                        && let Some((rh, rl)) = *range
                    {
                        let reference = if side > 0.0 { rl } else { rh };
                        event = Some((side_of(side), stop_dist(side, c.close, reference, a)));
                    }
                }
            }
            Kind::RsiEmaRetest {
                retest,
                rsi,
                rsi_ema,
                e200,
                e20,
                e50,
                prev,
            } => {
                let r = rsi.push(c.close);
                rsi_ema.push(r);
                let re = if r.is_finite() { rsi_ema.value() } else { f64::NAN };
                e200.push(c.close);
                e20.push(c.close);
                e50.push(c.close);
                let e = e200.value();
                if let Some((pr, pre)) = *prev
                    && a > 0.0
                    && re.is_finite()
                    && e.is_finite()
                    && c.close > e
                {
                    let cross = pr.is_finite() && pre.is_finite() && r > re && pr <= pre && re > 50.0;
                    let band = |x: f64| (44.0..=55.0).contains(&x);
                    let again = *retest && band(r) && !band(pr) && re > 50.0;
                    if (cross || again) && valid(1.5 * a) {
                        event = Some((Side::Long, 1.5 * a));
                    }
                }
                *prev = Some((r, re));
            }
            Kind::WillrRebound { hh, ll, prev, frame } => {
                let (h, l) = (hh.push(c.high), ll.push(c.low));
                let x = if h > l { -100.0 * (h - c.close) / (h - l) } else { -50.0 };
                let (long_sig, short_sig) = if i >= 1 {
                    (*prev <= -80.0 && x > -80.0, *prev >= -20.0 && x < -20.0)
                } else {
                    (false, false)
                };
                *prev = x;
                event = frame.event(c, a, long_sig, short_sig);
            }
            Kind::Rvi {
                below,
                body,
                span,
                num,
                den,
                signal,
                seen,
                prev,
                frame,
            } => {
                let index = *seen;
                *seen += 1;
                let n = num.push(zero_if_nan(body.push(c.close - c.open)));
                let d = den.push(zero_if_nan(span.push(c.high - c.low)));
                let mut r = if d != 0.0 { n / d } else { f64::NAN };
                if index < 4 {
                    r = f64::NAN;
                }
                let mut s = signal.push(zero_if_nan(r));
                if index < 8 {
                    s = f64::NAN;
                }
                let (long_sig, short_sig) = match *prev {
                    Some((pr, ps)) => {
                        let up = pr <= ps && r > s;
                        let down = pr >= ps && r < s;
                        if *below {
                            (up && r < 0.0, down && r > 0.0)
                        } else {
                            (up && r > 0.0, down && r < 0.0)
                        }
                    }
                    None => (false, false),
                };
                *prev = Some((r, s));
                event = frame.event(c, a, long_sig, short_sig);
            }
            Kind::AsiaLondonNy {
                tight,
                win,
                widths,
                last_day,
                tight_today,
                done,
            } => 'twp: {
                if *last_day != Some(day)
                    && let Some((ah, al)) = asia
                {
                    *last_day = Some(day);
                    let median = if widths.len() >= 10 {
                        let mut sorted: Vec<f64> = widths.iter().copied().collect();
                        sorted.sort_by(f64::total_cmp);
                        sorted[sorted.len() / 2]
                    } else {
                        f64::NAN
                    };
                    widths.push_back(ah - al);
                    while widths.len() > 20 {
                        widths.pop_front();
                    }
                    *tight_today = median.is_finite() && (ah - al) <= *tight * median;
                }
                if !(a > 0.0)
                    || *done == Some(day)
                    || !(570..=570 + *win).contains(&real)
                {
                    break 'twp;
                }
                let (Some((ah, al)), Some((lh, ll))) = (asia, london) else {
                    break 'twp;
                };
                if *last_day != Some(day) {
                    break 'twp;
                }
                if !*tight_today {
                    *done = Some(day);
                    break 'twp;
                }
                let (swept_low, swept_high) = (ll < al, lh > ah);
                if swept_low && !swept_high && c.close > c.open {
                    // `tradewithpat._emit` also refuses a target that is not
                    // positive and finite.
                    let d = stop_dist(1.0, c.close, ll, a);
                    let far = tgt(1.0, c.close, ah);
                    if valid(d) && far > 0.0 && far.is_finite() {
                        event = Some((Side::Long, d));
                        target = Some(far);
                    }
                    *done = Some(day);
                } else if swept_high && !swept_low && c.close < c.open {
                    // `tradewithpat._emit` also refuses a target that is not
                    // positive and finite.
                    let d = stop_dist(-1.0, c.close, lh, a);
                    let far = tgt(-1.0, c.close, al);
                    if valid(d) && far > 0.0 && far.is_finite() {
                        event = Some((Side::Short, d));
                        target = Some(far);
                    }
                    *done = Some(day);
                }
            }
            _ => {}
        }
        let widen = self.widen;
        self.event = event.map(|(side, distance)| (side, distance * widen, target));
    }

    /// The event at the candle last pushed.
    pub(crate) fn event(&self) -> Option<(Side, f64, Option<f64>)> {
        self.event
    }

    /// The family's own exit rule (`exit_mode == "signal"`) read at the candle
    /// last pushed, for a position on `side`.
    pub(crate) fn exit_now(&self, side: Side) -> bool {
        match &self.kind {
            Kind::MaCross { fast, slow, .. } => {
                let (f, s) = (fast.value(), slow.value());
                match side {
                    Side::Long => f < s,
                    Side::Short => f > s,
                }
            }
            // `alphatrend_exit`: the alternating filter fired the other way.
            Kind::AlphaTrend { sig, .. } => match side {
                Side::Long => *sig == -1,
                Side::Short => *sig == 1,
            },
            // `triple_ema_exit`: the 34 back through the 55.
            Kind::TripleEma { e34, e55, .. } => {
                let (f, s) = (e34.value(), e55.value());
                let sign = match side {
                    Side::Long => 1.0,
                    Side::Short => -1.0,
                };
                s.is_finite() && sign * (f - s) < 0.0
            }
            // `ema20_50_exit`.
            Kind::RsiEmaRetest { e20, e50, .. } => {
                let (f, s) = (e20.value(), e50.value());
                let sign = match side {
                    Side::Long => 1.0,
                    Side::Short => -1.0,
                };
                s.is_finite() && sign * (f - s) < 0.0
            }
            // `donchian_close_exit`: the reverse breakout.
            Kind::DonchianClose {
                channel: (hh, ll),
                close,
                ..
            } => match side {
                Side::Long => *close < *ll,
                Side::Short => *close > *hh,
            },
            // Only `qp_ma_cross` is seated with `exit_mode = "signal"`; a cell of
            // any other family on that exit is refused where the book is built
            // (`signal_exit_is_ported`), so this is never asked of one.
            _ => false,
        }
    }
}
