use super::{
    AccountStrategy, AccountStrategyInput, Database, LiveTrade, Mt5Account, Mt5AccountInput,
    Mt5AccountStatus, Mt5Command, Mt5Position, Mt5PositionInput,
};
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement, TransactionTrait};

impl Database {
    pub async fn live_trades(&self) -> Result<Vec<LiveTrade>, ApiError> {
        let rows = self
            .query(concat!(
                "SELECT l.id,a.id,COALESCE(a.login,''),COALESCE(a.name,''),a.environment_id,",
                "l.strategy_name,l.side,l.contract,l.engine_entry_price,",
                "l.engine_close_price,l.mt5_entry_price,l.mt5_close_price,",
                "l.engine_open_time,l.engine_close_time,l.mt5_open_time,l.mt5_close_time,",
                "l.position_key,l.exit_reason,l.closed_at ",
                "FROM live_trades l ",
                "LEFT JOIN mt5_strategy_positions p ON p.live_trade_id=l.id ",
                "LEFT JOIN mt5_accounts a ON a.id=p.account_id ",
                "ORDER BY l.id DESC LIMIT 256",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(LiveTrade {
                    id: row.try_get_by_index(0)?,
                    account_id: row.try_get_by_index(1)?,
                    account_login: row.try_get_by_index(2)?,
                    account_name: row.try_get_by_index(3)?,
                    environment_id: row.try_get_by_index(4)?,
                    strategy_name: row.try_get_by_index(5)?,
                    side: row.try_get_by_index(6)?,
                    contract: row.try_get_by_index(7)?,
                    engine_entry_price: row.try_get_by_index(8)?,
                    engine_close_price: row.try_get_by_index(9)?,
                    mt5_entry_price: row.try_get_by_index(10)?,
                    mt5_close_price: row.try_get_by_index(11)?,
                    engine_open_time: row.try_get_by_index(12)?,
                    engine_close_time: row.try_get_by_index(13)?,
                    mt5_open_time: row.try_get_by_index(14)?,
                    mt5_close_time: row.try_get_by_index(15)?,
                    position_key: row.try_get_by_index(16)?,
                    exit_reason: row.try_get_by_index(17)?,
                    closed_at: row.try_get_by_index(18)?,
                })
            })
            .collect()
    }

    pub async fn accounts(&self) -> Result<Vec<Mt5Account>, ApiError> {
        let rows = self
            .query("SELECT id,name,login,server FROM mt5_accounts ORDER BY id")
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(Mt5Account {
                    id: row.try_get_by_index(0)?,
                    name: row.try_get_by_index(1)?,
                    login: row.try_get_by_index(2)?,
                    server: row.try_get_by_index(3)?,
                })
            })
            .collect()
    }

    pub async fn add_account(&self, input: &Mt5AccountInput) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_accounts(name,login,password,server,created_at) ",
                "VALUES (?,?,?,?,datetime('now'))",
            ),
            [
                input.name.clone().into(),
                input.login.clone().into(),
                "".into(),
                input.server.clone().into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn delete_account(&self, id: i64) -> Result<(), ApiError> {
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_account_strategies WHERE account_id=?",
                [id.into()],
            ))
            .await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_accounts WHERE id=?",
                [id.into()],
            ))
            .await?;
        transaction.commit().await?;
        Ok(())
    }

    pub async fn account_strategies(
        &self,
        account_id: i64,
    ) -> Result<Vec<AccountStrategy>, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id,strategy,symbol,active,book FROM mt5_account_strategies ",
                "WHERE account_id=? ORDER BY id",
            ),
            [account_id.into()],
        );
        self.orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(AccountStrategy {
                    id: row.try_get_by_index(0)?,
                    strategy: row.try_get_by_index(1)?,
                    symbol: row.try_get_by_index(2)?,
                    active: row.try_get_by_index::<i64>(3)? != 0,
                    book: row.try_get_by_index(4)?,
                })
            })
            .collect()
    }

    pub async fn add_account_strategy(
        &self,
        account_id: i64,
        input: &AccountStrategyInput,
    ) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_account_strategies",
                "(account_id,strategy,symbol,book,created_at) ",
                "VALUES (?,?,?,?,datetime('now'))",
            ),
            [
                account_id.into(),
                input.strategy.clone().into(),
                input.symbol.clone().into(),
                crate::strategies::idk::exness_combined::book_of(&input.strategy).into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn delete_account_strategy(&self, id: i64) -> Result<(), ApiError> {
        self.execute_id("DELETE FROM mt5_account_strategies WHERE id=?", id)
            .await?;
        Ok(())
    }

    pub async fn set_account_strategy_active(
        &self,
        id: i64,
        active: bool,
    ) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "UPDATE mt5_account_strategies SET active=? WHERE id=?",
            [(active as i32).into(), id.into()],
        );
        Ok(self.orm().await?.execute(statement).await?.rows_affected() > 0)
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn enqueue_mt5_command(
        &self,
        account_id: i64,
        strategy: &str,
        action: &str,
        symbol: &str,
        volume: f64,
        trade_id: i64,
        closed_trade_id: i64,
        environment_trade_id: i64,
        closed_environment_trade_id: i64,
    ) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_execution_commands",
                "(account_id,strategy,action,symbol,volume,trade_id,closed_trade_id,",
                "environment_trade_id,closed_environment_trade_id,created_at) ",
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
            ),
            [
                account_id.into(),
                strategy.to_owned().into(),
                action.to_owned().into(),
                symbol.to_owned().into(),
                volume.into(),
                trade_id.into(),
                closed_trade_id.into(),
                environment_trade_id.into(),
                closed_environment_trade_id.into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn record_mt5_heartbeat(
        &self,
        login: &str,
        server: &str,
        balance: f64,
        equity: f64,
        currency: &str,
    ) -> Result<Option<i64>, ApiError> {
        let find = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id FROM mt5_accounts WHERE login=? AND (server='' OR server=?) ",
                "ORDER BY CASE WHEN server=? THEN 0 ELSE 1 END LIMIT 1",
            ),
            [
                login.to_owned().into(),
                server.to_owned().into(),
                server.to_owned().into(),
            ],
        );
        let Some(row) = self.orm().await?.query_one(find).await? else {
            return Ok(None);
        };
        let account_id = row.try_get_by_index::<i64>(0)?;
        let upsert = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_bridge_heartbeats",
                "(account_id,server,balance,equity,currency,detail,seen_at,",
                "initial_balance,day_start_balance) ",
                "VALUES (?,?,?,?,?,?,datetime('now'),?,?) ",
                "ON CONFLICT(account_id) DO UPDATE SET server=excluded.server,",
                "balance=excluded.balance,equity=excluded.equity,currency=excluded.currency,",
                "detail=excluded.detail,",
                "initial_balance=CASE WHEN mt5_bridge_heartbeats.initial_balance=0 ",
                "THEN mt5_bridge_heartbeats.balance ELSE mt5_bridge_heartbeats.initial_balance END,",
                "day_start_balance=CASE ",
                "WHEN date(mt5_bridge_heartbeats.seen_at)<>date('now') THEN excluded.balance ",
                "WHEN mt5_bridge_heartbeats.day_start_balance=0 THEN mt5_bridge_heartbeats.balance ",
                "ELSE mt5_bridge_heartbeats.day_start_balance END,",
                "seen_at=excluded.seen_at",
            ),
            [
                account_id.into(),
                server.to_owned().into(),
                balance.into(),
                equity.into(),
                currency.to_owned().into(),
                format!("{} · balance {:.2} {}", server, balance, currency).into(),
                balance.into(),
                balance.into(),
            ],
        );
        self.orm().await?.execute(upsert).await?;
        Ok(Some(account_id))
    }

    pub async fn claim_mt5_command(&self, account_id: i64) -> Result<Option<Mt5Command>, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_execution_commands SET status='superseded',",
                    "error='entry expired while the bridge was offline',",
                    "completed_at=datetime('now') WHERE account_id=? ",
                    "AND action IN ('long','short') AND status IN ('pending','leased') ",
                    "AND created_at < datetime('now','-30 seconds')",
                ),
                [account_id.into()],
            ))
            .await?;
        let claim = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "UPDATE mt5_execution_commands SET status='leased',leased_at=datetime('now') ",
                "WHERE id=(SELECT id FROM mt5_execution_commands WHERE account_id=? AND ",
                "(status='pending' OR (status='leased' AND leased_at < datetime('now','-15 seconds'))) ",
                "ORDER BY id ASC LIMIT 1) RETURNING id,action,symbol,volume,",
                "target_ticket,magic,account_strategy_id,position_key,",
                "CAST(strftime('%s',created_at) AS INTEGER)",
            ),
            [account_id.into()],
        );
        let row = transaction.query_one(claim).await?;
        let Some(row) = row else {
            transaction.commit().await?;
            return Ok(None);
        };
        let action = row.try_get_by_index::<String>(1)?;
        let account_strategy_id = row.try_get_by_index::<i64>(6)?;
        let position_key = row.try_get_by_index::<String>(7)?;
        let mut target_ticket = row.try_get_by_index::<i64>(4)?;
        if action == "close"
            && target_ticket == 0
            && account_strategy_id >= 0
            && !position_key.is_empty()
            && let Some(position) = transaction
                .query_one(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "SELECT ticket FROM mt5_strategy_positions ",
                        "WHERE account_strategy_id=? AND position_key=?",
                    ),
                    [account_strategy_id.into(), position_key.into()],
                ))
                .await?
        {
            target_ticket = position.try_get_by_index(0)?;
        }
        transaction.commit().await?;
        let command = Mt5Command {
            id: row.try_get_by_index(0)?,
            action,
            symbol: row.try_get_by_index(2)?,
            volume: row.try_get_by_index(3)?,
            target_ticket,
            magic: row.try_get_by_index(5)?,
            created_at: row.try_get_by_index(8)?,
        };
        Ok(Some(command))
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn complete_mt5_command(
        &self,
        command_id: i64,
        login: &str,
        filled: bool,
        ticket: i64,
        entry_price: f64,
        entry_spread: f64,
        close_price: f64,
        mt5pnl: f64,
        fill_time: &str,
        error: &str,
    ) -> Result<bool, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        let find = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT c.trade_id,c.closed_trade_id,c.environment_trade_id,",
                "c.closed_environment_trade_id,c.action,c.account_strategy_id,",
                "c.position_key,c.close_fraction,c.volume ",
                "FROM mt5_execution_commands c ",
                "JOIN mt5_accounts a ON a.id=c.account_id WHERE c.id=? AND a.login=?",
            ),
            [command_id.into(), login.to_owned().into()],
        );
        let Some(row) = transaction.query_one(find).await? else {
            transaction.rollback().await?;
            return Ok(false);
        };
        let trade_id = row.try_get_by_index::<i64>(0)?;
        let closed_trade_id = row.try_get_by_index::<i64>(1)?;
        let environment_trade_id = row.try_get_by_index::<i64>(2)?;
        let closed_environment_trade_id = row.try_get_by_index::<i64>(3)?;
        let action = row.try_get_by_index::<String>(4)?;
        let account_strategy_id = row.try_get_by_index::<i64>(5)?;
        let position_key = row.try_get_by_index::<String>(6)?;
        let close_fraction = row.try_get_by_index::<f64>(7)?;
        let command_volume = row.try_get_by_index::<f64>(8)?;
        let status = if filled { "filled" } else { "failed" };
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_execution_commands SET status=?,error=?,ticket=?,",
                    "completed_at=datetime('now') WHERE id=?",
                ),
                [
                    status.into(),
                    error.to_owned().into(),
                    ticket.into(),
                    command_id.into(),
                ],
            ))
            .await?;
        if account_strategy_id >= 0 && !position_key.is_empty() {
            if filled && matches!(action.as_str(), "long" | "short") {
                transaction
                    .execute(Statement::from_sql_and_values(
                        DatabaseBackend::Sqlite,
                        concat!(
                            "UPDATE mt5_strategy_positions SET ticket=?,status='open',",
                            "remaining_volume=requested_volume,updated_at=datetime('now') ",
                            "WHERE account_strategy_id=? AND position_key=?",
                        ),
                        [
                            ticket.into(),
                            account_strategy_id.into(),
                            position_key.clone().into(),
                        ],
                    ))
                    .await?;
            } else if filled && action == "close" {
                if close_fraction >= 1.0 - 1e-9 {
                    transaction
                        .execute(Statement::from_sql_and_values(
                            DatabaseBackend::Sqlite,
                            concat!(
                                "UPDATE mt5_strategy_positions SET remaining_volume=0,",
                                "status='closed',updated_at=datetime('now') ",
                                "WHERE account_strategy_id=? AND position_key=?",
                            ),
                            [account_strategy_id.into(), position_key.clone().into()],
                        ))
                        .await?;
                } else {
                    transaction
                        .execute(Statement::from_sql_and_values(
                            DatabaseBackend::Sqlite,
                            concat!(
                                "UPDATE mt5_strategy_positions SET ",
                                "remaining_volume=max(0,remaining_volume-?),status='open',",
                                "updated_at=datetime('now') ",
                                "WHERE account_strategy_id=? AND position_key=?",
                            ),
                            [
                                command_volume.into(),
                                account_strategy_id.into(),
                                position_key.clone().into(),
                            ],
                        ))
                        .await?;
                }
            } else if !filled && matches!(action.as_str(), "long" | "short") {
                transaction
                    .execute(Statement::from_sql_and_values(
                        DatabaseBackend::Sqlite,
                        concat!(
                            "UPDATE mt5_strategy_positions SET status='failed',",
                            "updated_at=datetime('now') ",
                            "WHERE account_strategy_id=? AND position_key=?",
                        ),
                        [account_strategy_id.into(), position_key.clone().into()],
                    ))
                    .await?;
            } else if !filled && action == "close" {
                transaction
                    .execute(Statement::from_sql_and_values(
                        DatabaseBackend::Sqlite,
                        concat!(
                            "UPDATE mt5_strategy_positions SET status='open',",
                            "updated_at=datetime('now') ",
                            "WHERE account_strategy_id=? AND position_key=?",
                        ),
                        [account_strategy_id.into(), position_key.clone().into()],
                    ))
                    .await?;
            }
        }
        if filled && trade_id >= 0 && entry_price > 0.0 {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE live_trades SET mt5_open_time=?,mt5_entry_price=?,",
                        "mt5_entry_price_spread=? WHERE id=? AND mt5_open_time=''",
                    ),
                    [
                        fill_time.to_owned().into(),
                        entry_price.into(),
                        (entry_price + entry_spread).into(),
                        trade_id.into(),
                    ],
                ))
                .await?;
        }
        if filled && closed_trade_id >= 0 {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    // MAX clamps a broker fill time that predates the entry.
                    // The bridge is the real fix, but a close stamped before
                    // its own entry corrupts every consumer that orders by
                    // time, so the column must not be able to hold one. The
                    // format sorts lexicographically and an unset open time is
                    // '', which loses the MAX, so both cases land correctly.
                    concat!(
                        "UPDATE live_trades SET mt5_close_time=MAX(?,mt5_open_time),",
                        "mt5_close_price=? WHERE id=? AND mt5_close_time=''",
                    ),
                    [
                        fill_time.to_owned().into(),
                        close_price.into(),
                        closed_trade_id.into(),
                    ],
                ))
                .await?;
        }
        if filled && environment_trade_id >= 0 && entry_price > 0.0 {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE environment_trades SET mt5_entry_time=?,",
                        "mt5_entry_price=? WHERE id=? AND mt5_entry_time=''"
                    ),
                    [
                        fill_time.to_owned().into(),
                        entry_price.into(),
                        environment_trade_id.into(),
                    ],
                ))
                .await?;
        }
        if filled && closed_environment_trade_id >= 0 {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        // Clamped for the same reason as live_trades above.
                        "UPDATE environment_trades SET mt5_exit_time=MAX(?,mt5_entry_time),",
                        "mt5_exit_price=?,mt5pnl=? WHERE id=? AND mt5_exit_time=''"
                    ),
                    [
                        fill_time.to_owned().into(),
                        close_price.into(),
                        mt5pnl.into(),
                        closed_environment_trade_id.into(),
                    ],
                ))
                .await?;
        }
        transaction.commit().await?;
        Ok(true)
    }

    pub async fn replace_mt5_positions(
        &self,
        login: &str,
        server: &str,
        positions: &[Mt5PositionInput],
    ) -> Result<bool, ApiError> {
        let find = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id FROM mt5_accounts WHERE login=? AND (server='' OR server=?) ",
                "ORDER BY CASE WHEN server=? THEN 0 ELSE 1 END LIMIT 1",
            ),
            [
                login.to_owned().into(),
                server.to_owned().into(),
                server.to_owned().into(),
            ],
        );
        let Some(row) = self.orm().await?.query_one(find).await? else {
            return Ok(false);
        };
        let account_id = row.try_get_by_index::<i64>(0)?;
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_bridge_positions WHERE account_id=?",
                [account_id.into()],
            ))
            .await?;
        for position in positions {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO mt5_bridge_positions",
                    "(account_id,ticket,position_type,symbol,volume,profit,open_price,open_time,magic) ",
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                ),
                    [
                        account_id.into(),
                        position.ticket.into(),
                        position.position_type.clone().into(),
                        position.symbol.clone().into(),
                        position.volume.into(),
                        position.profit.into(),
                    position.open_price.into(),
                    position.open_time.into(),
                    position.magic.into(),
                ],
                ))
                .await?;
        }
        transaction.commit().await?;
        Ok(true)
    }

    pub async fn mt5_account_statuses(&self) -> Result<Vec<Mt5AccountStatus>, ApiError> {
        let rows = self
            .query(concat!(
                "SELECT a.id,a.login,h.detail,h.balance,h.equity,h.currency,",
                "CASE WHEN h.seen_at IS NOT NULL AND h.seen_at >= datetime('now','-15 seconds') ",
                "THEN 'ready' ELSE 'offline' END FROM mt5_accounts a ",
                "LEFT JOIN mt5_bridge_heartbeats h ON h.account_id=a.id ORDER BY a.id",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                let status = row.try_get_by_index::<String>(6)?;
                Ok(Mt5AccountStatus {
                    account_id: row.try_get_by_index(0)?,
                    login: row.try_get_by_index(1)?,
                    detail: row
                        .try_get_by_index::<Option<String>>(2)?
                        .unwrap_or_else(|| "EA bridge has not connected".into()),
                    balance: row.try_get_by_index(3)?,
                    equity: row.try_get_by_index(4)?,
                    currency: row.try_get_by_index(5)?,
                    status,
                })
            })
            .collect()
    }

    pub async fn mt5_positions(&self) -> Result<Vec<Mt5Position>, ApiError> {
        let rows = self
            .query(concat!(
                "WITH mapped AS (SELECT p.*,a.login,a.name,a.environment_id,",
                "COALESCE(sp.strategy,(SELECT s.strategy FROM mt5_account_strategies s ",
                "WHERE s.account_id=p.account_id AND upper(s.symbol)=upper(p.symbol) ",
                "AND s.active=1 ORDER BY s.id LIMIT 1),'') strategy,sp.live_trade_id ",
                "FROM mt5_bridge_positions p JOIN mt5_accounts a ON a.id=p.account_id ",
                "LEFT JOIN mt5_strategy_positions sp ON sp.account_id=p.account_id ",
                "AND sp.ticket=p.ticket AND sp.status IN ('open','pending_close') ",
                "JOIN mt5_bridge_heartbeats h ON h.account_id=p.account_id ",
                "WHERE h.seen_at >= datetime('now','-15 seconds')) ",
                "SELECT login,name,environment_id,ticket,position_type,symbol,volume,profit,open_price,strategy,",
                "COALESCE((SELECT engine_entry_price FROM live_trades l WHERE l.id=m.live_trade_id),",
                "open_price),open_time FROM mapped m ",
                "ORDER BY login,ticket",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(Mt5Position {
                    account: row.try_get_by_index(0)?,
                    account_name: row.try_get_by_index(1)?,
                    environment_id: row.try_get_by_index(2)?,
                    ticket: row.try_get_by_index(3)?,
                    position_type: row.try_get_by_index(4)?,
                    symbol: row.try_get_by_index(5)?,
                    volume: row.try_get_by_index(6)?,
                    profit: row.try_get_by_index(7)?,
                    open_price: row.try_get_by_index(8)?,
                    strategy: row.try_get_by_index(9)?,
                    engine_entry_price: row.try_get_by_index(10)?,
                    engine_entry_time: row.try_get_by_index(11)?,
                })
            })
            .collect()
    }

    async fn query(&self, sql: &str) -> Result<Vec<sea_orm::QueryResult>, ApiError> {
        Ok(self
            .orm()
            .await?
            .query_all(Statement::from_string(DatabaseBackend::Sqlite, sql))
            .await?)
    }

    async fn execute_id(&self, sql: &str, id: i64) -> Result<u64, ApiError> {
        let statement = Statement::from_sql_and_values(DatabaseBackend::Sqlite, sql, [id.into()]);
        Ok(self.orm().await?.execute(statement).await?.rows_affected())
    }
}
