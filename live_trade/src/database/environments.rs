use super::{
    AccountStrategy, AccountStrategyInput, CreateEnvironment, Database, Environment,
    EnvironmentAccountTrade, EnvironmentCostRule, EnvironmentCostRuleMarket, EnvironmentMt5Account,
    EnvironmentMt5AccountInput, EnvironmentTrade,
};
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement, TransactionTrait};

impl Database {
    pub async fn environments(&self) -> Result<Vec<Environment>, ApiError> {
        let rows = self
            .orm()
            .await?
            .query_all(Statement::from_string(
                DatabaseBackend::Sqlite,
                "SELECT id,name FROM environments ORDER BY id",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(Environment {
                    id: row.try_get_by_index(0)?,
                    name: row.try_get_by_index(1)?,
                })
            })
            .collect()
    }

    pub async fn create_environment(&self, input: &CreateEnvironment) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "INSERT INTO environments(name,created_at) VALUES (?,datetime('now'))",
            [input.name.clone().into()],
        );
        match self.orm().await?.execute(statement).await {
            Ok(result) => Ok(result.last_insert_id() as i64),
            Err(error) if unique_constraint(&error) => {
                Err(ApiError::Conflict("environment name already exists".into()))
            }
            Err(error) => Err(error.into()),
        }
    }

    pub async fn environment_mt5_accounts(
        &self,
        environment_id: i64,
    ) -> Result<Option<Vec<EnvironmentMt5Account>>, ApiError> {
        if !self.environment_exists(environment_id).await? {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
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
                "ON h.account_id=a.id WHERE a.environment_id=? ORDER BY a.id"
            ),
            [environment_id.into()],
        );
        let rows = self.orm().await?.query_all(statement).await?;
        let accounts = rows
            .into_iter()
            .map(|row| {
                Ok(EnvironmentMt5Account {
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
            .collect::<Result<_, ApiError>>()?;
        Ok(Some(accounts))
    }

    pub async fn create_environment_mt5_account(
        &self,
        environment_id: i64,
        input: &EnvironmentMt5AccountInput,
    ) -> Result<i64, ApiError> {
        if !self.environment_exists(environment_id).await? {
            return Err(ApiError::NotFound("environment not found".into()));
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO mt5_accounts",
                "(name,login,password,server,created_at,environment_id,account_type) ",
                "VALUES ('',?,?,?,datetime('now'),?,?)"
            ),
            [
                input.login.clone().into(),
                input.password.clone().into(),
                input.server.clone().into(),
                environment_id.into(),
                input.account_type.clone().into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn update_environment_account_view_settings(
        &self,
        environment_id: i64,
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
                "WHERE id=? AND environment_id=?"
            ),
            [
                equity_chart_range.map(str::to_owned).into(),
                history_range.map(str::to_owned).into(),
                account_id.into(),
                environment_id.into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.rows_affected() > 0)
    }

    pub async fn create_environment_trade(
        &self,
        environment_id: i64,
        strategy: &str,
        entry_time: &str,
        entry_price: f64,
    ) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO environment_trades",
                "(environment_id,strategy_name,bookmap_entry_time,",
                "bookmap_entry_price,created_at) VALUES (?,?,?,?,datetime('now'))"
            ),
            [
                environment_id.into(),
                strategy.to_owned().into(),
                entry_time.to_owned().into(),
                entry_price.into(),
            ],
        );
        Ok(self.orm().await?.execute(statement).await?.last_insert_id() as i64)
    }

    pub async fn close_environment_trade(
        &self,
        environment_id: i64,
        account_id: i64,
        strategy: &str,
        exit_time: &str,
        exit_price: f64,
    ) -> Result<Option<i64>, ApiError> {
        let find = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT t.id FROM environment_trades t ",
                "JOIN mt5_execution_commands c ON c.environment_trade_id=t.id ",
                "WHERE t.environment_id=? AND c.account_id=? AND t.strategy_name=? ",
                "AND t.bookmap_exit_time='' ORDER BY t.id DESC LIMIT 1"
            ),
            [
                environment_id.into(),
                account_id.into(),
                strategy.to_owned().into(),
            ],
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
                    "UPDATE environment_trades SET bookmap_exit_time=?,",
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

    pub async fn environment_trades(
        &self,
        environment_id: i64,
    ) -> Result<Vec<EnvironmentTrade>, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id,environment_id,strategy_name,mt5pnl,",
                "bookmap_entry_time,bookmap_exit_time,bookmap_entry_price,",
                "bookmap_exit_price,mt5_entry_time,mt5_exit_time,",
                "mt5_entry_price,mt5_exit_price FROM environment_trades ",
                "WHERE environment_id=? ORDER BY id"
            ),
            [environment_id.into()],
        );
        self.orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(EnvironmentTrade {
                    id: row.try_get_by_index(0)?,
                    environment_id: row.try_get_by_index(1)?,
                    strategy_name: row.try_get_by_index(2)?,
                    mt5pnl: row.try_get_by_index(3)?,
                    bookmap_entry_time: row.try_get_by_index(4)?,
                    bookmap_exit_time: row.try_get_by_index(5)?,
                    bookmap_entry_price: row.try_get_by_index(6)?,
                    bookmap_exit_price: row.try_get_by_index(7)?,
                    mt5_entry_time: row.try_get_by_index(8)?,
                    mt5_exit_time: row.try_get_by_index(9)?,
                    mt5_entry_price: row.try_get_by_index(10)?,
                    mt5_exit_price: row.try_get_by_index(11)?,
                })
            })
            .collect()
    }

    pub async fn environment_account_strategies(
        &self,
        environment_id: i64,
        account_id: i64,
    ) -> Result<Option<Vec<AccountStrategy>>, ApiError> {
        if !self
            .environment_mt5_account_exists(environment_id, account_id)
            .await?
        {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT s.id,s.strategy,s.symbol,s.active,s.book ",
                "FROM mt5_account_strategies s ",
                "JOIN mt5_accounts a ON a.id=s.account_id ",
                "WHERE a.environment_id=? AND a.id=? AND s.active=1 ORDER BY s.id"
            ),
            [environment_id.into(), account_id.into()],
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

    pub async fn environment_account_trades(
        &self,
        environment_id: i64,
        account_id: i64,
    ) -> Result<Option<Vec<EnvironmentAccountTrade>>, ApiError> {
        if !self
            .environment_mt5_account_exists(environment_id, account_id)
            .await?
        {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT t.id,t.strategy_name,t.mt5pnl,t.mt5_entry_time,",
                "t.mt5_exit_time,t.mt5_entry_price,t.mt5_exit_price ",
                "FROM environment_trades t WHERE t.environment_id=? AND EXISTS (",
                "SELECT 1 FROM mt5_execution_commands c WHERE c.account_id=? ",
                "AND (c.environment_trade_id=t.id OR c.closed_environment_trade_id=t.id)) ",
                "ORDER BY t.id DESC"
            ),
            [environment_id.into(), account_id.into()],
        );
        let trades = self
            .orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(EnvironmentAccountTrade {
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

    pub async fn create_environment_account_strategy(
        &self,
        environment_id: i64,
        account_id: i64,
        input: &AccountStrategyInput,
    ) -> Result<i64, ApiError> {
        if !self
            .environment_mt5_account_exists(environment_id, account_id)
            .await?
        {
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
                crate::strategies::idk::exness_combined::book_of(&input.strategy).into(),
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

    pub async fn delete_environment_account_strategy(
        &self,
        environment_id: i64,
        account_id: i64,
        strategy_id: i64,
    ) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "DELETE FROM mt5_account_strategies WHERE id=? AND account_id IN (",
                "SELECT id FROM mt5_accounts WHERE id=? AND environment_id=?)"
            ),
            [strategy_id.into(), account_id.into(), environment_id.into()],
        );
        Ok(self.orm().await?.execute(statement).await?.rows_affected() > 0)
    }

    pub async fn delete_environment_mt5_account(
        &self,
        environment_id: i64,
        account_id: i64,
    ) -> Result<bool, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "DELETE FROM mt5_account_strategies WHERE account_id IN ",
                    "(SELECT id FROM mt5_accounts WHERE id=? AND environment_id=?)"
                ),
                [account_id.into(), environment_id.into()],
            ))
            .await?;
        let result = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "DELETE FROM mt5_accounts WHERE id=? AND environment_id=?",
                [account_id.into(), environment_id.into()],
            ))
            .await?;
        transaction.commit().await?;
        Ok(result.rows_affected() > 0)
    }

    pub async fn environment_name(&self, id: i64) -> Result<Option<String>, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT name FROM environments WHERE id=?",
            [id.into()],
        );
        self.orm()
            .await?
            .query_one(statement)
            .await?
            .map(|row| row.try_get_by_index(0).map_err(ApiError::from))
            .transpose()
    }

    /// Rewrites every environment's stored cost rule from `backtest::cost_model`.
    ///
    /// CALLED ON EVERY BOOT, and it DELETES BEFORE IT WRITES. These rows are a
    /// materialised view of a compiled table, so the only correct reconciliation
    /// is a full replacement: an upsert would leave behind a market the book no
    /// longer trades, and that stale line would then be rendered as though the
    /// engine still charged it.
    ///
    /// SCOPED BY REGISTERED STRATEGIES, NOT BY NAME. An environment gets the
    /// rule when `strategies::for_environment` gives it something to run --
    /// today that is `idk` and nothing else. Keying on the literal string "idk"
    /// would put the rule and the strategy list on two different definitions of
    /// the same question, and the cost model is built from `known_markets`,
    /// which is derived from that same book.
    pub async fn sync_environment_cost_rules(&self) -> Result<(), ApiError> {
        let model = crate::backtest::cost_model();
        let connection = self.orm().await?;
        let transaction = connection.begin().await?;
        transaction
            .execute(Statement::from_string(
                DatabaseBackend::Sqlite,
                "DELETE FROM environment_cost_rules",
            ))
            .await?;
        for environment in self.environments().await? {
            if crate::strategies::for_environment(&environment.name).is_empty() {
                continue;
            }
            for market in &model.markets {
                transaction
                    .execute(Statement::from_sql_and_values(
                        DatabaseBackend::Sqlite,
                        concat!(
                            "INSERT INTO environment_cost_rules",
                            "(environment_id,rule_id,name,detail,market,spread_bp,minimum_lots) ",
                            "VALUES (?,?,?,?,?,?,?)"
                        ),
                        [
                            environment.id.into(),
                            model.id.into(),
                            model.name.into(),
                            model.detail.into(),
                            market.market.into(),
                            market.spread_bp.into(),
                            market.minimum_lots.into(),
                        ],
                    ))
                    .await?;
            }
        }
        transaction.commit().await?;
        Ok(())
    }

    /// One environment's stored backtest rules.
    ///
    /// `None` means the environment does not exist. An empty vector means it
    /// exists and runs nothing -- which is a real answer and not a failure, so
    /// the rules screen shows a rule-less environment rather than an error.
    ///
    /// SELF-HEALING. A registered environment with no rows is a database that
    /// predates the table, so this syncs once and reads again rather than
    /// serving a blank screen. Without it the rules would appear only after a
    /// restart on the new binary.
    pub async fn environment_cost_rules(
        &self,
        environment_id: i64,
    ) -> Result<Option<Vec<EnvironmentCostRule>>, ApiError> {
        let Some(name) = self.environment_name(environment_id).await? else {
            return Ok(None);
        };
        let mut rows = self.read_environment_cost_rules(environment_id).await?;
        if rows.is_empty() && !crate::strategies::for_environment(&name).is_empty() {
            self.sync_environment_cost_rules().await?;
            rows = self.read_environment_cost_rules(environment_id).await?;
        }
        Ok(Some(rows))
    }

    /// The stored rows exactly as they are, grouped into one rule per `rule_id`.
    async fn read_environment_cost_rules(
        &self,
        environment_id: i64,
    ) -> Result<Vec<EnvironmentCostRule>, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT rule_id,name,detail,market,spread_bp,minimum_lots ",
                "FROM environment_cost_rules WHERE environment_id=? ",
                "ORDER BY rule_id,market"
            ),
            [environment_id.into()],
        );
        let mut rules: Vec<EnvironmentCostRule> = Vec::new();
        for row in self.orm().await?.query_all(statement).await? {
            let id: String = row.try_get_by_index(0)?;
            let market = EnvironmentCostRuleMarket {
                market: row.try_get_by_index(3)?,
                spread_bp: row.try_get_by_index(4)?,
                minimum_lots: row.try_get_by_index(5)?,
            };
            match rules.last_mut() {
                Some(rule) if rule.id == id => rule.markets.push(market),
                _ => rules.push(EnvironmentCostRule {
                    id,
                    name: row.try_get_by_index(1)?,
                    detail: row.try_get_by_index(2)?,
                    markets: vec![market],
                }),
            }
        }
        Ok(rules)
    }

    pub(crate) async fn environment_exists(&self, id: i64) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT 1 FROM environments WHERE id=?",
            [id.into()],
        );
        Ok(self.orm().await?.query_one(statement).await?.is_some())
    }

    async fn environment_mt5_account_exists(
        &self,
        environment_id: i64,
        account_id: i64,
    ) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT 1 FROM mt5_accounts WHERE id=? AND environment_id=?",
            [account_id.into(), environment_id.into()],
        );
        Ok(self.orm().await?.query_one(statement).await?.is_some())
    }
}

fn unique_constraint(error: &sea_orm::DbErr) -> bool {
    let message = error.to_string();
    message.contains("UNIQUE constraint") || message.contains("unique constraint")
}
