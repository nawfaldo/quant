use crate::{
    api::parquet_store::{self, NANOS_PER_SECOND, ParquetStore},
    backtest::{Side, Trade},
    error::ApiError,
};

/// The broker tick table repricing reads. It is not in the Parquet store.
const USTEC_TICKS: &str = "ustec_tick";

const MAX_GAP_MICROS: i64 = 5 * 60 * 1_000_000;
const STOP_SCAN_MICROS: i64 = 5 * 60 * 1_000_000;
const SPREAD: f64 = 0.2;

pub struct Repriced {
    pub trades: Vec<Trade>,
    pub in_window: usize,
    pub total: usize,
    pub strategies: Vec<String>,
}

#[derive(Clone, Copy)]
struct TimeRequest {
    target: i64,
    slot: usize,
    level_index: Option<usize>,
    fill: Option<Fill>,
}

#[derive(Clone, Copy)]
struct Fill {
    timestamp: i64,
    price: f64,
}

struct LevelRequest {
    slot: usize,
    distance: f64,
    long_stop: bool,
    armed_at: Option<i64>,
    level: f64,
    fill: Option<Fill>,
}

struct RepriceState {
    requests: Vec<TimeRequest>,
    levels: Vec<LevelRequest>,
    next_request: usize,
}

impl RepriceState {
    fn new(trades: &[Trade]) -> Self {
        let intrabar_count = trades.iter().filter(|trade| is_intrabar(trade)).count();
        let mut requests = Vec::with_capacity(trades.len() * 2 - intrabar_count);
        let mut levels = Vec::with_capacity(intrabar_count);

        for (index, trade) in trades.iter().enumerate() {
            let level_index = is_intrabar(trade).then_some(levels.len());
            requests.push(TimeRequest {
                target: trade.entry_timestamp * 1_000_000,
                slot: index * 2,
                level_index,
                fill: None,
            });

            if is_intrabar(trade) {
                let raw_entry = non_zero_or(trade.entry_raw, trade.entry_price);
                let raw_exit = non_zero_or(trade.exit_raw, trade.exit_price);
                levels.push(LevelRequest {
                    slot: index * 2 + 1,
                    distance: raw_exit - raw_entry,
                    long_stop: trade.side == Side::Long,
                    armed_at: None,
                    level: 0.0,
                    fill: None,
                });
            } else {
                requests.push(TimeRequest {
                    target: trade.exit_timestamp * 1_000_000,
                    slot: index * 2 + 1,
                    level_index: None,
                    fill: None,
                });
            }
        }

        requests.sort_by_key(|request| request.target);
        Self {
            requests,
            levels,
            next_request: 0,
        }
    }

    fn on_tick(&mut self, timestamp: i64, bid: f64, ask: f64) {
        if bid <= 0.0 || ask <= 0.0 {
            return;
        }
        let mid = (bid + ask) * 0.5;

        while self
            .requests
            .get(self.next_request)
            .is_some_and(|request| request.target <= timestamp)
        {
            let request = &mut self.requests[self.next_request];
            request.fill = Some(Fill {
                timestamp,
                price: mid,
            });

            if let Some(level_index) = request.level_index {
                let level = &mut self.levels[level_index];
                level.level = mid + level.distance;
                level.armed_at = Some(timestamp);
            }
            self.next_request += 1;
        }

        for level in &mut self.levels {
            let Some(armed_at) = level.armed_at else {
                continue;
            };
            if level.fill.is_some() {
                continue;
            }

            let crossed = if level.long_stop {
                mid <= level.level
            } else {
                mid >= level.level
            };
            if crossed || timestamp - armed_at > STOP_SCAN_MICROS {
                level.fill = Some(Fill {
                    timestamp,
                    price: if crossed { mid } else { level.level },
                });
            }
        }
    }

    fn finish(mut self, native: &[Trade]) -> Repriced {
        for level in &mut self.levels {
            if level.armed_at.is_some() && level.fill.is_none() {
                level.fill = Some(Fill {
                    timestamp: level.armed_at.unwrap_or_default(),
                    price: level.level,
                });
            }
        }

        let mut valid = vec![false; native.len() * 2];
        let mut prices = vec![0.0; native.len() * 2];

        for request in self.requests {
            if let Some(fill) = request.fill {
                valid[request.slot] = fill.timestamp - request.target <= MAX_GAP_MICROS;
                prices[request.slot] = fill.price;
            }
        }
        for level in self.levels {
            if let Some(fill) = level.fill {
                valid[level.slot] = true;
                prices[level.slot] = fill.price;
            }
        }

        let trades = native
            .iter()
            .enumerate()
            .filter(|(index, _)| valid[index * 2] && valid[index * 2 + 1])
            .map(|(index, trade)| repriced_trade(trade, prices[index * 2], prices[index * 2 + 1]))
            .collect::<Vec<_>>();

        let mut strategies = native
            .iter()
            .map(|trade| trade.strategy.clone())
            .filter(|strategy| !strategy.is_empty())
            .collect::<Vec<_>>();
        strategies.sort();
        strategies.dedup();

        Repriced {
            in_window: trades.len(),
            total: native.len(),
            trades,
            strategies,
        }
    }
}

pub async fn reprice(store: &ParquetStore, native: &[Trade]) -> Result<Option<Repriced>, ApiError> {
    let windows = coverage_windows(native);
    if windows.is_empty() {
        return Ok(None);
    }

    // NO TABLE, NO REPRICING. `ustec_tick` was a QuestDB table and was not
    // exported, so this reports "not available" rather than failing a backtest.
    // Every caller already handles `None`; the machinery below is left intact so
    // that dropping a `ustec_tick.parquet` into the store revives it unchanged.
    if !store.has_table(USTEC_TICKS) {
        return Ok(None);
    }

    let mut state = RepriceState::new(native);
    // A multi-month combined run can span tens of millions of USTEC ticks, but a
    // fill is valid only during the five minutes after each native entry or
    // exit. ONE SCAN over the whole span rather than a query per window: the
    // Parquet reader skips row groups by their footer statistics, so the cost is
    // set by how much of the file the windows touch, and the windows themselves
    // are then applied row by row -- which is exactly what the OR-of-intervals
    // predicate did.
    let first = windows.first().map(|(from, _)| *from).unwrap_or_default();
    let last = windows.last().map(|(_, to)| *to).unwrap_or_default();
    let mut cursor = 0usize;
    for batch in store.scan(
        USTEC_TICKS,
        &["bid", "ask"],
        Some(first * NANOS_PER_SECOND),
        // `BETWEEN` is inclusive at both ends and `scan` is half-open, so the
        // upper bound opens the second after the last window closes.
        Some((last + 1) * NANOS_PER_SECOND),
    )? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let bids = parquet_store::floats(&batch, "bid")?;
        let asks = parquet_store::floats(&batch, "ask")?;
        for index in 0..stamps.len() {
            // A few thousand rows carry a null bid or ask. They read as zero and
            // are dropped here, as `AND bid > 0 AND ask > 0` dropped them.
            if bids[index] <= 0.0 || asks[index] <= 0.0 {
                continue;
            }
            let second = stamps[index].div_euclid(NANOS_PER_SECOND);
            while cursor < windows.len() && windows[cursor].1 < second {
                cursor += 1;
            }
            let Some((from, to)) = windows.get(cursor) else {
                break;
            };
            if second < *from || second > *to {
                continue;
            }
            state.on_tick(stamps[index].div_euclid(1_000), bids[index], asks[index]);
        }
    }

    let repriced = state.finish(native);
    if repriced.trades.is_empty() {
        Ok(None)
    } else {
        Ok(Some(repriced))
    }
}

fn coverage_windows(trades: &[Trade]) -> Vec<(i64, i64)> {
    let gap_seconds = MAX_GAP_MICROS / 1_000_000;
    let mut targets = Vec::with_capacity(trades.len() * 2);
    for trade in trades {
        targets.push(trade.entry_timestamp);
        if !is_intrabar(trade) {
            targets.push(trade.exit_timestamp);
        }
    }
    targets.sort_unstable();
    targets.dedup();

    let mut windows: Vec<(i64, i64)> = Vec::with_capacity(targets.len());
    for target in targets {
        let end = target.saturating_add(gap_seconds);
        if let Some(last) = windows.last_mut()
            && target <= last.1
        {
            last.1 = last.1.max(end);
        } else {
            windows.push((target, end));
        }
    }
    windows
}

fn is_intrabar(trade: &Trade) -> bool {
    trade.entry_timestamp == trade.exit_timestamp
}

fn non_zero_or(value: f64, fallback: f64) -> f64 {
    if value == 0.0 { fallback } else { value }
}

fn repriced_trade(native: &Trade, entry_mid: f64, exit_mid: f64) -> Trade {
    let entry = apply_spread(entry_mid, native.side == Side::Long);
    let exit = apply_spread(exit_mid, native.side == Side::Short);
    let movement = match native.side {
        Side::Long => exit - entry,
        Side::Short => entry - exit,
    };

    Trade {
        strategy: native.strategy.clone(),
        side: native.side,
        entry_timestamp: native.entry_timestamp,
        exit_timestamp: native.exit_timestamp,
        entry_price: entry,
        exit_price: exit,
        pnl: movement * native.quantity,
        quantity: native.quantity,
        entry_raw: entry_mid,
        exit_raw: exit_mid,
    }
}

fn apply_spread(mid: f64, buying: bool) -> f64 {
    let half_spread = SPREAD / 2.0;
    if buying {
        mid + half_spread
    } else {
        mid - half_spread
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backtest::iso_day;

    fn trade(entry: i64, exit: i64, side: Side, raw_exit: f64) -> Trade {
        Trade {
            strategy: String::new(),
            side,
            entry_timestamp: entry,
            exit_timestamp: exit,
            entry_price: 100.0,
            exit_price: raw_exit,
            pnl: 0.0,
            quantity: 2.0,
            entry_raw: 100.0,
            exit_raw: raw_exit,
        }
    }

    #[test]
    fn time_fills_use_first_tick_and_fx_spread() {
        let native = [trade(1, 2, Side::Long, 101.0)];
        let mut state = RepriceState::new(&native);
        state.on_tick(1_000_000, 200.0, 200.0);
        state.on_tick(2_000_000, 201.0, 201.0);
        let result = state.finish(&native);

        assert_eq!(result.trades.len(), 1);
        assert_eq!(result.trades[0].entry_price, 200.1);
        assert_eq!(result.trades[0].exit_price, 200.9);
        assert!((result.trades[0].pnl - 1.6).abs() < 1e-9);
    }

    #[test]
    fn intrabar_stop_waits_for_level_crossing() {
        let native = [trade(1, 1, Side::Long, 99.0)];
        let mut state = RepriceState::new(&native);
        state.on_tick(1_000_000, 200.0, 200.0);
        state.on_tick(2_000_000, 199.5, 199.5);
        state.on_tick(3_000_000, 198.9, 198.9);
        let result = state.finish(&native);

        assert_eq!(result.trades.len(), 1);
        assert_eq!(result.trades[0].entry_raw, 200.0);
        assert_eq!(result.trades[0].exit_raw, 198.9);
    }

    #[test]
    fn coverage_windows_follow_trade_times_without_a_stale_data_cutoff() {
        let july_30 = iso_day("2026-07-30").expect("valid day") * 86_400;
        let native = [trade(july_30, july_30, Side::Long, 99.0)];

        assert_eq!(coverage_windows(&native), vec![(july_30, july_30 + 5 * 60)]);
    }

    #[test]
    fn coverage_windows_merge_overlapping_fill_ranges() {
        let native = [
            trade(1_000, 1_600, Side::Long, 101.0),
            trade(1_200, 2_000, Side::Short, 99.0),
        ];

        assert_eq!(
            coverage_windows(&native),
            vec![(1_000, 1_500), (1_600, 1_900), (2_000, 2_300)]
        );
    }
}
