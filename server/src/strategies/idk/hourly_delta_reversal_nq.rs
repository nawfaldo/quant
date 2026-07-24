use crate::backtest::types::{Action, Bar, Side, Strategy};

// Minute-resolution RTH grid-search defaults from the locally available NQ
// history (2026-05-27 through 2026-07-23), including 0.2 spread.
const ENTRY_RISK_FRACTION: f64 = 0.005;
const LONG_MARGIN_REQUIREMENT: f64 = 0.25;
const QUANTITY_STEP: f64 = 0.01;
const MARKET_OPEN_MINUTE: usize = 9 * 60 + 30;
const MARKET_CLOSE_MINUTE: usize = 16 * 60;
const THURSDAY: i64 = 3;
const BUY_MIN_DELTA: f64 = 50.0;
const BUY_STOP: f64 = 40.0;
const BUY_TARGET: f64 = 50.0;
const SELL_MIN_DELTA: f64 = 150.0;
const SELL_STOP: f64 = 40.0;
const SELL_TARGET: f64 = 50.0;

#[derive(Clone, Copy)]
struct OpenPosition {
    id: u64,
    side: Side,
    entry: f64,
    stop: f64,
    target: f64,
}

/// Buys the open of a new hour when the completed one-hour candle was red
/// despite positive aggressor volume delta. Minute bars retain sensible
/// intrahour stop/target ordering while the signal itself remains hourly.
pub(crate) struct HourlyDeltaReversalNq {
    hour: Option<i64>,
    hour_open: f64,
    hour_close: f64,
    hour_delta: f64,
    hour_depth_events: u64,
    next_position_id: u64,
    positions: Vec<OpenPosition>,
}

impl Default for HourlyDeltaReversalNq {
    fn default() -> Self {
        Self {
            hour: None,
            hour_open: 0.0,
            hour_close: 0.0,
            hour_delta: 0.0,
            hour_depth_events: 0,
            next_position_id: 1,
            positions: Vec::new(),
        }
    }
}

impl HourlyDeltaReversalNq {
    fn completed_signal(&self, next_hour: i64, entry_minute: usize) -> Option<Side> {
        let Some(_) = self.hour.filter(|hour| *hour + 1 == next_hour) else {
            return None;
        };
        let day = next_hour.div_euclid(24);
        let weekday = (day + 3).rem_euclid(7);
        if !(MARKET_OPEN_MINUTE..MARKET_CLOSE_MINUTE).contains(&entry_minute)
            || weekday == THURSDAY
            || self.hour_depth_events == 0
        {
            return None;
        }
        if self.hour_close < self.hour_open && self.hour_delta >= BUY_MIN_DELTA {
            Some(Side::Long)
        } else if self.hour_close > self.hour_open && self.hour_delta <= -SELL_MIN_DELTA {
            Some(Side::Short)
        } else {
            None
        }
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
        self.hour_depth_events = bar.depth_events;
    }

    fn accumulate(&mut self, bar: Bar) {
        self.hour_close = bar.close;
        self.hour_delta += bar.volume_delta;
        self.hour_depth_events = self.hour_depth_events.saturating_add(bar.depth_events);
    }

    fn actions(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        let hour = bar.ts.div_euclid(3_600);
        let minute = bar.ts.rem_euclid(86_400) as usize / 60;
        if minute >= MARKET_CLOSE_MINUTE || minute < MARKET_OPEN_MINUTE {
            self.positions.clear();
        }
        let entry_side = if self.hour == Some(hour) {
            self.accumulate(bar);
            None
        } else {
            let signal = self.completed_signal(hour, minute);
            self.start_hour(hour, bar);
            signal
        };

        let mut actions = Vec::new();
        self.positions.retain(|position| {
            let exit = match position.side {
                Side::Long if bar.low <= position.entry - position.stop => {
                    Some(bar.open.min(position.entry - position.stop))
                }
                Side::Long if bar.high >= position.entry + position.target => {
                    Some(bar.open.max(position.entry + position.target))
                }
                Side::Short if bar.high >= position.entry + position.stop => {
                    Some(bar.open.max(position.entry + position.stop))
                }
                Side::Short if bar.low <= position.entry - position.target => {
                    Some(bar.open.min(position.entry - position.target))
                }
                _ => None,
            };
            if let Some(price) = exit {
                actions.push(Action::ClosePosition {
                    id: position.id,
                    price,
                });
                false
            } else {
                true
            }
        });

        if let Some(side) = entry_side {
            let (stop, target) = match side {
                Side::Long => (BUY_STOP, BUY_TARGET),
                Side::Short => (SELL_STOP, SELL_TARGET),
            };
            if let Some(quantity) = Self::entry_quantity(equity, bar.open, stop) {
                let id = self.next_position_id;
                self.next_position_id += 1;
                self.positions.push(OpenPosition {
                    id,
                    side,
                    entry: bar.open,
                    stop,
                    target,
                });
                actions.push(Action::EnterPosition {
                    id,
                    side,
                    price: bar.open,
                    quantity,
                });
            }
        }
        actions
    }
}

impl Strategy for HourlyDeltaReversalNq {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        self.actions(bar, equity)
            .into_iter()
            .next()
            .unwrap_or(Action::Hold)
    }

    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.actions(bar, equity)
    }

    fn discard(&mut self, action: Action) {
        if let Action::EnterPosition { id, .. } = action {
            self.positions.retain(|position| position.id != id);
        }
    }

    fn session_end_minute(&self) -> Option<usize> {
        Some(MARKET_CLOSE_MINUTE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(minute: i64, open: f64, high: f64, low: f64, close: f64, delta: f64) -> Bar {
        Bar {
            // 1970-01-05 was a Monday.
            ts: (4 * 24 * 60 + minute) * 60,
            open,
            high,
            low,
            close,
            volume: delta.abs(),
            volume_delta: delta,
            depth_events: 1,
            vix: 18.0,
            order_flow: Default::default(),
        }
    }

    #[test]
    fn buys_next_hour_after_red_candle_with_positive_delta() {
        let mut strategy = HourlyDeltaReversalNq::default();
        assert!(matches!(
            strategy.update(bar(9 * 60, 100.0, 101.0, 98.0, 99.0, 100.0), 1_000.0),
            Action::Hold
        ));
        assert!(matches!(
            strategy.update(bar(9 * 60 + 1, 99.0, 100.0, 97.0, 98.0, 50.0), 1_000.0),
            Action::Hold
        ));
        assert!(matches!(
            strategy.update(bar(10 * 60, 98.0, 99.0, 97.0, 98.5, 0.0), 1_000.0),
            Action::EnterPosition {
                id: 1,
                side: Side::Long,
                price: 98.0,
                quantity: 0.12
            }
        ));
    }

    #[test]
    fn sells_next_hour_after_green_candle_with_negative_delta() {
        let mut strategy = HourlyDeltaReversalNq::default();
        strategy.update(bar(9 * 60, 100.0, 103.0, 99.0, 102.0, -200.0), 10_000.0);
        assert!(matches!(
            strategy.update(bar(10 * 60, 102.0, 103.0, 101.0, 101.5, 0.0), 10_000.0),
            Action::EnterPosition {
                id: 1,
                side: Side::Short,
                price: 102.0,
                quantity: 1.25
            }
        ));
    }

    #[test]
    fn permits_overlapping_hourly_positions() {
        let mut strategy = HourlyDeltaReversalNq::default();
        strategy.update(bar(9 * 60, 100.0, 103.0, 99.0, 102.0, -200.0), 10_000.0);
        assert_eq!(
            strategy
                .update_all(bar(10 * 60, 102.0, 103.0, 101.0, 102.5, -200.0), 10_000.0)
                .len(),
            1
        );
        let actions = strategy.update_all(bar(11 * 60, 102.5, 103.0, 102.0, 102.4, 0.0), 10_000.0);
        assert!(actions.iter().any(|action| matches!(
            action,
            Action::EnterPosition {
                id: 2,
                side: Side::Short,
                ..
            }
        )));
        assert_eq!(strategy.positions.len(), 2);
    }

    #[test]
    fn requires_depth_coverage_and_contiguous_hours() {
        let mut strategy = HourlyDeltaReversalNq::default();
        let mut signal = bar(9 * 60, 100.0, 101.0, 98.0, 99.0, 100.0);
        signal.depth_events = 0;
        strategy.update(signal, 1_000.0);
        assert!(matches!(
            strategy.update(bar(10 * 60, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));

        strategy.update(bar(11 * 60, 100.0, 101.0, 98.0, 99.0, 200.0), 1_000.0);
        assert!(matches!(
            strategy.update(bar(13 * 60, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));
    }

    #[test]
    fn rejects_closed_session_signals() {
        let mut closed = HourlyDeltaReversalNq::default();
        closed.update(bar(16 * 60, 100.0, 101.0, 98.0, 99.0, 200.0), 1_000.0);
        assert!(matches!(
            closed.update(bar(17 * 60, 99.0, 100.0, 98.0, 99.5, 0.0), 1_000.0),
            Action::Hold
        ));
    }

    #[test]
    fn rejects_thursday_signals() {
        let mut strategy = HourlyDeltaReversalNq::default();
        let mut thursday_signal = bar(9 * 60, 100.0, 101.0, 98.0, 99.0, 100.0);
        thursday_signal.ts = 9 * 60 * 60;
        strategy.update(thursday_signal, 1_000.0);
        let mut thursday_entry = bar(10 * 60, 99.0, 100.0, 98.0, 99.5, 0.0);
        thursday_entry.ts = 10 * 60 * 60;
        assert!(matches!(
            strategy.update(thursday_entry, 1_000.0),
            Action::Hold
        ));
    }

    #[test]
    fn stop_is_checked_before_target_for_conservative_ambiguous_bars() {
        let mut strategy = HourlyDeltaReversalNq {
            positions: vec![OpenPosition {
                id: 1,
                side: Side::Long,
                entry: 100.0,
                stop: 100.0,
                target: 200.0,
            }],
            ..HourlyDeltaReversalNq::default()
        };
        assert!(matches!(
            strategy.update(bar(10 * 60, 100.0, 310.0, -10.0, 100.0, 0.0), 1_000.0),
            Action::ClosePosition { id: 1, price: 0.0 }
        ));
    }

    #[test]
    fn equity_sizing_uses_the_tighter_of_risk_and_margin_limits() {
        assert_eq!(
            HourlyDeltaReversalNq::entry_quantity(10_000.0, 29_000.0, 100.0),
            Some(0.5)
        );
        assert_eq!(
            HourlyDeltaReversalNq::entry_quantity(10_000.0, 7_500.0, 2.0),
            Some(5.33)
        );
    }
}
