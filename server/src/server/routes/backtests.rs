use crate::{
    backtest::{self, RunRequest},
    database::CombineSource,
    error::ApiError,
    fx::{self, Repriced},
    state::AppState,
    strategies::StrategyEnvironment,
};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::{Value, json};

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/run", web::post().to(run))
        .route("/api/run/save", web::post().to(save_run))
        .route("/api/combine", web::post().to(combine))
        .route("/api/combine/save", web::post().to(save_combine))
        .route("/api/backtests", web::get().to(list))
        .route("/api/backtests/{id}", web::delete().to(delete))
        .route("/api/backtests/{id}/fx", web::get().to(backtest_fx))
        .route("/api/trades/{id}", web::get().to(trades))
        .route("/api/trades/{id}/fx", web::get().to(fx_trades))
        .route("/api/montecarlo/{id}", web::get().to(montecarlo));
}
fn binary(data: Vec<u8>) -> HttpResponse {
    HttpResponse::Ok()
        .content_type("application/octet-stream")
        .body(data)
}
async fn list(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.backtests().await?))
}
async fn delete(state: web::Data<AppState>, id: web::Path<i64>) -> Result<HttpResponse, ApiError> {
    state.db.delete_backtest(*id).await?;
    Ok(HttpResponse::Ok().json(json!({"status":"ok"})))
}
async fn trades(state: web::Data<AppState>, id: web::Path<i64>) -> Result<HttpResponse, ApiError> {
    Ok(binary(state.db.trades_binary(*id, false).await?))
}
async fn fx_trades(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    Ok(binary(state.db.trades_binary(*id, true).await?))
}
async fn montecarlo(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let data = state
        .db
        .montecarlo_binary(*id)
        .await?
        .ok_or_else(|| ApiError::NotFound("no montecarlo data".into()))?;
    Ok(binary(data))
}
async fn backtest_fx(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let mut sources = state.db.combine_sources(&[*id]).await?;
    let source = sources
        .pop()
        .ok_or_else(|| ApiError::NotFound("backtest not found".into()))?;
    let fx = if supports_fx(&source.symbol, &source.instrument) {
        match fx::reprice(&state.questdb, &source.trades).await {
            Ok(Some(repriced)) => Some(fx_report(
                &repriced,
                &source.strategy,
                &source.symbol,
                source.initial_balance,
            )),
            Ok(None) => None,
            Err(error) => {
                tracing::warn!(backtest_id = *id, %error, "FX repricing failed");
                None
            }
        }
    } else {
        None
    };

    Ok(HttpResponse::Ok().json(json!({ "fx": fx })))
}
async fn run(
    state: web::Data<AppState>,
    request: web::Json<RunRequest>,
) -> Result<HttpResponse, ApiError> {
    let mut request = request.into_inner();
    let costs = configure_strategy_environment(&state, &mut request).await?;
    let mut result = backtest::run(&state.questdb, &request, costs).await?;
    attach_fx_preview(&state, &request, &mut result).await;
    Ok(HttpResponse::Ok().json(result.body))
}
async fn save_run(
    state: web::Data<AppState>,
    request: web::Json<RunRequest>,
) -> Result<HttpResponse, ApiError> {
    let mut request = request.into_inner();
    let costs = configure_strategy_environment(&state, &mut request).await?;
    let environment_id = request.environment_id()?;
    let result = backtest::run(&state.questdb, &request, costs).await?;
    let strategy = match request.strategy.as_str() {
        "Hourly Delta Reversal NQ" => "HOURLY_DELTA_REVERSAL_NQ",
        "Hourly Delta Reversal ES" => "HOURLY_DELTA_REVERSAL_ES",
        "Night Drift" => "NIGHT_DRIFT",
        "Night Drift 2" => "NIGHT_DRIFT_2",
        "Noise Momentum" => "NOISE_MOMENTUM",
        "Noise Momentum 2" => "NOISE_MOMENTUM_2",
        _ => return Err(ApiError::BadRequest("unknown strategy".into())),
    };
    let id = state
        .db
        .save_backtest(strategy, environment_id, &result.body, &result.trades, None)
        .await?;
    if supports_fx(&request.symbol, &request.instrument) {
        match fx::reprice(&state.questdb, &result.trades).await {
            Ok(Some(repriced)) => {
                if let Err(error) = state.db.save_fx_trades(id, &repriced.trades).await {
                    tracing::warn!(backtest_id = id, %error, "saving FX trades failed");
                }
            }
            Ok(None) => {}
            Err(error) => {
                tracing::warn!(backtest_id = id, %error, "FX repricing failed");
            }
        }
    }
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}

async fn configure_strategy_environment(
    state: &AppState,
    request: &mut RunRequest,
) -> Result<crate::database::EnvironmentCosts, ApiError> {
    let environment_id = request
        .environment_id()?
        .ok_or_else(|| ApiError::BadRequest("missing environment id".into()))?;
    let name = state
        .db
        .environment_name(environment_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    request.strategy_environment = StrategyEnvironment::from_name(&name)
        .ok_or_else(|| ApiError::BadRequest("environment has no registered strategies".into()))?;
    state.db.environment_costs(Some(environment_id)).await
}

async fn attach_fx_preview(
    state: &AppState,
    request: &RunRequest,
    result: &mut backtest::RunResult,
) {
    if !supports_fx(&request.symbol, &request.instrument) {
        return;
    }

    match fx::reprice(&state.questdb, &result.trades).await {
        Ok(Some(repriced)) => {
            let initial_balance = result
                .body
                .get("initial_bal")
                .and_then(Value::as_f64)
                .unwrap_or_default();
            result.body["fx"] = fx_report(
                &repriced,
                &request.strategy,
                &request.symbol,
                initial_balance,
            );
        }
        Ok(None) => {}
        Err(error) => tracing::warn!(%error, "FX preview repricing failed"),
    }
}

fn fx_report(repriced: &Repriced, strategy: &str, symbol: &str, initial_balance: f64) -> Value {
    let source = CombineSource {
        id: 0,
        strategy: strategy.to_owned(),
        symbol: symbol.to_owned(),
        instrument: "forex".to_owned(),
        initial_balance,
        trades: repriced.trades.clone(),
    };
    let mut body = backtest::combine_realized(&[source], initial_balance, None, None).body;
    if let Some(object) = body.as_object_mut() {
        object.remove("fx");
        object.remove("environment_id");
        object.remove("strategy");
    }
    body["tradesInWindow"] = Value::from(repriced.in_window);
    body["tradesTotal"] = Value::from(repriced.total);
    body
}

fn supports_fx(symbol: &str, instrument: &str) -> bool {
    let symbol = symbol.to_ascii_lowercase();
    let instrument = instrument.to_ascii_lowercase();
    let futures = ["mini", "micro", "futures"]
        .iter()
        .any(|label| instrument.contains(label));

    symbol.contains("nq") && !futures
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CombineRequest {
    environment_id: Option<String>,
    ids: Vec<i64>,
    initial_balance: String,
    from_date: Option<String>,
    to_date: Option<String>,
}
impl CombineRequest {
    fn parsed(&self) -> Result<(Option<i64>, f64), ApiError> {
        if self.ids.len() < 2 {
            return Err(ApiError::BadRequest("need at least 2 ids".into()));
        }
        let environment = match self.environment_id.as_deref().filter(|v| !v.is_empty()) {
            Some(v) => Some(
                v.parse()
                    .map_err(|_| ApiError::BadRequest("invalid environment id".into()))?,
            ),
            None => None,
        };
        let balance = self
            .initial_balance
            .parse()
            .ok()
            .filter(|v: &f64| v.is_finite() && *v > 0.0)
            .ok_or_else(|| ApiError::BadRequest("missing initialBalance".into()))?;
        Ok((environment, balance))
    }
}
async fn compute_combine(
    state: &AppState,
    request: &CombineRequest,
) -> Result<(Option<i64>, backtest::RunResult), ApiError> {
    let (environment, balance) = request.parsed()?;
    state.db.environment_costs(environment).await?;
    let sources = state.db.combine_sources(&request.ids).await?;
    let from = request.from_date.as_deref().and_then(backtest::iso_day);
    let to = request.to_date.as_deref().and_then(backtest::iso_day);
    Ok((
        environment,
        backtest::combine(&state.questdb, &sources, balance, from, to).await?,
    ))
}
async fn combine(
    state: web::Data<AppState>,
    request: web::Json<CombineRequest>,
) -> Result<HttpResponse, ApiError> {
    let (_, result) = compute_combine(&state, &request).await?;
    Ok(HttpResponse::Ok().json(result.body))
}
async fn save_combine(
    state: web::Data<AppState>,
    request: web::Json<CombineRequest>,
) -> Result<HttpResponse, ApiError> {
    let (environment, result) = compute_combine(&state, &request).await?;
    let strategy = result
        .body
        .get("strategy")
        .and_then(|v| v.as_str())
        .unwrap_or("COMBINED");
    let id = state
        .db
        .save_backtest(strategy, environment, &result.body, &result.trades, None)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}
