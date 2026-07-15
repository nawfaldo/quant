use crate::{
    backtest::{LiveSignal, format_ts},
    database::Database,
    error::ApiError,
    live::state::MarchDecision,
};

/// Turns strategy transitions into durable, per-account commands. MT5 terminals
/// pull these commands through the execution EA; this process never embeds
/// strategy logic in the terminal.
#[derive(Clone, Default)]
pub struct ExecutionClient;

impl ExecutionClient {
    pub fn from_env() -> Self {
        Self
    }

    pub async fn publish(
        &self,
        db: &Database,
        strategy: &str,
        timestamp: &str,
        price: f64,
        decision: MarchDecision,
    ) -> Result<(), ApiError> {
        if decision.signal == decision.previous {
            return Ok(());
        }

        let closed_trade_id = if matches!(decision.previous, LiveSignal::Long | LiveSignal::Short) {
            db.log_live_trade_close(strategy, price, timestamp)
                .await?
                .unwrap_or(-1)
        } else {
            -1
        };
        let trade_id = if matches!(decision.signal, LiveSignal::Long | LiveSignal::Short) {
            db.log_live_trade_open(
                strategy,
                decision.signal.as_str(),
                decision.volume,
                price,
                timestamp,
            )
            .await?
        } else {
            -1
        };

        if !matches!(
            decision.signal,
            LiveSignal::Long | LiveSignal::Short | LiveSignal::Close
        ) {
            return Ok(());
        }

        let action = decision.signal.as_str();
        for target in db.execution_targets(strategy).await? {
            if target.symbol.trim().is_empty() || target.symbol.contains('|') {
                tracing::warn!(
                    account_id = target.account_id,
                    symbol = target.symbol,
                    "skipping MT5 command with invalid symbol"
                );
                continue;
            }
            let command_id = db
                .enqueue_mt5_command(
                    target.account_id,
                    strategy,
                    action,
                    &target.symbol,
                    decision.volume,
                    trade_id,
                    closed_trade_id,
                )
                .await?;
            tracing::info!(
                command_id,
                account_id = target.account_id,
                action,
                "queued MT5 command"
            );
        }
        Ok(())
    }

    pub async fn publish_unix(
        &self,
        db: &Database,
        strategy: &str,
        timestamp: i64,
        price: f64,
        decision: MarchDecision,
    ) -> Result<(), ApiError> {
        self.publish(db, strategy, &format_ts(timestamp), price, decision)
            .await
    }
}
