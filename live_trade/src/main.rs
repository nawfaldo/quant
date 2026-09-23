use live_trade::{database::Database, parquet_store::ParquetStore, state::AppState};

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                // ACTIX AT `warn`, NOT `info`. `Logger::default()` prints a line
                // per HTTP request and the MT5 bridge polls four times a second,
                // so `actix_web=info` buries every runtime event under roughly
                // 700k request lines a day. `live_trade=info` is kept: that is
                // where session-end flattens and exit retries surface, and trades
                // themselves go to the database rather than here.
                //
                // `RUST_LOG=live_trade=info,actix_web=info` restores the old view
                // when a request actually needs watching.
                //
                // THE TARGET IS THE CRATE NAME. This read `server=info` after the
                // crate was renamed `server` -> `live_trade`, and a filter target
                // that matches no span is silent rather than an error: the server
                // window ran completely blank while the process was healthy and
                // trading, which reads exactly like a crash on startup.
                .unwrap_or_else(|_| "live_trade=info,actix_web=warn".into()),
        )
        .init();

    let db = Database::from_env()?;
    db.initialize().await?;
    // The rules screen reads stored rows, and those rows are a view of the
    // engine's compiled cost tables. Refreshing them here is what stops the two
    // drifting: a binary that charges new spreads writes them before it serves
    // its first request.
    db.sync_environment_cost_rules().await?;
    let store = ParquetStore::from_env()?;
    let port = std::env::var("PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4000);
    let host = std::env::var("BIND_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let local_only = matches!(host.as_str(), "127.0.0.1" | "::1" | "localhost");
    if !local_only
        && std::env::var("MT5_BRIDGE_TOKEN")
            .unwrap_or_default()
            .is_empty()
    {
        anyhow::bail!("MT5_BRIDGE_TOKEN is required when BIND_HOST is not loopback");
    }

    live_trade::serve(AppState::new(db, store), (&host, port)).await
}
