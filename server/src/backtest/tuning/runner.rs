use super::score::score;
use crate::{
    backtest::{self, RunRequest},
    error::ApiError,
    state::AppState,
    strategies::StrategyEnvironment,
};
use actix_web::{HttpResponse, web};
use serde::Serialize;
use serde_json::{Value, json};
use std::time::Instant;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route("/api/tune", web::post().to(start))
        .route("/api/tune/status", web::get().to(status))
        .route("/api/tune/results", web::get().to(results))
        .route("/api/tune/results.json", web::get().to(results))
        .route("/api/tune/results.csv", web::get().to(csv))
        .route("/api/tune/report.md", web::get().to(markdown))
        .route("/api/tune/heatmap.json", web::get().to(heatmap));
}

fn text(body: &Value, key: &str) -> String {
    body.get(key)
        .map(|v| match v {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            _ => String::new(),
        })
        .unwrap_or_default()
}

fn list(value: String, default: f64, allow_zero: bool) -> Result<Vec<f64>, ApiError> {
    if value.trim().is_empty() {
        return Ok(vec![default]);
    }
    let values = value
        .split([',', ' '])
        .filter(|v| !v.is_empty())
        .map(|value| {
            value
                .parse::<f64>()
                .ok()
                .filter(|number| {
                    number.is_finite()
                        && if allow_zero {
                            *number >= 0.0
                        } else {
                            *number > 0.0
                        }
                })
                .ok_or_else(|| ApiError::BadRequest("invalid tuning parameter list".into()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    if values.is_empty() || values.len() > 16 {
        return Err(ApiError::BadRequest(
            "each tuning dimension must contain 1 to 16 values".into(),
        ));
    }
    Ok(values)
}

struct ParameterGrid {
    use_volatility: bool,
    base_lots: Vec<f64>,
    leverages: Vec<f64>,
    vol_targets: Vec<f64>,
    vol_halflifes: Vec<f64>,
    vol_max_multipliers: Vec<f64>,
    vol_minimum_days: Vec<f64>,
}

impl ParameterGrid {
    fn from_body(body: &Value) -> Result<Self, ApiError> {
        let use_volatility = text(body, "sizing") == "Vol Target";
        Ok(Self {
            use_volatility,
            base_lots: list(text(body, "baseLot"), 1.0, false)?,
            leverages: list(text(body, "leverage"), 1.0, false)?,
            vol_targets: if use_volatility {
                list(text(body, "volTarget"), 0.20, false)?
            } else {
                vec![0.20]
            },
            vol_halflifes: if use_volatility {
                list(text(body, "volHalflife"), 20.0, false)?
            } else {
                vec![20.0]
            },
            vol_max_multipliers: if use_volatility {
                list(text(body, "volMaxMult"), 3.0, false)?
            } else {
                vec![3.0]
            },
            vol_minimum_days: if use_volatility {
                list(text(body, "volMinDays"), 30.0, true)?
            } else {
                vec![30.0]
            },
        })
    }

    fn len(&self) -> usize {
        self.base_lots.len()
            * self.leverages.len()
            * self.vol_targets.len()
            * self.vol_halflifes.len()
            * self.vol_max_multipliers.len()
            * self.vol_minimum_days.len()
    }

    fn combinations(&self) -> Vec<Combination> {
        let mut output = Vec::with_capacity(self.len());
        for &base_lot in &self.base_lots {
            for &leverage in &self.leverages {
                for &vol_target in &self.vol_targets {
                    for &vol_halflife in &self.vol_halflifes {
                        for &vol_max_multiplier in &self.vol_max_multipliers {
                            for &vol_minimum_days in &self.vol_minimum_days {
                                output.push(Combination {
                                    base_lot,
                                    leverage,
                                    vol_target,
                                    vol_halflife,
                                    vol_max_multiplier,
                                    vol_minimum_days,
                                });
                            }
                        }
                    }
                }
            }
        }
        output
    }
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
struct Combination {
    base_lot: f64,
    leverage: f64,
    vol_target: f64,
    vol_halflife: f64,
    vol_max_multiplier: f64,
    vol_minimum_days: f64,
}

impl Combination {
    fn apply(self, request: &RunRequest) -> RunRequest {
        let mut request = request.clone();
        request.base_lot = Some(self.base_lot.to_string());
        request.leverage = Some(self.leverage.to_string());
        request.vol_target = Some(self.vol_target.to_string());
        request.vol_halflife = Some(self.vol_halflife.to_string());
        request.vol_max_mult = Some(self.vol_max_multiplier.to_string());
        request.vol_min_days = Some(self.vol_minimum_days.to_string());
        request
    }
}

async fn start(
    state: web::Data<AppState>,
    body: web::Json<Value>,
) -> Result<HttpResponse, ApiError> {
    if state.tune.get()["status"] == "running" {
        return Err(ApiError::Conflict("a tuning run is already active".into()));
    }
    if text(&body, "strategy") != "Night Drift" {
        return Err(ApiError::BadRequest(
            "only Night Drift has tunable sizing parameters".into(),
        ));
    }

    let grid = ParameterGrid::from_body(&body)?;
    let total = grid.len();
    if total > 10_000 {
        return Err(ApiError::BadRequest("invalid grid size".into()));
    }
    let request = RunRequest {
        strategy_environment: StrategyEnvironment::default(),
        environment_id: Some(text(&body, "environmentId")),
        strategy: text(&body, "strategy"),
        symbol: text(&body, "symbol"),
        instrument: text(&body, "instrument"),
        initial_balance: text(&body, "initialBalance"),
        base_lot: Some(text(&body, "baseLot")),
        leverage: Some(text(&body, "leverage")),
        sizing: Some(text(&body, "sizing")),
        vol_target: Some(text(&body, "volTarget")),
        vol_halflife: Some(text(&body, "volHalflife")),
        vol_max_mult: Some(text(&body, "volMaxMult")),
        vol_min_days: Some(text(&body, "volMinDays")),
        from_date: text(&body, "fromDate"),
        to_date: text(&body, "toDate"),
    };
    let environment = request
        .environment_id()?
        .ok_or_else(|| ApiError::BadRequest("missing environment id".into()))?;
    let name = state
        .db
        .environment_name(environment)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    let mut request = request;
    request.strategy_environment = StrategyEnvironment::from_name(&name)
        .ok_or_else(|| ApiError::BadRequest("environment has no registered strategies".into()))?;
    let costs = state.db.environment_costs(Some(environment)).await?;
    state
        .tune
        .set(json!({"status":"running","progress":0,"total":total}));
    let app = state.get_ref().clone();
    let started = Instant::now();
    actix_web::rt::spawn(async move {
        match run_grid(&app, &request, costs, grid).await {
            Ok((result, summary)) => app.tune.set(json!({
                "status": "completed",
                "total": total,
                "elapsed": started.elapsed().as_millis(),
                "result": result,
                "summary": summary,
            })),
            Err(error) => app.tune.set(
                json!({"status":"failed","error":error.to_string(),"progress":0,"total":total}),
            ),
        }
    });
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}

async fn run_grid(
    state: &AppState,
    request: &RunRequest,
    costs: crate::database::EnvironmentCosts,
    grid: ParameterGrid,
) -> Result<(Value, Value), ApiError> {
    let prepared = backtest::prepare(&state.questdb, request, costs).await?;
    let use_volatility = grid.use_volatility;
    let combinations = grid.combinations();
    let total = combinations.len();
    let mut results = Vec::with_capacity(total);
    let mut metrics = Vec::with_capacity(total);

    for (index, combination) in combinations.into_iter().enumerate() {
        let run_request = combination.apply(request);
        let run = backtest::execute_tuned(&prepared, &run_request)?;
        let (result, metric) = combo_result(combination, &run.body, use_volatility);
        results.push(result);
        metrics.push(metric);
        state.tune.set(json!({
            "status": "running",
            "progress": index + 1,
            "total": total,
        }));
    }

    let mut best_growth = results.clone();
    best_growth.sort_by(|left, right| metric(right, "growth").total_cmp(&metric(left, "growth")));
    let mut minimum_drawdown = results.clone();
    minimum_drawdown
        .sort_by(|left, right| metric(left, "drawdown").total_cmp(&metric(right, "drawdown")));
    let mut best_score = results.clone();
    best_score.sort_by(|left, right| metric(right, "score").total_cmp(&metric(left, "score")));

    let result = json!({
        "totalCombos": total,
        "bestGrowth": best_growth.into_iter().take(10).collect::<Vec<_>>(),
        "minDrawdown": minimum_drawdown.into_iter().take(10).collect::<Vec<_>>(),
        "bestOfTwo": best_score.into_iter().take(10).collect::<Vec<_>>(),
        "grid": results,
    });
    Ok((result, tune_summary(&metrics)))
}

fn combo_result(combination: Combination, report: &Value, use_volatility: bool) -> (Value, Value) {
    let value = |name: &str| report.get(name).and_then(Value::as_f64).unwrap_or(0.0);
    let sharpe = value("sharpe");
    let profit_factor = value("profit_factor");
    let drawdown = value("max_drawdown");
    let score = score(sharpe, profit_factor, drawdown);

    let mut result = json!({
        "baseLot": combination.base_lot,
        "leverage": combination.leverage,
        "growth": round4(value("net_growth")),
        "drawdown": round4(drawdown),
        "score": round4(score),
    });
    if use_volatility {
        result["volTarget"] = Value::from(combination.vol_target);
        result["volHalflife"] = Value::from(combination.vol_halflife);
        result["volMaxMult"] = Value::from(combination.vol_max_multiplier);
        result["volMinDays"] = Value::from(combination.vol_minimum_days);
    }
    let metric = json!({
        "baseLot": combination.base_lot,
        "leverage": combination.leverage,
        "volTarget": combination.vol_target,
        "volHalflife": combination.vol_halflife,
        "volMaxMult": combination.vol_max_multiplier,
        "volMinDays": combination.vol_minimum_days as u32,
        "finalBalance": round2(value("final_bal")),
        "returnPct": round4(value("net_growth")),
        "profitFactor": round4(profit_factor),
        "sharpe": round4(sharpe),
        "expectancy": round4(value("expectancy")),
        "winRate": round4(value("win_rate")),
        "maxDrawdown": round4(drawdown),
        "avgDrawdown": round4(value("avg_drawdown")),
        "score": round4(score),
    });
    (result, metric)
}

fn tune_summary(metrics: &[Value]) -> Value {
    if metrics.is_empty() {
        return json!({"empty":true});
    }
    let best = |field: &str, minimum: bool| {
        metrics
            .iter()
            .min_by(|left, right| {
                let left = metric(left, field);
                let right = metric(right, field);
                if minimum {
                    left.total_cmp(&right)
                } else {
                    right.total_cmp(&left)
                }
            })
            .cloned()
            .unwrap_or(Value::Null)
    };
    let average = |field: &str| {
        round4(
            metrics
                .iter()
                .map(|value| metric(value, field))
                .sum::<f64>()
                / metrics.len() as f64,
        )
    };
    json!({
        "bestByScore": best("score", false),
        "bestGrowth": best("returnPct", false),
        "bestSharpe": best("sharpe", false),
        "bestProfitFactor": best("profitFactor", false),
        "lowestDrawdown": best("maxDrawdown", true),
        "averageMetrics": {
            "returnPct": average("returnPct"),
            "profitFactor": average("profitFactor"),
            "sharpe": average("sharpe"),
            "expectancy": average("expectancy"),
            "winRate": average("winRate"),
            "maxDrawdown": average("maxDrawdown"),
            "avgDrawdown": average("avgDrawdown"),
            "score": average("score"),
        },
    })
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn metric(value: &Value, name: &str) -> f64 {
    value.get(name).and_then(Value::as_f64).unwrap_or(0.0)
}
async fn status(state: web::Data<AppState>) -> HttpResponse {
    HttpResponse::Ok().json(state.tune.get())
}
fn completed(state: &AppState) -> Result<Value, ApiError> {
    let value = state.tune.get();
    value
        .get("result")
        .cloned()
        .ok_or_else(|| ApiError::NotFound("no completed tune in memory".into()))
}
async fn results(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(completed(&state)?))
}
async fn csv(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    let result = completed(&state)?;
    let grid = result
        .get("grid")
        .and_then(Value::as_array)
        .ok_or_else(|| ApiError::NotFound("no completed tune in memory".into()))?;
    let mut out =
        "growth,drawdown,score,baseLot,leverage,volTarget,volHalflife,volMaxMult,volMinDays\n"
            .to_owned();
    for row in grid {
        out.push_str(&format!(
            "{},{},{},{},{},{},{},{},{}\n",
            row["growth"],
            row["drawdown"],
            row["score"],
            row["baseLot"],
            row["leverage"],
            row["volTarget"],
            row["volHalflife"],
            row["volMaxMult"],
            row["volMinDays"]
        ));
    }
    Ok(HttpResponse::Ok().content_type("text/csv").body(out))
}
async fn markdown(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    let result = completed(&state)?;
    Ok(HttpResponse::Ok()
        .content_type("text/markdown")
        .body(format!(
            "# Tuning report\n\nTotal combinations: {}\n",
            result["totalCombos"]
        )))
}
async fn heatmap(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    let result = completed(&state)?;
    Ok(HttpResponse::Ok().json(json!({"grid":result["grid"]})))
}
