use crate::{backtest, error::ApiError, market, state::AppState};
use actix_web::{HttpResponse, web};
use serde::Deserialize;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/candles/bin", web::get().to(candles))
        .route("/api/vwap/bin", web::get().to(vwap))
        .route("/api/vix", web::get().to(vix))
        .route("/api/database/summary", web::get().to(summary))
        .route("/api/march/candles/bin", web::get().to(march_candles))
        .route("/api/march/fx-candles/bin", web::get().to(fx_candles))
        .route("/api/march/ticks", web::get().to(ticks))
        .route(
            "/api/march/indicators/bookmap-heatmap/bin",
            web::get().to(bookmap_heatmap),
        )
        .route(
            "/api/march/indicators/volume-delta",
            web::get().to(volume_delta),
        )
        .route(
            "/api/march/indicators/volume-profile-delta",
            web::get().to(volume_profile_delta),
        )
        .route(
            "/api/march/indicators/noise-area",
            web::get().to(noise_area),
        );
}
#[derive(Deserialize)]
struct CandleQuery {
    #[serde(rename = "tf")]
    timeframe: Option<String>,
    symbol: Option<String>,
    from: Option<String>,
    to: Option<String>,
    since: Option<i64>,
    tick_size: Option<f64>,
}
fn binary(data: Vec<u8>) -> HttpResponse {
    HttpResponse::Ok()
        .content_type("application/octet-stream")
        .body(data)
}
async fn candles(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    let timeframe = match &query.timeframe {
        Some(value) => value.clone(),
        None => state.db.default_timeframe().await?,
    };
    let (from_default, to_default) = state.db.date_range().await?;
    let from = market::date(query.from.as_deref());
    let to = market::date(query.to.as_deref());
    Ok(binary(
        market::candles(
            &state.questdb,
            query.symbol.as_deref().unwrap_or("nq"),
            &timeframe,
            if from.is_empty() { &from_default } else { from },
            if to.is_empty() { &to_default } else { to },
        )
        .await?,
    ))
}
async fn vwap(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(binary(market::vwap(&state.questdb).await?))
}
async fn vix(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(
        market::vix(
            &state.questdb,
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
        )
        .await?,
    ))
}
async fn summary(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(market::database_summary(&state.questdb).await?))
}
async fn march_candles(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    let symbol = query.symbol.as_deref().unwrap_or("nq");
    if !market::SYMBOLS.contains(&symbol) {
        return Err(ApiError::BadRequest("unknown symbol for march".into()));
    }
    Ok(binary(
        market::march_candles(
            &state.questdb,
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
        &state.questdb,
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
}
async fn ticks(
    state: web::Data<AppState>,
    query: web::Query<TickQuery>,
) -> Result<HttpResponse, ApiError> {
    match market::ticks(
        &state.questdb,
        query.symbol.as_deref().unwrap_or("nq"),
        query.since,
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
    matches!(error, ApiError::QuestDb(detail) if detail.contains("table does not exist"))
}
async fn volume_delta(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(
        market::volume_delta(
            &state.questdb,
            query.symbol.as_deref().unwrap_or("nq"),
            query.timeframe.as_deref().unwrap_or("1m"),
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
        )
        .await?,
    ))
}
async fn volume_profile_delta(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    let tick_size = query.tick_size.unwrap_or(5.0);
    Ok(HttpResponse::Ok().json(
        market::volume_profile_delta(
            &state.questdb,
            query.symbol.as_deref().unwrap_or("nq"),
            tick_size,
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
        )
        .await?,
    ))
}

async fn bookmap_heatmap(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    Ok(binary(
        market::bookmap_heatmap(
            &state.questdb,
            query.symbol.as_deref().unwrap_or("nq"),
            query.timeframe.as_deref().unwrap_or("1m"),
            market::date(query.from.as_deref()),
            market::date(query.to.as_deref()),
            query.since,
        )
        .await?,
    ))
}
async fn noise_area(
    state: web::Data<AppState>,
    query: web::Query<CandleQuery>,
) -> Result<HttpResponse, ApiError> {
    let points = backtest::noise_area(
        &state.questdb,
        query.symbol.as_deref().unwrap_or("nq"),
        market::date(query.from.as_deref()),
        market::date(query.to.as_deref()),
    )
    .await?;

    Ok(HttpResponse::Ok().json(points))
}
