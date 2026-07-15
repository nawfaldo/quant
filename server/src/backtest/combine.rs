use super::{
    data::format_day,
    engine::{EngineConfig, RunResult},
    tuning::report::{Drawdowns, montecarlo, report},
    types::{Instrument, Side},
};
use crate::{error::ApiError, questdb::QuestDb};
use serde_json::Value;

pub async fn combine(
    questdb: &QuestDb,
    sources: &[crate::database::CombineSource],
    initial: f64,
    from: Option<i64>,
    to: Option<i64>,
) -> Result<RunResult, ApiError> {
    let mut result = combine_realized(sources, initial, from, to);
    if let Ok(drawdowns) = mark_to_market(questdb, sources, initial).await {
        apply_drawdowns(&mut result.body, drawdowns);
    }
    Ok(result)
}

pub fn combine_realized(
    sources: &[crate::database::CombineSource],
    initial: f64,
    from: Option<i64>,
    to: Option<i64>,
) -> RunResult {
    let mut trades = Vec::new();
    for source in sources {
        trades.extend(
            source
                .trades
                .iter()
                .filter(|t| {
                    from.is_none_or(|d| t.exit_timestamp.div_euclid(86400) >= d)
                        && to.is_none_or(|d| t.entry_timestamp.div_euclid(86400) <= d)
                })
                .cloned(),
        );
    }
    trades.sort_by_key(|t| t.exit_timestamp);
    let first = trades.first().map(|t| t.entry_timestamp).unwrap_or(0);
    let last = trades.last().map(|t| t.exit_timestamp).unwrap_or(0);
    let mut equity = initial;
    let mut peak = initial;
    let mut peak_day = first.div_euclid(86400);
    let mut max_dd = 0.0f64;
    let mut max_dollars = 0.0;
    let mut peak_at_max = peak_day;
    let mut trough = peak_day;
    let mut sum = 0.0;
    let mut sum_dollars = 0.0;
    let mut current_day = peak_day;
    let mut day_peak = initial;
    let mut day_max = 0.0f64;
    let mut day_max_dollars = 0.0;
    let mut max_idd = 0.0f64;
    let mut max_idd_dollars = 0.0;
    let mut max_idd_day = current_day;
    let mut idd_sum = 0.0;
    let mut idd_dollars_sum = 0.0;
    let mut idd_days = 0usize;
    for trade in &trades {
        let day = trade.exit_timestamp.div_euclid(86400);
        if day != current_day {
            idd_sum += day_max;
            idd_dollars_sum += day_max_dollars;
            idd_days += 1;
            current_day = day;
            day_peak = equity;
            day_max = 0.0;
            day_max_dollars = 0.0;
        }
        equity += trade.pnl;
        if equity > peak {
            peak = equity;
            peak_day = day;
        }
        let dollars = peak - equity;
        let pct = if peak > 0.0 {
            dollars / peak * 100.0
        } else {
            0.0
        };
        if pct > max_dd {
            max_dd = pct;
            max_dollars = dollars;
            peak_at_max = peak_day;
            trough = day;
        }
        sum += pct;
        sum_dollars += dollars;
        if equity > day_peak {
            day_peak = equity;
        }
        let day_dollars = day_peak - equity;
        let day_pct = if day_peak > 0.0 {
            day_dollars / day_peak * 100.0
        } else {
            0.0
        };
        if day_pct > day_max {
            day_max = day_pct;
            day_max_dollars = day_dollars;
        }
        if day_pct > max_idd {
            max_idd = day_pct;
            max_idd_dollars = day_dollars;
            max_idd_day = day;
        }
    }
    if !trades.is_empty() {
        idd_sum += day_max;
        idd_dollars_sum += day_max_dollars;
        idd_days += 1;
    }
    let symbol = unique_label(sources.iter().map(|s| s.symbol.as_str()), "combined");
    let instrument = unique_label(sources.iter().map(|s| s.instrument.as_str()), "combined");
    let strategy = sources
        .iter()
        .map(|s| format!("{} (#{})", strategy_display(&s.strategy), s.id))
        .collect::<Vec<_>>()
        .join(" + ");
    let cfg = EngineConfig {
        initial,
        instrument: Instrument::Forex,
        symbol: symbol.clone(),
        spread: 0.0,
        slippage: 0.0,
        commission: 0.0,
        start_day: first.div_euclid(86400),
        sizing: None,
    };
    let count = trades.len().max(1) as f64;
    let days = idd_days.max(1) as f64;
    let mut body = report(
        &trades,
        &cfg,
        first,
        last,
        equity,
        Drawdowns {
            max_dd,
            max_dd_dollars: max_dollars,
            max_dd_peak: peak_at_max,
            max_dd_trough: trough,
            avg_dd: sum / count,
            avg_dd_dollars: sum_dollars / count,
            max_idd,
            max_idd_dollars,
            max_idd_day,
            avg_idd: idd_sum / days,
            avg_idd_dollars: idd_dollars_sum / days,
        },
    );
    body["instrument"] = Value::String(instrument);
    body["strategy"] = Value::String(strategy);
    body["montecarlo"] = montecarlo(&trades, initial);
    RunResult { body, trades }
}

#[derive(Clone, Copy)]
struct Event {
    ts: i64,
    dataset: Option<usize>,
    amount: f64,
    entry_value: f64,
    pnl: f64,
}

struct Dataset {
    table: String,
    from_day: i64,
    to_day: i64,
    bars: Vec<(i64, f64)>,
}

async fn mark_to_market(
    questdb: &QuestDb,
    sources: &[crate::database::CombineSource],
    initial: f64,
) -> Result<Drawdowns, ApiError> {
    let mut datasets: Vec<Dataset> = Vec::new();
    let mut source_datasets = Vec::with_capacity(sources.len());

    for source in sources {
        let table = source_table(source);
        let dataset = if let Some(table) = table {
            let from_day = source
                .trades
                .iter()
                .map(|trade| trade.entry_timestamp.div_euclid(86_400))
                .min();
            let to_day = source
                .trades
                .iter()
                .map(|trade| trade.exit_timestamp.div_euclid(86_400))
                .max();
            match (from_day, to_day) {
                (Some(from_day), Some(to_day)) => {
                    if let Some(index) = datasets.iter().position(|data| data.table == table) {
                        datasets[index].from_day = datasets[index].from_day.min(from_day);
                        datasets[index].to_day = datasets[index].to_day.max(to_day);
                        Some(index)
                    } else {
                        let index = datasets.len();
                        datasets.push(Dataset {
                            table,
                            from_day,
                            to_day,
                            bars: Vec::new(),
                        });
                        Some(index)
                    }
                }
                _ => None,
            }
        } else {
            None
        };
        source_datasets.push(dataset);
    }

    if datasets.is_empty() {
        return Err(ApiError::Internal("no mark-to-market source table".into()));
    }

    for dataset in &mut datasets {
        let from = format_day(dataset.from_day);
        let to = format_day(dataset.to_day + 1);
        let sql = format!(
            concat!(
                "SELECT cast(timestamp as long) ts,close FROM {table} ",
                "WHERE timestamp >= '{from}' AND timestamp < '{to}' ORDER BY timestamp",
            ),
            table = dataset.table,
            from = from,
            to = to,
        );
        dataset.bars = questdb
            .csv(&sql)
            .await?
            .into_iter()
            .map(|row| {
                let ts = row
                    .get(0)
                    .ok_or_else(|| ApiError::QuestDb("missing MTM timestamp".into()))?
                    .parse::<i64>()
                    .map_err(|_| ApiError::QuestDb("invalid MTM timestamp".into()))?
                    / 1_000_000;
                let close = row
                    .get(1)
                    .ok_or_else(|| ApiError::QuestDb("missing MTM close".into()))?
                    .parse::<f64>()
                    .map_err(|_| ApiError::QuestDb("invalid MTM close".into()))?;
                Ok((ts, close))
            })
            .collect::<Result<Vec<_>, ApiError>>()?;
    }

    let mut opens = Vec::new();
    let mut closes = Vec::new();
    for (source_index, source) in sources.iter().enumerate() {
        let dataset = source_datasets[source_index];
        for trade in &source.trades {
            let sign = if trade.side == Side::Long { 1.0 } else { -1.0 };
            let amount = sign * trade.quantity;
            opens.push(Event {
                ts: trade.entry_timestamp,
                dataset,
                amount,
                entry_value: amount * trade.entry_price,
                pnl: 0.0,
            });
            closes.push(Event {
                ts: trade.exit_timestamp,
                dataset,
                amount,
                entry_value: amount * trade.entry_price,
                pnl: trade.pnl,
            });
        }
    }
    opens.sort_by_key(|event| event.ts);
    closes.sort_by_key(|event| event.ts);

    let count = datasets.len();
    let mut amount = vec![0.0; count];
    let mut entry_value = vec![0.0; count];
    let mut bar_positions = vec![0usize; count];
    let mut last_close = vec![0.0; count];
    let mut seen = vec![false; count];
    let mut realized = 0.0;
    let mut open_position = 0usize;
    let mut close_position = 0usize;

    let mut peak = initial;
    let mut peak_day = 0;
    let mut maximum = Drawdowns {
        max_dd: 0.0,
        max_dd_dollars: 0.0,
        max_dd_peak: 0,
        max_dd_trough: 0,
        avg_dd: 0.0,
        avg_dd_dollars: 0.0,
        max_idd: 0.0,
        max_idd_dollars: 0.0,
        max_idd_day: 0,
        avg_idd: 0.0,
        avg_idd_dollars: 0.0,
    };
    let mut drawdown_count = 0usize;
    let mut current_day = None;
    let mut day_peak = initial;
    let mut day_maximum = 0.0;
    let mut day_maximum_dollars = 0.0;
    let mut intraday_days = 0usize;

    loop {
        let mut timestamp = opens.get(open_position).map(|event| event.ts);
        if let Some(value) = closes.get(close_position).map(|event| event.ts) {
            timestamp = Some(timestamp.map_or(value, |current| current.min(value)));
        }
        for (index, dataset) in datasets.iter().enumerate() {
            if let Some((value, _)) = dataset.bars.get(bar_positions[index]) {
                timestamp = Some(timestamp.map_or(*value, |current| current.min(*value)));
            }
        }
        let Some(timestamp) = timestamp else { break };

        while opens
            .get(open_position)
            .is_some_and(|event| event.ts == timestamp)
        {
            let event = opens[open_position];
            if let Some(index) = event.dataset {
                amount[index] += event.amount;
                entry_value[index] += event.entry_value;
            }
            open_position += 1;
        }
        while closes
            .get(close_position)
            .is_some_and(|event| event.ts == timestamp)
        {
            let event = closes[close_position];
            realized += event.pnl;
            if let Some(index) = event.dataset {
                amount[index] -= event.amount;
                entry_value[index] -= event.entry_value;
            }
            close_position += 1;
        }
        for (index, dataset) in datasets.iter().enumerate() {
            while dataset
                .bars
                .get(bar_positions[index])
                .is_some_and(|(ts, _)| *ts == timestamp)
            {
                last_close[index] = dataset.bars[bar_positions[index]].1;
                seen[index] = true;
                bar_positions[index] += 1;
            }
        }

        let mut equity = initial + realized;
        for index in 0..count {
            if seen[index] {
                equity += last_close[index] * amount[index] - entry_value[index];
            }
        }
        let day = timestamp.div_euclid(86_400);
        if equity > peak {
            peak = equity;
            peak_day = day;
        }
        let dollars = peak - equity;
        let percent = if peak > 0.0 {
            dollars / peak * 100.0
        } else {
            0.0
        };
        if percent > maximum.max_dd {
            maximum.max_dd = percent;
            maximum.max_dd_dollars = dollars;
            maximum.max_dd_peak = peak_day;
            maximum.max_dd_trough = day;
        }
        maximum.avg_dd += percent;
        maximum.avg_dd_dollars += dollars;
        drawdown_count += 1;

        if current_day != Some(day) {
            if current_day.is_some() {
                maximum.avg_idd += day_maximum;
                maximum.avg_idd_dollars += day_maximum_dollars;
                intraday_days += 1;
            }
            current_day = Some(day);
            day_peak = equity;
            day_maximum = 0.0;
            day_maximum_dollars = 0.0;
        }
        day_peak = day_peak.max(equity);
        let day_dollars = day_peak - equity;
        let day_percent = if day_peak > 0.0 {
            day_dollars / day_peak * 100.0
        } else {
            0.0
        };
        if day_percent > day_maximum {
            day_maximum = day_percent;
            day_maximum_dollars = day_dollars;
        }
        if day_percent > maximum.max_idd {
            maximum.max_idd = day_percent;
            maximum.max_idd_dollars = day_dollars;
            maximum.max_idd_day = day;
        }
    }

    if current_day.is_some() {
        maximum.avg_idd += day_maximum;
        maximum.avg_idd_dollars += day_maximum_dollars;
        intraday_days += 1;
    }
    if drawdown_count > 0 {
        maximum.avg_dd /= drawdown_count as f64;
        maximum.avg_dd_dollars /= drawdown_count as f64;
    }
    if intraday_days > 0 {
        maximum.avg_idd /= intraday_days as f64;
        maximum.avg_idd_dollars /= intraday_days as f64;
    }
    Ok(maximum)
}

fn apply_drawdowns(body: &mut Value, drawdowns: Drawdowns) {
    body["max_drawdown"] = Value::from(round4(drawdowns.max_dd));
    body["max_drawdown_dollars"] = Value::from(round2(drawdowns.max_dd_dollars));
    body["max_drawdown_peak_date"] = Value::from(format_day(drawdowns.max_dd_peak));
    body["max_drawdown_trough_date"] = Value::from(format_day(drawdowns.max_dd_trough));
    body["avg_drawdown"] = Value::from(round4(drawdowns.avg_dd));
    body["avg_drawdown_dollars"] = Value::from(round2(drawdowns.avg_dd_dollars));
    body["max_intraday_drawdown"] = Value::from(round4(drawdowns.max_idd));
    body["max_intraday_drawdown_dollars"] = Value::from(round2(drawdowns.max_idd_dollars));
    body["max_intraday_drawdown_date"] = Value::from(format_day(drawdowns.max_idd_day));
    body["avg_intraday_drawdown"] = Value::from(round4(drawdowns.avg_idd));
    body["avg_intraday_drawdown_dollars"] = Value::from(round2(drawdowns.avg_idd_dollars));
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn source_table(source: &crate::database::CombineSource) -> Option<String> {
    let symbol = match source.symbol.trim().to_ascii_lowercase().as_str() {
        "nq" | "nasdaq 100 e-mini" => "nq",
        "es" | "s&p 500 e-mini" => "es",
        _ => return None,
    };
    let timeframe = match source.strategy.as_str() {
        "NIGHT_DRIFT" | "EU_OPEN" | "NOISE_MOMENTUM" => "1m",
        "INTRADAY_MOM" | "ZARA_MOMENTUM" => "30m",
        "BUY_HOLD" => "1d",
        _ => return None,
    };
    Some(format!("{symbol}_{timeframe}"))
}
fn unique_label<'a>(items: impl Iterator<Item = &'a str>, fallback: &str) -> String {
    let mut out = Vec::new();
    for item in items {
        if !out.contains(&item) {
            out.push(item);
        }
    }
    if out.is_empty() {
        fallback.into()
    } else {
        out.join(" + ")
    }
}
fn strategy_display(value: &str) -> &str {
    match value {
        "NIGHT_DRIFT" | "EU_OPEN" => "Night Drift",
        "NOISE_MOMENTUM" => "Noise Momentum",
        other => other,
    }
}
