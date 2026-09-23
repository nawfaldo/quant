use crate::{backtest::format_ts, database::Mt5PositionInput, error::ApiError, state::AppState};
use actix_web::{HttpResponse, web};
use futures_util::stream;
use serde::Deserialize;
use serde_json::json;
use std::time::Duration;

const DEFAULT_MAGIC: i64 = 26_032_026;
const DEFAULT_DEVIATION: i64 = 20;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/march/live/status", web::get().to(live_status))
        .route("/api/march/live/events", web::get().to(live_events))
        .route("/api/march/mt5/accounts/status", web::get().to(statuses))
        .route("/api/march/mt5/positions", web::get().to(positions))
        .route(
            "/api/march/mt5/positions/stream",
            web::get().to(position_stream),
        )
        .route("/api/march/mt5/bridge/poll", web::post().to(poll))
        .route("/api/march/mt5/bridge/result", web::post().to(result))
        .route(
            "/api/march/mt5/bridge/protective-close",
            web::post().to(protective_close),
        )
        .route(
            "/api/march/mt5/bridge/positions",
            web::post().to(position_snapshot),
        );
}

async fn position_stream(state: web::Data<AppState>) -> HttpResponse {
    let db = state.db.clone();
    let updates = stream::unfold(
        (db, tokio::time::interval(Duration::from_millis(250))),
        |(db, mut interval)| async move {
            interval.tick().await;
            let event = match db.mt5_positions().await {
                Ok(positions) => format!(
                    "data: {}\n\n",
                    serde_json::to_string(&positions).unwrap_or_else(|_| "[]".into())
                ),
                Err(error) => format!(
                    "event: error\ndata: {}\n\n",
                    serde_json::to_string(&error.to_string())
                        .unwrap_or_else(|_| "\"position stream failed\"".into())
                ),
            };
            Some((
                Ok::<_, actix_web::Error>(web::Bytes::from(event)),
                (db, interval),
            ))
        },
    );
    HttpResponse::Ok()
        .insert_header(("Cache-Control", "no-cache, no-store"))
        .insert_header(("X-Accel-Buffering", "no"))
        .content_type("text/event-stream")
        .streaming(updates)
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
    let magic = if command.magic > 0 {
        command.magic
    } else {
        env_i64("MT5_MAGIC_NUMBER", DEFAULT_MAGIC)
    };
    let deviation = env_i64("MT5_DEVIATION_POINTS", DEFAULT_DEVIATION);
    Ok(HttpResponse::Ok().content_type("text/plain").body(format!(
        "ORDER|{}|{}|{}|{:.8}|{}|{}|{}|{}",
        command.id,
        command.action,
        command.symbol,
        command.volume,
        magic,
        deviation,
        command.target_ticket,
        command.created_at
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
    mt5pnl: f64,
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
            body.mt5pnl,
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
struct ProtectiveCloseInput {
    #[serde(default)]
    token: String,
    login: String,
    ticket: i64,
    close_price: f64,
    #[serde(default)]
    pnl: f64,
    fill_time: i64,
    #[serde(default = "default_protective_reason")]
    reason: String,
}

fn default_protective_reason() -> String {
    "bridge_backend_failsafe".into()
}

async fn protective_close(
    state: web::Data<AppState>,
    body: web::Json<ProtectiveCloseInput>,
) -> Result<HttpResponse, ApiError> {
    authorize(&body.token)?;
    if body.ticket <= 0 || body.close_price <= 0.0 || body.fill_time <= 0 {
        return Err(ApiError::BadRequest(
            "invalid protective close receipt".into(),
        ));
    }
    if !state
        .db
        .reconcile_protective_close(
            &body.login,
            body.ticket,
            body.close_price,
            body.pnl,
            &format_ts(body.fill_time),
            &body.reason,
        )
        .await?
    {
        return Err(ApiError::NotFound(
            "managed protective position not found".into(),
        ));
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
    #[serde(default)]
    magic: i64,
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
            magic: position.magic,
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

async fn live_status(state: web::Data<AppState>) -> HttpResponse {
    HttpResponse::Ok().json(state.portfolio_status.snapshot())
}

async fn live_events(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.live_events(200).await?))
}

fn env_i64(name: &str, fallback: i64) -> i64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(fallback)
}
