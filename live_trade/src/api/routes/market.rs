use crate::{error::ApiError, market, state::AppState};
use actix_web::{HttpResponse, web};
use serde::Deserialize;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/vix", web::get().to(vix))
        .route("/api/database/summary", web::get().to(summary))
        .route("/api/database/symbols", web::get().to(symbols))
        .route("/api/march/candles/bin", web::get().to(march_candles))
        .route("/api/march/fx-candles/bin", web::get().to(fx_candles))
        .route("/api/march/ticks", web::get().to(ticks));
}
#[derive(Deserialize)]
struct CandleQuery {
    #[serde(rename = "tf")]
    timeframe: Option<String>,
    symbol: Option<String>,
    from: Option<String>,
    to: Option<String>,
}
fn binary(data: Vec<u8>) -> HttpResponse {
    HttpResponse::Ok()
        .content_type("application/octet-stream")
        .body(data)
}
async fn vix(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(
        market::vix(
            &state.store,
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
        )
        .await?,
    ))
}
async fn summary(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(market::database_summary(&state.store).await?))
}
async fn symbols(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(market::database_symbols(&state.store).await?))
}
async fn march_candles(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    let symbol = query.symbol.as_deref().unwrap_or("nq");
    market::validate_symbol(symbol)?;
    Ok(binary(
        market::march_candles(
            &state.store,
            symbol,
            query.timeframe.as_deref().unwrap_or("1m"),
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
        )
        .await?,
    ))
}
async fn fx_candles(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    match market::fx_candles(
        &state.store,
        query.timeframe.as_deref().unwrap_or("1m"),
        market::date(query.from.as_deref()),
        market::date(query.to.as_deref()),
    )
    .await
    {
        Ok(data) => Ok(binary(data)),
        Err(error) if missing_optional_tick_table(&error) => {
            let mut empty = Vec::with_capacity(8);
            empty.extend_from_slice(&market::CANDLE_MAGIC.to_le_bytes());
            empty.extend_from_slice(&0u32.to_le_bytes());
            Ok(binary(empty))
        }
        Err(error) => Err(error),
    }
}
#[derive(Deserialize)]
struct TickQuery {
    symbol: Option<String>,
    since: Option<i64>,
    before: Option<i64>,
    limit: Option<i64>,
}
async fn ticks(
    state: web::Data<AppState>,
    query: web::Query<TickQuery>,
) -> Result<HttpResponse, ApiError> {
    match market::ticks(
        &state.store,
        query.symbol.as_deref().unwrap_or("nq"),
        query.since,
        query.before,
        query.limit,
    )
    .await
    {
        Ok(ticks) => Ok(HttpResponse::Ok().json(ticks)),
        Err(error) if missing_optional_tick_table(&error) => {
            Ok(HttpResponse::Ok().json(Vec::<market::Tick>::new()))
        }
        Err(error) => Err(error),
    }
}
fn missing_optional_tick_table(error: &ApiError) -> bool {
    matches!(error, ApiError::Data(detail) if detail.contains("table does not exist"))
}
