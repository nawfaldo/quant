use crate::{backtest, live};
use actix_web::{HttpResponse, http::Method, web};
use serde_json::json;

mod backtests;
mod environments;
mod march;
mod market_data;
mod settings;

pub fn configure(config: &mut web::ServiceConfig) {
    config
        .route(
            "/health",
            web::get().to(|| async { HttpResponse::Ok().json(json!({"status":"ok"})) }),
        )
        .configure(settings::configure)
        .configure(environments::configure)
        .configure(market_data::configure)
        .configure(backtests::configure)
        .configure(backtest::tuning::configure)
        .configure(march::configure)
        .configure(live::mt5::bridge::configure)
        .route(
            "/{tail:.*}",
            web::method(Method::OPTIONS).to(|| async {
                HttpResponse::Ok()
                    .content_type("application/json")
                    .insert_header((
                        "Access-Control-Allow-Methods",
                        "GET, POST, PUT, DELETE, OPTIONS",
                    ))
                    .insert_header(("Access-Control-Allow-Headers", "content-type"))
                    .body("")
            }),
        )
        .default_service(web::to(|| async {
            HttpResponse::NotFound().json(json!({"error":"route not found"}))
        }));
}
