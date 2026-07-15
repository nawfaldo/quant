use crate::{database::CreateEnvironment, error::ApiError, state::AppState};
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
        .service(
            web::resource("/api/environments/{id}/rules")
                .route(web::get().to(rules))
                .route(web::post().to(create_rule))
                .route(web::put().to(update_rule))
                .route(web::delete().to(delete_rule)),
        )
        .service(
            web::resource("/api/environments/{id}/strategies").route(web::get().to(strategies)),
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
    if !name
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(ApiError::BadRequest(
            "name must use only letters, numbers, hyphens, or underscores".into(),
        ));
    }
    let is_mt5 = body
        .get("isMt5")
        .and_then(|v| v.as_bool().or_else(|| v.as_str().map(|s| s == "true")))
        .unwrap_or(false);
    let id = state
        .db
        .create_environment(&CreateEnvironment {
            name,
            is_mt5,
            server: if is_mt5 {
                string("server")
            } else {
                String::new()
            },
            login: if is_mt5 {
                string("login")
            } else {
                String::new()
            },
            password: if is_mt5 {
                string("password")
            } else {
                String::new()
            },
        })
        .await?;
    Ok(HttpResponse::Ok().json(json!({"id":id})))
}
async fn rules(state: web::Data<AppState>, id: web::Path<i64>) -> Result<HttpResponse, ApiError> {
    let result = state
        .db
        .environment_rules(*id)
        .await?
        .ok_or_else(|| ApiError::NotFound("environment not found".into()))?;
    Ok(HttpResponse::Ok().json(result))
}
#[derive(Deserialize)]
struct RuleInput {
    #[serde(rename = "type")]
    rule_type: String,
    value: Option<Value>,
}
fn parsed_rule(input: &RuleInput) -> Result<(&str, f64), ApiError> {
    if !["spread", "slippage", "commission"].contains(&input.rule_type.as_str()) {
        return Err(ApiError::BadRequest("invalid environment rule type".into()));
    }
    let value = input
        .value
        .as_ref()
        .and_then(|v| v.as_f64().or_else(|| v.as_str()?.parse().ok()))
        .ok_or_else(|| ApiError::BadRequest("value must be a non-negative number".into()))?;
    if !value.is_finite() || value < 0.0 {
        return Err(ApiError::BadRequest(
            "value must be a non-negative number".into(),
        ));
    }
    Ok((&input.rule_type, value))
}
async fn create_rule(
    state: web::Data<AppState>,
    id: web::Path<i64>,
    input: web::Json<RuleInput>,
) -> Result<HttpResponse, ApiError> {
    let (rule_type, value) = parsed_rule(&input)?;
    state
        .db
        .create_environment_rule(*id, rule_type, value)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn update_rule(
    state: web::Data<AppState>,
    id: web::Path<i64>,
    input: web::Json<RuleInput>,
) -> Result<HttpResponse, ApiError> {
    let (rule_type, value) = parsed_rule(&input)?;
    state
        .db
        .update_environment_rule(*id, rule_type, value)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn delete_rule(
    state: web::Data<AppState>,
    id: web::Path<i64>,
    input: web::Json<RuleInput>,
) -> Result<HttpResponse, ApiError> {
    if !["spread", "slippage", "commission"].contains(&input.rule_type.as_str()) {
        return Err(ApiError::BadRequest("invalid environment rule type".into()));
    }
    state
        .db
        .delete_environment_rule(*id, &input.rule_type)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
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
    let result = if name == "idk" {
        json!([
            { "id": "night_drift", "name": "Night Drift" },
            { "id": "noise_momentum", "name": "Noise Momentum" },
        ])
    } else {
        json!([])
    };
    Ok(HttpResponse::Ok().json(result))
}
