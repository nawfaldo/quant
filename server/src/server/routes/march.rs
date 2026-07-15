use crate::{
    backtest::{LiveBar, LiveNightDrift, iso_day, warm_live_night_drift},
    database::{AccountStrategyInput, Mt5AccountInput},
    error::ApiError,
    state::AppState,
};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::json;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/march/strategies", web::get().to(strategies))
        .route(
            "/api/march/strategies/{name}/{action}",
            web::put().to(toggle_strategy),
        )
        .route("/api/march/bar", web::post().to(bar))
        .route("/api/march/trades", web::get().to(trades))
        .service(
            web::resource("/api/march/mt5/accounts")
                .route(web::get().to(accounts))
                .route(web::post().to(add_account)),
        )
        .route(
            "/api/march/mt5/accounts/{id}",
            web::delete().to(delete_account),
        )
        .service(
            web::resource("/api/march/mt5/accounts/{id}/strategies")
                .route(web::get().to(account_strategies))
                .route(web::post().to(add_account_strategy)),
        )
        .route(
            "/api/march/mt5/accounts/{id}/strategies/{sid}",
            web::delete().to(delete_account_strategy),
        )
        .route(
            "/api/march/mt5/accounts/{id}/strategies/{sid}/{action}",
            web::put().to(toggle_account_strategy),
        );
}
async fn strategies(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.strategies().await?))
}
async fn toggle_strategy(
    state: web::Data<AppState>,
    path: web::Path<(String, String)>,
) -> Result<HttpResponse, ApiError> {
    let (name, action) = path.into_inner();
    let active = match action.as_str() {
        "on" => true,
        "off" => false,
        _ => return Err(ApiError::NotFound("not found".into())),
    };
    if !state.db.set_strategy_active(&name, active).await? {
        return Err(ApiError::NotFound("not found".into()));
    }
    if active {
        let strategy = match warm_live_night_drift(&state.questdb).await {
            Ok(strategy) => strategy,
            Err(error) => {
                tracing::warn!(%error, "March warm-up failed; activating empty state");
                LiveNightDrift::default()
            }
        };
        state.march.activate(strategy);
    } else {
        state.march.deactivate();
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

#[derive(Deserialize)]
struct BarInput {
    strategy: String,
    ts: String,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    #[serde(default)]
    volume: f64,
    vix: Option<f64>,
}

async fn bar(
    state: web::Data<AppState>,
    body: web::Json<BarInput>,
) -> Result<HttpResponse, ApiError> {
    if body.strategy != "night_drift" {
        return Ok(HttpResponse::Ok().json(json!({ "signal": "inactive" })));
    }
    let timestamp = parse_timestamp(&body.ts)?;
    let Some(decision) = state.march.update(LiveBar {
        timestamp,
        open: body.open,
        high: body.high,
        low: body.low,
        close: body.close,
        volume: body.volume,
        vix: body.vix,
    }) else {
        return Ok(HttpResponse::Ok().json(json!({ "signal": "inactive" })));
    };

    let signal = decision.signal;
    state
        .executor
        .publish(&state.db, &body.strategy, &body.ts, body.close, decision)
        .await?;

    Ok(HttpResponse::Ok().json(json!({ "signal": signal.as_str() })))
}

fn parse_timestamp(value: &str) -> Result<i64, ApiError> {
    if value.len() < 16 {
        return Err(ApiError::BadRequest("invalid timestamp".into()));
    }
    let day =
        iso_day(&value[0..10]).ok_or_else(|| ApiError::BadRequest("invalid timestamp".into()))?;
    let hour = value[11..13]
        .parse::<i64>()
        .ok()
        .filter(|hour| *hour < 24)
        .ok_or_else(|| ApiError::BadRequest("invalid timestamp".into()))?;
    let minute = value[14..16]
        .parse::<i64>()
        .ok()
        .filter(|minute| *minute < 60)
        .ok_or_else(|| ApiError::BadRequest("invalid timestamp".into()))?;
    Ok(day * 86_400 + hour * 3_600 + minute * 60)
}
async fn trades(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.live_trades().await?))
}
async fn accounts(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.accounts().await?))
}
#[derive(Deserialize)]
struct AccountInput {
    name: Option<String>,
    login: String,
    server: Option<String>,
}
async fn add_account(
    state: web::Data<AppState>,
    body: web::Json<AccountInput>,
) -> Result<HttpResponse, ApiError> {
    if body.login.is_empty() {
        return Err(ApiError::BadRequest("missing login".into()));
    }
    let id = state
        .db
        .add_account(&Mt5AccountInput {
            name: body.name.clone().unwrap_or_default(),
            login: body.login.clone(),
            server: body.server.clone().unwrap_or_default(),
        })
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}
async fn delete_account(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    state.db.delete_account(*id).await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn account_strategies(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.account_strategies(*id).await?))
}
#[derive(Deserialize)]
struct StrategyInput {
    strategy: String,
    symbol: Option<String>,
}
async fn add_account_strategy(
    state: web::Data<AppState>,
    id: web::Path<i64>,
    body: web::Json<StrategyInput>,
) -> Result<HttpResponse, ApiError> {
    if body.strategy.is_empty() {
        return Err(ApiError::BadRequest("missing strategy".into()));
    }
    let strategy_id = state
        .db
        .add_account_strategy(
            *id,
            &AccountStrategyInput {
                strategy: body.strategy.clone(),
                symbol: body.symbol.clone().unwrap_or_default(),
            },
        )
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":strategy_id})))
}
async fn delete_account_strategy(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    state.db.delete_account_strategy(path.1).await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn toggle_account_strategy(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64, String)>,
) -> Result<HttpResponse, ApiError> {
    let active = match path.2.as_str() {
        "on" => true,
        "off" => false,
        _ => return Err(ApiError::NotFound("not found".into())),
    };
    if !state.db.set_account_strategy_active(path.1, active).await? {
        return Err(ApiError::NotFound("not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
