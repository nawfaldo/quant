use super::{
    data::format_day,
    drawdown::DrawdownTracker,
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
    let mut drawdowns = DrawdownTracker::new(initial, first.div_euclid(86_400));
    for trade in &trades {
        let day = trade.exit_timestamp.div_euclid(86400);
        equity += trade.pnl;
        drawdowns.observe(equity, day);
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
    let mut body = report(&trades, &cfg, first, last, equity, drawdowns.finish());
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

    let start_day = opens
        .first()
        .map(|event| event.ts.div_euclid(86_400))
        .unwrap_or(0);
    let mut drawdowns = DrawdownTracker::new(initial, start_day);

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
        drawdowns.observe(equity, day);
    }
    Ok(drawdowns.finish())
}

fn apply_drawdowns(body: &mut Value, drawdowns: Drawdowns) {
    body["max_drawdown"] = Value::from(round4(drawdowns.max_dd));
    body["max_drawdown_dollars"] = Value::from(round2(drawdowns.max_dd_dollars));
    body["max_drawdown_peak_date"] = Value::from(format_day(drawdowns.max_dd_peak));
    body["max_drawdown_trough_date"] = Value::from(format_day(drawdowns.max_dd_trough));
    body["avg_drawdown"] = Value::from(round4(drawdowns.avg_dd));
    body["avg_drawdown_dollars"] = Value::from(round2(drawdowns.avg_dd_dollars));
    body["avg_drawdown_time_days"] = Value::from(round2(drawdowns.avg_dd_time_days));
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
        "NIGHT_DRIFT" | "NIGHT_DRIFT_2" | "EU_OPEN" | "NOISE_MOMENTUM" | "NOISE_MOMENTUM_2" => "1m",
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
        "NIGHT_DRIFT_2" => "Night Drift 2",
        "NOISE_MOMENTUM" => "Noise Momentum",
        "NOISE_MOMENTUM_2" => "Noise Momentum 2",
        other => other,
    }
}
