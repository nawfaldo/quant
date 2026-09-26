use super::{Database, LiveEvent, LiveStrategyTarget, StrategyPosition};
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement, TransactionTrait};

pub(crate) fn strategy_magic(account_strategy_id: i64) -> i64 {
    26_100_000 + account_strategy_id.rem_euclid(800_000)
}

impl Database {
    /// Current durable state of one execution command.  The live strategy keeps
    /// its emitted action until this reaches a terminal state, so an
    /// asynchronous bridge rejection can be rolled back instead of leaving the
    /// in-memory book ahead of MT5.
    pub async fn mt5_command_status(&self, command_id: i64) -> Result<Option<String>, ApiError> {
        let row = self
            .orm()
            .await?
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "SELECT status FROM mt5_execution_commands WHERE id=?",
                [command_id.into()],
            ))
            .await?;
        row.map(|row| row.try_get_by_index(0))
            .transpose()
            .map_err(Into::into)
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn reconcile_protective_close(
        &self,
        login: &str,
        ticket: i64,
        close_price: f64,
        pnl: f64,
        fill_time: &str,
        reason: &str,
    ) -> Result<bool, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        let row = transaction
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "SELECT sp.account_strategy_id,sp.position_key,sp.live_trade_id,",
                    "sp.environment_trade_id,sp.strategy FROM mt5_strategy_positions sp ",
                    "JOIN mt5_accounts a ON a.id=sp.account_id WHERE a.login=? ",
                    "AND sp.ticket=? AND sp.status IN ('open','pending_close') LIMIT 1",
                ),
                [login.to_owned().into(), ticket.into()],
            ))
            .await?;
        let Some(row) = row else {
            transaction.rollback().await?;
            return Ok(false);
        };
        let account_strategy_id = row.try_get_by_index::<i64>(0)?;
        let position_key = row.try_get_by_index::<String>(1)?;
        let live_trade_id = row.try_get_by_index::<i64>(2)?;
        let environment_trade_id = row.try_get_by_index::<i64>(3)?;
        let strategy = row.try_get_by_index::<String>(4)?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_strategy_positions SET remaining_volume=0,status='closed',",
                    "updated_at=datetime('now') WHERE account_strategy_id=? AND position_key=?",
                ),
                [account_strategy_id.into(), position_key.clone().into()],
            ))
            .await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_execution_commands SET status='superseded',",
                    "error=?,completed_at=datetime('now') WHERE account_strategy_id=? ",
                    "AND position_key=? AND action='close' AND status IN ('pending','leased')",
                ),
                [
                    reason.to_owned().into(),
                    account_strategy_id.into(),
                    position_key.clone().into(),
                ],
            ))
            .await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE live_trades SET ",
                    "engine_close_time=CASE WHEN engine_close_time='' THEN ? ELSE engine_close_time END,",
                    "engine_close_price=CASE WHEN engine_close_price=0 THEN ? ELSE engine_close_price END,",
                    "mt5_close_time=CASE WHEN mt5_close_time='' THEN ? ELSE mt5_close_time END,",
                    "mt5_close_price=CASE WHEN mt5_close_price=0 THEN ? ELSE mt5_close_price END,",
                    "exit_reason=CASE WHEN exit_reason='' THEN ? ELSE exit_reason END,",
                    "closed_at=COALESCE(NULLIF(closed_at,''),datetime('now')) WHERE id=?",
                ),
                [
                    fill_time.to_owned().into(),
                    close_price.into(),
                    fill_time.to_owned().into(),
                    close_price.into(),
                    reason.to_owned().into(),
                    live_trade_id.into(),
                ],
            ))
            .await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE environment_trades SET ",
                    "bookmap_exit_time=CASE WHEN bookmap_exit_time='' THEN ? ELSE bookmap_exit_time END,",
                    "bookmap_exit_price=CASE WHEN bookmap_exit_price=0 THEN ? ELSE bookmap_exit_price END,",
                    "mt5_exit_time=CASE WHEN mt5_exit_time='' THEN ? ELSE mt5_exit_time END,",
                    "mt5_exit_price=CASE WHEN mt5_exit_price=0 THEN ? ELSE mt5_exit_price END,",
                    "mt5pnl=? WHERE id=?",
                ),
                [
                    fill_time.to_owned().into(),
                    close_price.into(),
                    fill_time.to_owned().into(),
                    close_price.into(),
                    pnl.into(),
                    environment_trade_id.into(),
                ],
            ))
            .await?;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO live_events(at,bar_time,account_strategy_id,strategy,kind,",
                    "position_key,detail) VALUES(datetime('now'),?,?,?,?,?,?)",
                ),
                [
                    fill_time.to_owned().into(),
                    account_strategy_id.into(),
                    strategy.into(),
                    "protective_close".into(),
                    position_key.into(),
                    reason.to_owned().into(),
                ],
            ))
            .await?;
        transaction.commit().await?;
        Ok(true)
    }

    /// Reconciles strategy positions that are marked 'open' in durable storage but
    /// no longer exist on the broker, e.g. if the operator closed the position
    /// manually in MT5 while the bridge was offline or before it sent a receipt.
    pub async fn reconcile_missing_bridge_positions(
        &self,
        account_strategy_id: i64,
    ) -> Result<Vec<i64>, ApiError> {
        let rows = self
            .orm()
            .await?
            .query_all(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "SELECT sp.ticket, sp.position_key, sp.live_trade_id, sp.strategy ",
                    "FROM mt5_strategy_positions sp ",
                    "WHERE sp.account_strategy_id=? AND sp.status='open' AND sp.ticket>0 ",
                    "AND sp.updated_at < datetime('now', '-3 seconds') ",
                    "AND NOT EXISTS (SELECT 1 FROM mt5_bridge_positions bp ",
                    "WHERE bp.account_id=sp.account_id AND bp.ticket=sp.ticket)"
                ),
                [account_strategy_id.into()],
            ))
            .await?;
        if rows.is_empty() {
            return Ok(Vec::new());
        }
        let mut reconciled = Vec::new();
        let transaction = self.orm().await?.begin().await?;
        for row in rows {
            let ticket = row.try_get_by_index::<i64>(0)?;
            let position_key = row.try_get_by_index::<String>(1)?;
            let live_trade_id = row.try_get_by_index::<i64>(2)?;
            let strategy = row.try_get_by_index::<String>(3)?;

            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE mt5_strategy_positions SET remaining_volume=0, status='closed', ",
                        "updated_at=datetime('now') WHERE account_strategy_id=? AND position_key=?"
                    ),
                    [account_strategy_id.into(), position_key.clone().into()],
                ))
                .await?;

            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE mt5_execution_commands SET status='superseded', ",
                        "error='manual_close', completed_at=datetime('now') WHERE account_strategy_id=? ",
                        "AND position_key=? AND action='close' AND status IN ('pending','leased')"
                    ),
                    [account_strategy_id.into(), position_key.clone().into()],
                ))
                .await?;

            if live_trade_id > 0 {
                transaction
                    .execute(Statement::from_sql_and_values(
                        DatabaseBackend::Sqlite,
                        concat!(
                            "UPDATE live_trades SET ",
                            "exit_reason=CASE WHEN exit_reason='' THEN 'manual_close' ELSE exit_reason END, ",
                            "mt5_close_time=CASE WHEN mt5_close_time='' THEN strftime('%Y-%m-%d %H:%M:%S', 'now') ELSE mt5_close_time END, ",
                            "closed_at=COALESCE(NULLIF(closed_at,''), datetime('now')) WHERE id=?"
                        ),
                        [live_trade_id.into()],
                    ))
                    .await?;
            }

            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "INSERT INTO live_events(at,bar_time,account_strategy_id,strategy,kind,",
                        "position_key,detail) VALUES(datetime('now'),strftime('%Y-%m-%d %H:%M:%S', 'now'),?,?,?,?,?)"
                    ),
                    [
                        account_strategy_id.into(),
                        strategy.into(),
                        "manual_close".into(),
                        position_key.into(),
                        format!("reconciled missing broker ticket {ticket}").into(),
                    ],
                ))
                .await?;

            reconciled.push(ticket);
        }
        transaction.commit().await?;
        Ok(reconciled)
    }

    pub async fn live_strategy_targets(&self) -> Result<Vec<LiveStrategyTarget>, ApiError> {
        let statement = Statement::from_string(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT s.id,s.account_id,a.environment_id,s.strategy,s.symbol,",
                // 0.0 and not 0: SQLite types a bare `0` as INTEGER, and this
                // column is decoded as f64. An account with no heartbeat row
                // yet -- every newly added account, before its bridge first
                // polls -- took the literal branch and the whole live runtime
                // crash-looped on "Rust type Option<f64> is not compatible with
                // SQL type INTEGER". It survived unnoticed only because the
                // previous account had always had a heartbeat row.
                // Canonical replay compounds only settled trades. MT5 `equity`
                // includes every open mark (and any manual position), which
                // changes the next sleeve's quantity while the same backtest
                // still sizes from realized balance. Use broker balance here so
                // the live and replay sizing clocks are the same.
                "s.live_started_at,COALESCE(h.balance,0.0),",
                "CASE WHEN h.seen_at>=datetime('now','-5 seconds') THEN 1 ELSE 0 END ",
                "FROM mt5_account_strategies s JOIN mt5_accounts a ON a.id=s.account_id ",
                "LEFT JOIN mt5_bridge_heartbeats h ON h.account_id=a.id ",
                "WHERE s.active=1 AND a.environment_id IS NOT NULL ORDER BY s.id",
            ),
        );
        self.orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(LiveStrategyTarget {
                    account_strategy_id: row.try_get_by_index(0)?,
                    account_id: row.try_get_by_index(1)?,
                    environment_id: row.try_get_by_index(2)?,
                    strategy: row.try_get_by_index(3)?,
                    symbol: row.try_get_by_index(4)?,
                    live_started_at: row.try_get_by_index(5)?,
                    equity: row.try_get_by_index(6)?,
                    connected: row.try_get_by_index::<i64>(7)? != 0,
                })
            })
            .collect()
    }

    pub async fn set_live_strategy_start(
        &self,
        account_strategy_id: i64,
        timestamp: i64,
    ) -> Result<i64, ApiError> {
        self.orm()
            .await?
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_account_strategies SET live_started_at=? ",
                    "WHERE id=? AND live_started_at=0",
                ),
                [timestamp.into(), account_strategy_id.into()],
            ))
            .await?;
        let row = self
            .orm()
            .await?
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "SELECT live_started_at FROM mt5_account_strategies WHERE id=?",
                [account_strategy_id.into()],
            ))
            .await?
            .ok_or_else(|| ApiError::NotFound("active strategy not found".into()))?;
        Ok(row.try_get_by_index(0)?)
    }

    pub async fn strategy_positions(
        &self,
        account_strategy_id: i64,
    ) -> Result<Vec<StrategyPosition>, ApiError> {
        let rows = self
            .orm()
            .await?
            .query_all(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "SELECT position_key,side,remaining_volume,ticket,status ",
                    "FROM mt5_strategy_positions WHERE account_strategy_id=? ",
                    "AND status IN ('pending_open','open','pending_close') ORDER BY position_key",
                ),
                [account_strategy_id.into()],
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(StrategyPosition {
                    position_key: row.try_get_by_index(0)?,
                    side: row.try_get_by_index(1)?,
                    remaining_volume: row.try_get_by_index(2)?,
                    ticket: row.try_get_by_index(3)?,
                    status: row.try_get_by_index(4)?,
                })
            })
            .collect()
    }

    pub async fn strategy_position_mismatch(
        &self,
        account_strategy_id: i64,
    ) -> Result<bool, ApiError> {
        let row = self
            .orm()
            .await?
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                // BOTH LEGS FORGIVE A FILL THAT IS STILL LANDING. The bridge
                // pushes its snapshot and the command result separately, in
                // either order. On 2026-09-25 06:00:01 `usdjpy_rvol`'s short
                // reached the snapshot while its durable row was still
                // `pending_open` with no ticket, and the sleeve was blocked for
                // one second over its own trade. So a recent `pending_open`
                // row accounts for an unrecognised ticket of ours, and a row
                // that turned `open` in the last 3 seconds may be missing from
                // a snapshot taken before the fill -- the same grace
                // `reconcile_missing_bridge_positions` already allows.
                concat!(
                    "SELECT CASE WHEN EXISTS(",
                    "SELECT 1 FROM mt5_strategy_positions sp ",
                    "WHERE sp.account_strategy_id=? AND sp.status='open' AND sp.ticket>0 ",
                    "AND sp.updated_at < datetime('now', '-3 seconds') ",
                    "AND NOT EXISTS(SELECT 1 FROM mt5_bridge_positions bp ",
                    "WHERE bp.account_id=sp.account_id AND bp.ticket=sp.ticket)) ",
                    "OR EXISTS(SELECT 1 FROM mt5_bridge_positions bp ",
                    "JOIN mt5_account_strategies s ON s.account_id=bp.account_id ",
                    "WHERE s.id=? AND bp.magic=? AND NOT EXISTS(",
                    "SELECT 1 FROM mt5_strategy_positions sp ",
                    "WHERE sp.account_strategy_id=s.id AND sp.ticket=bp.ticket ",
                    "AND sp.status IN ('open','pending_close')) AND NOT EXISTS(",
                    "SELECT 1 FROM mt5_strategy_positions sp ",
                    "WHERE sp.account_strategy_id=s.id AND sp.status='pending_open' ",
                    "AND sp.updated_at >= datetime('now', '-60 seconds'))) THEN 1 ELSE 0 END",
                ),
                [
                    account_strategy_id.into(),
                    account_strategy_id.into(),
                    strategy_magic(account_strategy_id).into(),
                ],
            ))
            .await?
            .ok_or_else(|| ApiError::NotFound("active strategy not found".into()))?;
        Ok(row.try_get_by_index::<i64>(0)? != 0)
    }

    /// Records a live-runtime decision that is not a trade. Deliberately
    /// infallible from the caller's point of view: telemetry must never be the
    /// reason an exit or an entry fails, so a write error is logged and
    /// swallowed rather than propagated.
    pub async fn log_live_event(
        &self,
        account_strategy_id: i64,
        strategy: &str,
        kind: &str,
        position_key: &str,
        bar_time: &str,
        detail: &str,
    ) {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO live_events",
                "(at,bar_time,account_strategy_id,strategy,kind,position_key,detail) ",
                "VALUES (datetime('now'),?,?,?,?,?,?)",
            ),
            [
                bar_time.to_owned().into(),
                account_strategy_id.into(),
                strategy.to_owned().into(),
                kind.to_owned().into(),
                position_key.to_owned().into(),
                detail.chars().take(500).collect::<String>().into(),
            ],
        );
        let write = async {
            self.orm().await?.execute(statement).await?;
            Ok::<_, ApiError>(())
        };
        if let Err(error) = write.await {
            tracing::warn!(%error, kind, strategy, "unable to record live event");
        }
    }

    /// Most recent live events, newest first.
    pub async fn live_events(&self, limit: i64) -> Result<Vec<LiveEvent>, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT at,bar_time,account_strategy_id,strategy,kind,position_key,detail ",
                "FROM live_events ORDER BY id DESC LIMIT ?",
            ),
            [limit.clamp(1, 1000).into()],
        );
        self.orm()
            .await?
            .query_all(statement)
            .await?
            .into_iter()
            .map(|row| {
                Ok(LiveEvent {
                    at: row.try_get_by_index(0)?,
                    bar_time: row.try_get_by_index(1)?,
                    account_strategy_id: row.try_get_by_index(2)?,
                    strategy: row.try_get_by_index(3)?,
                    kind: row.try_get_by_index(4)?,
                    position_key: row.try_get_by_index(5)?,
                    detail: row.try_get_by_index(6)?,
                })
            })
            .collect()
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn enqueue_live_open(
        &self,
        account_strategy_id: i64,
        account_id: i64,
        environment_id: i64,
        strategy: &str,
        position_key: &str,
        side: &str,
        symbol: &str,
        volume: f64,
        timestamp: &str,
        price: f64,
    ) -> Result<i64, ApiError> {
        let transaction = self.orm().await?.begin().await?;
        let live_trade = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO live_trades",
                    "(strategy_name,side,contract,engine_entry_price,engine_open_time,",
                    "position_key,created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
                ),
                [
                    strategy.to_owned().into(),
                    side.to_owned().into(),
                    volume.into(),
                    price.into(),
                    timestamp.to_owned().into(),
                    position_key.to_owned().into(),
                ],
            ))
            .await?
            .last_insert_id() as i64;
        let environment_trade = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO environment_trades",
                    "(environment_id,strategy_name,bookmap_entry_time,",
                    "bookmap_entry_price,created_at) VALUES (?,?,?,?,datetime('now'))",
                ),
                [
                    environment_id.into(),
                    strategy.to_owned().into(),
                    timestamp.to_owned().into(),
                    price.into(),
                ],
            ))
            .await?
            .last_insert_id() as i64;
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO mt5_strategy_positions",
                    "(account_strategy_id,position_key,account_id,strategy,symbol,side,",
                    "requested_volume,remaining_volume,status,live_trade_id,",
                    "environment_trade_id,updated_at) VALUES (?,?,?,?,?,?,?,?,",
                    "'pending_open',?,?,datetime('now')) ",
                    "ON CONFLICT(account_strategy_id,position_key) DO UPDATE SET ",
                    "account_id=excluded.account_id,strategy=excluded.strategy,",
                    "symbol=excluded.symbol,side=excluded.side,",
                    "requested_volume=excluded.requested_volume,",
                    "remaining_volume=excluded.remaining_volume,ticket=0,",
                    "status='pending_open',live_trade_id=excluded.live_trade_id,",
                    "environment_trade_id=excluded.environment_trade_id,",
                    "updated_at=datetime('now')",
                ),
                [
                    account_strategy_id.into(),
                    position_key.to_owned().into(),
                    account_id.into(),
                    strategy.to_owned().into(),
                    symbol.to_owned().into(),
                    side.to_owned().into(),
                    volume.into(),
                    volume.into(),
                    live_trade.into(),
                    environment_trade.into(),
                ],
            ))
            .await?;
        let command = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO mt5_execution_commands",
                    "(account_id,strategy,action,symbol,volume,trade_id,environment_trade_id,",
                    "account_strategy_id,position_key,target_ticket,magic,created_at) ",
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                ),
                [
                    account_id.into(),
                    strategy.to_owned().into(),
                    side.to_owned().into(),
                    symbol.to_owned().into(),
                    volume.into(),
                    live_trade.into(),
                    environment_trade.into(),
                    account_strategy_id.into(),
                    position_key.to_owned().into(),
                    (-1).into(),
                    strategy_magic(account_strategy_id).into(),
                ],
            ))
            .await?
            .last_insert_id() as i64;
        transaction.commit().await?;
        Ok(command)
    }
}

/// One strategy-driven close, as the live runtime asks for it.
///
/// A struct rather than nine positional arguments: three of the fields are
/// `&str` and two are `f64`, so a transposed pair would type-check and enqueue a
/// real order against the wrong position.
pub struct LiveCloseRequest<'a> {
    pub account_strategy_id: i64,
    pub account_id: i64,
    pub strategy: &'a str,
    pub position_key: &'a str,
    pub symbol: &'a str,
    /// How much of the remaining volume to close, 0.0..=1.0.
    pub fraction: f64,
    pub timestamp: &'a str,
    pub price: f64,
    pub exit_reason: &'a str,
}

impl Database {
    pub async fn enqueue_live_close(
        &self,
        request: &LiveCloseRequest<'_>,
    ) -> Result<Option<i64>, ApiError> {
        let LiveCloseRequest {
            account_strategy_id,
            account_id,
            strategy,
            position_key,
            symbol,
            fraction,
            timestamp,
            price,
            exit_reason,
        } = *request;
        let transaction = self.orm().await?.begin().await?;
        let row = transaction
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "SELECT remaining_volume,ticket,live_trade_id,environment_trade_id ",
                    "FROM mt5_strategy_positions WHERE account_strategy_id=? ",
                    // Never emit a close without the broker ticket. Falling
                    // back to magic-only matching is unsafe for strategies
                    // with several tagged positions: one early exit could
                    // flatten every position owned by that strategy. The live
                    // slot retains the exit as owed and retries after the open
                    // acknowledgement supplies a positive ticket.
                    "AND position_key=? AND status='open' AND ticket>0",
                ),
                [account_strategy_id.into(), position_key.to_owned().into()],
            ))
            .await?;
        let Some(row) = row else {
            transaction.rollback().await?;
            return Ok(None);
        };
        let remaining = row.try_get_by_index::<f64>(0)?;
        let target_ticket = row.try_get_by_index::<i64>(1)?;
        let live_trade_id = row.try_get_by_index::<i64>(2)?;
        let environment_trade_id = row.try_get_by_index::<i64>(3)?;
        let fraction = fraction.clamp(0.0, 1.0);
        let final_close = fraction >= 1.0 - 1e-9;
        let close_volume = if final_close {
            remaining
        } else {
            remaining * fraction
        };
        if !close_volume.is_finite() || close_volume <= 0.0 {
            transaction.rollback().await?;
            return Ok(None);
        }
        if final_close {
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE live_trades SET engine_close_price=?,engine_close_time=?,",
                        "exit_reason=?,closed_at=datetime('now') ",
                        "WHERE id=? AND engine_close_time=''",
                    ),
                    [
                        price.into(),
                        timestamp.to_owned().into(),
                        exit_reason.to_owned().into(),
                        live_trade_id.into(),
                    ],
                ))
                .await?;
            transaction
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    concat!(
                        "UPDATE environment_trades SET bookmap_exit_time=?,",
                        "bookmap_exit_price=? WHERE id=? AND bookmap_exit_time=''",
                    ),
                    [
                        timestamp.to_owned().into(),
                        price.into(),
                        environment_trade_id.into(),
                    ],
                ))
                .await?;
        }
        transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "UPDATE mt5_strategy_positions SET status='pending_close',",
                    "updated_at=datetime('now') WHERE account_strategy_id=? AND position_key=?",
                ),
                [account_strategy_id.into(), position_key.to_owned().into()],
            ))
            .await?;
        let command = transaction
            .execute(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                concat!(
                    "INSERT INTO mt5_execution_commands",
                    "(account_id,strategy,action,symbol,volume,closed_trade_id,",
                    "closed_environment_trade_id,account_strategy_id,position_key,",
                    "target_ticket,magic,close_fraction,created_at) ",
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                ),
                [
                    account_id.into(),
                    strategy.to_owned().into(),
                    "close".into(),
                    symbol.to_owned().into(),
                    close_volume.into(),
                    (if final_close { live_trade_id } else { -1 }).into(),
                    (if final_close {
                        environment_trade_id
                    } else {
                        -1
                    })
                    .into(),
                    account_strategy_id.into(),
                    position_key.to_owned().into(),
                    target_ticket.into(),
                    strategy_magic(account_strategy_id).into(),
                    fraction.into(),
                ],
            ))
            .await?
            .last_insert_id() as i64;
        transaction.commit().await?;
        Ok(Some(command))
    }
}
