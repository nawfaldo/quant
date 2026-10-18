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
    "ALTER TABLE mt5_execution_commands ADD COLUMN account_trade_id INTEGER NOT NULL DEFAULT -1",
    "ALTER TABLE mt5_execution_commands ADD COLUMN closed_account_trade_id INTEGER NOT NULL DEFAULT -1",
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

/// Columns that carried an environment's trade id, renamed when environments
/// were removed. `(table, old, new)`; skipped once the old column is gone.
const RENAMED_ACCOUNT_TRADE_COLUMNS: &[(&str, &str, &str)] = &[
    (
        "mt5_execution_commands",
        "environment_trade_id",
        "account_trade_id",
    ),
    (
        "mt5_execution_commands",
        "closed_environment_trade_id",
        "closed_account_trade_id",
    ),
    (
        "mt5_strategy_positions",
        "environment_trade_id",
        "account_trade_id",
    ),
];

/// Tables that used to carry an `environment_id` foreign key and are rebuilt
/// from `SCHEMA` without it.
const ENVIRONMENT_SCOPED_TABLES: &[&str] = &["mt5_accounts", "backtests"];

const OFI_ML_PROMOTION: &str = r#"
BEGIN IMMEDIATE;
UPDATE backtests
SET strategy=REPLACE(strategy,'OFI_MOMENTUM','OFI_MOMENTUM_LEGACY')
WHERE strategy LIKE '%OFI_MOMENTUM%';
UPDATE backtests
SET strategy=REPLACE(strategy,'OFI_MOMENTUM_LEGACY_ML','OFI_MOMENTUM')
WHERE strategy LIKE '%OFI_MOMENTUM_LEGACY_ML%';

UPDATE account_trades SET strategy_name='ofi_momentum_legacy'
WHERE strategy_name='ofi_momentum';
UPDATE account_trades SET strategy_name='ofi_momentum'
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
        connection
            .execute_unprepared("PRAGMA foreign_keys=ON")
            .await?;
        connection.execute_unprepared(SCHEMA).await?;
        self.drop_environments().await?;
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
                "UPDATE account_trades SET mt5_exit_time=mt5_entry_time \
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

    /// Removes environments from a database written before they were retired.
    ///
    /// An environment was a named row every account, backtest and account trade
    /// hung off, and exactly one ever ran anything. What replaces it: accounts
    /// stand alone, a backtest records the cost MODEL it was priced under, and
    /// `environment_trades` is `account_trades`.
    ///
    /// A NO-OP ON A CURRENT DATABASE, and every step checks before it acts, so
    /// it runs on every boot.
    ///
    /// ONE CONNECTION, FOREIGN KEYS OFF. `mt5_accounts` is the parent of four
    /// `ON DELETE CASCADE` tables, and rebuilding it with foreign keys on would
    /// empty every one of them. `foreign_keys` is per connection and cannot be
    /// changed inside a transaction, so this opens its own single connection
    /// rather than borrowing one from the pool. `legacy_alter_table` stops the
    /// rename of the old table from rewriting the children's references to
    /// follow it, so they keep pointing at the rebuilt table by name.
    ///
    /// ALL OR NOTHING: the whole change is one transaction, and the foreign keys
    /// are checked before it is accepted.
    async fn drop_environments(&self) -> Result<(), ApiError> {
        let url = format!("sqlite://{}?mode=rwc", self.path.display());
        let mut options = ConnectOptions::new(url);
        options
            .max_connections(1)
            .min_connections(1)
            .connect_timeout(Duration::from_secs(5))
            .sqlx_logging(false);
        let connection = SeaDatabase::connect(options).await?;
        let result = drop_environments_on(&connection).await;
        connection.close().await?;
        result
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

async fn table_exists(connection: &DatabaseConnection, table: &str) -> Result<bool, ApiError> {
    Ok(connection
        .query_one(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            [table.to_owned().into()],
        ))
        .await?
        .is_some())
}

async fn columns(connection: &DatabaseConnection, table: &str) -> Result<Vec<String>, ApiError> {
    connection
        .query_all(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT name FROM pragma_table_info(?) ORDER BY cid",
            [table.to_owned().into()],
        ))
        .await?
        .into_iter()
        .map(|row| row.try_get_by_index::<String>(0).map_err(ApiError::from))
        .collect()
}

async fn has_column(
    connection: &DatabaseConnection,
    table: &str,
    column: &str,
) -> Result<bool, ApiError> {
    Ok(columns(connection, table)
        .await?
        .iter()
        .any(|c| c == column))
}

/// Copies every column `from` and `to` share, then drops `from`. A column only
/// the new table has takes its default, which is how `backtests.model` gets
/// `exness_pro`.
async fn move_rows(connection: &DatabaseConnection, from: &str, to: &str) -> Result<(), ApiError> {
    let target = columns(connection, to).await?;
    let shared = columns(connection, from)
        .await?
        .into_iter()
        .filter(|column| target.contains(column))
        .collect::<Vec<_>>()
        .join(",");
    connection
        .execute_unprepared(&format!(
            "INSERT INTO {to}({shared}) SELECT {shared} FROM {from}; DROP TABLE {from};"
        ))
        .await?;
    Ok(())
}

/// Every row whose foreign key points nowhere, as `(table, rowid, parent)`.
async fn foreign_key_violations(
    connection: &DatabaseConnection,
) -> Result<std::collections::BTreeSet<(String, i64, String)>, ApiError> {
    connection
        .query_all(Statement::from_string(
            DatabaseBackend::Sqlite,
            "SELECT \"table\",COALESCE(rowid,-1),parent FROM pragma_foreign_key_check",
        ))
        .await?
        .into_iter()
        .map(|row| {
            Ok((
                row.try_get_by_index(0)?,
                row.try_get_by_index(1)?,
                row.try_get_by_index(2)?,
            ))
        })
        .collect()
}

async fn drop_environments_on(connection: &DatabaseConnection) -> Result<(), ApiError> {
    let mut pending = false;
    for table in [
        "environments",
        "environment_trades",
        "environment_cost_rules",
        "environment_rules",
    ] {
        pending |= table_exists(connection, table).await?;
    }
    for table in ENVIRONMENT_SCOPED_TABLES {
        pending |= has_column(connection, table, "environment_id").await?;
    }
    for (table, old, _) in RENAMED_ACCOUNT_TRADE_COLUMNS {
        pending |= has_column(connection, table, old).await?;
    }
    if !pending {
        return Ok(());
    }
    connection
        .execute_unprepared("PRAGMA foreign_keys=OFF; PRAGMA legacy_alter_table=ON;")
        .await?;
    connection.execute_unprepared("BEGIN IMMEDIATE").await?;
    let migrated = async {
        // Violations already present are not this migration's to judge: the
        // live database carries `mt5_strategy_positions` rows whose strategy
        // row an activation deleted with foreign keys off. Only a violation
        // this change INTRODUCES refuses it.
        let before = foreign_key_violations(connection).await?;
        for (table, old, new) in RENAMED_ACCOUNT_TRADE_COLUMNS {
            if has_column(connection, table, old).await? {
                connection
                    .execute_unprepared(&format!(
                        "ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
                    ))
                    .await?;
            }
        }
        let mut rebuilt = Vec::new();
        for table in ENVIRONMENT_SCOPED_TABLES {
            if has_column(connection, table, "environment_id").await? {
                // Its indexes travel with it and are dropped with it.
                connection
                    .execute_unprepared(&format!(
                        "ALTER TABLE {table} RENAME TO {table}_environment_old"
                    ))
                    .await?;
                rebuilt.push(*table);
            }
        }
        // Recreates whatever was renamed away, from the one canonical schema.
        connection.execute_unprepared(SCHEMA).await?;
        for table in rebuilt {
            move_rows(connection, &format!("{table}_environment_old"), table).await?;
        }
        if table_exists(connection, "environment_trades").await? {
            move_rows(connection, "environment_trades", "account_trades").await?;
        }
        connection
            .execute_unprepared(concat!(
                "DROP TABLE IF EXISTS environment_rules;",
                "DROP TABLE IF EXISTS environment_cost_rules;",
                "DROP TABLE IF EXISTS environments;",
            ))
            .await?;
        let introduced = foreign_key_violations(connection)
            .await?
            .difference(&before)
            .count();
        if introduced > 0 {
            return Err(ApiError::Internal(format!(
                "dropping environments would break {introduced} foreign keys"
            )));
        }
        Ok::<_, ApiError>(())
    }
    .await;
    match migrated {
        Ok(()) => connection.execute_unprepared("COMMIT").await?,
        Err(error) => {
            connection.execute_unprepared("ROLLBACK").await?;
            return Err(error);
        }
    };
    connection
        .execute_unprepared("PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
        .await?;
    Ok(())
}
