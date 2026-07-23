use super::schema::SCHEMA;
use crate::error::ApiError;
use sea_orm::{
    ConnectOptions, ConnectionTrait, Database as SeaDatabase, DatabaseBackend, DatabaseConnection,
    Statement,
};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::OnceCell;

const DEFAULT_SETTINGS: &[(&str, &str)] = &[
    ("from_date", "2026-01-01"),
    ("to_date", "2026-04-30"),
    ("default_timeframe", "5m"),
    ("march_symbol", "nq"),
    ("march_tf", "1m"),
    ("march_from", "2026-06-18"),
    ("march_to", "2026-06-25"),
    ("march_mode", "latest"),
    ("march_bottom_open", "true"),
    ("march_layout", "single"),
    ("march_bottom_height", "400"),
];

const COLUMN_MIGRATIONS: &[&str] = &[
    "ALTER TABLE backtests ADD COLUMN avg_drawdown_time_days REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN avg_annual REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN avg_annual_pct REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN annualised_std REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN skew REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN lower_tail REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN upper_tail REAL NOT NULL DEFAULT 0",
];

const RETIRED_PAPER_ENVIRONMENTS_CLEANUP: &str = r#"
DELETE FROM montecarlo_paths
WHERE mc_id IN (
  SELECT id FROM montecarlo WHERE source_id IN (
    SELECT id FROM backtests WHERE environment_id IN (
      SELECT id FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE
    )
  )
);
DELETE FROM montecarlo
WHERE source_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE
  )
);
DELETE FROM fx_trades
WHERE backtest_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE
  )
);
DELETE FROM trades
WHERE backtest_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE
  )
);
DELETE FROM backtests
WHERE environment_id IN (
  SELECT id FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE
);
DELETE FROM environments WHERE name IN ('paper', 'paper2') COLLATE NOCASE;
"#;

#[derive(Clone)]
pub struct Database {
    path: Arc<PathBuf>,
    connection: Arc<OnceCell<DatabaseConnection>>,
}

impl Database {
    pub fn from_env() -> anyhow::Result<Self> {
        let path = std::env::var_os("APP_DB_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("app.db"));
        Ok(Self::new(path))
    }

    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: Arc::new(path.into()),
            connection: Arc::new(OnceCell::new()),
        }
    }

    pub fn path(&self) -> &std::path::Path {
        self.path.as_ref()
    }

    pub async fn initialize(&self) -> Result<(), ApiError> {
        let connection = self.orm().await?;
        connection.execute_unprepared(SCHEMA).await?;
        // Best-effort column additions for databases created before the column
        // existed. SQLite rejects a duplicate column, which we treat as success.
        for statement in COLUMN_MIGRATIONS {
            if let Err(error) = connection.execute_unprepared(statement).await {
                if !error.to_string().contains("duplicate column name") {
                    return Err(error.into());
                }
            }
        }
        connection
            .execute_unprepared(RETIRED_PAPER_ENVIRONMENTS_CLEANUP)
            .await?;
        for (key, value) in DEFAULT_SETTINGS {
            connection
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
                    [(*key).to_owned().into(), (*value).to_owned().into()],
                ))
                .await?;
        }
        connection
            .execute(Statement::from_string(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT OR IGNORE INTO strategies(name,active,updated_at) ",
                    "VALUES ('night_drift',0,'')",
                ),
            ))
            .await?;
        // MT5 credentials now stay inside the logged-in terminal. Remove old
        // plaintext passwords left by the retired Python login bridge.
        connection
            .execute(Statement::from_string(
                DatabaseBackend::Sqlite,
                "UPDATE mt5_accounts SET password='' WHERE password<>''",
            ))
            .await?;
        Ok(())
    }

    pub async fn orm(&self) -> Result<&DatabaseConnection, ApiError> {
        self.connection
            .get_or_try_init(|| async {
                let url = format!("sqlite://{}?mode=rwc", self.path.display());
                let mut options = ConnectOptions::new(url);
                options
                    .max_connections(8)
                    .min_connections(1)
                    .connect_timeout(Duration::from_secs(5))
                    .sqlx_logging(false);
                SeaDatabase::connect(options).await
            })
            .await
            .map_err(ApiError::from)
    }
}
