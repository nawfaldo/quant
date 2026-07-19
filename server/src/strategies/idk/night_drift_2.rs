use crate::backtest::types::{Action, Bar, Side, Strategy};
use std::collections::VecDeque;

const SIGMA_WINDOW: usize = 14;
const PRE_ENTRY_CUTOFF: u16 = 17 * 60;
const SESSION_OPEN: u16 = 18 * 60;
const ENTRY_MINUTE: u16 = 19 * 60 + 30;
const EXIT_MINUTE: u16 = 5 * 60 + 5;
const VIX_FLOOR: f64 = 15.0;
const MIN_SELLOFF_SIGMA: f64 = 0.05;
const TARGET_SIGMA: f64 = 1.4;
const BOOST_SIGMA: f64 = 0.6;
const BOOST_MULTIPLIER: f64 = 1.55;
const BASE_LOT: f64 = 1.41;
const SIZING_ANCHOR_BALANCE: f64 = 6_000.0;
// Monday and Tuesday, where Monday is bit zero.
const DAY_MASK: u8 = 0b000_0011;

/// July 2026 production configuration ported from night_drift.zig.
#[derive(Default)]
pub(crate) struct NightDrift2 {
    current_day: Option<i64>,
    day_open: f64,
    day_close: f64,
    pre_entry_close: f64,
    pre_entry_ready: bool,
    previous_daily_close: Option<f64>,
    daily_diffs: VecDeque<f64>,
    completed_sigma: Option<f64>,
    session_day: Option<i64>,
    armed: bool,
    in_position: bool,
    active_sigma: f64,
    target: Option<f64>,
}

impl NightDrift2 {
    fn complete_day(&mut self) {
        if let Some(previous) = self.previous_daily_close {
            if self.daily_diffs.len() == SIGMA_WINDOW {
                self.daily_diffs.pop_front();
            }
            self.daily_diffs.push_back(self.day_close - previous);
        }
        self.previous_daily_close = Some(self.day_close);
        self.completed_sigma = if self.daily_diffs.len() == SIGMA_WINDOW {
            let mean = self.daily_diffs.iter().sum::<f64>() / SIGMA_WINDOW as f64;
            let variance = self
                .daily_diffs
                .iter()
                .map(|value| (value - mean).powi(2))
                .sum::<f64>()
                / (SIGMA_WINDOW - 1) as f64;
            Some(variance.max(0.0).sqrt()).filter(|sigma| *sigma > 0.0)
        } else {
            None
        };
    }

    fn clear(&mut self) {
        self.armed = false;
        self.in_position = false;
        self.active_sigma = 0.0;
        self.target = None;
    }

    fn holding_window(minute: u16) -> bool {
        minute >= ENTRY_MINUTE || minute < EXIT_MINUTE
    }

    fn entry_quantity(equity: f64, selloff: f64) -> f64 {
        let boost = if selloff >= BOOST_SIGMA {
            BOOST_MULTIPLIER
        } else {
            1.0
        };
        BASE_LOT * (equity.max(0.0) / SIZING_ANCHOR_BALANCE) * boost
    }
}

impl Strategy for NightDrift2 {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let day = bar.ts.div_euclid(86_400);
        let minute = (bar.ts.rem_euclid(86_400) / 60) as u16;

        if self.current_day != Some(day) {
            if self.current_day.is_some() {
                self.complete_day();
            }
            self.current_day = Some(day);
            self.day_open = bar.open;
            self.pre_entry_close = 0.0;
            self.pre_entry_ready = false;
        }
        self.day_close = bar.close;
        if minute < PRE_ENTRY_CUTOFF {
            self.pre_entry_close = bar.close;
            self.pre_entry_ready = true;
        }

        if self.in_position {
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
            if !Self::holding_window(minute) {
                self.clear();
                return Action::Close {
                    price: bar.open,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }

        if minute >= SESSION_OPEN && self.session_day != Some(day) {
            self.session_day = Some(day);
            self.clear();
            if let Some(sigma) = self.completed_sigma {
                let weekday = (day + 3).rem_euclid(7) as u8;
                let selloff = if self.pre_entry_ready {
                    (self.day_open - self.pre_entry_close) / sigma
                } else {
                    0.0
                };
                if weekday < 7
                    && DAY_MASK & (1 << weekday) != 0
                    && bar.vix >= VIX_FLOOR
                    && self.pre_entry_ready
                    && selloff > MIN_SELLOFF_SIGMA
                {
                    self.armed = true;
                    self.active_sigma = sigma;
                }
            }
        }

        if self.armed && minute >= ENTRY_MINUTE {
            let selloff = (self.day_open - self.pre_entry_close) / self.active_sigma;
            let quantity = Self::entry_quantity(equity, selloff);
            self.armed = false;
            self.in_position = true;
            self.target = Some(bar.open + TARGET_SIGMA * self.active_sigma);
            return Action::Enter {
                side: Side::Long,
                price: bar.open,
                quantity,
            };
        }
        Action::Hold
    }

    fn discard(&mut self, action: Action) {
        if matches!(action, Action::Enter { .. }) {
            self.clear();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calibrated_lot_compounds_and_boosts() {
        assert!((NightDrift2::entry_quantity(6_000.0, 0.5) - 1.41).abs() < 1e-12);
        assert!((NightDrift2::entry_quantity(12_000.0, 0.6) - 4.371).abs() < 1e-12);
    }

    #[test]
    fn holding_window_ends_at_0505() {
        assert!(NightDrift2::holding_window(5 * 60 + 4));
        assert!(!NightDrift2::holding_window(EXIT_MINUTE));
        assert!(NightDrift2::holding_window(ENTRY_MINUTE));
    }
}
