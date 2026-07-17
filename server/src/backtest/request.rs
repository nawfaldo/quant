use super::engine::PositionSizing;
use crate::{error::ApiError, sizing::VolTargetConfig, strategies::StrategyEnvironment};
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
        if self.strategy != "Night Drift" {
            return Ok(None);
        }
        let Some(base_lot) = parse_optional_positive(self.base_lot.as_deref(), "base lot")? else {
            return Ok(None);
        };
        let leverage =
            parse_optional_positive(self.leverage.as_deref(), "leverage")?.unwrap_or(1.0);
        let volatility = if self.sizing.as_deref() == Some("Vol Target") {
            Some(VolTargetConfig {
                target: parse_optional_positive(self.vol_target.as_deref(), "vol target")?
                    .unwrap_or(0.20),
                halflife: parse_optional_positive(self.vol_halflife.as_deref(), "vol halflife")?
                    .unwrap_or(20.0),
                max_multiplier: parse_optional_positive(
                    self.vol_max_mult.as_deref(),
                    "vol max multiplier",
                )?
                .unwrap_or(3.0),
                minimum_days: parse_optional_non_negative(
                    self.vol_min_days.as_deref(),
                    "vol minimum days",
                )?
                .unwrap_or(30.0) as u32,
            })
        } else {
            None
        };

        Ok(Some(PositionSizing {
            base_lot,
            leverage,
            volatility,
        }))
    }
}

fn parse_optional_positive(value: Option<&str>, name: &str) -> Result<Option<f64>, ApiError> {
    parse_optional_number(value, name, |number| number > 0.0)
}

fn parse_optional_non_negative(value: Option<&str>, name: &str) -> Result<Option<f64>, ApiError> {
    parse_optional_number(value, name, |number| number >= 0.0)
}

fn parse_optional_number(
    value: Option<&str>,
    name: &str,
    valid: impl FnOnce(f64) -> bool,
) -> Result<Option<f64>, ApiError> {
    let Some(value) = value.filter(|value| !value.trim().is_empty()) else {
        return Ok(None);
    };
    let number = value
        .parse::<f64>()
        .ok()
        .filter(|number| number.is_finite() && valid(*number))
        .ok_or_else(|| ApiError::BadRequest(format!("invalid {name}")))?;
    Ok(Some(number))
}
