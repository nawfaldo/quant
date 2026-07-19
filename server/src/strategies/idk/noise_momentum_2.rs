use crate::backtest::types::{Action, Bar, Side, Strategy};

const OPEN: usize = 9 * 60 + 30;
const CLOSE: usize = 16 * 60;
const EXIT: usize = CLOSE - 30;
const SLOTS: usize = CLOSE - OPEN;
const LOOKBACK: usize = 14;

const BOUNDARY_MULTIPLIER: f64 = 1.35;
const TARGET_DAILY_VOLATILITY: f64 = 0.024;
const START_AFTER_OPEN: usize = 30;
const FREQUENCY: usize = 30;
const RISK_UNIT_SIGMA: f64 = 0.36;
const LADDER_STOP: [f64; 2] = [-0.425, -0.25];
const LADDER_TARGET: [f64; 2] = [2.55, 8.0];
const LONG_MARGIN: f64 = 0.25;
const SHORT_MARGIN: f64 = 0.30;
const SIZING_SCALE: f64 = 1.15;

/// The volatility-scaled production configuration from noise_momentum.zig.
pub(crate) struct NoiseMomentum2 {
    quantity_step: f64,
    current_day: Option<i64>,
    day_open: f64,
    previous_close: Option<f64>,
    day_last_close: f64,
    moves: Vec<[f64; LOOKBACK]>,
    move_counts: [u8; SLOTS],
    move_heads: [u8; SLOTS],
    daily_returns: [f64; LOOKBACK],
    return_count: u8,
    return_head: u8,
    entries_today: u8,
    position: Option<Side>,
    ladder_step: usize,
    entry_price: f64,
    risk_unit: f64,
    pub(crate) upper_bound: f64,
    pub(crate) lower_bound: f64,
}

impl Default for NoiseMomentum2 {
    fn default() -> Self {
        Self {
            quantity_step: 0.01,
            current_day: None,
            day_open: 0.0,
            previous_close: None,
            day_last_close: 0.0,
            moves: vec![[0.0; LOOKBACK]; SLOTS],
            move_counts: [0; SLOTS],
            move_heads: [0; SLOTS],
            daily_returns: [0.0; LOOKBACK],
            return_count: 0,
            return_head: 0,
            entries_today: 0,
            position: None,
            ladder_step: 0,
            entry_price: 0.0,
            risk_unit: 0.0,
            upper_bound: 0.0,
            lower_bound: 0.0,
        }
    }
}

impl NoiseMomentum2 {
    pub(crate) fn new(quantity_step: f64) -> Self {
        Self {
            quantity_step,
            ..Self::default()
        }
    }

    fn start_day(&mut self, day: i64) {
        if self.current_day == Some(day) {
            return;
        }
        if self.day_last_close > 0.0 {
            if let Some(previous) = self.previous_close.filter(|v| *v > 0.0) {
                self.daily_returns[self.return_head as usize] =
                    self.day_last_close / previous - 1.0;
                self.return_head = (self.return_head + 1) % LOOKBACK as u8;
                self.return_count = self.return_count.saturating_add(1).min(LOOKBACK as u8);
            }
            self.previous_close = Some(self.day_last_close);
        }
        self.current_day = Some(day);
        self.day_open = 0.0;
        self.day_last_close = 0.0;
        self.entries_today = 0;
        self.reset_position();
        self.upper_bound = 0.0;
        self.lower_bound = 0.0;
    }

    fn volatility(&self) -> Option<f64> {
        if self.return_count < LOOKBACK as u8 {
            return None;
        }
        let mean = self.daily_returns.iter().sum::<f64>() / LOOKBACK as f64;
        let variance = self
            .daily_returns
            .iter()
            .map(|v| (v - mean).powi(2))
            .sum::<f64>()
            / LOOKBACK as f64;
        Some(variance.sqrt()).filter(|v| v.is_finite() && *v > 0.0)
    }

    fn bounds(&self, slot: usize) -> Option<(f64, f64)> {
        if self.move_counts[slot] < LOOKBACK as u8 {
            return None;
        }
        let sigma = self.moves[slot].iter().sum::<f64>() / LOOKBACK as f64;
        let reference = self.previous_close.unwrap_or(self.day_open);
        Some((
            self.day_open.max(reference) * (1.0 + BOUNDARY_MULTIPLIER * sigma),
            self.day_open.min(reference) * (1.0 - BOUNDARY_MULTIPLIER * sigma),
        ))
    }

    fn record_move(&mut self, slot: usize, close: f64) {
        let head = self.move_heads[slot] as usize;
        self.moves[slot][head] = (close / self.day_open - 1.0).abs();
        self.move_heads[slot] = ((head + 1) % LOOKBACK) as u8;
        self.move_counts[slot] = self.move_counts[slot].saturating_add(1).min(LOOKBACK as u8);
    }

    fn entry_quantity(&self, side: Side, equity: f64, price: f64, volatility: f64) -> Option<f64> {
        if equity <= 0.0 || price <= 0.0 || self.quantity_step <= 0.0 {
            return None;
        }
        let margin = if side == Side::Long {
            LONG_MARGIN
        } else {
            SHORT_MARGIN
        };
        let scale = (TARGET_DAILY_VOLATILITY / volatility).min(1.0);
        let raw = (equity / margin) * scale * SIZING_SCALE / price;
        let quantity = (raw / self.quantity_step).round() * self.quantity_step;
        (quantity.is_finite() && quantity >= self.quantity_step).then_some(quantity)
    }

    fn level(&self, side: Side, multiple: f64) -> f64 {
        let direction = if side == Side::Long { 1.0 } else { -1.0 };
        self.entry_price + direction * multiple * self.risk_unit
    }

    fn reset_position(&mut self) {
        self.position = None;
        self.ladder_step = 0;
        self.entry_price = 0.0;
        self.risk_unit = 0.0;
    }
}

impl Strategy for NoiseMomentum2 {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let day = bar.ts.div_euclid(86_400);
        let minute = (bar.ts.rem_euclid(86_400) / 60) as usize;
        self.start_day(day);

        if (OPEN..CLOSE).contains(&minute) {
            if minute == OPEN {
                self.day_open = bar.open;
            }
            self.day_last_close = bar.close;
        }
        if minute == EXIT {
            if self.position.is_some() {
                self.reset_position();
                return Action::Close {
                    price: bar.open,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }
        if !(OPEN..EXIT).contains(&minute) || self.day_open <= 0.0 {
            return Action::Hold;
        }

        let slot = minute - OPEN;
        (self.upper_bound, self.lower_bound) = self.bounds(slot).unwrap_or((0.0, 0.0));
        let mut action = Action::Hold;

        if let Some(side) = self.position {
            let stop = self.level(side, LADDER_STOP[self.ladder_step]);
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
                self.reset_position();
                action = Action::Close {
                    price,
                    fraction: 1.0,
                };
            } else {
                let target = self.level(side, LADDER_TARGET[self.ladder_step]);
                let target_hit = if side == Side::Long {
                    bar.high >= target
                } else {
                    bar.low <= target
                };
                if target_hit {
                    let price = if side == Side::Long {
                        bar.open.max(target)
                    } else {
                        bar.open.min(target)
                    };
                    if self.ladder_step == 0 {
                        self.ladder_step = 1;
                        action = Action::Close {
                            price,
                            fraction: 0.5,
                        };
                    } else {
                        self.reset_position();
                        action = Action::Close {
                            price,
                            fraction: 1.0,
                        };
                    }
                }
            }
        } else {
            let elapsed = minute - OPEN;
            let weekday = (day + 3).rem_euclid(7) as u8;
            let scheduled = elapsed >= START_AFTER_OPEN
                && (elapsed - START_AFTER_OPEN).is_multiple_of(FREQUENCY)
                && weekday < 5;
            if scheduled && self.upper_bound > 0.0 && self.return_count == LOOKBACK as u8 {
                let long = bar.high >= self.upper_bound;
                let short = bar.low <= self.lower_bound;
                if long != short {
                    let side = if long { Side::Long } else { Side::Short };
                    let price = if long {
                        bar.open.max(self.upper_bound)
                    } else {
                        bar.open.min(self.lower_bound)
                    };
                    if let Some(volatility) = self.volatility()
                        && let Some(quantity) = self.entry_quantity(side, equity, price, volatility)
                    {
                        self.position = Some(side);
                        self.entries_today = self.entries_today.saturating_add(1);
                        self.entry_price = price;
                        self.risk_unit = price * RISK_UNIT_SIGMA * volatility;
                        action = Action::Enter {
                            side,
                            price,
                            quantity,
                        };
                    }
                }
            }
        }
        self.record_move(slot, bar.close);
        action
    }

    fn discard(&mut self, action: Action) {
        if matches!(action, Action::Enter { .. }) {
            self.reset_position();
        }
    }

    fn session_end_minute(&self) -> Option<usize> {
        Some(EXIT)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_schedule_is_half_hourly() {
        let scheduled = |minute: usize| {
            let elapsed = minute - OPEN;
            elapsed >= START_AFTER_OPEN && (elapsed - START_AFTER_OPEN).is_multiple_of(FREQUENCY)
        };
        assert!(scheduled(10 * 60));
        assert!(scheduled(10 * 60 + 30));
        assert!(!scheduled(10 * 60 + 15));
    }

    #[test]
    fn sizing_matches_zig_directional_margin_and_scale() {
        let strategy = NoiseMomentum2::new(0.01);
        assert_eq!(
            strategy.entry_quantity(Side::Long, 10_000.0, 100.0, 0.024),
            Some(460.0)
        );
        assert_eq!(
            strategy.entry_quantity(Side::Short, 10_000.0, 100.0, 0.024),
            Some(383.33)
        );
    }
}
