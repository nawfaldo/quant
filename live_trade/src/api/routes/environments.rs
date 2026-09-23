use crate::{
    database::{AccountStrategyInput, CreateEnvironment, EnvironmentMt5AccountInput},
    error::ApiError,
    state::AppState,
    strategies::{for_environment, registered_sleeves},
};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::{Value, json};

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .service(
            web::resource("/api/environments")
                .route(web::get().to(list))
                .route(web::post().to(create)),
        )
        .service(web::resource("/api/environments/{id}/rules").route(web::get().to(rules)))
        .service(
            web::resource("/api/environments/{id}/strategies").route(web::get().to(strategies)),
        )
        .service(
            web::resource("/api/environments/{id}/mt5-accounts")
                .route(web::get().to(mt5_accounts))
                .route(web::post().to(create_mt5_account)),
        )
        .service(
            web::resource("/api/environments/{id}/mt5-accounts/{account_id}")
                .route(web::put().to(update_account_view_settings))
                .route(web::delete().to(delete_mt5_account)),
        )
        .service(
            web::resource("/api/environments/{id}/mt5-accounts/{account_id}/strategies")
                .route(web::get().to(account_strategies))
                .route(web::post().to(create_account_strategy)),
        )
        .service(
            web::resource("/api/environments/{id}/mt5-accounts/{account_id}/trades")
                .route(web::get().to(account_trades)),
        )
        .service(
            web::resource(
                "/api/environments/{id}/mt5-accounts/{account_id}/strategies/{strategy_id}",
            )
            .route(web::delete().to(delete_account_strategy)),
        );
}
async fn list(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.environments().await?))
}
async fn create(
    state: web::Data<AppState>,
    body: web::Json<Value>,
) -> Result<HttpResponse, ApiError> {
    let string = |key: &str| {
        body.get(key)
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_owned()
    };
    let name = string("name");
    if name.is_empty() {
        return Err(ApiError::BadRequest("name is required".into()));
    }
    if name.chars().count() > 100 {
        return Err(ApiError::BadRequest(
            "name must be 100 characters or fewer".into(),
        ));
    }
    let id = state
        .db
        .create_environment(&CreateEnvironment { name })
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}
/// The backtest rules an environment runs under.
///
/// READ-ONLY, AND SCOPED TO THE ENVIRONMENT. This used to be a table of spread,
/// slippage and commission rows a user could add, edit and delete. Every one of
/// them was overridden by the engine's own Exness Pro figures before a single
/// bar was replayed, so what the screen showed and what the backtest charged
/// were unrelated numbers.
///
/// The rule is now the Exness Pro cost model, stored per environment by
/// `sync_environment_cost_rules` and rewritten from the engine's own tables on
/// every boot. It used to be returned for ANY environment that merely existed,
/// which made it a global constant wearing an environment-scoped URL --
/// `strategies` below has always resolved through `for_environment`, and this
/// now answers the same question the same way. An environment that runs nothing
/// gets an empty list.
async fn rules(state: web::Data<AppState>, id: web::Path<i64>) -> Result<HttpResponse, ApiError> {
    let rules = state
        .db
        .environment_cost_rules(*id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    Ok(HttpResponse::Ok().json(rules))
}

async fn strategies(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let name = state
        .db
        .environment_name(*id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    Ok(HttpResponse::Ok().json(for_environment(&name)))
}

async fn mt5_accounts(
    state: web::Data<AppState>,
    id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let accounts = state
        .db
        .environment_mt5_accounts(*id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    Ok(HttpResponse::Ok().json(accounts))
}

#[derive(Deserialize)]
struct Mt5AccountInput {
    server: String,
    login: String,
    password: String,
    account_type: String,
}

async fn create_mt5_account(
    state: web::Data<AppState>,
    id: web::Path<i64>,
    body: web::Json<Mt5AccountInput>,
) -> Result<HttpResponse, ApiError> {
    let server = body.server.trim();
    let login = body.login.trim();
    let account_type = body.account_type.trim().to_ascii_lowercase();
    if server.is_empty() || login.is_empty() || body.password.is_empty() {
        return Err(ApiError::BadRequest(
            "server, login, and password are required".into(),
        ));
    }
    if account_type != "mt5" {
        return Err(ApiError::BadRequest("account type must be mt5".into()));
    }
    if server.chars().count() > 255
        || login.chars().count() > 100
        || body.password.chars().count() > 255
    {
        return Err(ApiError::BadRequest(
            "MT5 account fields are too long".into(),
        ));
    }
    let account_id = state
        .db
        .create_environment_mt5_account(
            *id,
            &EnvironmentMt5AccountInput {
                server: server.to_owned(),
                login: login.to_owned(),
                password: body.password.clone(),
                account_type,
            },
        )
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":account_id})))
}

async fn delete_mt5_account(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    let (environment_id, account_id) = path.into_inner();
    if !state
        .db
        .delete_environment_mt5_account(environment_id, account_id)
        .await?
    {
        return Err(ApiError::NotFound("MT5 account not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

#[derive(Deserialize)]
struct AccountViewSettingsInput {
    equity_chart_range: Option<String>,
    history_range: Option<String>,
}

async fn update_account_view_settings(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
    body: web::Json<AccountViewSettingsInput>,
) -> Result<HttpResponse, ApiError> {
    let valid_range = |value: &str| matches!(value, "1day" | "1week" | "1month" | "1year" | "all");
    if body
        .equity_chart_range
        .as_deref()
        .is_some_and(|value| !valid_range(value))
        || body
            .history_range
            .as_deref()
            .is_some_and(|value| !valid_range(value))
    {
        return Err(ApiError::BadRequest("invalid account view range".into()));
    }
    let (environment_id, account_id) = path.into_inner();
    if !state
        .db
        .update_environment_account_view_settings(
            environment_id,
            account_id,
            body.equity_chart_range.as_deref(),
            body.history_range.as_deref(),
        )
        .await?
    {
        return Err(ApiError::NotFound("MT5 account not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

async fn account_strategies(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    let (environment_id, account_id) = path.into_inner();
    let strategies = state
        .db
        .environment_account_strategies(environment_id, account_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("MT5 account not found".into()))?;
    Ok(HttpResponse::Ok().json(strategies))
}

async fn account_trades(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    let (environment_id, account_id) = path.into_inner();
    let trades = state
        .db
        .environment_account_trades(environment_id, account_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("MT5 account not found".into()))?;
    Ok(HttpResponse::Ok().json(trades))
}

#[derive(Deserialize)]
struct AccountStrategyInputBody {
    strategy: String,
    symbol: String,
}

async fn create_account_strategy(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64)>,
    body: web::Json<AccountStrategyInputBody>,
) -> Result<HttpResponse, ApiError> {
    let (environment_id, account_id) = path.into_inner();
    let strategy = body.strategy.trim();
    let requested_symbol = body.symbol.trim();
    let symbol = if requested_symbol.eq_ignore_ascii_case("nq") {
        "USTEC"
    } else {
        requested_symbol
    };
    if strategy.is_empty() || symbol.is_empty() {
        return Err(ApiError::BadRequest(
            "strategy and symbol are required".into(),
        ));
    }
    if strategy.chars().count() > 100 || symbol.chars().count() > 100 {
        return Err(ApiError::BadRequest(
            "strategy and symbol must be 100 characters or fewer".into(),
        ));
    }
    let environment_name = state
        .db
        .environment_name(environment_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    // `registered_sleeves` AND NOT `for_environment`, which also lists the BOOK.
    //
    // The book is a SELECTION, not a strategy: `build_strategy` cannot construct
    // it, so a live row naming it is a slot that looks enabled and never fires.
    // The symbol check below cannot catch that either -- `required_symbol`
    // returns `None` for anything the runtime does not support, and
    // `symbol_matches_strategy` reads a missing requirement as "nothing to
    // violate" and lets it through.
    if !registered_sleeves(&environment_name)
        .iter()
        .any(|available| available.id == strategy)
    {
        return Err(ApiError::BadRequest(
            "strategy is not available for this environment".into(),
        ));
    }
    // The symbol is a text box and the live runtime routes orders with it, so a
    // typo here is a real order on the wrong instrument. Every sleeve of the
    // 2026-09-07 book declares the market it trades; hold the row to it.
    if !crate::live::portfolio::symbol_matches_strategy(strategy, symbol) {
        return Err(ApiError::BadRequest(format!(
            "{strategy} trades {}, not {symbol}",
            crate::live::portfolio::required_symbol(strategy).unwrap_or("nothing"),
        )));
    }
    let strategy_id = state
        .db
        .create_environment_account_strategy(
            environment_id,
            account_id,
            &AccountStrategyInput {
                strategy: strategy.to_owned(),
                symbol: symbol.to_owned(),
            },
        )
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":strategy_id})))
}

async fn delete_account_strategy(
    state: web::Data<AppState>,
    path: web::Path<(i64, i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    let (environment_id, account_id, strategy_id) = path.into_inner();
    if !state
        .db
        .delete_environment_account_strategy(environment_id, account_id, strategy_id)
        .await?
    {
        return Err(ApiError::NotFound("active strategy not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
