use crate::backtest::types::{Action, Bar, Side, Strategy};

const MARKET_OPEN_MINUTE: usize = 9 * 60 + 30;
const MARKET_CLOSE_MINUTE: usize = 16 * 60;
const EXIT_MINUTE: usize = MARKET_CLOSE_MINUTE - 30;
const SESSION_MINUTES: usize = MARKET_CLOSE_MINUTE - MARKET_OPEN_MINUTE;

// "Ladder #2" from Maroy (2025), Table 11.
const LOOKBACK_DAYS: usize = 4;
const BOUNDARY_MULTIPLIER: f64 = 1.10;
const TARGET_DAILY_VOLATILITY: f64 = 0.052;
const MINIMUM_VOLATILITY: f64 = 0.004;
const MAXIMUM_VOLATILITY: f64 = 0.025;
const START_AFTER_OPEN_MINUTES: usize = 30;
const LAST_ENTRY_MINUTE: usize = 15 * 60;
const TRADE_FREQUENCY_MINUTES: usize = 45;
const MAXIMUM_DAILY_ENTRIES: u8 = 5;
// Monday, Tuesday, Thursday, Friday. Wednesday was the least stable IS bucket.
const WEEKDAY_MASK: u8 = 0b1_1011;
const LADDER_STOP: [f64; 2] = [-0.45, -0.25];
const LADDER_TARGET: [f64; 2] = [2.40, 12.0];
const AUM_RISK_FRACTION: f64 = 0.021;
const LONG_MARGIN_REQUIREMENT: f64 = 0.25;
const SHORT_MARGIN_REQUIREMENT: f64 = 0.30;

pub(crate) struct NoiseMomentum {
    point_value: f64,
    quantity_step: f64,
    current_day: Option<i64>,
    day_open: f64,
    last_close: f64,
    previous_close: Option<f64>,
    moves: Vec<[f64; LOOKBACK_DAYS]>,
    move_counts: [u8; SESSION_MINUTES],
    move_heads: [u8; SESSION_MINUTES],
    daily_returns: [f64; LOOKBACK_DAYS],
    return_count: u8,
    return_head: u8,
    entries_today: u8,
    position: Option<Side>,
    ladder_step: usize,
    entry_price: f64,
    risk_per_point: f64,
    pub(crate) upper_bound: f64,
    pub(crate) lower_bound: f64,
}

impl Default for NoiseMomentum {
    fn default() -> Self {
        Self {
            point_value: 1.0,
            quantity_step: 0.01,
            current_day: None,
            day_open: 0.0,
            last_close: 0.0,
            previous_close: None,
            moves: vec![[0.0; LOOKBACK_DAYS]; SESSION_MINUTES],
            move_counts: [0; SESSION_MINUTES],
            move_heads: [0; SESSION_MINUTES],
            daily_returns: [0.0; LOOKBACK_DAYS],
            return_count: 0,
            return_head: 0,
            entries_today: 0,
            position: None,
            ladder_step: 0,
            entry_price: 0.0,
            risk_per_point: 0.0,
            upper_bound: 0.0,
            lower_bound: 0.0,
        }
    }
}

impl NoiseMomentum {
    pub(crate) fn new(point_value: f64, quantity_step: f64) -> Self {
        Self {
            point_value,
            quantity_step,
            ..Self::default()
        }
    }

    fn start_day(&mut self, day: i64) {
        if self.current_day == Some(day) {
            return;
        }

        if self.last_close > 0.0 {
            if let Some(previous_close) = self.previous_close.filter(|close| *close > 0.0) {
                self.daily_returns[self.return_head as usize] =
                    self.last_close / previous_close - 1.0;
                self.return_head = (self.return_head + 1) % LOOKBACK_DAYS as u8;
                self.return_count = self.return_count.saturating_add(1).min(LOOKBACK_DAYS as u8);
            }
            self.previous_close = Some(self.last_close);
        }

        self.current_day = Some(day);
        self.day_open = 0.0;
        self.last_close = 0.0;
        self.position = None;
        self.entries_today = 0;
        self.ladder_step = 0;
        self.entry_price = 0.0;
        self.risk_per_point = 0.0;
        self.upper_bound = 0.0;
        self.lower_bound = 0.0;
    }

    fn historical_volatility(&self) -> Option<f64> {
        if self.return_count < LOOKBACK_DAYS as u8 {
            return None;
        }
        let mean = self.daily_returns.iter().sum::<f64>() / LOOKBACK_DAYS as f64;
        let variance = self
            .daily_returns
            .iter()
            .map(|value| (value - mean).powi(2))
            .sum::<f64>()
            / LOOKBACK_DAYS as f64;
        Some(variance.sqrt()).filter(|value| value.is_finite() && *value > 0.0)
    }

    fn bounds(&self, slot: usize) -> Option<(f64, f64)> {
        if self.move_counts[slot] < LOOKBACK_DAYS as u8 {
            return None;
        }
        let average_move = self.moves[slot].iter().sum::<f64>() / LOOKBACK_DAYS as f64;
        let previous_close = self.previous_close?;
        Some((
            self.day_open.max(previous_close) * (1.0 + BOUNDARY_MULTIPLIER * average_move),
            self.day_open.min(previous_close) * (1.0 - BOUNDARY_MULTIPLIER * average_move),
        ))
    }

    fn record_move(&mut self, slot: usize, close: f64) {
        let head = self.move_heads[slot] as usize;
        self.moves[slot][head] = (close / self.day_open - 1.0).abs();
        self.move_heads[slot] = ((head + 1) % LOOKBACK_DAYS) as u8;
        self.move_counts[slot] = self.move_counts[slot]
            .saturating_add(1)
            .min(LOOKBACK_DAYS as u8);
    }

    fn entry_quantity(&self, side: Side, equity: f64, price: f64, volatility: f64) -> Option<f64> {
        if equity <= 0.0
            || price <= 0.0
            || volatility <= 0.0
            || self.point_value <= 0.0
            || self.quantity_step <= 0.0
        {
            return None;
        }
        let margin_requirement = match side {
            Side::Long => LONG_MARGIN_REQUIREMENT,
            Side::Short => SHORT_MARGIN_REQUIREMENT,
        };
        let margin_available = equity / margin_requirement;
        let volatility_scale = (TARGET_DAILY_VOLATILITY / volatility).min(1.0);
        let raw_quantity = margin_available * volatility_scale / (price * self.point_value);
        let quantity = (raw_quantity / self.quantity_step).floor() * self.quantity_step;
        (quantity >= self.quantity_step && quantity.is_finite()).then_some(quantity)
    }

    fn ladder_price(&self, side: Side, multiplier: f64) -> f64 {
        let direction = if side == Side::Long { 1.0 } else { -1.0 };
        self.entry_price + direction * multiplier * self.risk_per_point
    }

    fn reset_position(&mut self) {
        self.position = None;
        self.ladder_step = 0;
        self.entry_price = 0.0;
        self.risk_per_point = 0.0;
    }
}

impl Strategy for NoiseMomentum {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let day = bar.ts.div_euclid(86_400);
        let minute = (bar.ts.rem_euclid(86_400) / 60) as usize;
        self.start_day(day);

        if (MARKET_OPEN_MINUTE..MARKET_CLOSE_MINUTE).contains(&minute) {
            if minute == MARKET_OPEN_MINUTE {
                self.day_open = bar.open;
            }
            self.last_close = bar.close;
        }

        if !(MARKET_OPEN_MINUTE..EXIT_MINUTE).contains(&minute) || self.day_open <= 0.0 {
            self.upper_bound = 0.0;
            self.lower_bound = 0.0;
            if minute >= EXIT_MINUTE {
                self.reset_position();
            }
            return Action::Hold;
        }

        let slot = minute - MARKET_OPEN_MINUTE;
        (self.upper_bound, self.lower_bound) = self.bounds(slot).unwrap_or((0.0, 0.0));
        let mut action = Action::Hold;

        if let Some(side) = self.position {
            let stop = self.ladder_price(side, LADDER_STOP[self.ladder_step]);
            let stop_hit = match side {
                Side::Long => bar.low <= stop,
                Side::Short => bar.high >= stop,
            };
            if stop_hit {
                let price = match side {
                    Side::Long => bar.open.min(stop),
                    Side::Short => bar.open.max(stop),
                };
                self.reset_position();
                action = Action::Close {
                    price,
                    fraction: 1.0,
                };
            } else {
                let target = self.ladder_price(side, LADDER_TARGET[self.ladder_step]);
                let target_hit = match side {
                    Side::Long => bar.high >= target,
                    Side::Short => bar.low <= target,
                };
                if target_hit {
                    let price = match side {
                        Side::Long => bar.open.max(target),
                        Side::Short => bar.open.min(target),
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
            let elapsed = minute - MARKET_OPEN_MINUTE;
            let weekday = (day + 3).rem_euclid(7) as u8;
            let scheduled = elapsed >= START_AFTER_OPEN_MINUTES
                && minute <= LAST_ENTRY_MINUTE
                && (elapsed - START_AFTER_OPEN_MINUTES).is_multiple_of(TRADE_FREQUENCY_MINUTES)
                && weekday < 5
                && WEEKDAY_MASK & (1 << weekday) != 0;
            if scheduled
                && self.entries_today < MAXIMUM_DAILY_ENTRIES
                && self.upper_bound > 0.0
                && self.return_count == LOOKBACK_DAYS as u8
            {
                let crossed_upper = bar.high >= self.upper_bound;
                let crossed_lower = bar.low <= self.lower_bound;
                if crossed_upper != crossed_lower {
                    let side = if crossed_upper {
                        Side::Long
                    } else {
                        Side::Short
                    };
                    let price = match side {
                        Side::Long => bar.open.max(self.upper_bound),
                        Side::Short => bar.open.min(self.lower_bound),
                    };
                    if let Some(volatility) = self.historical_volatility()
                        && volatility >= MINIMUM_VOLATILITY
                        && volatility <= MAXIMUM_VOLATILITY
                        && let Some(quantity) = self.entry_quantity(side, equity, price, volatility)
                    {
                        self.position = Some(side);
                        self.entries_today = self.entries_today.saturating_add(1);
                        self.ladder_step = 0;
                        self.entry_price = price;
                        // r is 2% of AUM. Divide it by contracts and the NQ
                        // instrument's dollar point value to obtain the price
                        // distance used by the ladder multipliers.
                        self.risk_per_point =
                            equity * AUM_RISK_FRACTION / (quantity * self.point_value);
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
        Some(EXIT_MINUTE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equity_sizing_uses_directional_margin_and_volatility_cap() {
        assert_eq!(
            NoiseMomentum::new(20.0, 1.0).entry_quantity(Side::Long, 100_000.0, 20_000.0, 0.032,),
            Some(1.0)
        );
        assert_eq!(
            NoiseMomentum::new(2.0, 1.0).entry_quantity(Side::Short, 100_000.0, 20_000.0, 0.064,),
            Some(6.0)
        );
    }

    #[test]
    fn forex_equity_sizing_keeps_centilots_for_small_accounts() {
        assert_eq!(
            NoiseMomentum::new(1.0, 0.01).entry_quantity(Side::Long, 1_000.0, 15_000.0, 0.032,),
            Some(0.26)
        );
    }

    #[test]
    fn ladder_two_levels_are_account_risk_multiples() {
        let strategy = NoiseMomentum {
            entry_price: 100.0,
            risk_per_point: 10.0,
            ..NoiseMomentum::default()
        };
        assert_eq!(strategy.ladder_price(Side::Long, LADDER_STOP[0]), 95.5);
        assert_eq!(strategy.ladder_price(Side::Long, LADDER_TARGET[0]), 124.0);
        assert_eq!(strategy.ladder_price(Side::Short, LADDER_STOP[1]), 102.5);
    }

    #[test]
    fn optimized_schedule_starts_at_1000_and_repeats_every_45_minutes() {
        let scheduled = |minute: usize| {
            let elapsed = minute - MARKET_OPEN_MINUTE;
            elapsed >= START_AFTER_OPEN_MINUTES
                && (elapsed - START_AFTER_OPEN_MINUTES).is_multiple_of(TRADE_FREQUENCY_MINUTES)
        };
        assert!(scheduled(10 * 60));
        assert!(scheduled(10 * 60 + 45));
        assert!(!scheduled(10 * 60 + 30));
        assert_eq!(EXIT_MINUTE, 15 * 60 + 30);
    }
}
