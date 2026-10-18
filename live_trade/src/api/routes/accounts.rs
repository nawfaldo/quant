use crate::{
    backtest::{cost_model, fundednext_cost_model},
    database::{AccountStrategyInput, NewMt5Account},
    error::ApiError,
    state::AppState,
    strategies::{registered, registered_sleeves},
};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::json;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/model", web::get().to(model))
        .route("/api/models", web::get().to(models))
        .route("/api/strategies", web::get().to(strategies))
        .service(
            web::resource("/api/accounts")
                .route(web::get().to(mt5_accounts))
                .route(web::post().to(create_mt5_account)),
        )
        .service(
            web::resource("/api/accounts/{account_id}")
                .route(web::put().to(update_account_view_settings))
                .route(web::delete().to(delete_mt5_account)),
        )
        .service(
            web::resource("/api/accounts/{account_id}/strategies")
                .route(web::get().to(account_strategies))
                .route(web::post().to(create_account_strategy)),
        )
        .service(
            web::resource("/api/accounts/{account_id}/trades").route(web::get().to(account_trades)),
        )
        .service(
            web::resource("/api/accounts/{account_id}/strategies/{strategy_id}")
                .route(web::delete().to(delete_account_strategy)),
        );
}

/// The cost model every backtest is priced under: Exness Pro, and only it.
///
/// READ-ONLY AND NOT STORED. It used to be a table of spread, slippage and
/// commission rows per environment, every one of which the engine overrode
/// with its own Exness Pro figures before a bar was replayed, and then a
/// per-environment copy of the compiled tables. It is now served straight from
/// `backtest::cost_model`, the same tables the engine charges.
async fn model() -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(cost_model()))
}

/// Every cost model a run may name: Exness Pro (the default) and FundedNext,
/// which prices the FundedNext book only.
async fn models() -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json([cost_model(), fundednext_cost_model()]))
}

/// The book, then its sleeves: everything a backtest can be asked to run.
async fn strategies() -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(registered()))
}

async fn mt5_accounts(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.account_overviews().await?))
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
        .create_mt5_account(&NewMt5Account {
            server: server.to_owned(),
            login: login.to_owned(),
            password: body.password.clone(),
            account_type,
        })
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":account_id})))
}

async fn delete_mt5_account(
    state: web::Data<AppState>,
    account_id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    if !state.db.delete_mt5_account(*account_id).await? {
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
    account_id: web::Path<i64>,
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
    if !state
        .db
        .update_account_view_settings(
            *account_id,
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
    account_id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let strategies = state
        .db
        .active_account_strategies(*account_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("MT5 account not found".into()))?;
    Ok(HttpResponse::Ok().json(strategies))
}

async fn account_trades(
    state: web::Data<AppState>,
    account_id: web::Path<i64>,
) -> Result<HttpResponse, ApiError> {
    let trades = state
        .db
        .account_trades(*account_id)
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
    account_id: web::Path<i64>,
    body: web::Json<AccountStrategyInputBody>,
) -> Result<HttpResponse, ApiError> {
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
    // `registered_sleeves` AND NOT `registered`, which also lists the BOOK.
    //
    // The book is a SELECTION, not a strategy: `build_strategy` cannot construct
    // it, so a live row naming it is a slot that looks enabled and never fires.
    // The symbol check below cannot catch that either -- `required_symbol`
    // returns `None` for anything the runtime does not support, and
    // `symbol_matches_strategy` reads a missing requirement as "nothing to
    // violate" and lets it through.
    if !registered_sleeves()
        .iter()
        .any(|available| available.id == strategy)
    {
        return Err(ApiError::BadRequest(
            "strategy is not a sleeve of the book".into(),
        ));
    }
    // The symbol is a text box and the live runtime routes orders with it, so a
    // typo here is a real order on the wrong instrument. Every sleeve of the
    // book declares the market it trades; hold the row to it.
    if !crate::live::portfolio::symbol_matches_strategy(strategy, symbol) {
        return Err(ApiError::BadRequest(format!(
            "{strategy} trades {}, not {symbol}",
            crate::live::portfolio::required_symbol(strategy).unwrap_or("nothing"),
        )));
    }
    let strategy_id = state
        .db
        .create_active_account_strategy(
            *account_id,
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
    path: web::Path<(i64, i64)>,
) -> Result<HttpResponse, ApiError> {
    let (account_id, strategy_id) = path.into_inner();
    if !state
        .db
        .delete_account_strategy_of(account_id, strategy_id)
        .await?
    {
        return Err(ApiError::NotFound("active strategy not found".into()));
    }
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
