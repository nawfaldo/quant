use crate::backtest::types::{Action, Bar, Side, Strategy};

#[derive(Default)]
pub(crate) struct NightDrift {
    current_day: Option<i64>,
    day_open: f64,
    day_close: f64,
    pre_close: f64,
    pre_ready: bool,
    previous_close: Option<f64>,
    diffs: std::collections::VecDeque<f64>,
    sigma: Option<f64>,
    session_day: Option<i64>,
    armed: bool,
    pub(crate) in_position: bool,
    expected_day: i64,
    active_sigma: f64,
    target: Option<f64>,
    entry_selloff: f64,
}
impl NightDrift {
    fn clear(&mut self) {
        self.armed = false;
        self.in_position = false;
        self.target = None;
        self.active_sigma = 0.0;
        self.entry_selloff = 0.0;
    }
    fn complete_day(&mut self) {
        if let Some(previous) = self.previous_close {
            if self.diffs.len() == 14 {
                self.diffs.pop_front();
            }
            self.diffs.push_back(self.day_close - previous);
        }
        self.previous_close = Some(self.day_close);
        self.sigma = if self.diffs.len() == 14 {
            let mean = self.diffs.iter().sum::<f64>() / 14.0;
            Some((self.diffs.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / 13.0).sqrt())
                .filter(|v| *v > 0.0)
        } else {
            None
        };
    }

    pub(crate) fn update_tick(&mut self, price: f64, timestamp: i64) -> Action {
        let day = timestamp.div_euclid(86_400);
        let minute = (timestamp.rem_euclid(86_400) / 60) as u16;

        if self.in_position {
            if self.target.is_some_and(|target| price >= target) || (305..1170).contains(&minute) {
                self.clear();
                return Action::Close {
                    price,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }

        if !self.armed {
            return Action::Hold;
        }
        if day > self.expected_day {
            self.clear();
            return Action::Hold;
        }
        if day != self.expected_day || minute < 1170 {
            return Action::Hold;
        }

        self.armed = false;
        self.in_position = true;
        self.target = Some(price + 1.4 * self.active_sigma);
        Action::Enter {
            side: Side::Long,
            price,
            quantity: 0.01,
        }
    }
}
impl Strategy for NightDrift {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let day = bar.ts.div_euclid(86400);
        let minute = (bar.ts.rem_euclid(86400) / 60) as u16;
        if self.current_day != Some(day) {
            if self.current_day.is_some() {
                self.complete_day();
            }
            self.current_day = Some(day);
            self.day_open = bar.open;
            self.pre_ready = false;
        }
        self.day_close = bar.close;
        if minute < 1020 {
            self.pre_close = bar.close;
            self.pre_ready = true;
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
            if (305..1170).contains(&minute) {
                let price = bar.open;
                self.clear();
                return Action::Close {
                    price,
                    fraction: 1.0,
                };
            }
            return Action::Hold;
        }
        if minute >= 1080 && self.session_day != Some(day) {
            self.session_day = Some(day);
            self.clear();
            if let Some(sigma) = self.sigma {
                let weekday = (day + 3).rem_euclid(7);
                let allowed = weekday <= 1;
                let selloff = if self.pre_ready {
                    (self.day_open - self.pre_close) / sigma
                } else {
                    0.0
                };
                if allowed && bar.vix >= 15.0 && selloff > 0.05 {
                    self.armed = true;
                    self.expected_day = day;
                    self.active_sigma = sigma;
                    self.entry_selloff = selloff;
                }
            }
        }
        if self.armed && day == self.expected_day && minute >= 1170 {
            self.armed = false;
            self.in_position = true;
            self.target = Some(bar.open + 1.4 * self.active_sigma);
            let boost = if self.entry_selloff >= 0.6 { 1.55 } else { 1.0 };
            let quantity = 1.41 * (equity / 6000.0).max(0.0) * boost;
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
