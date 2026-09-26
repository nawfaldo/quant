use super::*;
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement};
use serde_json::{Value, json};
/// A newly added account has NO heartbeat row until its bridge first polls, and
/// the live runtime reads its targets before that can happen.
///
/// `COALESCE(h.equity,0)` returned a bare integer on that path, which sqlx
/// refused to decode into the f64 `equity` field, so the entire multi-strategy
/// runtime crash-looped with "Rust type Option<f64> is not compatible with SQL
/// type INTEGER" the moment a fresh account was configured. It went unnoticed
/// because the only existing account had always had a heartbeat row, and
/// because the surrounding test exercised a different query.
#[actix_web::test]
async fn live_targets_decode_before_the_first_heartbeat() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();

    let environment = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    let account = db
        .add_account(&Mt5AccountInput {
            name: "pro".into(),
            login: "416209807".into(),
            server: "Exness-MT5Trial14".into(),
        })
        .await
        .unwrap();
    db.orm()
        .await
        .unwrap()
        .execute(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "UPDATE mt5_accounts SET environment_id=? WHERE id=?",
            [environment.into(), account.into()],
        ))
        .await
        .unwrap();
    let strategy = db
        .add_account_strategy(
            account,
            &AccountStrategyInput {
                strategy: "ofi_momentum".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    db.set_account_strategy_active(strategy, true)
        .await
        .unwrap();

    // No row in mt5_bridge_heartbeats: this must still decode.
    let targets = db.live_strategy_targets().await.unwrap();
    assert_eq!(targets.len(), 1);
    assert_eq!(targets[0].strategy, "ofi_momentum");
    assert_eq!(targets[0].equity, 0.0);
    assert!(!targets[0].connected);

    db.record_mt5_heartbeat("416209807", "Exness-MT5Trial14", 400.0, 350.0, "USD")
        .await
        .unwrap();
    let targets = db.live_strategy_targets().await.unwrap();
    assert_eq!(
        targets[0].equity, 400.0,
        "live sizing follows realized balance, not mark-to-market equity"
    );
}

#[actix_web::test]
async fn promotes_the_ml_ofi_strategy_and_retires_the_unfiltered_one() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("app.db");
    let db = Database::new(&path);
    db.initialize().await.unwrap();
    db.orm()
        .await
        .unwrap()
        .execute_unprepared(
            "DELETE FROM settings WHERE key='ofi_ml_promoted_v1';
             INSERT INTO environments(id,name) VALUES (1,'idk');
             INSERT INTO mt5_accounts(id,login,password,server,environment_id)
             VALUES (1,'123','secret','demo',1);
             INSERT INTO mt5_account_strategies(account_id,strategy,symbol,active)
             VALUES (1,'ofi_momentum','USTEC',1),
                    (1,'ofi_momentum_ml','USTEC',1);
             INSERT INTO backtests(strategy)
             VALUES ('OFI_MOMENTUM'),('OFI_MOMENTUM_ML');
             INSERT INTO live_trades(strategy_name,contract)
             VALUES ('ofi_momentum',0.01),('ofi_momentum_ml',0.01);
             INSERT INTO environment_trades(environment_id,strategy_name)
             VALUES (1,'ofi_momentum'),(1,'ofi_momentum_ml');",
        )
        .await
        .unwrap();
    drop(db);

    let migrated = Database::new(path);
    migrated.initialize().await.unwrap();
    let active = migrated
        .orm()
        .await
        .unwrap()
        .query_all(Statement::from_string(
            DatabaseBackend::Sqlite,
            "SELECT strategy FROM mt5_account_strategies ORDER BY id",
        ))
        .await
        .unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(
        active[0].try_get_by_index::<String>(0).unwrap(),
        "ofi_momentum"
    );
    let backtests = migrated
        .orm()
        .await
        .unwrap()
        .query_all(Statement::from_string(
            DatabaseBackend::Sqlite,
            "SELECT strategy FROM backtests ORDER BY id",
        ))
        .await
        .unwrap();
    assert_eq!(
        backtests[0].try_get_by_index::<String>(0).unwrap(),
        "OFI_MOMENTUM_LEGACY"
    );
    assert_eq!(
        backtests[1].try_get_by_index::<String>(0).unwrap(),
        "OFI_MOMENTUM"
    );
    let live_names = migrated
        .orm()
        .await
        .unwrap()
        .query_all(Statement::from_string(
            DatabaseBackend::Sqlite,
            "SELECT strategy_name FROM live_trades ORDER BY id",
        ))
        .await
        .unwrap();
    assert_eq!(
        live_names[0].try_get_by_index::<String>(0).unwrap(),
        "ofi_momentum_legacy"
    );
    assert_eq!(
        live_names[1].try_get_by_index::<String>(0).unwrap(),
        "ofi_momentum"
    );
}

#[actix_web::test]
async fn removes_retired_environment_fields_and_local_environment() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.orm()
        .await
        .unwrap()
        .execute_unprepared(
            "CREATE TABLE environments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_mt5 INTEGER NOT NULL DEFAULT 0,
                server TEXT NOT NULL DEFAULT '',
                login TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )",
        )
        .await
        .unwrap();
    db.orm()
        .await
        .unwrap()
        .execute_unprepared(
            "INSERT INTO environments(name,created_at)
             VALUES ('local',''),('idk','')",
        )
        .await
        .unwrap();
    db.orm()
        .await
        .unwrap()
        .execute_unprepared(
            "CREATE TABLE mt5_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                login TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                server TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )",
        )
        .await
        .unwrap();

    db.initialize().await.unwrap();

    for column in ["is_mt5", "server", "login", "password"] {
        let row = db
            .orm()
            .await
            .unwrap()
            .query_one(Statement::from_sql_and_values(
                DatabaseBackend::Sqlite,
                "SELECT 1 FROM pragma_table_info('environments') WHERE name=?",
                [column.into()],
            ))
            .await
            .unwrap();
        assert!(row.is_none(), "{column} should have been removed");
    }
    let environments = db.environments().await.unwrap();
    assert_eq!(environments.len(), 1);
    assert_eq!(environments[0].name, "idk");
    let environment_id_column = db
        .orm()
        .await
        .unwrap()
        .query_one(Statement::from_string(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT 1 FROM pragma_table_info('mt5_accounts') ",
                "WHERE name='environment_id'"
            ),
        ))
        .await
        .unwrap();
    assert!(environment_id_column.is_some());
}

#[actix_web::test]
async fn saves_backtest_trades_and_montecarlo() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let mut report = serde_json::Map::new();
    for field in [
        "first_ts",
        "last_ts",
        "symbol",
        "instrument",
        "max_drawdown_peak_date",
        "max_drawdown_trough_date",
        "max_intraday_drawdown_date",
        "max_daily_loss_date",
    ] {
        report.insert(field.into(), Value::String("2026-01-01 00:00".into()));
    }
    for field in [
        "total_days",
        "initial_bal",
        "final_bal",
        "net_growth",
        "max_drawdown",
        "num_trades",
        "avg_drawdown",
        "sharpe",
        "total_win",
        "total_loss",
        "win_rate",
        "win_count",
        "profit_factor",
        "expectancy",
        "max_lose_streak",
        "avg_size",
        "min_size",
        "max_size",
        "avg_weekly",
        "avg_monthly",
        "avg_weekly_pct",
        "avg_monthly_pct",
        "max_drawdown_dollars",
        "avg_drawdown_dollars",
        "avg_drawdown_time_days",
        "avg_annual",
        "avg_annual_pct",
        "annualised_std",
        "skew",
        "lower_tail",
        "upper_tail",
        "max_intraday_drawdown",
        "max_intraday_drawdown_dollars",
        "avg_intraday_drawdown",
        "avg_intraday_drawdown_dollars",
        "max_daily_loss",
        "avg_daily_loss",
    ] {
        report.insert(field.into(), Value::from(0.0));
    }
    report.insert(
        "montecarlo".into(),
        json!({
            "initialBalance": 1000.0,
            "sims": 1,
            "p5": 1001.0,
            "p25": 1001.0,
            "p50": 1001.0,
            "p75": 1001.0,
            "p95": 1001.0,
            "pProfit": 1.0,
            "pRuin": 0.0,
            "ddP5": 0.0,
            "ddP25": 0.0,
            "ddP50": 0.0,
            "ddP75": 0.0,
            "ddP95": 0.0,
            "stepValues": [1],
            "paths": [[1001.0]],
        }),
    );
    let trade = crate::backtest::Trade {
        strategy: String::new(),
        side: crate::backtest::Side::Long,
        entry_timestamp: 1_767_225_600,
        exit_timestamp: 1_767_225_660,
        entry_price: 100.0,
        exit_price: 101.0,
        pnl: 1.0,
        quantity: 1.0,
        entry_raw: 100.0,
        exit_raw: 101.0,
    };
    let id = db
        .save_backtest("TEST", None, &Value::Object(report), &[trade], None)
        .await
        .unwrap();
    assert_eq!(db.trades_binary(id, false).await.unwrap().len(), 33);
    assert!(db.montecarlo_binary(id).await.unwrap().is_some());
}

#[actix_web::test]
async fn seaorm_round_trips_environment_and_march_records() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();

    let environment = db
        .create_environment(&CreateEnvironment {
            name: "sandbox".into(),
        })
        .await
        .unwrap();
    let environment_account = db
        .create_environment_mt5_account(
            environment,
            &EnvironmentMt5AccountInput {
                server: "broker-demo".into(),
                login: "456".into(),
                password: "secret".into(),
                account_type: "mt5".into(),
            },
        )
        .await
        .unwrap();
    let environment_accounts = db
        .environment_mt5_accounts(environment)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(environment_accounts.len(), 1);
    assert_eq!(environment_accounts[0].id, environment_account);
    assert_eq!(environment_accounts[0].server, "broker-demo");
    assert_eq!(environment_accounts[0].login, "456");
    assert_eq!(environment_accounts[0].balance, None);
    assert_eq!(environment_accounts[0].equity, None);
    assert_eq!(
        db.record_mt5_heartbeat("456", "broker-demo", 25_000.0, 25_125.5, "USD")
            .await
            .unwrap(),
        Some(environment_account)
    );
    let environment_accounts = db
        .environment_mt5_accounts(environment)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(environment_accounts[0].balance, Some(25_000.0));
    assert_eq!(environment_accounts[0].equity, Some(25_125.5));
    let environment_strategy = db
        .create_environment_account_strategy(
            environment,
            environment_account,
            &AccountStrategyInput {
                strategy: "nq_ofi".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    let environment_strategies = db
        .environment_account_strategies(environment, environment_account)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(environment_strategies.len(), 1);
    assert_eq!(environment_strategies[0].id, environment_strategy);
    assert!(environment_strategies[0].active);
    assert!(matches!(
        db.create_environment_account_strategy(
            environment,
            environment_account,
            &AccountStrategyInput {
                strategy: "nq_ofi".into(),
                symbol: "USTEC".into(),
            },
        )
        .await,
        Err(ApiError::Conflict(_))
    ));
    let environment_trade = db
        .create_environment_trade(environment, "nq_ofi", "2026-07-15 10:00", 20_000.0)
        .await
        .unwrap();
    let environment_entry_command = db
        .enqueue_mt5_command(
            environment_account,
            "nq_ofi",
            "long",
            "USTEC",
            0.1,
            -1,
            -1,
            environment_trade,
            -1,
        )
        .await
        .unwrap();
    db.claim_mt5_command(environment_account).await.unwrap();
    assert!(
        db.complete_mt5_command(
            environment_entry_command,
            "456",
            true,
            700,
            20_001.0,
            1.0,
            0.0,
            0.0,
            "2026-07-15 10:01",
            "",
        )
        .await
        .unwrap()
    );
    assert_eq!(
        db.close_environment_trade(
            environment,
            environment_account,
            "nq_ofi",
            "2026-07-15 11:00",
            20_100.0,
        )
        .await
        .unwrap(),
        Some(environment_trade)
    );
    let environment_exit_command = db
        .enqueue_mt5_command(
            environment_account,
            "nq_ofi",
            "close",
            "USTEC",
            0.1,
            -1,
            -1,
            -1,
            environment_trade,
        )
        .await
        .unwrap();
    db.claim_mt5_command(environment_account).await.unwrap();
    assert!(
        db.complete_mt5_command(
            environment_exit_command,
            "456",
            true,
            701,
            0.0,
            0.0,
            20_101.0,
            125.5,
            "2026-07-15 11:01",
            "",
        )
        .await
        .unwrap()
    );
    let saved_environment_trade = db.environment_trades(environment).await.unwrap().remove(0);
    assert_eq!(saved_environment_trade.environment_id, environment);
    assert_eq!(saved_environment_trade.strategy_name, "nq_ofi");
    assert_eq!(
        saved_environment_trade.bookmap_entry_time,
        "2026-07-15 10:00"
    );
    assert_eq!(
        saved_environment_trade.bookmap_exit_time,
        "2026-07-15 11:00"
    );
    assert_eq!(saved_environment_trade.bookmap_entry_price, 20_000.0);
    assert_eq!(saved_environment_trade.bookmap_exit_price, 20_100.0);
    assert_eq!(saved_environment_trade.mt5_entry_time, "2026-07-15 10:01");
    assert_eq!(saved_environment_trade.mt5_exit_time, "2026-07-15 11:01");
    assert_eq!(saved_environment_trade.mt5_entry_price, 20_001.0);
    assert_eq!(saved_environment_trade.mt5_exit_price, 20_101.0);
    assert_eq!(saved_environment_trade.mt5pnl, 125.5);
    assert!(
        db.delete_environment_account_strategy(
            environment,
            environment_account,
            environment_strategy,
        )
        .await
        .unwrap()
    );
    assert!(
        db.environment_account_strategies(environment, environment_account)
            .await
            .unwrap()
            .unwrap()
            .is_empty()
    );

    let account = db
        .add_account(&Mt5AccountInput {
            name: "demo".into(),
            login: "123".into(),
            server: "test".into(),
        })
        .await
        .unwrap();
    assert!(
        db.accounts()
            .await
            .unwrap()
            .iter()
            .any(|saved| saved.id == account)
    );
    let account_strategy = db
        .add_account_strategy(
            account,
            &AccountStrategyInput {
                strategy: "nq_ofi".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    db.set_account_strategy_active(account_strategy, true)
        .await
        .unwrap();
    let command_id = db
        .enqueue_mt5_command(account, "nq_ofi", "long", "USTEC", 0.1, -1, -1, -1, -1)
        .await
        .unwrap();
    let heartbeat_account = db
        .record_mt5_heartbeat("123", "test", 10_000.0, 10_001.0, "USD")
        .await
        .unwrap();
    assert_eq!(heartbeat_account, Some(account));
    let (first_claim, second_claim) =
        futures_util::future::join(db.claim_mt5_command(account), db.claim_mt5_command(account))
            .await;
    let mut claims = [first_claim.unwrap(), second_claim.unwrap()];
    assert_eq!(claims.iter().filter(|command| command.is_some()).count(), 1);
    let command = claims
        .iter_mut()
        .find_map(Option::take)
        .expect("one poller should claim the command");
    assert_eq!(command.id, command_id);
    assert_eq!(command.action, "long");
    assert!(
        db.complete_mt5_command(
            command_id,
            "123",
            true,
            456,
            20_001.0,
            1.0,
            0.0,
            0.0,
            "2026-07-15 10:01",
            "",
        )
        .await
        .unwrap()
    );
    let statuses = db.mt5_account_statuses().await.unwrap();
    assert_eq!(
        statuses
            .iter()
            .find(|status| status.account_id == account)
            .unwrap()
            .status,
        "ready"
    );

    db.replace_mt5_positions(
        "123",
        "test",
        &[Mt5PositionInput {
            ticket: 456,
            position_type: "long".into(),
            symbol: "USTEC".into(),
            volume: 0.1,
            profit: 12.5,
            open_price: 20_001.0,
            open_time: 1_768_000_000,
            magic: 26_032_026,
        }],
    )
    .await
    .unwrap();
    let positions = db.mt5_positions().await.unwrap();
    assert_eq!(positions.len(), 1);
    assert_eq!(positions[0].environment_id, None);
    assert_eq!(positions[0].strategy, "nq_ofi");
    assert_eq!(positions[0].ticket, 456);
    db.replace_mt5_positions(
        "456",
        "broker-demo",
        &[Mt5PositionInput {
            ticket: 789,
            position_type: "long".into(),
            symbol: "USTEC".into(),
            volume: 0.1,
            profit: 7.5,
            open_price: 20_010.0,
            open_time: 1_768_000_100,
            magic: 26_032_026,
        }],
    )
    .await
    .unwrap();
    let environment_position = db
        .mt5_positions()
        .await
        .unwrap()
        .into_iter()
        .find(|position| position.ticket == 789)
        .unwrap();
    assert_eq!(environment_position.environment_id, Some(environment));
    db.delete_account(account).await.unwrap();
    assert!(
        db.accounts()
            .await
            .unwrap()
            .iter()
            .all(|saved| saved.id != account)
    );
    assert!(
        db.delete_environment_mt5_account(environment, environment_account)
            .await
            .unwrap()
    );
    assert!(db.accounts().await.unwrap().is_empty());
}

#[actix_web::test]
async fn mt5_bridge_expires_a_stale_entry_but_preserves_its_close() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let account = db
        .add_account(&Mt5AccountInput {
            name: "demo".into(),
            login: "123".into(),
            server: "test".into(),
        })
        .await
        .unwrap();
    let first = db
        .enqueue_mt5_command(
            account,
            "nq_volatility_breakout",
            "long",
            "USTEC",
            0.01,
            -1,
            -1,
            -1,
            -1,
        )
        .await
        .unwrap();
    let latest = db
        .enqueue_mt5_command(
            account,
            "nq_volatility_breakout",
            "close",
            "USTEC",
            0.01,
            -1,
            -1,
            -1,
            -1,
        )
        .await
        .unwrap();
    db.orm()
        .await
        .unwrap()
        .execute(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "UPDATE mt5_execution_commands SET created_at=datetime('now','-2 minutes') WHERE id=?",
            [first.into()],
        ))
        .await
        .unwrap();

    let claimed = db.claim_mt5_command(account).await.unwrap().unwrap();
    assert_eq!(claimed.id, latest);
    assert_eq!(claimed.action, "close");
    let row = db
        .orm()
        .await
        .unwrap()
        .query_one(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT status FROM mt5_execution_commands WHERE id=?",
            [first.into()],
        ))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(row.try_get_by_index::<String>(0).unwrap(), "superseded");
}

#[actix_web::test]
async fn mt5_bridge_claims_fresh_multi_strategy_commands_in_fifo_order() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let account = db
        .add_account(&Mt5AccountInput {
            name: "demo".into(),
            login: "123".into(),
            server: "test".into(),
        })
        .await
        .unwrap();
    let first = db
        .enqueue_mt5_command(account, "one", "long", "USTEC", 0.01, -1, -1, -1, -1)
        .await
        .unwrap();
    db.enqueue_mt5_command(account, "two", "short", "USTEC", 0.01, -1, -1, -1, -1)
        .await
        .unwrap();
    let claimed = db.claim_mt5_command(account).await.unwrap().unwrap();
    assert_eq!(claimed.id, first);
    assert_eq!(claimed.action, "long");
}

#[actix_web::test]
async fn live_strategy_commands_keep_ticket_ownership_and_partial_volume() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let environment = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    let account = db
        .create_environment_mt5_account(
            environment,
            &EnvironmentMt5AccountInput {
                server: "broker-demo".into(),
                login: "456".into(),
                password: "secret".into(),
                account_type: "mt5".into(),
            },
        )
        .await
        .unwrap();
    let strategy = db
        .create_environment_account_strategy(
            environment,
            account,
            &AccountStrategyInput {
                strategy: "nq_drift_vwap".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();

    db.enqueue_live_open(
        strategy,
        account,
        environment,
        "nq_drift_vwap",
        "primary",
        "long",
        "USTEC",
        0.10,
        "2026-07-27 10:00",
        20_000.0,
    )
    .await
    .unwrap();
    let premature_close = db
        .enqueue_live_close(&LiveCloseRequest {
            account_strategy_id: strategy,
            account_id: account,
            strategy: "nq_drift_vwap",
            position_key: "primary",
            symbol: "USTEC",
            fraction: 1.0,
            timestamp: "2026-07-27 10:00",
            price: 20_000.0,
            exit_reason: "strategy",
        })
        .await
        .unwrap();
    assert_eq!(
        premature_close, None,
        "a close without an acknowledged broker ticket must stay owed"
    );
    let open = db.claim_mt5_command(account).await.unwrap().unwrap();
    assert_eq!(open.target_ticket, -1);
    assert!(open.magic > 26_100_000);
    db.complete_mt5_command(
        open.id,
        "456",
        true,
        900,
        20_000.5,
        0.5,
        0.0,
        0.0,
        "2026-07-27 10:00",
        "",
    )
    .await
    .unwrap();
    let positions = db.strategy_positions(strategy).await.unwrap();
    assert_eq!(positions.len(), 1);
    assert_eq!(positions[0].ticket, 900);
    assert_eq!(positions[0].remaining_volume, 0.10);

    db.enqueue_live_close(&LiveCloseRequest {
        account_strategy_id: strategy,
        account_id: account,
        strategy: "nq_drift_vwap",
        position_key: "primary",
        symbol: "USTEC",
        fraction: 0.5,
        timestamp: "2026-07-27 10:30",
        price: 20_010.0,
        exit_reason: "strategy",
    })
    .await
    .unwrap();
    let partial = db.claim_mt5_command(account).await.unwrap().unwrap();
    assert_eq!(partial.target_ticket, 900);
    assert_eq!(partial.volume, 0.05);
    db.complete_mt5_command(
        partial.id,
        "456",
        true,
        900,
        0.0,
        0.0,
        20_009.5,
        4.5,
        "2026-07-27 10:30",
        "",
    )
    .await
    .unwrap();
    let positions = db.strategy_positions(strategy).await.unwrap();
    assert_eq!(positions[0].status, "open");
    assert_eq!(positions[0].remaining_volume, 0.05);

    db.enqueue_live_close(&LiveCloseRequest {
        account_strategy_id: strategy,
        account_id: account,
        strategy: "nq_drift_vwap",
        position_key: "primary",
        symbol: "USTEC",
        fraction: 1.0,
        timestamp: "2026-07-27 11:00",
        price: 20_020.0,
        exit_reason: "session_end",
    })
    .await
    .unwrap();
    let close = db.claim_mt5_command(account).await.unwrap().unwrap();
    assert_eq!(close.target_ticket, 900);
    assert_eq!(close.volume, 0.05);
    db.complete_mt5_command(
        close.id,
        "456",
        true,
        900,
        0.0,
        0.0,
        20_019.5,
        9.5,
        "2026-07-27 11:00",
        "",
    )
    .await
    .unwrap();
    assert!(db.strategy_positions(strategy).await.unwrap().is_empty());
    let trade = db.environment_trades(environment).await.unwrap().remove(0);
    assert_eq!(trade.mt5pnl, 9.5);
    assert_eq!(trade.mt5_exit_price, 20_019.5);

    // Telemetry: which code path closed the position, and the join key back to
    // the command that filled it. Reconstructing either from timestamps alone is
    // ambiguous once two strategies act on the same minute.
    let live = db.live_trades().await.unwrap().remove(0);
    assert_eq!(live.position_key, "primary");
    assert_eq!(live.exit_reason, "session_end");
    assert!(!live.closed_at.is_empty());

    db.log_live_event(
        strategy,
        "nq_drift_vwap",
        "blocked",
        "primary",
        "2026-07-27 11:00",
        "durable MT5 position is missing",
    )
    .await;
    let events = db.live_events(10).await.unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].kind, "blocked");
    assert_eq!(events[0].strategy, "nq_drift_vwap");
    assert_eq!(events[0].detail, "durable MT5 position is missing");
}

#[actix_web::test]
async fn bridge_protective_close_reconciles_owned_position_and_pending_exit() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let environment = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    let account = db
        .create_environment_mt5_account(
            environment,
            &EnvironmentMt5AccountInput {
                server: "broker-demo".into(),
                login: "456".into(),
                password: "secret".into(),
                account_type: "mt5".into(),
            },
        )
        .await
        .unwrap();
    let strategy = db
        .create_environment_account_strategy(
            environment,
            account,
            &AccountStrategyInput {
                strategy: "ofi_momentum".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    let open_id = db
        .enqueue_live_open(
            strategy,
            account,
            environment,
            "ofi_momentum",
            "tag:1",
            "long",
            "USTEC",
            0.05,
            "2026-08-14 10:00",
            30_000.0,
        )
        .await
        .unwrap();
    db.complete_mt5_command(
        open_id,
        "456",
        true,
        991,
        30_001.0,
        1.0,
        0.0,
        0.0,
        "2026-08-14 10:00",
        "",
    )
    .await
    .unwrap();
    let close_id = db
        .enqueue_live_close(&LiveCloseRequest {
            account_strategy_id: strategy,
            account_id: account,
            strategy: "ofi_momentum",
            position_key: "tag:1",
            symbol: "USTEC",
            fraction: 1.0,
            timestamp: "2026-08-14 10:05",
            price: 29_990.0,
            exit_reason: "strategy",
        })
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        db.mt5_command_status(close_id).await.unwrap().as_deref(),
        Some("pending")
    );

    assert!(
        db.reconcile_protective_close(
            "456",
            991,
            29_985.0,
            -0.80,
            "2026-08-14 10:06",
            "bridge_backend_failsafe",
        )
        .await
        .unwrap()
    );
    assert!(db.strategy_positions(strategy).await.unwrap().is_empty());
    assert_eq!(
        db.mt5_command_status(close_id).await.unwrap().as_deref(),
        Some("superseded")
    );
    let live = db.live_trades().await.unwrap().remove(0);
    assert_eq!(live.mt5_close_price, 29_985.0);
    assert_eq!(live.exit_reason, "strategy");
    let trade = db.environment_trades(environment).await.unwrap().remove(0);
    assert_eq!(trade.mt5pnl, -0.80);
    assert_eq!(trade.mt5_exit_price, 29_985.0);
    let event = db.live_events(1).await.unwrap().remove(0);
    assert_eq!(event.kind, "protective_close");
}

/// The Exness Pro rule is the `idk` environment's, and an environment that runs
/// nothing gets none of it.
///
/// It used to be served for ANY environment that merely existed, which made it
/// a global constant wearing an environment-scoped URL.
#[actix_web::test]
async fn cost_rules_are_scoped_to_an_environment_that_runs_something() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();

    let idk = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    let empty = db
        .create_environment(&CreateEnvironment {
            name: "scratch".into(),
        })
        .await
        .unwrap();
    db.sync_environment_cost_rules().await.unwrap();

    let rules = db.environment_cost_rules(idk).await.unwrap().unwrap();
    assert_eq!(rules.len(), 1, "one rule, the cost model");
    assert_eq!(rules[0].id, "exness_pro");
    assert_eq!(rules[0].name, "Exness Pro");

    assert!(
        db.environment_cost_rules(empty)
            .await
            .unwrap()
            .unwrap()
            .is_empty(),
        "an environment with no strategies runs under no rule"
    );
    assert!(
        db.environment_cost_rules(9_999).await.unwrap().is_none(),
        "a missing environment is a 404, not an empty rule list"
    );
}

/// The stored rows must BE the engine's tables, market for market.
///
/// The defect the old editable `environment_rules` table had was that the screen
/// and the engine were unrelated numbers; storing a copy reintroduces that risk
/// unless the copy is checked against its source.
#[actix_web::test]
async fn stored_cost_rules_match_the_compiled_cost_model() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let idk = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    db.sync_environment_cost_rules().await.unwrap();

    let model = crate::backtest::cost_model();
    let rules = db.environment_cost_rules(idk).await.unwrap().unwrap();
    let stored = &rules[0];
    assert_eq!(stored.detail, model.detail);
    assert_eq!(
        stored.markets.len(),
        model.markets.len(),
        "every market the book trades has a line"
    );
    for line in &model.markets {
        let found = stored
            .markets
            .iter()
            .find(|row| row.market == line.market)
            .unwrap_or_else(|| panic!("{} is missing from the stored rule", line.market));
        assert_eq!(found.spread_bp, line.spread_bp);
        assert_eq!(found.minimum_lots, line.minimum_lots);
    }
}

/// A second sync must REPLACE rather than accumulate, or a market the book has
/// stopped trading would still be rendered as one the engine charges for.
#[actix_web::test]
async fn syncing_twice_replaces_the_stored_rule() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let idk = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    db.sync_environment_cost_rules().await.unwrap();
    let first = db.environment_cost_rules(idk).await.unwrap().unwrap();

    db.orm()
        .await
        .unwrap()
        .execute(Statement::from_string(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO environment_cost_rules",
                "(environment_id,rule_id,name,detail,market,spread_bp,minimum_lots) ",
                "SELECT environment_id,rule_id,name,detail,'retired',9.9,1.0 ",
                "FROM environment_cost_rules LIMIT 1"
            ),
        ))
        .await
        .unwrap();
    db.sync_environment_cost_rules().await.unwrap();

    let second = db.environment_cost_rules(idk).await.unwrap().unwrap();
    assert_eq!(second[0].markets.len(), first[0].markets.len());
    assert!(
        !second[0].markets.iter().any(|row| row.market == "retired"),
        "a stale market must not survive a resync"
    );
}

/// A database written before the table existed must serve the rule on its first
/// request rather than only after a restart.
#[actix_web::test]
async fn a_database_with_no_stored_rule_heals_itself_on_read() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let idk = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    // No `sync_environment_cost_rules` here: this is the pre-migration state.
    let rules = db.environment_cost_rules(idk).await.unwrap().unwrap();
    assert_eq!(rules.len(), 1);
    assert_eq!(rules[0].id, "exness_pro");
}

/// The old editable rules table goes on the way through, along with whatever
/// number was last typed into it.
#[actix_web::test]
async fn the_legacy_editable_rules_table_is_dropped() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("app.db");
    let db = Database::new(path.clone());
    db.initialize().await.unwrap();
    let environment = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    // Recreate the retired table exactly as an old database carried it.
    let connection = db.orm().await.unwrap();
    connection
        .execute_unprepared(concat!(
            "CREATE TABLE environment_rules (",
            "id INTEGER PRIMARY KEY AUTOINCREMENT,",
            "environment_id INTEGER NOT NULL REFERENCES environments(id) ON DELETE CASCADE,",
            "rule_type TEXT NOT NULL CHECK(rule_type IN ('spread','slippage','commission')),",
            "value REAL NOT NULL CHECK(value >= 0), created_at TEXT NOT NULL DEFAULT '',",
            "UNIQUE(environment_id, rule_type))"
        ))
        .await
        .unwrap();
    connection
        .execute(Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "INSERT INTO environment_rules(environment_id,rule_type,value) VALUES (?,'spread',0.2)",
            [environment.into()],
        ))
        .await
        .unwrap();

    db.initialize().await.unwrap();

    assert!(
        connection
            .query_one(Statement::from_string(
                DatabaseBackend::Sqlite,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='environment_rules'",
            ))
            .await
            .unwrap()
            .is_none(),
        "the editable rules table must not survive initialisation"
    );
}

/// The two orders a fill can land in, both seen live. The bridge pushes its
/// position snapshot and the command result separately; `usdjpy_rvol` was
/// blocked for one second on 2026-09-25 because its own new ticket reached
/// the snapshot while the durable row was still `pending_open`.
#[actix_web::test]
async fn ownership_check_forgives_a_fill_that_is_still_landing() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    let environment = db
        .create_environment(&CreateEnvironment { name: "idk".into() })
        .await
        .unwrap();
    let account = db
        .create_environment_mt5_account(
            environment,
            &EnvironmentMt5AccountInput {
                server: "broker-demo".into(),
                login: "456".into(),
                password: "secret".into(),
                account_type: "mt5".into(),
            },
        )
        .await
        .unwrap();
    let strategy = db
        .create_environment_account_strategy(
            environment,
            account,
            &AccountStrategyInput {
                strategy: "ofi_momentum".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    let sql = |text: &'static str, values: Vec<sea_orm::Value>| {
        let db = &db;
        async move {
            db.orm()
                .await
                .unwrap()
                .execute(Statement::from_sql_and_values(
                    DatabaseBackend::Sqlite,
                    text,
                    values,
                ))
                .await
                .unwrap();
        }
    };
    let open_id = db
        .enqueue_live_open(
            strategy,
            account,
            environment,
            "ofi_momentum",
            "primary",
            "long",
            "USTEC",
            0.05,
            "2026-08-14 10:00",
            30_000.0,
        )
        .await
        .unwrap();

    // Snapshot first: our magic on a ticket no row knows yet.
    sql(
        concat!(
            "INSERT INTO mt5_bridge_positions",
            "(account_id,ticket,position_type,symbol,volume,profit,open_price,open_time,magic) ",
            "VALUES (?,991,'long','USTEC',0.05,0,30001,0,?)",
        ),
        vec![
            account.into(),
            super::live_runtime::strategy_magic(strategy).into(),
        ],
    )
    .await;
    assert!(!db.strategy_position_mismatch(strategy).await.unwrap());
    // A `pending_open` that never resolves stops covering for the orphan.
    sql(
        "UPDATE mt5_strategy_positions SET updated_at=datetime('now','-2 minutes') WHERE account_strategy_id=?",
        vec![strategy.into()],
    )
    .await;
    assert!(db.strategy_position_mismatch(strategy).await.unwrap());

    // Result first: the row turns `open` before a snapshot carries the ticket.
    sql("DELETE FROM mt5_bridge_positions", vec![]).await;
    db.complete_mt5_command(
        open_id,
        "456",
        true,
        991,
        30_001.0,
        1.0,
        0.0,
        0.0,
        "2026-08-14 10:00",
        "",
    )
    .await
    .unwrap();
    assert!(!db.strategy_position_mismatch(strategy).await.unwrap());
    // Still missing after the grace: that is a real disagreement.
    sql(
        "UPDATE mt5_strategy_positions SET updated_at=datetime('now','-10 seconds') WHERE account_strategy_id=?",
        vec![strategy.into()],
    )
    .await;
    assert!(db.strategy_position_mismatch(strategy).await.unwrap());
}
