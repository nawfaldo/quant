use super::*;
use serde_json::{Value, json};
#[actix_web::test]
async fn initializes_and_round_trips_settings() {
    let dir = tempfile::tempdir().unwrap();
    let db = Database::new(dir.path().join("app.db"));
    db.initialize().await.unwrap();
    assert_eq!(db.default_timeframe().await.unwrap(), "5m");
    db.save_app_settings("2020-01-01", "2020-02-01")
        .await
        .unwrap();
    assert_eq!(
        db.date_range().await.unwrap(),
        ("2020-01-01".into(), "2020-02-01".into())
    );
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
            is_mt5: false,
            server: String::new(),
            login: String::new(),
            password: String::new(),
        })
        .await
        .unwrap();
    db.create_environment_rule(environment, "spread", 0.2)
        .await
        .unwrap();
    assert_eq!(
        db.environment_costs(Some(environment))
            .await
            .unwrap()
            .spread,
        0.2
    );

    let account = db
        .add_account(&Mt5AccountInput {
            name: "demo".into(),
            login: "123".into(),
            server: "test".into(),
        })
        .await
        .unwrap();
    assert_eq!(db.accounts().await.unwrap()[0].id, account);
    let account_strategy = db
        .add_account_strategy(
            account,
            &AccountStrategyInput {
                strategy: "night_drift".into(),
                symbol: "USTEC".into(),
            },
        )
        .await
        .unwrap();
    db.set_account_strategy_active(account_strategy, true)
        .await
        .unwrap();
    let targets = db.execution_targets("night_drift").await.unwrap();
    assert_eq!(targets.len(), 1);
    assert_eq!(targets[0].symbol, "USTEC");

    let trade_id = db
        .log_live_trade_open("night_drift", "long", 0.1, 20_000.0, "2026-07-15 10:00")
        .await
        .unwrap();
    let command_id = db
        .enqueue_mt5_command(account, "night_drift", "long", "USTEC", 0.1, trade_id, -1)
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
            "2026-07-15 10:01",
            "",
        )
        .await
        .unwrap()
    );
    let trade = db.live_trades().await.unwrap().remove(0);
    assert_eq!(trade.mt5_entry_price, 20_001.0);
    assert_eq!(db.mt5_account_statuses().await.unwrap()[0].status, "ready");

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
        }],
    )
    .await
    .unwrap();
    let positions = db.mt5_positions().await.unwrap();
    assert_eq!(positions.len(), 1);
    assert_eq!(positions[0].strategy, "night_drift");
    assert_eq!(positions[0].ticket, 456);
    db.delete_account(account).await.unwrap();
    assert!(db.accounts().await.unwrap().is_empty());
}
