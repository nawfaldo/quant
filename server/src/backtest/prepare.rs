use super::{
    data::{parse_iso_days, valid_date},
    engine::{EngineConfig, RunResult, execute},
    request::RunRequest,
    types::{Bar, Instrument, OrderFlowFeatures},
};
use crate::{
    database::EnvironmentCosts,
    error::ApiError,
    questdb::QuestDb,
    strategies::idk::{PreferredData, preferred_data},
};
use std::collections::BTreeMap;

pub async fn run(
    questdb: &QuestDb,
    request: &RunRequest,
    costs: EnvironmentCosts,
) -> Result<RunResult, ApiError> {
    let prepared = prepare(questdb, request, costs).await?;
    execute(&prepared, request)
}

pub struct PreparedRun {
    pub(crate) bars: Vec<Bar>,
    pub(crate) engine: EngineConfig,
}

pub async fn prepare(
    questdb: &QuestDb,
    request: &RunRequest,
    costs: EnvironmentCosts,
) -> Result<PreparedRun, ApiError> {
    let symbol = request.symbol.to_ascii_lowercase();
    if !matches!(symbol.as_str(), "nq" | "es") {
        return Err(ApiError::BadRequest("unknown symbol".into()));
    }
    if !matches!(
        request.strategy.as_str(),
        "Hourly Delta Reversal NQ"
            | "Hourly Delta Reversal ES"
            | "Night Drift"
            | "Night Drift 2"
            | "Noise Momentum"
            | "Noise Momentum 2"
    ) {
        return Err(ApiError::BadRequest("unknown strategy".into()));
    }
    let expected_symbol = match request.strategy.as_str() {
        "Hourly Delta Reversal ES" => "es",
        _ => "nq",
    };
    if symbol != expected_symbol {
        return Err(ApiError::BadRequest(format!(
            "{} only supports {}",
            request.strategy,
            expected_symbol.to_ascii_uppercase()
        )));
    }
    if !valid_date(&request.from_date) || !valid_date(&request.to_date) {
        return Err(ApiError::BadRequest("invalid date range".into()));
    }
    let instrument = Instrument::parse(&request.instrument)
        .ok_or_else(|| ApiError::BadRequest("unknown instrument".into()))?;
    let valid_instrument = matches!(instrument, Instrument::Forex);
    if !valid_instrument {
        return Err(ApiError::BadRequest(format!(
            "{} only supports {}",
            request.strategy, "the Forex instrument",
        )));
    }
    let source = preferred_data(&request.strategy)
        .ok_or_else(|| ApiError::BadRequest("strategy has no market-data preference".into()))?;
    let with_vix = matches!(request.strategy.as_str(), "Night Drift" | "Night Drift 2");
    let bars = load_bars(
        questdb,
        &symbol,
        &request.from_date,
        &request.to_date,
        with_vix,
        source,
    )
    .await?;
    if bars.is_empty() {
        return Err(ApiError::QuestDb("no data in requested range".into()));
    }
    let start_day = parse_iso_days(&request.from_date)
        .ok_or_else(|| ApiError::BadRequest("invalid from date".into()))?;
    let balance = request.balance()?;
    Ok(PreparedRun {
        bars,
        engine: EngineConfig {
            initial: balance,
            instrument,
            symbol,
            spread: costs.spread,
            slippage: costs.slippage,
            commission: costs.commission,
            start_day,
            sizing: None,
        },
    })
}

async fn load_bars(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
    with_vix: bool,
    source: PreferredData,
) -> Result<Vec<Bar>, ApiError> {
    match source {
        PreferredData::Ohlcv => load_ohlcv_bars(questdb, symbol, from, to, with_vix).await,
        PreferredData::Bookmap => load_bookmap_bars(questdb, symbol, from, to, with_vix).await,
        PreferredData::Combined => {
            let mut merged: BTreeMap<i64, Bar> =
                load_ohlcv_bars(questdb, symbol, from, to, with_vix)
                    .await?
                    .into_iter()
                    .map(|bar| (bar.ts, bar))
                    .collect();

            // A Bookmap minute is more granular and intentionally replaces an
            // OHLCV minute at the same timestamp. Missing Bookmap minutes keep
            // their normal OHLCV bar.
            match load_bookmap_bars(questdb, symbol, from, to, with_vix).await {
                Ok(bookmap) => {
                    for bar in bookmap {
                        merged.insert(bar.ts, bar);
                    }
                }
                Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => {}
                Err(error) => return Err(error),
            }
            Ok(merged.into_values().collect())
        }
        PreferredData::OrderFlow => {
            let mut bars = load_order_flow_bars(questdb, symbol, from, to).await?;
            attach_hourly_vix(questdb, to, &mut bars).await?;
            Ok(bars)
        }
    }
}

async fn load_order_flow_bars(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Bar>, ApiError> {
    let mut merged = BTreeMap::new();
    match load_order_flow_source(questdb, symbol, from, to, true).await {
        Ok(databento) => {
            for bar in databento {
                merged.insert(bar.ts, bar);
            }
        }
        Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => {}
        Err(error) => return Err(error),
    }
    match load_order_flow_source(questdb, symbol, from, to, false).await {
        Ok(bookmap) => {
            // Bookmap is the live source and wins if a minute ever overlaps.
            for bar in bookmap {
                merged.insert(bar.ts, bar);
            }
        }
        Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => {}
        Err(error) => return Err(error),
    }
    let mut bars: Vec<_> = merged.into_values().collect();
    attach_l2_features(questdb, symbol, from, to, &mut bars).await?;
    Ok(bars)
}

async fn attach_l2_features(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
    bars: &mut [Bar],
) -> Result<(), ApiError> {
    let mut features = BTreeMap::new();
    // Databento supplies the older history; Bookmap is loaded second and wins
    // for any minute where both normalized sources are present.
    for source in ["dbento", "bm"] {
        let sql = format!(
            concat!(
                "SELECT cast(timestamp as long) ts,last(midprice),last(spread),last(microprice),",
                "last(top1_imbalance),last(top5_imbalance),last(top10_imbalance),",
                "sum(bid_add_volume),sum(bid_cancel_volume),sum(ask_add_volume),",
                "sum(ask_cancel_volume),sum(aggressive_buy_volume),sum(aggressive_sell_volume),",
                "sum(trade_delta),sum(price_change),sum(delta_per_tick),",
                "sum(executed_at_bid),sum(executed_at_ask),sum(bid_replenishment),",
                "sum(ask_replenishment),last(bid_depth_distance),last(ask_depth_distance),",
                "last(depth_weighted_distance),sum(trade_count),sum(depth_event_count),",
                "last(book_valid) FROM {symbol}_l2_features_1s ",
                "WHERE source = '{source}' AND timestamp >= '{from}' ",
                "AND timestamp < dateadd('d',1,'{to}') ",
                "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR",
            ),
            symbol = symbol,
            source = source,
            from = from,
            to = to,
        );
        let rows = match questdb.csv(&sql).await {
            Ok(rows) => rows,
            Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => {
                return Ok(());
            }
            Err(error) => return Err(error),
        };
        for row in rows {
            let field = |index: usize| {
                row.get(index)
                    .ok_or_else(|| ApiError::QuestDb("missing L2 feature column".into()))
            };
            let number = |index: usize| -> Result<f64, ApiError> {
                field(index)?
                    .parse()
                    .map_err(|_| ApiError::QuestDb(format!("invalid L2 feature in column {index}")))
            };
            let count = |index: usize| -> Result<u64, ApiError> {
                field(index)?
                    .parse()
                    .map_err(|_| ApiError::QuestDb(format!("invalid L2 count in column {index}")))
            };
            let ts = field(0)?
                .parse::<i64>()
                .map_err(|_| ApiError::QuestDb("invalid L2 feature timestamp".into()))?
                / 1_000_000;
            let bid_replenishment = number(18)?;
            let ask_replenishment = number(19)?;
            let total_replenishment = bid_replenishment + ask_replenishment;
            features.insert(
                ts,
                OrderFlowFeatures {
                    midprice: number(1)?,
                    spread: number(2)?,
                    microprice: number(3)?,
                    top1_imbalance: number(4)?,
                    top5_imbalance: number(5)?,
                    top10_imbalance: number(6)?,
                    bid_add_volume: number(7)?,
                    bid_cancel_volume: number(8)?,
                    ask_add_volume: number(9)?,
                    ask_cancel_volume: number(10)?,
                    aggressive_buy_volume: number(11)?,
                    aggressive_sell_volume: number(12)?,
                    trade_delta: number(13)?,
                    price_change: number(14)?,
                    delta_per_tick: number(15)?,
                    executed_at_bid: number(16)?,
                    executed_at_ask: number(17)?,
                    bid_replenishment,
                    ask_replenishment,
                    replenishment_score: if total_replenishment > 0.0 {
                        (bid_replenishment - ask_replenishment) / total_replenishment
                    } else {
                        0.0
                    },
                    bid_depth_distance: number(20)?,
                    ask_depth_distance: number(21)?,
                    depth_weighted_distance: number(22)?,
                    trade_count: count(23)?,
                    depth_event_count: count(24)?,
                    book_valid: field(25)?
                        .parse()
                        .map_err(|_| ApiError::QuestDb("invalid L2 book-valid flag".into()))?,
                },
            );
        }
    }
    for bar in bars {
        if let Some(feature) = features.get(&bar.ts) {
            bar.order_flow = *feature;
        }
    }
    Ok(())
}

async fn load_order_flow_source(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
    databento: bool,
) -> Result<Vec<Bar>, ApiError> {
    let table_prefix = if databento { "dbento" } else { "bm" };
    let (timestamp_select, timestamp_filter, aggregation) = if databento {
        (
            "cast(date_trunc('minute',timestamp) as long)",
            format!(
                "timestamp >= '{from}' \
                 AND timestamp < dateadd('d',1,'{to}')"
            ),
            "GROUP BY ts ORDER BY ts",
        )
    } else {
        (
            "cast(timestamp as long)",
            format!("timestamp >= '{from}' AND timestamp < dateadd('d',1,'{to}')"),
            "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR",
        )
    };
    let tick_sql = format!(
        concat!(
            "SELECT {timestamp_select} ts,first(price) open,max(price) high,",
            "min(price) low,last(price) close,sum(size) volume,",
            "sum(CASE WHEN side = 'BUY' THEN size WHEN side = 'SELL' THEN -size ELSE 0 END) volume_delta ",
            "FROM {table_prefix}_{symbol}_ticks WHERE size > 0 AND {timestamp_filter} ",
            "{aggregation}",
        ),
        timestamp_select = timestamp_select,
        table_prefix = table_prefix,
        symbol = symbol,
        timestamp_filter = timestamp_filter,
        aggregation = aggregation,
    );
    let depth_sql = format!(
        concat!(
            "SELECT {timestamp_select} ts,count() depth_events ",
            "FROM {table_prefix}_{symbol}_depth WHERE {timestamp_filter} ",
            "{aggregation}",
        ),
        timestamp_select = timestamp_select,
        table_prefix = table_prefix,
        symbol = symbol,
        timestamp_filter = timestamp_filter,
        aggregation = aggregation,
    );

    let depth = questdb
        .csv(&depth_sql)
        .await?
        .into_iter()
        .map(|row| {
            let timestamp = row
                .get(0)
                .ok_or_else(|| ApiError::QuestDb("missing Bookmap depth timestamp".into()))?
                .parse::<i64>()
                .map_err(|_| ApiError::QuestDb("invalid Bookmap depth timestamp".into()))?
                / 1_000_000_000;
            let count = row
                .get(1)
                .ok_or_else(|| ApiError::QuestDb("missing Bookmap depth count".into()))?
                .parse::<u64>()
                .map_err(|_| ApiError::QuestDb("invalid Bookmap depth count".into()))?;
            Ok((timestamp, count))
        })
        .collect::<Result<BTreeMap<_, _>, ApiError>>()?;

    questdb
        .csv(&tick_sql)
        .await?
        .into_iter()
        .map(|row| order_flow_bar_from_csv(&row, &depth))
        .collect()
}

fn order_flow_bar_from_csv(
    row: &csv::StringRecord,
    depth: &BTreeMap<i64, u64>,
) -> Result<Bar, ApiError> {
    let field = |index: usize| {
        row.get(index)
            .ok_or_else(|| ApiError::QuestDb("missing Bookmap order-flow column".into()))
    };
    let number = |index: usize| -> Result<f64, ApiError> {
        field(index)?
            .parse()
            .map_err(|_| ApiError::QuestDb(format!("invalid order-flow value in column {index}")))
    };
    let ts = field(0)?
        .parse::<i64>()
        .map_err(|_| ApiError::QuestDb("invalid Bookmap order-flow timestamp".into()))?
        / 1_000_000_000;
    Ok(Bar {
        ts,
        open: number(1)?,
        high: number(2)?,
        low: number(3)?,
        close: number(4)?,
        volume: number(5)?,
        volume_delta: number(6)?,
        depth_events: depth.get(&ts).copied().unwrap_or(0),
        vix: 0.0,
        order_flow: OrderFlowFeatures::default(),
    })
}

async fn attach_hourly_vix(questdb: &QuestDb, to: &str, bars: &mut [Bar]) -> Result<(), ApiError> {
    let sql = format!(
        "SELECT cast(timestamp as long) ts,close FROM vix_1h \
         WHERE timestamp < dateadd('d',1,'{to}') ORDER BY timestamp"
    );
    let rows = questdb.csv(&sql).await?;
    let mut values = Vec::with_capacity(rows.len());
    for row in rows {
        let timestamp = row
            .get(0)
            .ok_or_else(|| ApiError::QuestDb("missing hourly VIX timestamp".into()))?
            .parse::<i64>()
            .map_err(|_| ApiError::QuestDb("invalid hourly VIX timestamp".into()))?
            / 1_000_000;
        let close = row
            .get(1)
            .ok_or_else(|| ApiError::QuestDb("missing hourly VIX close".into()))?
            .parse::<f64>()
            .map_err(|_| ApiError::QuestDb("invalid hourly VIX close".into()))?;
        values.push((timestamp, close));
    }

    let mut value_index = 0;
    for bar in bars {
        while value_index + 1 < values.len() && values[value_index + 1].0 <= bar.ts {
            value_index += 1;
        }
        bar.vix = values
            .get(value_index)
            .filter(|(timestamp, _)| *timestamp <= bar.ts)
            .map(|(_, close)| *close)
            .unwrap_or(0.0);
    }
    Ok(())
}

async fn load_ohlcv_bars(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
    with_vix: bool,
) -> Result<Vec<Bar>, ApiError> {
    let select = if with_vix {
        "n.timestamp,n.open,n.high,n.low,n.close,n.volume,v.close"
    } else {
        "timestamp,open,high,low,close,volume,0.0"
    };
    let table = if with_vix {
        format!("{symbol}_1m n ASOF JOIN vix_1d v")
    } else {
        format!("{symbol}_1m")
    };
    let prefix = if with_vix { "n." } else { "" };
    let sql = format!(
        concat!(
            "SELECT cast({prefix}timestamp as long) ts,{select} ",
            "FROM {table} ",
            "WHERE {prefix}timestamp >= dateadd('d',-90,'{from}') ",
            "AND {prefix}timestamp < dateadd('d',1,'{to}') ",
            "ORDER BY {prefix}timestamp",
        ),
        prefix = prefix,
        select = select,
        table = table,
        from = from,
        to = to
    );
    let rows = questdb.csv(&sql).await?;
    rows.into_iter()
        .map(|row| backtest_bar_from_csv(&row))
        .collect()
}

async fn load_bookmap_bars(
    questdb: &QuestDb,
    symbol: &str,
    from: &str,
    to: &str,
    with_vix: bool,
) -> Result<Vec<Bar>, ApiError> {
    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,first(price) open,max(price) high,",
            "min(price) low,last(price) close,sum(size) volume ",
            "FROM bm_{symbol}_ticks WHERE size > 0 ",
            "AND timestamp >= dateadd('d',-90,'{from}') ",
            "AND timestamp < dateadd('d',1,'{to}') ",
            "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR",
        ),
        symbol = symbol,
        from = from,
        to = to,
    );
    let rows = questdb.csv(&sql).await?;
    let mut bars = Vec::with_capacity(rows.len());
    for row in rows {
        bars.push(bookmap_bar_from_csv(&row)?);
    }
    if with_vix {
        attach_vix(questdb, to, &mut bars).await?;
    }
    Ok(bars)
}

async fn attach_vix(questdb: &QuestDb, to: &str, bars: &mut [Bar]) -> Result<(), ApiError> {
    let sql = format!(
        "SELECT cast(timestamp as long) ts,close FROM vix_1d WHERE timestamp < dateadd('d',1,'{to}') ORDER BY timestamp"
    );
    let rows = questdb.csv(&sql).await?;
    let mut values = Vec::with_capacity(rows.len());
    for row in rows {
        let timestamp = row
            .get(0)
            .ok_or_else(|| ApiError::QuestDb("missing VIX timestamp".into()))?
            .parse::<i64>()
            .map_err(|_| ApiError::QuestDb("invalid VIX timestamp".into()))?
            / 1_000_000;
        let close = row
            .get(1)
            .ok_or_else(|| ApiError::QuestDb("missing VIX close".into()))?
            .parse::<f64>()
            .map_err(|_| ApiError::QuestDb("invalid VIX close".into()))?;
        values.push((timestamp, close));
    }
    let mut value_index = 0;
    for bar in bars {
        while value_index + 1 < values.len() && values[value_index + 1].0 <= bar.ts {
            value_index += 1;
        }
        bar.vix = values
            .get(value_index)
            .filter(|(timestamp, _)| *timestamp <= bar.ts)
            .map(|(_, close)| *close)
            .unwrap_or(0.0);
    }
    Ok(())
}

fn bookmap_bar_from_csv(row: &csv::StringRecord) -> Result<Bar, ApiError> {
    let field = |index: usize| {
        row.get(index)
            .ok_or_else(|| ApiError::QuestDb("missing Bookmap bar column".into()))
    };
    let number = |index: usize| -> Result<f64, ApiError> {
        field(index)?
            .parse()
            .map_err(|_| ApiError::QuestDb(format!("invalid Bookmap bar value in column {index}")))
    };
    Ok(Bar {
        // The collector writes QuestDB TIMESTAMP_NS, unlike imported OHLCV
        // tables which use microseconds.
        ts: field(0)?
            .parse::<i64>()
            .map_err(|_| ApiError::QuestDb("invalid Bookmap timestamp".into()))?
            / 1_000_000_000,
        open: number(1)?,
        high: number(2)?,
        low: number(3)?,
        close: number(4)?,
        volume: number(5)?,
        volume_delta: 0.0,
        depth_events: 0,
        vix: 0.0,
        order_flow: OrderFlowFeatures::default(),
    })
}

pub(crate) fn backtest_bar_from_csv(row: &csv::StringRecord) -> Result<Bar, ApiError> {
    let field = |index: usize| {
        row.get(index)
            .ok_or_else(|| ApiError::QuestDb("missing backtest CSV column".into()))
    };
    let number = |index: usize| -> Result<f64, ApiError> {
        field(index)?
            .parse()
            .map_err(|_| ApiError::QuestDb(format!("invalid backtest value in column {index}")))
    };

    Ok(Bar {
        ts: field(0)?
            .parse::<i64>()
            .map_err(|_| ApiError::QuestDb("invalid timestamp".into()))?
            / 1_000_000,
        open: number(2)?,
        high: number(3)?,
        low: number(4)?,
        close: number(5)?,
        volume: number(6)?,
        volume_delta: 0.0,
        depth_events: 0,
        vix: number(7)?,
        order_flow: OrderFlowFeatures::default(),
    })
}
