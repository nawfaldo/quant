use super::{
    data::{parse_iso_days, valid_date},
    engine::{EngineConfig, RunResult, execute},
    request::RunRequest,
    types::{Bar, Instrument},
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
    if symbol != "nq" {
        return Err(ApiError::BadRequest("unknown symbol".into()));
    }
    if !matches!(
        request.strategy.as_str(),
        "Night Drift" | "Night Drift 2" | "Noise Momentum" | "Noise Momentum 2"
    ) {
        return Err(ApiError::BadRequest("unknown strategy".into()));
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
    }
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
        vix: 0.0,
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
        vix: number(7)?,
    })
}
