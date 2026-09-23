use crate::{
    database::{AccountStrategyInput, Mt5AccountInput},
    error::ApiError,
    state::AppState,
};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::json;

pub fn configure(config: &mut web::ServiceConfig) {
    config
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
