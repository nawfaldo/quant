use crate::error::ApiError;
use serde::Deserialize;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunRequest {
    pub environment_id: Option<String>,
    pub strategy: String,
    pub symbol: String,
    pub instrument: String,
    pub initial_balance: String,
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
}
