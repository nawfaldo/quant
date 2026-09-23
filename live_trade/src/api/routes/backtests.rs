use crate::{
    backtest::{self, RunRequest},
    database::CombineSource,
    error::ApiError,
    fx::{self, Repriced},
    state::AppState,
    strategies::idk::exness_combined,
};
use actix_web::{HttpResponse, web};
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
        match fx::reprice(&state.store, &source.trades).await {
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
/// The whole book, expressed as the combine request a run of it really is.
///
/// `BOOK_NAME` is offered in the strategy list like any other entry, so a person
/// can pick the book without ticking twenty boxes -- but there is no such
/// STRATEGY. It is twenty of them on one balance, so the single-run path hands
/// it to the combined one rather than growing a second engine.
fn book_request(request: &RunRequest) -> Option<CombineRequest> {
    (request.strategy == exness_combined::BOOK_NAME).then(|| CombineRequest {
        environment_id: request.environment_id.clone(),
        strategies: exness_combined::BOOK
            .into_iter()
            .map(|sleeve| sleeve.display().to_owned())
            .collect(),
        symbol: request.symbol.clone(),
        instrument: request.instrument.clone(),
        initial_balance: request.initial_balance.clone(),
        from_date: request.from_date.clone(),
        to_date: request.to_date.clone(),
        include_fx: false,
    })
}

async fn run(
    state: web::Data<AppState>,
    request: web::Json<RunRequest>,
) -> Result<HttpResponse, ApiError> {
    let mut request = request.into_inner();
    if let Some(book) = book_request(&request) {
        let (_, run, mut result) = compute_combine(&state, &book).await?;
        attach_fx_preview(&state, &run, &mut result).await;
        return Ok(HttpResponse::Ok().json(result.body));
    }
    configure_strategy_environment(&state, &mut request).await?;
    let mut result = backtest::run(&state.store, &request).await?;
    attach_fx_preview(&state, &request, &mut result).await;
    Ok(HttpResponse::Ok().json(result.body))
}
async fn save_run(
    state: web::Data<AppState>,
    request: web::Json<RunRequest>,
) -> Result<HttpResponse, ApiError> {
    let mut request = request.into_inner();
    if let Some(book) = book_request(&request) {
        let (environment, _, result) = compute_combine(&state, &book).await?;
        let id = state
            .db
            .save_backtest(
                exness_combined::BOOK_ID,
                environment,
                &result.body,
                &result.trades,
                None,
            )
            .await?;
        return Ok(HttpResponse::Ok().json(json!({"id":id})));
    }
    configure_strategy_environment(&state, &mut request).await?;
    let environment_id = request.environment_id()?;
    let result = backtest::run(&state.store, &request).await?;
    let strategy = strategy_code(&request.strategy)?;
    let id = state
        .db
        .save_backtest(strategy, environment_id, &result.body, &result.trades, None)
        .await?;
    if supports_fx(&request.symbol, &request.instrument) {
        match fx::reprice(&state.store, &result.trades).await {
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

/// The stored identifier for a strategy display name.
///
/// A THIRD SPELLING OF THE SAME SLEEVE, on top of the display name the API takes
/// and the snake_case id the live runtime uses. Saved rows outlive the code that
/// wrote them, so the database gets a key that changes only when the sleeve
/// itself does -- rewording a display name must not orphan every backtest run
/// under it.
///
/// Delegated to `Sleeve::code` rather than transcribed here: a name this table
/// knows and the registry does not would store a run under a strategy the engine
/// cannot build.
fn strategy_code(name: &str) -> Result<&'static str, ApiError> {
    crate::strategies::idk::exness_combined::Sleeve::from_display(name)
        .map(|sleeve| sleeve.code())
        .ok_or_else(|| ApiError::BadRequest("unknown strategy".into()))
}

/// Resolves the run's environment name into its registered strategy set.
///
/// It used to also return that environment's spread, slippage and commission.
/// Those were removed: the only cost model is Exness Pro, and the engine holds
/// its measured figures itself.
async fn configure_strategy_environment(
    state: &AppState,
    request: &mut RunRequest,
) -> Result<(), ApiError> {
    let environment_id = request
        .environment_id()?
        .ok_or_else(|| ApiError::BadRequest("missing environment id".into()))?;
    let name = state
        .db
        .environment_name(environment_id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    if crate::strategies::for_environment(&name).is_empty() {
        return Err(ApiError::BadRequest(
            "environment has no registered strategies".into(),
        ));
    }
    Ok(())
}

async fn attach_fx_preview(
    state: &AppState,
    request: &RunRequest,
    result: &mut backtest::RunResult,
) {
    if !supports_fx(&request.symbol, &request.instrument) {
        return;
    }

    match fx::reprice(&state.store, &result.trades).await {
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
    let mut grouped = std::collections::BTreeMap::<String, Vec<_>>::new();
    for name in &repriced.strategies {
        grouped.entry(name.clone()).or_default();
    }
    for trade in &repriced.trades {
        let name = if trade.strategy.is_empty() {
            strategy.to_owned()
        } else {
            trade.strategy.clone()
        };
        grouped.entry(name).or_default().push(trade.clone());
    }
    let sources = grouped
        .into_iter()
        .enumerate()
        .map(|(id, (strategy, trades))| CombineSource {
            id: id as i64,
            strategy,
            symbol: symbol.to_owned(),
            instrument: "forex".to_owned(),
            initial_balance,
            trades,
        })
        .collect::<Vec<_>>();
    let mut body = backtest::combine_realized(&sources, initial_balance, None, None).body;
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

/// A Combine run: several strategies trading one shared account over a single
/// date range. Not to be confused with merging saved backtests after the fact,
/// which is what `/api/backtests/{id}` and `combine_realized` do.
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct CombineRequest {
    environment_id: Option<String>,
    strategies: Vec<String>,
    symbol: String,
    instrument: String,
    initial_balance: String,
    from_date: String,
    to_date: String,
    /// March/USTEC repricing can take much longer than the native backtest.
    /// The web client requests it only when that result view is opened.
    #[serde(default)]
    include_fx: bool,
}
impl CombineRequest {
    /// The shared account the strategies trade, expressed as a normal run
    /// request; `strategy` carries the first name only so environment lookup
    /// and validation reuse the single-strategy path.
    fn as_run_request(&self) -> Result<RunRequest, ApiError> {
        let first = self
            .strategies
            .first()
            .ok_or_else(|| ApiError::BadRequest("need at least 2 strategies".into()))?;
        Ok(RunRequest {
            environment_id: self.environment_id.clone(),
            strategy: first.clone(),
            symbol: self.symbol.clone(),
            instrument: self.instrument.clone(),
            initial_balance: self.initial_balance.clone(),
            from_date: self.from_date.clone(),
            to_date: self.to_date.clone(),
        })
    }
}

/// The resolved account (`RunRequest`) is returned alongside the result so the
/// caller can reuse it for the FX preview, exactly as the single-run path does.
async fn compute_combine(
    state: &AppState,
    request: &CombineRequest,
) -> Result<(Option<i64>, RunRequest, backtest::RunResult), ApiError> {
    if request.strategies.len() < 2 {
        return Err(ApiError::BadRequest("need at least 2 strategies".into()));
    }
    let mut run = request.as_run_request()?;
    configure_strategy_environment(state, &mut run).await?;
    let environment = run.environment_id()?;
    let mut result = backtest::run_combined(&state.store, &run, &request.strategies).await?;
    result.body["strategy"] = Value::String(combine_label(&request.strategies));
    Ok((environment, run, result))
}

/// What to call a combined run: the BOOK if that is what it is, else the join.
///
/// A run of all twenty sleeves is a run of one sealed selection and is named
/// after it. A run of nineteen, or of three, is a research selection and keeps
/// the composite name -- calling that the book would file a different strategy
/// under the book's record.
fn combine_label(strategies: &[String]) -> String {
    if exness_combined::is_whole_book(strategies.iter().map(String::as_str)) {
        return exness_combined::BOOK_NAME.to_owned();
    }
    strategies.join(" + ")
}

/// The stored id for a combined run, under the same rule.
fn combine_code(strategies: &[String]) -> Result<String, ApiError> {
    if exness_combined::is_whole_book(strategies.iter().map(String::as_str)) {
        return Ok(exness_combined::BOOK_ID.to_owned());
    }
    Ok(strategies
        .iter()
        .map(|name| strategy_code(name))
        .collect::<Result<Vec<_>, _>>()?
        .join(" + "))
}
async fn combine(
    state: web::Data<AppState>,
    request: web::Json<CombineRequest>,
) -> Result<HttpResponse, ApiError> {
    let (_, run, mut result) = compute_combine(&state, &request).await?;
    if request.include_fx {
        attach_fx_preview(&state, &run, &mut result).await;
    }
    Ok(HttpResponse::Ok().json(result.body))
}
async fn save_combine(
    state: web::Data<AppState>,
    request: web::Json<CombineRequest>,
) -> Result<HttpResponse, ApiError> {
    let (environment, _, result) = compute_combine(&state, &request).await?;
    let strategy = combine_code(&request.strategies)?;
    let id = state
        .db
        .save_backtest(&strategy, environment, &result.body, &result.trades, None)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(strategy: &str) -> RunRequest {
        serde_json::from_value(json!({
            "strategy": strategy,
            "symbol": "nq",
            "instrument": "forex",
            "initialBalance": "400",
            "fromDate": "2025-01-01",
            "toDate": "2026-08-21",
        }))
        .expect("a run request")
    }

    /// Picking the book runs all twenty sleeves, not one strategy called the
    /// book. There is no such strategy: `build_strategy` cannot construct it,
    /// and a single-strategy run of it would fail with "unknown strategy".
    #[test]
    fn the_book_name_expands_to_every_sleeve() {
        let book = book_request(&request(exness_combined::BOOK_NAME)).expect("the book expands");
        let expected: Vec<String> = exness_combined::BOOK
            .into_iter()
            .map(|sleeve| sleeve.display().to_owned())
            .collect();
        assert_eq!(book.strategies, expected);
        assert!(exness_combined::is_whole_book(
            book.strategies.iter().map(String::as_str)
        ));
        // The run's own account settings are carried across untouched.
        assert_eq!(book.initial_balance, "400");
        assert_eq!(book.from_date, "2025-01-01");
    }

    /// A sleeve is still a sleeve. Only the book's own name takes the fan-out,
    /// or every single-strategy run would silently become a twenty-sleeve one.
    #[test]
    fn a_sleeve_name_is_left_alone() {
        assert!(book_request(&request("NQ OFI")).is_none());
        assert!(book_request(&request("ETHUSD Idio Break")).is_none());
        assert!(book_request(&request("BTC Maroy Ladder")).is_none());
    }

    /// A run of all twenty is RECORDED as the book; a partial selection keeps
    /// its own composite name, because filing it under the book's id would make
    /// the stored row a claim about a strategy that was never run.
    #[test]
    fn only_the_whole_book_is_labelled_as_the_book() {
        let all: Vec<String> = exness_combined::BOOK
            .into_iter()
            .map(|sleeve| sleeve.display().to_owned())
            .collect();
        assert_eq!(combine_label(&all), exness_combined::BOOK_NAME);
        assert_eq!(combine_code(&all).unwrap(), exness_combined::BOOK_ID);

        let partial = all[..3].to_vec();
        assert_eq!(combine_label(&partial), partial.join(" + "));
        let code = combine_code(&partial).unwrap();
        assert!(code.contains(" + "), "{code}");
        assert_ne!(code, exness_combined::BOOK_ID);
    }
}
