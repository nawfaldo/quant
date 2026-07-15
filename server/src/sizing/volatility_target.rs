#[derive(Clone, Copy)]
pub struct VolTargetConfig {
    pub target: f64,
    pub halflife: f64,
    pub max_multiplier: f64,
    pub minimum_days: u32,
}

impl Default for VolTargetConfig {
    fn default() -> Self {
        Self {
            target: 0.20,
            halflife: 20.0,
            max_multiplier: 3.0,
            minimum_days: 30,
        }
    }
}

pub struct VolTarget {
    config: VolTargetConfig,
    variance: f64,
    previous_close: Option<f64>,
    latest_close: Option<f64>,
    days_seen: u32,
}

impl VolTarget {
    pub fn new(config: VolTargetConfig) -> Self {
        Self {
            config,
            variance: 0.0,
            previous_close: None,
            latest_close: None,
            days_seen: 0,
        }
    }

    pub fn on_bar(&mut self, close: f64, day_changed: bool) {
        if day_changed {
            self.complete_day();
        }
        self.latest_close = Some(close);
    }

    pub fn multiplier(&self) -> f64 {
        if self.days_seen < self.config.minimum_days || self.variance <= 0.0 {
            return 1.0;
        }

        let annualized_volatility = (self.variance * 252.0).sqrt();
        if annualized_volatility <= 0.0 {
            1.0
        } else {
            (self.config.target / annualized_volatility).min(self.config.max_multiplier)
        }
    }

    fn complete_day(&mut self) {
        let Some(latest_close) = self.latest_close else {
            return;
        };
        let Some(previous_close) = self.previous_close.replace(latest_close) else {
            return;
        };
        if previous_close <= 0.0 || latest_close <= 0.0 {
            return;
        }

        let daily_return = latest_close / previous_close - 1.0;
        if self.days_seen == 0 {
            self.variance = daily_return * daily_return;
        } else {
            let lambda = 0.5_f64.powf(1.0 / self.config.halflife);
            self.variance = lambda * self.variance + (1.0 - lambda) * daily_return * daily_return;
        }
        self.days_seen += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stays_at_base_size_during_warmup() {
        let mut target = VolTarget::new(VolTargetConfig {
            minimum_days: 2,
            ..VolTargetConfig::default()
        });
        target.on_bar(100.0, true);
        target.on_bar(101.0, true);
        target.on_bar(102.0, true);

        assert_eq!(target.multiplier(), 1.0);
    }

    #[test]
    fn scales_after_warmup_and_honors_cap() {
        let mut target = VolTarget::new(VolTargetConfig {
            target: 1.0,
            max_multiplier: 2.0,
            minimum_days: 1,
            ..VolTargetConfig::default()
        });
        target.on_bar(100.0, true);
        target.on_bar(101.0, true);
        target.on_bar(102.0, true);

        assert_eq!(target.multiplier(), 2.0);
    }
}
