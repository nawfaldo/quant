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
        let right_h = (k + 1..width).map(|j| self.highs[j]).fold(f64::NEG_INFINITY, f64::max);
        let right_l = (k + 1..width).map(|j| self.lows[j]).fold(f64::INFINITY, f64::min);
        let left_h = (0..k).map(|j| self.highs[j]).fold(f64::NEG_INFINITY, f64::max);
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

/// `_emit`'s acceptance test for a distance with no target.
fn valid(distance: f64) -> bool {
    distance > 0.0 && distance.is_finite()
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

enum Kind {
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

pub(super) struct TtState {
    kind: Kind,
    atr: WilderAtr,
    /// Index of the current bar in the session-bar series.
    index: i64,
    /// The event at the current bar, before the entry cutoff.
    event: Option<(Side, f64)>,
    manip_atr: Option<f64>,
}

impl TtState {
    pub(super) fn new(family: Family) -> Option<Self> {
        let kind = match family {
            Family::QpMaCross { fast, slow, both, stop_atr } => Kind::MaCross {
                fast: SeededEma::new(fast, true),
                slow: SeededEma::new(slow, true),
                prev: None,
                both,
                stop_atr,
            },
            Family::LuxBodyMomentum { mult, stop } => Kind::BodyMomentum {
                bodies: VecDeque::with_capacity(23),
                opens: VecDeque::with_capacity(3),
                pivots: Pivots::new(3),
                mult,
                swing: stop == TtStop::Swing,
            },
            Family::LuxHtfManipulation { htf_m, close_above, prior, ema, stop } => {
                Kind::HtfManipulation {
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
                }
            }
            Family::LuxSwingSweepMss { k, w, mss_entry, ema } => Kind::SwingSweepMss {
                major: Pivots::new(k),
                minor: Pivots::new(2),
                ema: (ema > 0).then(|| SeededEma::new(ema, true)),
                w: w as i64,
                mss_entry,
                long: None,
                short: None,
            },
            Family::LuxalgoManipulation { prior_bars, ema, engulf, stop, fade } => {
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
            _ => return None,
        };
        Some(Self {
            kind,
            atr: WilderAtr::new(14),
            index: -1,
            event: None,
            manip_atr: None,
        })
    }

    /// Folds one in-session candle in and records the event at it, if any.
    pub(super) fn push(&mut self, c: &Candle, day: i64) {
        self.index += 1;
        let i = self.index;
        self.atr.push(c);
        let a = self.atr.value();
        self.event = None;
        let mut event = None;
        match &mut self.kind {
            Kind::MaCross { fast, slow, prev, both, stop_atr } => {
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
            Kind::BodyMomentum { bodies, opens, pivots, mult, swing } => {
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
                            let reference = if up { pivots.last_low } else { pivots.last_high };
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
                    if current_day.is_some() && let Some(open) = cur.take() {
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
                            let d = if *swing_candle { stop_dist(1.0, c.close, pl, a) } else { 2.0 * a };
                            if valid(d) {
                                event = Some((Side::Long, d));
                            }
                        } else if bear {
                            let d = if *swing_candle { stop_dist(-1.0, c.close, ph, a) } else { 2.0 * a };
                            if valid(d) {
                                event = Some((Side::Short, d));
                            }
                        }
                    }
                }
            }
            Kind::SwingSweepMss { major, minor, ema, w, mss_entry, long, short } => {
                major.push(c);
                minor.push(c);
                if let Some(e) = ema {
                    e.push(c.close);
                }
                let (ph, pl) = (major.last_high, major.last_low);
                let (mh, ml) = (minor.last_high, minor.last_low);
                if ph.is_finite() && c.high > ph && short.is_none_or(|s| i - s.t > *w) {
                    *short = Some(Sweep { t: i, ext: c.high, mss: None });
                }
                if pl.is_finite() && c.low < pl && long.is_none_or(|s| i - s.t > *w) {
                    *long = Some(Sweep { t: i, ext: c.low, mss: None });
                }
                let ema_value = ema.as_ref().map(SeededEma::value);
                for side in [1.0f64, -1.0] {
                    let slot = if side > 0.0 { &mut *long } else { &mut *short };
                    let Some(s) = slot.as_mut() else { continue };
                    if i == s.t {
                        continue;
                    }
                    if s.mss.is_none() {
                        s.ext = if side > 0.0 { s.ext.min(c.low) } else { s.ext.max(c.high) };
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
                let line = if slow.seen > 26 { fast.value() - slow.value() } else { f64::NAN };
                signal.push(line);
                let sg = if line.is_finite() { signal.value() } else { f64::NAN };
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
        }
        self.event = event;
    }

    /// The event at the candle last pushed.
    pub(super) fn event(&self) -> Option<(Side, f64)> {
        self.event
    }

    /// The family's own exit rule (`exit_mode == "signal"`) read at the candle
    /// last pushed, for a position on `side`.
    pub(super) fn exit_now(&self, side: Side) -> bool {
        match &self.kind {
            Kind::MaCross { fast, slow, .. } => {
                let (f, s) = (fast.value(), slow.value());
                match side {
                    Side::Long => f < s,
                    Side::Short => f > s,
                }
            }
            // Only `qp_ma_cross` is seated with `exit_mode = "signal"`; a cell of
            // any other family on that exit is refused where the book is built
            // (`signal_exit_is_ported`), so this is never asked of one.
            _ => false,
        }
    }
}
