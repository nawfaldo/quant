use crate::{backtest::format_ts, database::Mt5PositionInput, error::ApiError, state::AppState};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::json;

const DEFAULT_MAGIC: i64 = 26_032_026;
const DEFAULT_DEVIATION: i64 = 20;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/march/mt5/accounts/status", web::get().to(statuses))
        .route("/api/march/mt5/positions", web::get().to(positions))
        .route("/api/march/mt5/bridge/poll", web::post().to(poll))
        .route("/api/march/mt5/bridge/result", web::post().to(result))
        .route(
            "/api/march/mt5/bridge/positions",
            web::post().to(position_snapshot),
        );
}

fn authorize(token: &str) -> Result<(), ApiError> {
    let expected = std::env::var("MT5_BRIDGE_TOKEN").unwrap_or_default();
    if token != expected {
        return Err(ApiError::BadRequest("invalid MT5 bridge token".into()));
    }
    Ok(())
}

#[derive(Deserialize)]
struct PollInput {
    #[serde(default)]
    token: String,
    login: String,
    #[serde(default)]
    server: String,
    #[serde(default)]
    balance: f64,
    #[serde(default)]
    equity: f64,
    #[serde(default)]
    currency: String,
}

async fn poll(
    state: web::Data<AppState>,
    body: web::Json<PollInput>,
) -> Result<HttpResponse, ApiError> {
    authorize(&body.token)?;
    let Some(account_id) = state
        .db
        .record_mt5_heartbeat(
            &body.login,
            &body.server,
            body.balance,
            body.equity,
            &body.currency,
        )
        .await?
    else {
        return Err(ApiError::NotFound(format!(
            "MT5 login {} is not configured",
            body.login
        )));
    };
    let Some(command) = state.db.claim_mt5_command(account_id).await? else {
        return Ok(HttpResponse::Ok().content_type("text/plain").body("NONE"));
    };
    let magic = env_i64("MT5_MAGIC_NUMBER", DEFAULT_MAGIC);
    let deviation = env_i64("MT5_DEVIATION_POINTS", DEFAULT_DEVIATION);
    Ok(HttpResponse::Ok().content_type("text/plain").body(format!(
        "ORDER|{}|{}|{}|{:.8}|{}|{}",
        command.id, command.action, command.symbol, command.volume, magic, deviation
    )))
}

#[derive(Deserialize)]
struct ResultInput {
    #[serde(default)]
    token: String,
    login: String,
    command_id: i64,
    status: String,
    #[serde(default)]
    ticket: i64,
    #[serde(default)]
    entry_price: f64,
    #[serde(default)]
    entry_spread: f64,
    #[serde(default)]
    close_price: f64,
    #[serde(default)]
    fill_time: i64,
    #[serde(default)]
    error: String,
}

async fn result(
    state: web::Data<AppState>,
    body: web::Json<ResultInput>,
) -> Result<HttpResponse, ApiError> {
    authorize(&body.token)?;
    if body.status != "filled" && body.status != "failed" {
        return Err(ApiError::BadRequest("invalid execution status".into()));
    }
    if body.status == "failed" {
        tracing::warn!(
            command_id = body.command_id,
            login = body.login,
            error = body.error,
            "MT5 command failed"
        );
    }
    let fill_time = format_ts(body.fill_time);
    if !state
        .db
        .complete_mt5_command(
            body.command_id,
            &body.login,
            body.status == "filled",
            body.ticket,
            body.entry_price,
            body.entry_spread,
            body.close_price,
            &fill_time,
            &body.error,
        )
        .await?
    {
        return Err(ApiError::NotFound("execution command not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

#[derive(Deserialize)]
struct PositionsInput {
    #[serde(default)]
    token: String,
    login: String,
    #[serde(default)]
    server: String,
    #[serde(default)]
    positions: Vec<PositionInput>,
}

#[derive(Deserialize)]
struct PositionInput {
    ticket: i64,
    #[serde(rename = "type")]
    position_type: String,
    symbol: String,
    volume: f64,
    profit: f64,
    open_price: f64,
    open_time: i64,
}

async fn position_snapshot(
    state: web::Data<AppState>,
    body: web::Json<PositionsInput>,
) -> Result<HttpResponse, ApiError> {
    authorize(&body.token)?;
    let positions = body
        .positions
        .iter()
        .map(|position| Mt5PositionInput {
            ticket: position.ticket,
            position_type: position.position_type.clone(),
            symbol: position.symbol.clone(),
            volume: position.volume,
            profit: position.profit,
            open_price: position.open_price,
            open_time: position.open_time,
        })
        .collect::<Vec<_>>();
    if !state
        .db
        .replace_mt5_positions(&body.login, &body.server, &positions)
        .await?
    {
        return Err(ApiError::NotFound("MT5 account not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

async fn statuses(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.mt5_account_statuses().await?))
}

async fn positions(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.mt5_positions().await?))
}

fn env_i64(name: &str, fallback: i64) -> i64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(fallback)
}
