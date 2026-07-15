use crate::backtest::types::{Action, Bar, Side, Strategy};

pub(crate) struct NoiseMomentum {
    current_day: Option<i64>,
    day_open: f64,
    last_close: f64,
    prev_close: f64,
    moves: Vec<[f64; 14]>,
    counts: [u8; 390],
    heads: [u8; 390],
    returns: [f64; 14],
    return_count: u8,
    return_head: u8,
    entries: u8,
    position: Option<Side>,
    ladder: u8,
    entry: f64,
    entry_qty: f64,
    risk: f64,
    vwap_pv: f64,
    vwap_volume: f64,
    pub(crate) upper_bound: f64,
    pub(crate) lower_bound: f64,
}
impl Default for NoiseMomentum {
    fn default() -> Self {
        Self {
            current_day: None,
            day_open: 0.0,
            last_close: 0.0,
            prev_close: 0.0,
            moves: vec![[0.0; 14]; 390],
            counts: [0; 390],
            heads: [0; 390],
            returns: [0.0; 14],
            return_count: 0,
            return_head: 0,
            entries: 0,
            position: None,
            ladder: 0,
            entry: 0.0,
            entry_qty: 0.0,
            risk: 0.0,
            vwap_pv: 0.0,
            vwap_volume: 0.0,
            upper_bound: 0.0,
            lower_bound: 0.0,
        }
    }
}
impl NoiseMomentum {
    fn roll(&mut self, day: i64) {
        if self.current_day == Some(day) {
            return;
        }
        if self.last_close > 0.0 {
            if self.prev_close > 0.0 {
                self.returns[self.return_head as usize] = self.last_close / self.prev_close - 1.0;
                self.return_head = (self.return_head + 1) % 14;
                self.return_count = self.return_count.saturating_add(1).min(14);
            }
            self.prev_close = self.last_close;
        }
        self.current_day = Some(day);
        self.day_open = 0.0;
        self.last_close = 0.0;
        self.position = None;
        self.ladder = 0;
        self.entries = 0;
        self.vwap_pv = 0.0;
        self.vwap_volume = 0.0;
    }
    fn volatility(&self) -> f64 {
        let mean = self.returns.iter().sum::<f64>() / 14.0;
        (self.returns.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / 14.0).sqrt()
    }
    fn bounds(&self, slot: usize) -> (f64, f64) {
        let sigma = self.moves[slot].iter().sum::<f64>() / 14.0;
        let reference = if self.prev_close > 0.0 {
            self.prev_close
        } else {
            self.day_open
        };
        (
            self.day_open.max(reference) * (1.0 + 1.35 * sigma),
            self.day_open.min(reference) * (1.0 - 1.35 * sigma),
        )
    }
}
impl Strategy for NoiseMomentum {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let day = bar.ts.div_euclid(86400);
        let minute = (bar.ts.rem_euclid(86400) / 60) as usize;
        self.roll(day);
        if (570..960).contains(&minute) {
            if minute == 570 {
                self.day_open = bar.open;
            }
            self.last_close = bar.close;
        }
        if minute == 930 {
            if self.position.take().is_some() {
                return Action::Close {
                    price: bar.open,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }
        if !(570..930).contains(&minute) || self.day_open <= 0.0 {
            self.upper_bound = 0.0;
            self.lower_bound = 0.0;
            return Action::Hold;
        }
        let slot = minute - 570;
        (self.upper_bound, self.lower_bound) = if self.counts[slot] == 14 {
            self.bounds(slot)
        } else {
            (0.0, 0.0)
        };
        let mut action = Action::Hold;
        if let Some(side) = self.position {
            let direction = if side == Side::Long { 1.0 } else { -1.0 };
            let stop_mult = if self.ladder == 0 { -0.425 } else { -0.25 };
            let stop = self.entry + direction * stop_mult * self.risk;
            let stop_hit = if side == Side::Long {
                bar.low <= stop
            } else {
                bar.high >= stop
            };
            if stop_hit {
                let price = if side == Side::Long {
                    bar.open.min(stop)
                } else {
                    bar.open.max(stop)
                };
                self.position = None;
                action = Action::Close {
                    price,
                    fraction: 1.0,
                };
            } else {
                let target_mult = if self.ladder == 0 { 2.55 } else { 8.0 };
                let target = self.entry + direction * target_mult * self.risk;
                let hit = if side == Side::Long {
                    bar.high >= target
                } else {
                    bar.low <= target
                };
                if hit {
                    let price = if side == Side::Long {
                        bar.open.max(target)
                    } else {
                        bar.open.min(target)
                    };
                    if self.ladder == 0 {
                        self.ladder = 1;
                        action = Action::Close {
                            price,
                            fraction: 0.5,
                        };
                    } else {
                        self.position = None;
                        action = Action::Close {
                            price,
                            fraction: 1.0,
                        };
                    }
                }
            }
        } else if self.counts[slot] == 14
            && self.return_count == 14
            && minute >= 600
            && (minute - 600).is_multiple_of(30)
        {
            let (upper, lower) = (self.upper_bound, self.lower_bound);
            let long = bar.high >= upper;
            let short = bar.low <= lower;
            if long != short {
                let side = if long { Side::Long } else { Side::Short };
                let price = if long {
                    bar.open.max(upper)
                } else {
                    bar.open.min(lower)
                };
                let vol = self.volatility();
                let scale = if vol > 0.0 {
                    (0.024 / vol).min(1.0)
                } else {
                    1.0
                };
                let margin = if long { 0.25 } else { 0.30 };
                let quantity = ((equity.max(0.0) / margin) * scale * 1.15 / price / 0.01)
                    .round()
                    .max(1.0)
                    * 0.01;
                self.entry = price;
                self.entry_qty = quantity;
                self.risk = price * 0.36 * if vol > 0.0 { vol } else { 0.01 };
                self.position = Some(side);
                self.ladder = 0;
                self.entries = self.entries.saturating_add(1);
                action = Action::Enter {
                    side,
                    price,
                    quantity,
                };
            }
        }
        if bar.volume > 0.0 {
            self.vwap_pv += (bar.high + bar.low + bar.close) / 3.0 * bar.volume;
            self.vwap_volume += bar.volume;
        }
        let movement = (bar.close / self.day_open - 1.0).abs();
        let head = self.heads[slot] as usize;
        self.moves[slot][head] = movement;
        self.heads[slot] = ((head + 1) % 14) as u8;
        self.counts[slot] = self.counts[slot].saturating_add(1).min(14);
        action
    }
}
