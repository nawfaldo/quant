use crate::{
    backtest::{Side, Trade, iso_day},
    error::ApiError,
    questdb::QuestDb,
};

const COVERAGE_FROM: &str = "2026-01-01";
const COVERAGE_LAST: &str = "2026-06-26";
const MAX_GAP_MICROS: i64 = 5 * 60 * 1_000_000;
const STOP_SCAN_MICROS: i64 = 5 * 60 * 1_000_000;
const SPREAD: f64 = 0.2;

pub struct Repriced {
    pub trades: Vec<Trade>,
    pub in_window: usize,
    pub total: usize,
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

        Repriced {
            in_window: trades.len(),
            total: native.len(),
            trades,
        }
    }
}

pub async fn reprice(questdb: &QuestDb, native: &[Trade]) -> Result<Option<Repriced>, ApiError> {
    let Some((from, to)) = coverage_window(native) else {
        return Ok(None);
    };

    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,BID,ASK FROM fx_nq_ticks ",
            "WHERE timestamp >= '{from}' AND timestamp < '{to}' ",
            "ORDER BY timestamp",
        ),
        from = from,
        to = to,
    );
    let mut state = RepriceState::new(native);
    questdb
        .stream_csv(&sql, |row| {
            let timestamp = parse_field::<i64>(&row, 0)?;
            let bid = parse_field::<f64>(&row, 1)?;
            let ask = parse_field::<f64>(&row, 2)?;
            state.on_tick(timestamp, bid, ask);
            Ok(())
        })
        .await?;

    let repriced = state.finish(native);
    if repriced.trades.is_empty() {
        Ok(None)
    } else {
        Ok(Some(repriced))
    }
}

fn coverage_window(trades: &[Trade]) -> Option<(String, String)> {
    let first_trade_day = trades
        .iter()
        .map(|trade| trade.entry_timestamp.div_euclid(86_400))
        .min()?;
    let last_trade_day = trades
        .iter()
        .map(|trade| trade.exit_timestamp.div_euclid(86_400))
        .max()?;
    let coverage_from = iso_day(COVERAGE_FROM)?;
    let coverage_last = iso_day(COVERAGE_LAST)?;
    let first = first_trade_day.max(coverage_from);
    let last = last_trade_day.min(coverage_last);

    (first <= last).then(|| (format_day(first), format_day(last + 1)))
}

fn format_day(day: i64) -> String {
    let z = day + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 }.div_euclid(146_097);
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = if month_prime < 10 {
        month_prime + 3
    } else {
        month_prime - 9
    };
    let year = year + i64::from(month <= 2);

    format!("{year:04}-{month:02}-{day:02}")
}

fn parse_field<T>(row: &csv::StringRecord, index: usize) -> Result<T, ApiError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    row.get(index)
        .ok_or_else(|| ApiError::QuestDb(format!("missing FX CSV column {index}")))?
        .parse()
        .map_err(|error| ApiError::QuestDb(format!("invalid FX CSV column {index}: {error}")))
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

    fn trade(entry: i64, exit: i64, side: Side, raw_exit: f64) -> Trade {
        Trade {
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
}
