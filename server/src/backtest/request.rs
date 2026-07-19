use super::engine::PositionSizing;
use crate::{error::ApiError, strategies::StrategyEnvironment};
use serde::Deserialize;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunRequest {
    #[serde(skip, default)]
    pub(crate) strategy_environment: StrategyEnvironment,
    pub environment_id: Option<String>,
    pub strategy: String,
    pub symbol: String,
    pub instrument: String,
    pub initial_balance: String,
    pub base_lot: Option<String>,
    pub leverage: Option<String>,
    pub exit_strategy: Option<String>,
    pub sizing: Option<String>,
    pub vol_target: Option<String>,
    pub vol_halflife: Option<String>,
    pub vol_max_mult: Option<String>,
    pub vol_min_days: Option<String>,
    pub from_date: String,
    pub to_date: String,
}
impl RunRequest {
    pub fn balance(&self) -> Result<f64, ApiError> {
        self.initial_balance
            .parse()
            .ok()
            .filter(|v: &f64| v.is_finite() && *v > 0.0)
            .ok_or_else(|| ApiError::BadRequest("invalid initial balance".into()))
    }
    pub fn environment_id(&self) -> Result<Option<i64>, ApiError> {
        match self.environment_id.as_deref().filter(|v| !v.is_empty()) {
            Some(value) => value
                .parse()
                .map(Some)
                .map_err(|_| ApiError::BadRequest("invalid environment id".into())),
            None => Ok(None),
        }
    }

    pub(crate) fn position_sizing(&self) -> Result<Option<PositionSizing>, ApiError> {
        // Both registered strategies size internally. Night Drift uses current
        // equity and its volatility stop, so generic lot/leverage inputs must
        // not alter its risk.
        Ok(None)
    }

    pub(crate) fn validate_noise_momentum(&self) -> Result<(), ApiError> {
        let exit = self.exit_strategy.as_deref().unwrap_or("Ladder #2");
        if exit != "Ladder #2" {
            return Err(ApiError::BadRequest(
                "Noise Momentum only supports the Ladder #2 exit".into(),
            ));
        }
        let sizing = self.sizing.as_deref().unwrap_or("Equity");
        if sizing != "Equity" {
            return Err(ApiError::BadRequest(
                "Noise Momentum only supports Equity position sizing".into(),
            ));
        }
        Ok(())
    }
}
