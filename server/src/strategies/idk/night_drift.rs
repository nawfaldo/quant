use crate::backtest::types::{Action, Bar, Side, Strategy};
use std::collections::VecDeque;

// Selected on NQ one-minute bars from 2020-2024 with the IDK environment's
// 0.2-point spread. Data from 2025 onward was excluded from selection.
const SIGMA_LOOKBACK: usize = 30;
const PRE_CLOSE_END: u16 = 17 * 60 + 30;
const CLOSE_DELTA_START: u16 = 17 * 60;
const CLOSE_DELTA_END: u16 = 18 * 60;
const MAX_RELATIVE_CLOSE_DELTA: f64 = 0.15;
const MIN_SELLOFF_SIGMA: f64 = -0.75;
const MAX_SELLOFF_SIGMA: f64 = 1.75;
const MIN_VIX: f64 = 12.0;
const MAX_VIX: f64 = 26.0;
const ENTRY_MINUTE: u16 = 20 * 60;
const EXIT_MINUTE: u16 = 5 * 60;
const TARGET_SIGMA: f64 = 0.8;
const STOP_SIGMA: f64 = 1.0;
const BASE_RISK_FRACTION: f64 = 0.053;
const SELLOFF_BOOST: f64 = 1.3;
// Tuesday, Wednesday, and Friday. Thursday is excluded after weekday review.
const WEEKDAY_MASK: u8 = 0b10110;

#[derive(Default)]
pub(crate) struct NightDrift {
    current_day: Option<i64>,
    day_open: f64,
    day_close: f64,
    pre_close: f64,
    pre_ready: bool,
    close_delta: f64,
    close_volume: f64,
    previous_close: Option<f64>,
    diffs: VecDeque<f64>,
    sigma: Option<f64>,
    session_day: Option<i64>,
    armed: bool,
    pub(crate) in_position: bool,
    active_sigma: f64,
    target: Option<f64>,
    stop: Option<f64>,
    entry_selloff: f64,
    entry_risk_fraction: f64,
}

impl NightDrift {
    fn estimated_delta(bar: Bar) -> f64 {
        let range = bar.high - bar.low;
        if range > 0.0 {
            ((2.0 * bar.close - bar.high - bar.low) / range) * bar.volume
        } else {
            0.0
        }
    }

    fn clear(&mut self) {
        self.armed = false;
        self.in_position = false;
        self.target = None;
        self.stop = None;
        self.active_sigma = 0.0;
        self.entry_selloff = 0.0;
        self.entry_risk_fraction = 0.0;
    }

    fn complete_day(&mut self) {
        if let Some(previous) = self.previous_close {
            if self.diffs.len() == SIGMA_LOOKBACK {
                self.diffs.pop_front();
            }
            self.diffs.push_back(self.day_close - previous);
        }
        self.previous_close = Some(self.day_close);
        self.sigma = if self.diffs.len() == SIGMA_LOOKBACK {
            let mean = self.diffs.iter().sum::<f64>() / SIGMA_LOOKBACK as f64;
            let variance = self
                .diffs
                .iter()
                .map(|value| (value - mean).powi(2))
                .sum::<f64>()
                / (SIGMA_LOOKBACK - 1) as f64;
            (variance > 0.0).then(|| variance.sqrt())
        } else {
            None
        };
    }

    fn enter(&mut self, price: f64, selloff: f64) -> Action {
        self.armed = false;
        self.in_position = true;
        self.target = Some(price + TARGET_SIGMA * self.active_sigma);
        self.stop = Some(price - STOP_SIGMA * self.active_sigma);
        self.entry_risk_fraction =
            BASE_RISK_FRACTION * if selloff >= 0.6 { SELLOFF_BOOST } else { 1.0 };
        Action::Enter {
            side: Side::Long,
            price,
            quantity: 0.01,
        }
    }

    pub(crate) fn update_tick(&mut self, price: f64, timestamp: i64) -> Action {
        let minute = (timestamp.rem_euclid(86_400) / 60) as u16;
        if self.in_position
            && (self.stop.is_some_and(|stop| price <= stop)
                || self.target.is_some_and(|target| price >= target)
                || (EXIT_MINUTE..ENTRY_MINUTE).contains(&minute))
        {
            self.clear();
            return Action::Close {
                price,
                fraction: 1.0,
            };
        }
        Action::Hold
    }
}

impl Strategy for NightDrift {
    fn update(&mut self, bar: Bar, _equity: f64) -> Action {
        let day = bar.ts.div_euclid(86_400);
        let minute = (bar.ts.rem_euclid(86_400) / 60) as u16;
        if self.current_day != Some(day) {
            if self.current_day.is_some() {
                self.complete_day();
            }
            self.current_day = Some(day);
            self.day_open = bar.open;
            self.pre_ready = false;
            self.close_delta = 0.0;
            self.close_volume = 0.0;
        }
        self.day_close = bar.close;
        if minute < PRE_CLOSE_END {
            self.pre_close = bar.close;
            self.pre_ready = true;
        }
        if (CLOSE_DELTA_START..CLOSE_DELTA_END).contains(&minute) {
            self.close_delta += Self::estimated_delta(bar);
            self.close_volume += bar.volume;
        }

        if self.in_position {
            if let Some(stop) = self.stop
                && bar.low <= stop
            {
                let price = bar.open.min(stop);
                self.clear();
                return Action::Close {
                    price,
                    fraction: 1.0,
                };
            }
            if let Some(target) = self.target
                && bar.high >= target
            {
                let price = bar.open.max(target);
                self.clear();
                return Action::Close {
                    price,
                    fraction: 1.0,
                };
            }
            if (EXIT_MINUTE..ENTRY_MINUTE).contains(&minute) {
                let price = bar.open;
                self.clear();
                return Action::Close {
                    price,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }

        if minute >= CLOSE_DELTA_END && self.session_day != Some(day) {
            self.session_day = Some(day);
            self.clear();
            if let Some(sigma) = self.sigma {
                let weekday = (day + 3).rem_euclid(7) as u8;
                let selloff = if self.pre_ready {
                    (self.day_open - self.pre_close) / sigma
                } else {
                    0.0
                };
                let relative_delta = if self.close_volume > 0.0 {
                    self.close_delta / self.close_volume
                } else {
                    0.0
                };
                if weekday <= 4
                    && WEEKDAY_MASK & (1 << weekday) != 0
                    && selloff > MIN_SELLOFF_SIGMA
                    && selloff < MAX_SELLOFF_SIGMA
                    && relative_delta < MAX_RELATIVE_CLOSE_DELTA
                    && (MIN_VIX..MAX_VIX).contains(&bar.vix)
                {
                    self.armed = true;
                    self.active_sigma = sigma;
                    self.entry_selloff = selloff;
                }
            }
        }
        if self.armed && minute >= ENTRY_MINUTE {
            return self.enter(bar.open, self.entry_selloff);
        }
        Action::Hold
    }

    fn discard(&mut self, action: Action) {
        if matches!(action, Action::Enter { .. }) {
            self.clear();
        }
    }

    fn entry_risk_fraction(&self) -> Option<f64> {
        (self.entry_risk_fraction > 0.0).then_some(self.entry_risk_fraction)
    }

    fn entry_stop_price(&self) -> Option<f64> {
        self.stop
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(open: f64, high: f64, low: f64, close: f64, volume: f64) -> Bar {
        Bar {
            ts: 0,
            open,
            high,
            low,
            close,
            volume,
            volume_delta: 0.0,
            depth_events: 0,
            vix: 20.0,
            order_flow: Default::default(),
        }
    }

    #[test]
    fn estimated_delta_matches_the_volume_delta_formula() {
        assert_eq!(
            NightDrift::estimated_delta(bar(5.0, 10.0, 0.0, 10.0, 100.0)),
            100.0
        );
        assert_eq!(
            NightDrift::estimated_delta(bar(5.0, 10.0, 0.0, 0.0, 100.0)),
            -100.0
        );
    }

    #[test]
    fn stop_is_conservatively_checked_before_target() {
        let mut strategy = NightDrift {
            in_position: true,
            target: Some(110.0),
            stop: Some(90.0),
            ..NightDrift::default()
        };
        let action = strategy.update(bar(100.0, 120.0, 80.0, 100.0, 1.0), 1_000.0);
        assert!(matches!(
            action,
            Action::Close {
                price: 90.0,
                fraction: 1.0
            }
        ));
    }
}
