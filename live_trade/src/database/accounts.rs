use super::{
    AccountOverview, AccountStrategy, AccountStrategyInput, AccountTrade, AccountTradeSummary,
    Database, NewMt5Account,
};
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement, TransactionTrait};

impl Database {
    /// Every MT5 account with its latest bridge balance.
    ///
    /// NOT SCOPED TO ANYTHING. Accounts used to hang off an environment row, and
    /// exactly one environment ever held an account that traded; an account now
    /// stands alone, and what it trades is its own `mt5_account_strategies`.
    pub async fn account_overviews(&self) -> Result<Vec<AccountOverview>, ApiError> {
        let statement = Statement::from_string(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT a.id,a.server,a.login,h.balance,h.equity,",
                "COALESCE(NULLIF(h.initial_balance,0),h.balance),",
                "CASE WHEN COALESCE(NULLIF(h.initial_balance,0),h.balance)>0 ",
                "THEN (h.balance-COALESCE(NULLIF(h.initial_balance,0),h.balance))",
                "*100.0/COALESCE(NULLIF(h.initial_balance,0),h.balance) END,",
                "CASE WHEN COALESCE(NULLIF(h.day_start_balance,0),h.balance)>0 ",
                "THEN (h.balance-COALESCE(NULLIF(h.day_start_balance,0),h.balance))",
                "*100.0/COALESCE(NULLIF(h.day_start_balance,0),h.balance) END,",
                "(SELECT COUNT(*) FROM mt5_account_strategies s ",
                "WHERE s.account_id=a.id AND s.active=1),",
                "a.equity_chart_range,a.history_range,a.account_type ",
                "FROM mt5_accounts a LEFT JOIN mt5_bridge_heartbeats h ",
                "ON h.account_id=a.id ORDER BY a.id"
            ),
        );
        let rows = self.orm().await?.query_all(statement).await?;
        rows.into_iter()
            .map(|row| {
                Ok(AccountOverview {
                    id: row.try_get_by_index(0)?,
                    server: row.try_get_by_index(1)?,
                    login: row.try_get_by_index(2)?,
                    balance: row.try_get_by_index(3)?,
                    equity: row.try_get_by_index(4)?,
                    initial_balance: row.try_get_by_index(5)?,
                    all_time_change_percent: row.try_get_by_index(6)?,
                    day_change_percent: row.try_get_by_index(7)?,
                    active_strategies: row.try_get_by_index(8)?,
                    equity_chart_range: row.try_get_by_index(9)?,
                    history_range: row.try_get_by_index(10)?,
                    account_type: row.try_get_by_index(11)?,
                })
            })
            .collect()
    }

    pub async fn create_mt5_account(&self, input: &NewMt5Account) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_accounts",
                "(name,login,password,server,created_at,account_type) ",
                "VALUES ('',?,?,?,datetime('now'),?)"
            ),
            [
                input.login.clone().into(),
                input.password.clone().into(),
                input.server.clone().into(),
                input.account_type.clone().into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn update_account_view_settings(
        &self,
        account_id: i64,
        equity_chart_range: Option<&str>,
        history_range: Option<&str>,
    ) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "UPDATE mt5_accounts SET ",
                "equity_chart_range=COALESCE(?,equity_chart_range),",
                "history_range=COALESCE(?,history_range) ",
                "WHERE id=?"
            ),
            [
                equity_chart_range.map(str::to_owned).into(),
                history_range.map(str::to_owned).into(),
                account_id.into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.rows_affected() > 0)
    }

    pub async fn create_account_trade(
        &self,
        strategy: &str,
        entry_time: &str,
        entry_price: f64,
    ) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO account_trades",
                "(strategy_name,bookmap_entry_time,",
                "bookmap_entry_price,created_at) VALUES (?,?,?,datetime('now'))"
            ),
            [
                strategy.to_owned().into(),
                entry_time.to_owned().into(),
                entry_price.into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    /// Closes the latest open trade `strategy` opened ON `account_id`.
    ///
    /// The account is read off the command that opened the trade, which is what
    /// keeps one account's close from landing on another's trade of the same
    /// strategy.
    pub async fn close_account_trade(
        &self,
        account_id: i64,
        strategy: &str,
        exit_time: &str,
        exit_price: f64,
    ) -> Result<Option<i64>, ApiError> {
        let find = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT t.id FROM account_trades t ",
                "JOIN mt5_execution_commands c ON c.account_trade_id=t.id ",
                "WHERE c.account_id=? AND t.strategy_name=? ",
                "AND t.bookmap_exit_time='' ORDER BY t.id DESC LIMIT 1"
            ),
            [account_id.into(), strategy.to_owned().into()],
        );
        let Some(row) = self.orm().await?.query_one(find).await? else {
            return Ok(None);
        };
        let trade_id = row.try_get_by_index::<i64>(0)?;
        self.orm()
            .await?
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE account_trades SET bookmap_exit_time=?,",
                    "bookmap_exit_price=? WHERE id=? AND bookmap_exit_time=''"
                ),
                [
                    exit_time.to_owned().into(),
                    exit_price.into(),
                    trade_id.into(),
                ],
            ))
            .await?;
        Ok(Some(trade_id))
    }

    /// Every row of the account trade ledger, oldest first.
    pub async fn account_trade_ledger(&self) -> Result<Vec<AccountTrade>, ApiError> {
        let statement = Statement::from_string(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id,strategy_name,mt5pnl,",
                "bookmap_entry_time,bookmap_exit_time,bookmap_entry_price,",
                "bookmap_exit_price,mt5_entry_time,mt5_exit_time,",
                "mt5_entry_price,mt5_exit_price FROM account_trades ",
                "ORDER BY id"
            ),
        );
        self.orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(AccountTrade {
                    id: row.try_get_by_index(0)?,
                    strategy_name: row.try_get_by_index(1)?,
                    mt5pnl: row.try_get_by_index(2)?,
                    bookmap_entry_time: row.try_get_by_index(3)?,
                    bookmap_exit_time: row.try_get_by_index(4)?,
                    bookmap_entry_price: row.try_get_by_index(5)?,
                    bookmap_exit_price: row.try_get_by_index(6)?,
                    mt5_entry_time: row.try_get_by_index(7)?,
                    mt5_exit_time: row.try_get_by_index(8)?,
                    mt5_entry_price: row.try_get_by_index(9)?,
                    mt5_exit_price: row.try_get_by_index(10)?,
                })
            })
            .collect()
    }

    /// The account's ACTIVE strategies, or `None` for an account that does not
    /// exist.
    pub async fn active_account_strategies(
        &self,
        account_id: i64,
    ) -> Result<Option<Vec<AccountStrategy>>, ApiError> {
        if !self.mt5_account_exists(account_id).await? {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT s.id,s.strategy,s.symbol,s.active,s.book ",
                "FROM mt5_account_strategies s ",
                "WHERE s.account_id=? AND s.active=1 ORDER BY s.id"
            ),
            [account_id.into()],
        );
        let rows = self.orm().await?.query_all(statement).await?;
        let strategies = rows
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
            .collect::<Result<_, ApiError>>()?;
        Ok(Some(strategies))
    }

    pub async fn account_trades(
        &self,
        account_id: i64,
    ) -> Result<Option<Vec<AccountTradeSummary>>, ApiError> {
        if !self.mt5_account_exists(account_id).await? {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT t.id,t.strategy_name,t.mt5pnl,t.mt5_entry_time,",
                "t.mt5_exit_time,t.mt5_entry_price,t.mt5_exit_price ",
                "FROM account_trades t WHERE EXISTS (",
                "SELECT 1 FROM mt5_execution_commands c WHERE c.account_id=? ",
                "AND (c.account_trade_id=t.id OR c.closed_account_trade_id=t.id)) ",
                "ORDER BY t.id DESC"
            ),
            [account_id.into()],
        );
        let trades = self
            .orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(AccountTradeSummary {
                    id: row.try_get_by_index(0)?,
                    strategy_name: row.try_get_by_index(1)?,
                    pnl: row.try_get_by_index(2)?,
                    entry_time: row.try_get_by_index(3)?,
                    exit_time: row.try_get_by_index(4)?,
                    entry_price: row.try_get_by_index(5)?,
                    exit_price: row.try_get_by_index(6)?,
                })
            })
            .collect::<Result<_, ApiError>>()?;
        Ok(Some(trades))
    }

    pub async fn create_active_account_strategy(
        &self,
        account_id: i64,
        input: &AccountStrategyInput,
    ) -> Result<i64, ApiError> {
        if !self.mt5_account_exists(account_id).await? {
            return Err(ApiError::NotFound("MT5 account not found".into()));
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_account_strategies",
                "(account_id,strategy,symbol,active,book,created_at) ",
                "SELECT ?,?,?,1,?,datetime('now') WHERE NOT EXISTS (",
                "SELECT 1 FROM mt5_account_strategies ",
                "WHERE account_id=? AND strategy=?)"
            ),
            [
                account_id.into(),
                input.strategy.clone().into(),
                input.symbol.clone().into(),
                // DERIVED, never taken from the request: see `book_of`.
                {
                    let exness = crate::strategies::exness::oct_12_2026::book_of(&input.strategy);
                    if exness.is_empty()
                        && crate::strategies::fundednext::oct_18_2026::BOOK
                            .iter()
                            .any(|sleeve| sleeve.id() == input.strategy)
                    {
                        crate::strategies::fundednext::oct_18_2026::BOOK_ID.into()
                    } else {
                        exness.into()
                    }
                },
                account_id.into(),
                input.strategy.clone().into(),
            ],
        );
        let result = self.orm().await?.execute(statement).await?;
        if result.rows_affected() == 0 {
            return Err(ApiError::Conflict(
                "this strategy is already active for the account".into(),
            ));
        }
        Ok(result.last_insert_id() as i64)
    }

    pub async fn delete_account_strategy_of(
        &self,
        account_id: i64,
        strategy_id: i64,
    ) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "DELETE FROM mt5_account_strategies WHERE id=? AND account_id=?",
            [strategy_id.into(), account_id.into()],
        );
        Ok(self.orm().await?.execute(statement).await?.rows_affected() > 0)
    }

    pub async fn delete_mt5_account(&self, account_id: i64) -> Result<bool, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_account_strategies WHERE account_id=?",
                [account_id.into()],
            ))
            .await?;
        let result = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_accounts WHERE id=?",
                [account_id.into()],
            ))
            .await?;
        transaction.commit().await?;
        Ok(result.rows_affected() > 0)
    }

    async fn mt5_account_exists(&self, account_id: i64) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT 1 FROM mt5_accounts WHERE id=?",
            [account_id.into()],
        );
        Ok(self.orm().await?.query_one(statement).await?.is_some())
    }
}
