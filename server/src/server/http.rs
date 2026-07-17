use crate::{live, server::routes, state::AppState};
use actix_cors::Cors;
use actix_web::{App, HttpServer, middleware, web};
use anyhow::Context;

pub async fn serve(state: AppState, bind: (&str, u16)) -> anyhow::Result<()> {
    let workers = std::env::var("ACTIX_WORKERS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(2usize);

    if let Err(error) = state.initialize_march().await {
        tracing::warn!(%error, "unable to warm active March strategy");
    }
    live::bookmap::spawn(state.clone());

    tracing::info!(
        host = bind.0,
        port = bind.1,
        workers,
        "backend_rust listening"
    );
    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(state.clone()))
            .app_data(web::PayloadConfig::new(2 * 1024 * 1024))
            .wrap(middleware::Logger::default())
            .wrap(
                Cors::default()
                    .allow_any_origin()
                    .send_wildcard()
                    .allow_any_method()
                    .allow_any_header()
                    .max_age(3600),
            )
            .configure(routes::configure)
    })
    .workers(workers)
    .bind(bind)
    .with_context(|| format!("failed to bind {}:{}", bind.0, bind.1))?
    .run()
    .await
    .context("Actix server failed")
}
