use crate::backtest::types::{Action, Bar, Side, Strategy};

// Minute-resolution grid-search defaults from the locally available NQ
// Bookmap history (2026-07-17 through 2026-07-22), including 0.2 spread.
const VIX_MIN: f64 = 17.0;
const VIX_MAX: f64 = 19.0;
const ENTRY_RISK_FRACTION: f64 = 0.02;
const LONG_MARGIN_REQUIREMENT: f64 = 0.25;
const QUANTITY_STEP: f64 = 0.01;

#[derive(Clone, Copy)]
struct SessionConfig {
    min_delta: f64,
    stop: f64,
    target: f64,
}

const OVERNIGHT: SessionConfig = SessionConfig {
    min_delta: 75.0,
    stop: 100.0,
    target: 250.0,
};
const US_MORNING: SessionConfig = SessionConfig {
    min_delta: 100.0,
    stop: 125.0,
    target: 200.0,
};
const US_AFTERNOON: SessionConfig = SessionConfig {
    min_delta: 100.0,
    stop: 25.0,
    target: 50.0,
};

/// Buys the open of a new hour when the completed one-hour candle was red
/// despite positive aggressor volume delta. Minute bars retain sensible
/// intrahour stop/target ordering while the signal itself remains hourly.
pub(crate) struct HourlyDeltaReversalNq {
    hour: Option<i64>,
    hour_open: f64,
    hour_close: f64,
    hour_delta: f64,
    hour_vix: f64,
    hour_depth_events: u64,
    position_entry: Option<f64>,
    position_stop: f64,
    position_target: f64,
}

impl Default for HourlyDeltaReversalNq {
    fn default() -> Self {
        Self {
            hour: None,
            hour_open: 0.0,
            hour_close: 0.0,
            hour_delta: 0.0,
            hour_vix: 0.0,
            hour_depth_events: 0,
            position_entry: None,
            position_stop: 0.0,
            position_target: 0.0,
        }
    }
}

impl HourlyDeltaReversalNq {
    fn session_config(hour: i64) -> Option<SessionConfig> {
        match hour.rem_euclid(24) {
            18..=23 | 0..=8 => Some(OVERNIGHT),
            9..=11 => Some(US_MORNING),
            12..=15 => Some(US_AFTERNOON),
            _ => None,
        }
    }

    fn completed_hour_config(&self, next_hour: i64) -> Option<SessionConfig> {
        let hour = self.hour.filter(|hour| *hour + 1 == next_hour)?;
        let config = Self::session_config(hour)?;
        (self.hour_close < self.hour_open
            && self.hour_delta >= config.min_delta
            && (VIX_MIN..=VIX_MAX).contains(&self.hour_vix)
            && self.hour_depth_events > 0)
            .then_some(config)
    }

    fn entry_quantity(equity: f64, price: f64, stop: f64) -> Option<f64> {
        if equity <= 0.0 || price <= 0.0 || stop <= 0.0 {
            return None;
        }
        let risk_quantity = equity * ENTRY_RISK_FRACTION / stop;
        let margin_quantity = (equity / LONG_MARGIN_REQUIREMENT) / price;
        let raw = risk_quantity.min(margin_quantity);
        let quantity = (raw / QUANTITY_STEP).floor() * QUANTITY_STEP;
        (quantity >= QUANTITY_STEP && quantity.is_finite()).then_some(quantity)
    }

    fn start_hour(&mut self, hour: i64, bar: Bar) {
        self.hour = Some(hour);
        self.hour_open = bar.open;
        self.hour_close = bar.close;
        self.hour_delta = bar.volume_delta;
        self.hour_vix = bar.vix;
        self.hour_depth_events = bar.depth_events;
    }

    fn accumulate(&mut self, bar: Bar) {
        self.hour_close = bar.close;
        self.hour_delta += bar.volume_delta;
        self.hour_vix = bar.vix;
        self.hour_depth_events = self.hour_depth_events.saturating_add(bar.depth_events);
    }
}

impl Strategy for HourlyDeltaReversalNq {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        let hour = bar.ts.div_euclid(3_600);
        let entry_config = if self.hour == Some(hour) {
            self.accumulate(bar);
            None
        } else {
            let config = self
                .position_entry
                .is_none()
                .then(|| self.completed_hour_config(hour))
                .flatten();
            self.start_hour(hour, bar);
            config
        };

        if let Some(entry) = self.position_entry {
            let stop = entry - self.position_stop;
            if bar.low <= stop {
                self.position_entry = None;
                return Action::Close {
                    price: bar.open.min(stop),
                    fraction: 1.0,
                };
            }

            let target = entry + self.position_target;
            if bar.high >= target {
                self.position_entry = None;
                return Action::Close {
                    price: bar.open.max(target),
                    fraction: 1.0,
                };
            }
        }
        if let Some(config) = entry_config {
            let Some(quantity) = Self::entry_quantity(equity, bar.open, config.stop) else {
                return Action::Hold;
            };
            self.position_entry = Some(bar.open);
            self.position_stop = config.stop;
            self.position_target = config.target;
            Action::Enter {
                side: Side::Long,
                price: bar.open,
                quantity,
            }
        } else {
            Action::Hold
        }
    }

    fn discard(&mut self, action: Action) {
        if matches!(action, Action::Enter { .. }) {
            self.position_entry = None;
            self.position_stop = 0.0;
            self.position_target = 0.0;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(minute: i64, open: f64, high: f64, low: f64, close: f64, delta: f64) -> Bar {
        Bar {
            ts: minute * 60,
            open,
            high,
            low,
            close,
            volume: delta.abs(),
            volume_delta: delta,
            depth_events: 1,
            vix: 18.0,
        }
    }

    #[test]
    fn buys_next_hour_after_red_candle_with_positive_delta() {
        let mut strategy = HourlyDeltaReversalNq::default();
        assert!(matches!(
            strategy.update(bar(60, 100.0, 101.0, 98.0, 99.0, 50.0), 1_000.0),
            Action::Hold
        ));
        assert!(matches!(
            strategy.update(bar(61, 99.0, 100.0, 97.0, 98.0, 25.0), 1_000.0),
            Action::Hold
        ));
        assert!(matches!(
            strategy.update(bar(120, 98.0, 99.0, 97.0, 98.5, 0.0), 1_000.0),
            Action::Enter {
                side: Side::Long,
                price: 98.0,
                quantity: 0.2
            }
        ));
    }

    #[test]
    fn requires_depth_coverage_and_contiguous_hours() {
        let mut strategy = HourlyDeltaReversalNq::default();
        let mut signal = bar(60, 100.0, 101.0, 98.0, 99.0, 100.0);
        signal.depth_events = 0;
        strategy.update(signal, 1_000.0);
        assert!(matches!(
            strategy.update(bar(120, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));

        strategy.update(bar(180, 100.0, 101.0, 98.0, 99.0, 100.0), 1_000.0);
        assert!(matches!(
            strategy.update(bar(300, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));
    }

    #[test]
    fn rejects_out_of_band_vix_and_closed_session_signals() {
        let mut high_vix = HourlyDeltaReversalNq::default();
        let mut signal = bar(60, 100.0, 101.0, 98.0, 99.0, 200.0);
        signal.vix = 20.0;
        high_vix.update(signal, 1_000.0);
        assert!(matches!(
            high_vix.update(bar(120, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));

        let mut closed = HourlyDeltaReversalNq::default();
        closed.update(bar(16 * 60, 100.0, 101.0, 98.0, 99.0, 200.0), 1_000.0);
        assert!(matches!(
            closed.update(bar(17 * 60, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));
    }

    #[test]
    fn stop_is_checked_before_target_for_conservative_ambiguous_bars() {
        let mut strategy = HourlyDeltaReversalNq {
            position_entry: Some(100.0),
            position_stop: 100.0,
            position_target: 200.0,
            ..HourlyDeltaReversalNq::default()
        };
        assert!(matches!(
            strategy.update(bar(60, 100.0, 310.0, -10.0, 100.0, 0.0), 1_000.0),
            Action::Close {
                price: 0.0,
                fraction: 1.0
            }
        ));
    }

    #[test]
    fn equity_sizing_uses_the_tighter_of_risk_and_margin_limits() {
        assert_eq!(
            HourlyDeltaReversalNq::entry_quantity(10_000.0, 29_000.0, 100.0),
            Some(1.37)
        );
        assert_eq!(
            HourlyDeltaReversalNq::entry_quantity(10_000.0, 7_500.0, 2.0),
            Some(5.33)
        );
    }
}
