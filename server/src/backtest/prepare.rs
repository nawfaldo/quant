use super::{
    data::{parse_iso_days, valid_date},
    engine::{EngineConfig, RunResult, execute},
    request::RunRequest,
    types::{Bar, Instrument},
};
use crate::{database::EnvironmentCosts, error::ApiError, questdb::QuestDb};

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
    if !["nq", "es"].contains(&symbol.as_str()) {
        return Err(ApiError::BadRequest("unknown symbol".into()));
    }
    if !["Night Drift", "Noise Momentum"].contains(&request.strategy.as_str()) {
        return Err(ApiError::BadRequest("unknown strategy".into()));
    }
    if !valid_date(&request.from_date) || !valid_date(&request.to_date) {
        return Err(ApiError::BadRequest("invalid date range".into()));
    }
    let instrument = Instrument::parse(&request.instrument)
        .ok_or_else(|| ApiError::BadRequest("unknown instrument".into()))?;
    let with_vix = request.strategy == "Night Drift";
    let from = &request.from_date;
    let to = &request.to_date;
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
    let mut bars = Vec::with_capacity(rows.len());
    for row in rows {
        bars.push(backtest_bar_from_csv(&row)?);
    }
    if bars.is_empty() {
        return Err(ApiError::QuestDb("no data in requested range".into()));
    }
    let start_day =
        parse_iso_days(from).ok_or_else(|| ApiError::BadRequest("invalid from date".into()))?;
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
