use backend_rust::{database::Database, questdb::QuestDb, state::AppState};

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "backend_rust=info,actix_web=info".into()),
        )
        .init();

    let db = Database::from_env()?;
    db.initialize().await?;
    let questdb = QuestDb::from_env()?;
    let port = std::env::var("PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(8080);
    let host = std::env::var("BIND_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let local_only = matches!(host.as_str(), "127.0.0.1" | "::1" | "localhost");
    if !local_only
        && std::env::var("MT5_BRIDGE_TOKEN")
            .unwrap_or_default()
            .is_empty()
    {
        anyhow::bail!("MT5_BRIDGE_TOKEN is required when BIND_HOST is not loopback");
    }

    backend_rust::serve(AppState::new(db, questdb), (&host, port)).await
}
