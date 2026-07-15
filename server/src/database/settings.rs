use super::Database;
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement};
use serde_json::{Value, json};

impl Database {
    async fn setting(&self, key: &str, fallback: &str) -> Result<String, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT value FROM settings WHERE key=?",
            [key.to_owned().into()],
        );
        let value = self
            .orm()
            .await?
            .query_one(statement)
            .await?
            .map(|row| row.try_get_by_index::<String>(0))
            .transpose()?;
        Ok(value
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| fallback.to_owned()))
    }

    async fn save_setting(&self, key: &str, value: &str) -> Result<(), ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)",
            [key.to_owned().into(), value.to_owned().into()],
        );
        self.orm().await?.execute(statement).await?;
        Ok(())
    }

    pub async fn app_settings(&self) -> Result<Value, ApiError> {
        Ok(json!({
            "from_date": self.setting("from_date", "2026-01-01").await?,
            "to_date": self.setting("to_date", "2026-04-30").await?,
            "default_timeframe": self.setting("default_timeframe", "5m").await?,
        }))
    }

    pub async fn date_range(&self) -> Result<(String, String), ApiError> {
        Ok((
            self.setting("from_date", "2026-01-01").await?,
            self.setting("to_date", "2026-04-30").await?,
        ))
    }

    pub async fn default_timeframe(&self) -> Result<String, ApiError> {
        self.setting("default_timeframe", "5m").await
    }

    pub async fn save_app_settings(&self, from: &str, to: &str) -> Result<(), ApiError> {
        self.save_setting("from_date", from).await?;
        self.save_setting("to_date", to).await
    }

    pub async fn march_settings(&self) -> Result<Value, ApiError> {
        Ok(json!({
            "symbol": self.setting("march_symbol", "nq").await?,
            "tf": self.setting("march_tf", "1m").await?,
            "from": self.setting("march_from", "2026-06-18").await?,
            "to": self.setting("march_to", "2026-06-25").await?,
            "mode": self.setting("march_mode", "latest").await?,
            "bottomOpen": self.setting("march_bottom_open", "true").await?,
            "layout": self.setting("march_layout", "single").await?,
            "bottomHeight": self.setting("march_bottom_height", "400").await?,
        }))
    }

    pub async fn save_march_settings(&self, values: &Value) -> Result<(), ApiError> {
        let fields = [
            ("symbol", "march_symbol"),
            ("tf", "march_tf"),
            ("from", "march_from"),
            ("to", "march_to"),
            ("mode", "march_mode"),
            ("bottomOpen", "march_bottom_open"),
            ("layout", "march_layout"),
            ("bottomHeight", "march_bottom_height"),
        ];
        for (json_key, database_key) in fields {
            let Some(value) = values.get(json_key) else {
                continue;
            };
            let text = match value {
                Value::String(value) => value.clone(),
                Value::Bool(value) => value.to_string(),
                Value::Number(value) => value.to_string(),
                _ => continue,
            };
            if !text.is_empty() {
                self.save_setting(database_key, &text).await?;
            }
        }
        Ok(())
    }

    pub async fn get_json_setting(&self, key: &str, fallback: Value) -> Result<Value, ApiError> {
        let text = self.setting(key, "").await?;
        if text.is_empty() {
            return Ok(fallback);
        }
        serde_json::from_str(&text).map_err(ApiError::internal)
    }

    pub async fn save_json_setting(&self, key: &str, value: &Value) -> Result<(), ApiError> {
        let text = serde_json::to_string(value).map_err(ApiError::internal)?;
        self.save_setting(key, &text).await
    }
}
