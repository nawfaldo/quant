use crate::{error::ApiError, state::AppState};
use actix_web::{HttpResponse, web};
use serde::Deserialize;
use serde_json::{Value, json};

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .service(
            web::resource("/api/settings")
                .route(web::get().to(get))
                .route(web::post().to(save)),
        )
        .service(
            web::resource("/api/march/settings")
                .route(web::get().to(get_march))
                .route(web::post().to(save_march)),
        )
        .service(
            web::resource("/api/march/layouts")
                .route(web::get().to(get_layouts))
                .route(web::post().to(save_layouts)),
        )
        .service(
            web::resource("/api/march/workspace")
                .route(web::get().to(get_workspace))
                .route(web::post().to(save_workspace)),
        );
}
async fn get(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.app_settings().await?))
}
#[derive(Deserialize)]
struct SettingsInput {
    from_date: String,
    to_date: String,
}
async fn save(
    state: web::Data<AppState>,
    input: web::Json<SettingsInput>,
) -> Result<HttpResponse, ApiError> {
    state
        .db
        .save_app_settings(&input.from_date, &input.to_date)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn get_march(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(state.db.march_settings().await?))
}
async fn save_march(
    state: web::Data<AppState>,
    input: web::Json<Value>,
) -> Result<HttpResponse, ApiError> {
    state.db.save_march_settings(&input).await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn get_layouts(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(
        state
            .db
            .get_json_setting("march_layouts", json!({}))
            .await?,
    ))
}
async fn save_layouts(
    state: web::Data<AppState>,
    input: web::Json<Value>,
) -> Result<HttpResponse, ApiError> {
    state.db.save_json_setting("march_layouts", &input).await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
async fn get_workspace(state: web::Data<AppState>) -> Result<HttpResponse, ApiError> {
    Ok(HttpResponse::Ok().json(
        state
            .db
            .get_json_setting("march_workspace", Value::Null)
            .await?,
    ))
}
async fn save_workspace(
    state: web::Data<AppState>,
    input: web::Json<Value>,
) -> Result<HttpResponse, ApiError> {
    state
        .db
        .save_json_setting("march_workspace", &input)
        .await?;
    Ok(HttpResponse::Ok().json(json!({"ok":true})))
}
