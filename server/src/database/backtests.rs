use super::{CombineSource, Database};
use crate::{
    backtest::{Side, Trade},
    error::ApiError,
};
use sea_orm::{
    ConnectionTrait, DatabaseBackend, DatabaseTransaction, QueryResult, Statement, TransactionTrait,
};
use serde_json::{Map, Value as JsonValue};

const BACKTEST_COLUMNS: &[(&str, ColumnKind)] = &[
    ("id", ColumnKind::Integer),
    ("strategy", ColumnKind::Text),
    ("run_at", ColumnKind::Text),
    ("first_ts", ColumnKind::Text),
    ("last_ts", ColumnKind::Text),
    ("total_days", ColumnKind::Integer),
    ("initial_bal", ColumnKind::Real),
    ("final_bal", ColumnKind::Real),
    ("net_growth", ColumnKind::Real),
    ("max_drawdown", ColumnKind::Real),
    ("num_trades", ColumnKind::Integer),
    ("symbol", ColumnKind::Text),
    ("avg_drawdown", ColumnKind::Real),
    ("sharpe", ColumnKind::Real),
    ("total_win", ColumnKind::Real),
    ("total_loss", ColumnKind::Real),
    ("win_rate", ColumnKind::Real),
    ("win_count", ColumnKind::Integer),
    ("profit_factor", ColumnKind::Real),
    ("expectancy", ColumnKind::Real),
    ("max_lose_streak", ColumnKind::Integer),
    ("avg_size", ColumnKind::Real),
    ("min_size", ColumnKind::Real),
    ("max_size", ColumnKind::Real),
    ("avg_weekly", ColumnKind::Real),
    ("avg_monthly", ColumnKind::Real),
    ("avg_weekly_pct", ColumnKind::Real),
    ("avg_monthly_pct", ColumnKind::Real),
    ("instrument", ColumnKind::Text),
    ("max_drawdown_dollars", ColumnKind::Real),
    ("max_drawdown_peak_date", ColumnKind::Text),
    ("max_drawdown_trough_date", ColumnKind::Text),
    ("avg_drawdown_dollars", ColumnKind::Real),
    ("max_intraday_drawdown", ColumnKind::Real),
    ("max_intraday_drawdown_dollars", ColumnKind::Real),
    ("max_intraday_drawdown_date", ColumnKind::Text),
    ("avg_intraday_drawdown", ColumnKind::Real),
    ("avg_intraday_drawdown_dollars", ColumnKind::Real),
    ("max_daily_loss", ColumnKind::Real),
    ("max_daily_loss_date", ColumnKind::Text),
    ("avg_daily_loss", ColumnKind::Real),
    ("environment_id", ColumnKind::Integer),
];

const REPORT_FIELDS: &[&str] = &[
    "first_ts",
    "last_ts",
    "total_days",
    "initial_bal",
    "final_bal",
    "net_growth",
    "max_drawdown",
    "num_trades",
    "symbol",
    "avg_drawdown",
    "sharpe",
    "total_win",
    "total_loss",
    "win_rate",
    "win_count",
    "profit_factor",
    "expectancy",
    "max_lose_streak",
    "avg_size",
    "min_size",
    "max_size",
    "avg_weekly",
    "avg_monthly",
    "avg_weekly_pct",
    "avg_monthly_pct",
    "instrument",
    "max_drawdown_dollars",
    "max_drawdown_peak_date",
    "max_drawdown_trough_date",
    "avg_drawdown_dollars",
    "max_intraday_drawdown",
    "max_intraday_drawdown_dollars",
    "max_intraday_drawdown_date",
    "avg_intraday_drawdown",
    "avg_intraday_drawdown_dollars",
    "max_daily_loss",
    "max_daily_loss_date",
    "avg_daily_loss",
];

impl Database {
    pub async fn backtests(&self) -> Result<Vec<JsonValue>, ApiError> {
        let select = BACKTEST_COLUMNS
            .iter()
            .map(|(name, _)| match *name {
                "environment_id" => "COALESCE(environment_id,0)",
                other => other,
            })
            .collect::<Vec<_>>()
            .join(",");
        let rows = self
            .orm()
            .await?
            .query_all(sql(&format!(
                "SELECT {select} FROM backtests ORDER BY run_at DESC"
            )))
            .await?;
        rows.into_iter().map(backtest_json).collect()
    }

    pub async fn trades_binary(&self, id: i64, fx: bool) -> Result<Vec<u8>, ApiError> {
        let table = if fx { "fx_trades" } else { "trades" };
        let statement = bound(
            &format!(
                concat!(
                    "SELECT side,CAST(strftime('%s',entry_ts) AS INTEGER),",
                    "CAST(strftime('%s',exit_ts) AS INTEGER),",
                    "entry_price,exit_price,pnl,contracts FROM {table} ",
                    "WHERE backtest_id=? ORDER BY entry_ts",
                ),
                table = table,
            ),
            [id.into()],
        );
        let rows = self.orm().await?.query_all(statement).await?;
        let mut output = vec![0; 8];
        for row in &rows {
            output.push(if row.try_get_by_index::<String>(0)? == "long" {
                0
            } else {
                1
            });
            output.extend_from_slice(&(row.try_get_by_index::<i64>(1)? as u32).to_le_bytes());
            output.extend_from_slice(&(row.try_get_by_index::<i64>(2)? as u32).to_le_bytes());
            for column in 3..=6 {
                let value = row.try_get_by_index::<f64>(column)? as f32;
                output.extend_from_slice(&value.to_le_bytes());
            }
        }
        output[0..4].copy_from_slice(&0x5452_4445u32.to_le_bytes());
        output[4..8].copy_from_slice(&(rows.len() as u32).to_le_bytes());
        Ok(output)
    }

    pub async fn montecarlo_binary(&self, id: i64) -> Result<Option<Vec<u8>>, ApiError> {
        let statement = bound(
            concat!(
                "SELECT id,initial_balance,final_p5,final_p25,final_p50,final_p75,final_p95,",
                "p_profit,p_ruin,sims,dd_p5,dd_p25,dd_p50,dd_p75,dd_p95 FROM montecarlo ",
                "WHERE source_id=? ORDER BY run_at DESC LIMIT 1",
            ),
            [id.into()],
        );
        let Some(row) = self.orm().await?.query_one(statement).await? else {
            return Ok(None);
        };
        let record = MonteCarloRecord::from_row(&row)?;
        let steps = self
            .orm()
            .await?
            .query_all(bound(
                "SELECT step FROM montecarlo_paths WHERE mc_id=? AND path_idx=0 ORDER BY step",
                [record.id.into()],
            ))
            .await?
            .into_iter()
            .map(|row| row.try_get_by_index::<i64>(0).map(|value| value as u32))
            .collect::<Result<Vec<_>, _>>()?;
        if steps.is_empty() {
            return Ok(None);
        }
        let equities = self
            .orm()
            .await?
            .query_all(bound(
                "SELECT equity FROM montecarlo_paths WHERE mc_id=? ORDER BY path_idx,step",
                [record.id.into()],
            ))
            .await?
            .into_iter()
            .map(|row| row.try_get_by_index::<f64>(0).map(|value| value as f32))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Some(encode_monte_carlo(record, steps, equities)))
    }

    pub async fn delete_backtest(&self, id: i64) -> Result<(), ApiError> {
        let transaction = self.orm().await?.begin().await?;
        for statement in [
            concat!(
                "DELETE FROM montecarlo_paths WHERE mc_id IN ",
                "(SELECT id FROM montecarlo WHERE source_id=?)",
            ),
            "DELETE FROM montecarlo WHERE source_id=?",
            "DELETE FROM trades WHERE backtest_id=?",
            "DELETE FROM fx_trades WHERE backtest_id=?",
            "DELETE FROM backtests WHERE id=?",
        ] {
            transaction.execute(bound(statement, [id.into()])).await?;
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn save_backtest(
        &self,
        strategy: &str,
        environment_id: Option<i64>,
        report: &JsonValue,
        trades: &[Trade],
        monte_carlo_drawdown_ruin_limit: Option<f64>,
    ) -> Result<i64, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        let columns = REPORT_FIELDS.join(",");
        let placeholders = vec!["?"; REPORT_FIELDS.len()].join(",");
        let statement = format!(
            concat!(
                "INSERT INTO backtests(strategy,run_at,{columns},environment_id) ",
                "VALUES (?,datetime('now'),{placeholders},?)",
            ),
            columns = columns,
            placeholders = placeholders,
        );
        let mut values = Vec::with_capacity(REPORT_FIELDS.len() + 2);
        values.push(strategy.to_owned().into());
        values.extend(
            REPORT_FIELDS
                .iter()
                .map(|field| json_db_value(&report[*field])),
        );
        values.push(environment_id.into());
        let id = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                statement,
                values,
            ))
            .await?
            .last_insert_id() as i64;
        save_trades(&transaction, "trades", id, trades).await?;
        if report.get("montecarlo").is_some_and(JsonValue::is_object) {
            let initial = report
                .get("initial_bal")
                .and_then(JsonValue::as_f64)
                .unwrap_or_default();
            let pnls = trades.iter().map(|trade| trade.pnl).collect::<Vec<_>>();
            let monte_carlo = if let Some(limit) = monte_carlo_drawdown_ruin_limit {
                crate::backtest::monte_carlo::run_for_storage_with_drawdown_ruin(
                    &pnls, initial, limit,
                )
            } else {
                crate::backtest::monte_carlo::run_for_storage(&pnls, initial)
            };
            save_monte_carlo(&transaction, id, &monte_carlo).await?;
        }
        transaction.commit().await?;
        Ok(id)
    }

    pub async fn save_fx_trades(&self, id: i64, trades: &[Trade]) -> Result<(), ApiError> {
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(bound(
                "DELETE FROM fx_trades WHERE backtest_id=?",
                [id.into()],
            ))
            .await?;
        save_trades(&transaction, "fx_trades", id, trades).await?;
        transaction.commit().await?;
        Ok(())
    }

    pub async fn combine_sources(&self, ids: &[i64]) -> Result<Vec<CombineSource>, ApiError> {
        let connection = self.orm().await?;
        let mut output = Vec::with_capacity(ids.len());
        for &id in ids {
            let meta = connection
                .query_one(bound(
                    "SELECT strategy,symbol,instrument,initial_bal FROM backtests WHERE id=?",
                    [id.into()],
                ))
                .await?
                .ok_or_else(|| ApiError::NotFound(format!("backtest {id} not found")))?;
            let rows = connection
                .query_all(bound(
                    concat!(
                        "SELECT side,CAST(strftime('%s',entry_ts) AS INTEGER),",
                        "CAST(strftime('%s',exit_ts) AS INTEGER),entry_price,exit_price,pnl,",
                        "contracts,entry_raw,exit_raw FROM trades WHERE backtest_id=? ",
                        "ORDER BY entry_ts",
                    ),
                    [id.into()],
                ))
                .await?;
            let trades = rows
                .into_iter()
                .map(trade_from_row)
                .collect::<Result<_, ApiError>>()?;
            output.push(CombineSource {
                id,
                strategy: meta.try_get_by_index(0)?,
                symbol: meta.try_get_by_index(1)?,
                instrument: meta.try_get_by_index(2)?,
                initial_balance: meta.try_get_by_index(3)?,
                trades,
            });
        }
        Ok(output)
    }
}

async fn save_trades(
    transaction: &DatabaseTransaction,
    table: &str,
    backtest_id: i64,
    trades: &[Trade],
) -> Result<(), ApiError> {
    let statement = format!(
        concat!(
            "INSERT INTO {table}(backtest_id,side,entry_ts,exit_ts,entry_price,exit_price,",
            "entry_raw,exit_raw,pnl,contracts) VALUES (?,?,datetime(?,'unixepoch'),",
            "datetime(?,'unixepoch'),?,?,?,?,?,?)",
        ),
        table = table,
    );
    for trade in trades {
        transaction
            .execute(bound(
                &statement,
                [
                    backtest_id.into(),
                    side_name(trade.side).into(),
                    trade.entry_timestamp.into(),
                    trade.exit_timestamp.into(),
                    trade.entry_price.into(),
                    trade.exit_price.into(),
                    trade.entry_raw.into(),
                    trade.exit_raw.into(),
                    trade.pnl.into(),
                    trade.quantity.into(),
                ],
            ))
            .await?;
    }
    Ok(())
}

async fn save_monte_carlo(
    transaction: &DatabaseTransaction,
    source_id: i64,
    value: &JsonValue,
) -> Result<(), ApiError> {
    let number = |key: &str| value.get(key).and_then(JsonValue::as_f64).unwrap_or(0.0);
    let result = transaction
        .execute(bound(
            concat!(
                "INSERT INTO montecarlo(run_at,source_id,initial_balance,final_p5,final_p25,",
                "final_p50,final_p75,final_p95,p_profit,p_ruin,sims,dd_p5,dd_p25,dd_p50,",
                "dd_p75,dd_p95) VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ),
            [
                source_id.into(),
                number("initialBalance").into(),
                number("p5").into(),
                number("p25").into(),
                number("p50").into(),
                number("p75").into(),
                number("p95").into(),
                number("pProfit").into(),
                number("pRuin").into(),
                value
                    .get("sims")
                    .and_then(JsonValue::as_i64)
                    .unwrap_or(0)
                    .into(),
                number("ddP5").into(),
                number("ddP25").into(),
                number("ddP50").into(),
                number("ddP75").into(),
                number("ddP95").into(),
            ],
        ))
        .await?;
    let mc_id = result.last_insert_id() as i64;
    let Some(steps) = value.get("stepValues").and_then(JsonValue::as_array) else {
        return Ok(());
    };
    let Some(paths) = value.get("paths").and_then(JsonValue::as_array) else {
        return Ok(());
    };
    for (path_index, path) in paths.iter().filter_map(JsonValue::as_array).enumerate() {
        for (index, equity) in path.iter().enumerate() {
            let step = steps
                .get(index)
                .and_then(JsonValue::as_i64)
                .unwrap_or(index as i64);
            transaction
                .execute(bound(
                    concat!(
                        "INSERT INTO montecarlo_paths(mc_id,path_idx,step,equity) ",
                        "VALUES (?,?,?,?)",
                    ),
                    [
                        mc_id.into(),
                        (path_index as i64).into(),
                        step.into(),
                        equity.as_f64().unwrap_or(0.0).into(),
                    ],
                ))
                .await?;
        }
    }
    Ok(())
}

fn backtest_json(row: QueryResult) -> Result<JsonValue, ApiError> {
    let mut object = Map::new();
    for (index, (name, kind)) in BACKTEST_COLUMNS.iter().enumerate() {
        let value = match kind {
            ColumnKind::Text => JsonValue::String(row.try_get_by_index(index)?),
            ColumnKind::Integer => JsonValue::from(row.try_get_by_index::<i64>(index)?),
            ColumnKind::Real => {
                let value = row.try_get_by_index::<f64>(index)?;
                let decimals = match *name {
                    "max_drawdown"
                    | "avg_drawdown"
                    | "sharpe"
                    | "win_rate"
                    | "profit_factor"
                    | "expectancy"
                    | "avg_size"
                    | "min_size"
                    | "max_size"
                    | "avg_weekly_pct"
                    | "avg_monthly_pct"
                    | "max_intraday_drawdown"
                    | "avg_intraday_drawdown" => 4,
                    _ => 2,
                };
                let scale = if decimals == 4 { 10_000.0 } else { 100.0 };
                JsonValue::from((value * scale).round() / scale)
            }
        };
        object.insert((*name).to_owned(), value);
    }
    Ok(JsonValue::Object(object))
}

fn trade_from_row(row: QueryResult) -> Result<Trade, ApiError> {
    Ok(Trade {
        side: if row.try_get_by_index::<String>(0)? == "long" {
            Side::Long
        } else {
            Side::Short
        },
        entry_timestamp: row.try_get_by_index(1)?,
        exit_timestamp: row.try_get_by_index(2)?,
        entry_price: row.try_get_by_index(3)?,
        exit_price: row.try_get_by_index(4)?,
        pnl: row.try_get_by_index(5)?,
        quantity: row.try_get_by_index(6)?,
        entry_raw: row.try_get_by_index(7)?,
        exit_raw: row.try_get_by_index(8)?,
    })
}

fn json_db_value(value: &JsonValue) -> sea_orm::Value {
    match value {
        JsonValue::String(value) => value.clone().into(),
        JsonValue::Number(value) if value.is_i64() => value.as_i64().into(),
        JsonValue::Number(value) => value.as_f64().unwrap_or(0.0).into(),
        _ => Option::<i64>::None.into(),
    }
}

fn side_name(side: Side) -> String {
    if side == Side::Long {
        "long".into()
    } else {
        "short".into()
    }
}

fn sql(value: &str) -> Statement {
    Statement::from_string(DatabaseBackend::Sqlite, value)
}

fn bound(value: &str, parameters: impl IntoIterator<Item = sea_orm::Value>) -> Statement {
    Statement::from_sql_and_values(DatabaseBackend::Sqlite, value, parameters)
}

fn encode_monte_carlo(record: MonteCarloRecord, steps: Vec<u32>, equities: Vec<f32>) -> Vec<u8> {
    let paths = equities.len() as u32 / steps.len() as u32;
    let mut output = Vec::with_capacity(68 + 4 * (steps.len() + equities.len()));
    output.extend_from_slice(&0x4D43_5054u32.to_le_bytes());
    output.extend_from_slice(&paths.to_le_bytes());
    output.extend_from_slice(&(steps.len() as u32).to_le_bytes());
    for value in record.summary {
        output.extend_from_slice(&value.to_le_bytes());
    }
    output.extend_from_slice(&record.simulations.to_le_bytes());
    for value in record.drawdowns {
        output.extend_from_slice(&value.to_le_bytes());
    }
    for step in steps {
        output.extend_from_slice(&step.to_le_bytes());
    }
    for equity in equities {
        output.extend_from_slice(&equity.to_le_bytes());
    }
    output
}

enum ColumnKind {
    Text,
    Integer,
    Real,
}

struct MonteCarloRecord {
    id: i64,
    summary: Vec<f32>,
    simulations: u32,
    drawdowns: Vec<f32>,
}

impl MonteCarloRecord {
    fn from_row(row: &QueryResult) -> Result<Self, sea_orm::DbErr> {
        let summary = (1..=8)
            .map(|index| row.try_get_by_index::<f64>(index).map(|value| value as f32))
            .collect::<Result<_, _>>()?;
        let drawdowns = (10..=14)
            .map(|index| row.try_get_by_index::<f64>(index).map(|value| value as f32))
            .collect::<Result<_, _>>()?;
        Ok(Self {
            id: row.try_get_by_index(0)?,
            summary,
            simulations: row.try_get_by_index::<i64>(9)? as u32,
            drawdowns,
        })
    }
}
