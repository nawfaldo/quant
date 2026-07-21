use super::tuning::report::Drawdowns;

pub(crate) struct DrawdownTracker {
    peak: f64,
    peak_day: i64,
    max_dd: f64,
    max_dd_dollars: f64,
    max_dd_peak: i64,
    max_dd_trough: i64,
    episode_dd: f64,
    episode_dd_dollars: f64,
    episode_sum: f64,
    episode_dollars_sum: f64,
    episode_days_sum: i64,
    episode_count: usize,
    in_drawdown: bool,
    last_day: i64,
    current_day: Option<i64>,
    day_peak: f64,
    day_max: f64,
    day_max_dollars: f64,
    max_idd: f64,
    max_idd_dollars: f64,
    max_idd_day: i64,
    idd_sum: f64,
    idd_dollars_sum: f64,
    idd_days: usize,
}

impl DrawdownTracker {
    pub(crate) fn new(initial: f64, start_day: i64) -> Self {
        Self {
            peak: initial,
            peak_day: start_day,
            max_dd: 0.0,
            max_dd_dollars: 0.0,
            max_dd_peak: start_day,
            max_dd_trough: start_day,
            episode_dd: 0.0,
            episode_dd_dollars: 0.0,
            episode_sum: 0.0,
            episode_dollars_sum: 0.0,
            episode_days_sum: 0,
            episode_count: 0,
            in_drawdown: false,
            last_day: start_day,
            current_day: None,
            day_peak: initial,
            day_max: 0.0,
            day_max_dollars: 0.0,
            max_idd: 0.0,
            max_idd_dollars: 0.0,
            max_idd_day: start_day,
            idd_sum: 0.0,
            idd_dollars_sum: 0.0,
            idd_days: 0,
        }
    }

    pub(crate) fn observe(&mut self, equity: f64, day: i64) {
        self.last_day = day;
        if equity >= self.peak {
            self.finish_episode();
            self.peak = equity;
            self.peak_day = day;
        } else {
            let dollars = self.peak - equity;
            let percent = percentage(dollars, self.peak);
            self.in_drawdown = true;
            if percent > self.episode_dd {
                self.episode_dd = percent;
                self.episode_dd_dollars = dollars;
            }
            if percent > self.max_dd {
                self.max_dd = percent;
                self.max_dd_dollars = dollars;
                self.max_dd_peak = self.peak_day;
                self.max_dd_trough = day;
            }
        }

        if self.current_day != Some(day) {
            self.finish_day();
            self.current_day = Some(day);
            self.day_peak = equity;
        }
        self.day_peak = self.day_peak.max(equity);
        let day_dollars = self.day_peak - equity;
        let day_percent = percentage(day_dollars, self.day_peak);
        if day_percent > self.day_max {
            self.day_max = day_percent;
            self.day_max_dollars = day_dollars;
        }
        if day_percent > self.max_idd {
            self.max_idd = day_percent;
            self.max_idd_dollars = day_dollars;
            self.max_idd_day = day;
        }
    }

    pub(crate) fn finish(mut self) -> Drawdowns {
        self.finish_episode();
        self.finish_day();
        let episodes = self.episode_count.max(1) as f64;
        let days = self.idd_days.max(1) as f64;
        Drawdowns {
            max_dd: self.max_dd,
            max_dd_dollars: self.max_dd_dollars,
            max_dd_peak: self.max_dd_peak,
            max_dd_trough: self.max_dd_trough,
            avg_dd: self.episode_sum / episodes,
            avg_dd_dollars: self.episode_dollars_sum / episodes,
            avg_dd_time_days: self.episode_days_sum as f64 / episodes,
            max_idd: self.max_idd,
            max_idd_dollars: self.max_idd_dollars,
            max_idd_day: self.max_idd_day,
            avg_idd: self.idd_sum / days,
            avg_idd_dollars: self.idd_dollars_sum / days,
        }
    }

    fn finish_episode(&mut self) {
        if !self.in_drawdown {
            return;
        }
        self.episode_sum += self.episode_dd;
        self.episode_dollars_sum += self.episode_dd_dollars;
        self.episode_days_sum += (self.last_day - self.peak_day).max(0);
        self.episode_count += 1;
        self.episode_dd = 0.0;
        self.episode_dd_dollars = 0.0;
        self.in_drawdown = false;
    }

    fn finish_day(&mut self) {
        if self.current_day.is_none() {
            return;
        }
        self.idd_sum += self.day_max;
        self.idd_dollars_sum += self.day_max_dollars;
        self.idd_days += 1;
        self.day_max = 0.0;
        self.day_max_dollars = 0.0;
    }
}

fn percentage(value: f64, base: f64) -> f64 {
    if base > 0.0 {
        value / base * 100.0
    } else {
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn average_is_the_mean_of_peak_to_low_episodes() {
        let mut tracker = DrawdownTracker::new(100.0, 1);
        tracker.observe(110.0, 1);
        tracker.observe(99.0, 1);
        tracker.observe(104.0, 1);
        tracker.observe(111.0, 2);
        tracker.observe(105.0, 2);

        let result = tracker.finish();
        assert!((result.max_dd - 10.0).abs() < 1e-9);
        assert!((result.max_dd_dollars - 11.0).abs() < 1e-9);
        assert_eq!(result.max_dd_peak, 1);
        assert_eq!(result.max_dd_trough, 1);
        assert!((result.avg_dd - 7.702_702_702_7).abs() < 1e-9);
        assert!((result.avg_dd_dollars - 8.5).abs() < 1e-9);
    }

    #[test]
    fn flat_equity_has_zero_average_drawdown() {
        let mut tracker = DrawdownTracker::new(100.0, 1);
        tracker.observe(100.0, 1);
        tracker.observe(100.0, 2);
        let result = tracker.finish();
        assert_eq!(result.avg_dd, 0.0);
        assert_eq!(result.avg_dd_dollars, 0.0);
    }
}
