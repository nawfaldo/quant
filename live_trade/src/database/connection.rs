use super::schema::SCHEMA;
use crate::error::ApiError;
use sea_orm::{
    ConnectOptions, ConnectionTrait, Database as SeaDatabase, DatabaseBackend, DatabaseConnection,
    Statement,
};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::OnceCell;

const DEFAULT_SETTINGS: &[(&str, &str)] = &[
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
    "ALTER TABLE backtests ADD COLUMN contribution TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE backtests ADD COLUMN avg_drawdown_time_days REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN avg_annual REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN avg_annual_pct REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN annualised_std REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN skew REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN lower_tail REAL NOT NULL DEFAULT 0",
    "ALTER TABLE backtests ADD COLUMN upper_tail REAL NOT NULL DEFAULT 0",
    concat!(
        "ALTER TABLE mt5_accounts ADD COLUMN environment_id INTEGER ",
        "REFERENCES environments(id) ON DELETE CASCADE"
    ),
    "ALTER TABLE mt5_execution_commands ADD COLUMN environment_trade_id INTEGER NOT NULL DEFAULT -1",
    "ALTER TABLE mt5_execution_commands ADD COLUMN closed_environment_trade_id INTEGER NOT NULL DEFAULT -1",
    "ALTER TABLE mt5_account_strategies ADD COLUMN live_started_at INTEGER NOT NULL DEFAULT 0",
    // WHICH SEALED BOOK A LIVE ROW BELONGS TO.
    //
    // A book is one selection made on one day, but it needs one row per sleeve
    // because each trades a different MT5 symbol and the per-row symbol check is
    // what stops an order reaching the wrong instrument. Without this column the
    // account is twenty loose strategies and nothing records that they are one
    // thing -- and a second book activated later would be indistinguishable from
    // the first, row by row.
    //
    // Empty is the honest default for a row written before books existed: it
    // means "no book recorded", not "belongs to the current one".
    "ALTER TABLE mt5_account_strategies ADD COLUMN book TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE mt5_execution_commands ADD COLUMN account_strategy_id INTEGER NOT NULL DEFAULT -1",
    "ALTER TABLE mt5_execution_commands ADD COLUMN position_key TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE mt5_execution_commands ADD COLUMN target_ticket INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE mt5_execution_commands ADD COLUMN magic INTEGER NOT NULL DEFAULT 26032026",
    "ALTER TABLE mt5_execution_commands ADD COLUMN close_fraction REAL NOT NULL DEFAULT 1",
    "ALTER TABLE mt5_bridge_positions ADD COLUMN magic INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE mt5_bridge_heartbeats ADD COLUMN initial_balance REAL NOT NULL DEFAULT 0",
    "ALTER TABLE mt5_bridge_heartbeats ADD COLUMN day_start_balance REAL NOT NULL DEFAULT 0",
    "ALTER TABLE mt5_accounts ADD COLUMN equity_chart_range TEXT NOT NULL DEFAULT 'all'",
    "ALTER TABLE mt5_accounts ADD COLUMN history_range TEXT NOT NULL DEFAULT '1week'",
    "ALTER TABLE mt5_accounts ADD COLUMN account_type TEXT NOT NULL DEFAULT 'mt5'",
    "ALTER TABLE live_trades ADD COLUMN position_key TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE live_trades ADD COLUMN exit_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE live_trades ADD COLUMN closed_at TEXT NOT NULL DEFAULT ''",
];

const RETIRED_ENVIRONMENT_COLUMNS: &[&str] = &["is_mt5", "server", "login", "password"];

/// `live_trades` named the strategy engine's own prices and times `zig_*` from
/// when the engine was written in Zig. It is Rust now and nothing in the tree
/// is Zig, so the columns say `engine_*`. Renames are skipped when the old
/// column is already gone, which is also the fresh-database case.
const RENAMED_LIVE_TRADE_COLUMNS: &[(&str, &str)] = &[
    ("zig_entry_price", "engine_entry_price"),
    ("zig_close_price", "engine_close_price"),
    ("zig_open_time", "engine_open_time"),
    ("zig_close_time", "engine_close_time"),
];

const RETIRED_ENVIRONMENTS_CLEANUP: &str = r#"
DELETE FROM mt5_account_strategies
WHERE account_id IN (
  SELECT id FROM mt5_accounts WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
  )
);
DELETE FROM montecarlo_paths
WHERE mc_id IN (
  SELECT id FROM montecarlo WHERE source_id IN (
    SELECT id FROM backtests WHERE environment_id IN (
      SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
    )
  )
);
DELETE FROM montecarlo
WHERE source_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
  )
);
DELETE FROM fx_trades
WHERE backtest_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
  )
);
DELETE FROM trades
WHERE backtest_id IN (
  SELECT id FROM backtests WHERE environment_id IN (
    SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
  )
);
DELETE FROM backtests
WHERE environment_id IN (
  SELECT id FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE
);
DELETE FROM environments WHERE name IN ('local', 'paper', 'paper2', 'fn') COLLATE NOCASE;
"#;

const OFI_ML_PROMOTION: &str = r#"
BEGIN IMMEDIATE;
UPDATE backtests
SET strategy=REPLACE(strategy,'OFI_MOMENTUM','OFI_MOMENTUM_LEGACY')
WHERE strategy LIKE '%OFI_MOMENTUM%';
UPDATE backtests
SET strategy=REPLACE(strategy,'OFI_MOMENTUM_LEGACY_ML','OFI_MOMENTUM')
WHERE strategy LIKE '%OFI_MOMENTUM_LEGACY_ML%';

UPDATE environment_trades SET strategy_name='ofi_momentum_legacy'
WHERE strategy_name='ofi_momentum';
UPDATE environment_trades SET strategy_name='ofi_momentum'
WHERE strategy_name='ofi_momentum_ml';
UPDATE live_trades SET strategy_name='ofi_momentum_legacy'
WHERE strategy_name='ofi_momentum';
UPDATE live_trades SET strategy_name='ofi_momentum'
WHERE strategy_name='ofi_momentum_ml';

UPDATE mt5_execution_commands
SET status='failed', error='retired unfiltered OFI strategy'
WHERE strategy='ofi_momentum' AND status IN ('pending','leased');
UPDATE mt5_execution_commands SET strategy='ofi_momentum_legacy'
WHERE strategy='ofi_momentum';
UPDATE mt5_execution_commands SET strategy='ofi_momentum'
WHERE strategy='ofi_momentum_ml';
UPDATE mt5_strategy_positions SET strategy='ofi_momentum_legacy'
WHERE strategy='ofi_momentum';
UPDATE mt5_strategy_positions SET strategy='ofi_momentum'
WHERE strategy='ofi_momentum_ml';

DELETE FROM mt5_account_strategies WHERE strategy='ofi_momentum';
UPDATE mt5_account_strategies SET strategy='ofi_momentum'
WHERE strategy='ofi_momentum_ml';

INSERT INTO settings(key,value) VALUES ('ofi_ml_promoted_v1','1');
COMMIT;
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
        // WAL is a property of the file, so one statement here settles it for
        // every connection in the pool and every later run. It matters because
        // the live runtime writes execution commands into the same database the
        // API reads chart and trade lists from: under the default rollback
        // journal a write takes an exclusive lock and every reader waits behind
        // it.
        connection
            .execute_unprepared("PRAGMA journal_mode=WAL")
            .await?;
        connection.execute_unprepared(SCHEMA).await?;
        // Best-effort column additions for databases created before the column
        // existed. SQLite rejects a duplicate column, which we treat as success.
        for statement in COLUMN_MIGRATIONS {
            if let Err(error) = connection.execute_unprepared(statement).await
                && !error.to_string().contains("duplicate column name")
            {
                return Err(error.into());
            }
        }
        for (old, new) in RENAMED_LIVE_TRADE_COLUMNS {
            let exists = connection
                .query_one(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    "SELECT 1 FROM pragma_table_info('live_trades') WHERE name=?",
                    [(*old).to_owned().into()],
                ))
                .await?
                .is_some();
            if exists {
                connection
                    .execute_unprepared(&format!(
                        "ALTER TABLE live_trades RENAME COLUMN {old} TO {new}"
                    ))
                    .await?;
            }
        }
        // Repairs rows written before the fill time was clamped. A close
        // stamped before its own entry breaks anything that orders by time --
        // the equity chart asserts on it -- and the entry time is the closest
        // defensible value, since the close cannot have preceded it.
        connection
            .execute_unprepared(
                "UPDATE live_trades SET mt5_close_time=mt5_open_time \
                 WHERE mt5_close_time!='' AND mt5_open_time!='' \
                 AND mt5_close_time < mt5_open_time",
            )
            .await?;
        connection
            .execute_unprepared(
                "UPDATE environment_trades SET mt5_exit_time=mt5_entry_time \
                 WHERE mt5_exit_time!='' AND mt5_entry_time!='' \
                 AND mt5_exit_time < mt5_entry_time",
            )
            .await?;
        connection
            .execute_unprepared(
                "UPDATE mt5_accounts SET account_type='mt5' \
                 WHERE account_type='' OR account_type IN ('demo','live')",
            )
            .await?;
        connection
            .execute_unprepared(concat!(
                "CREATE INDEX IF NOT EXISTS idx_mt5_accounts_environment ",
                "ON mt5_accounts(environment_id)"
            ))
            .await?;
        for column in RETIRED_ENVIRONMENT_COLUMNS {
            let exists = connection
                .query_one(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    "SELECT 1 FROM pragma_table_info('environments') WHERE name=?",
                    [(*column).to_owned().into()],
                ))
                .await?
                .is_some();
            if exists {
                connection
                    .execute_unprepared(&format!("ALTER TABLE environments DROP COLUMN {column}"))
                    .await?;
            }
        }
        connection
            .execute_unprepared(RETIRED_ENVIRONMENTS_CLEANUP)
            .await?;
        // THE OLD EDITABLE RULES TABLE. `environment_rules` held one
        // user-typed `spread`/`slippage`/`commission` number per environment,
        // and the engine overrode every one of them with its own Exness Pro
        // figures before a bar was replayed -- so what the screen showed and
        // what the backtest charged were unrelated numbers. Nothing has read it
        // since; the surviving row here was a `spread` of 0.2 written in July.
        // It goes rather than being migrated, because there is nothing in it
        // worth carrying into `environment_cost_rules`.
        connection
            .execute_unprepared("DROP TABLE IF EXISTS environment_rules")
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
        let ofi_promoted = connection
            .query_one(Statement::from_string(
                DatabaseBackend::Sqlite,
                "SELECT 1 FROM settings WHERE key='ofi_ml_promoted_v1'",
            ))
            .await?
            .is_some();
        if !ofi_promoted {
            connection.execute_unprepared(OFI_ML_PROMOTION).await?;
        }
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
