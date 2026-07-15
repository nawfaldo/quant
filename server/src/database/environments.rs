use super::{CreateEnvironment, Database, Environment, EnvironmentCosts, EnvironmentRule};
use crate::error::ApiError;
use sea_orm::{ConnectionTrait, DatabaseBackend, Statement};

impl Database {
    pub async fn environments(&self) -> Result<Vec<Environment>, ApiError> {
        let rows = self
            .orm()
            .await?
            .query_all(Statement::from_string(
                DatabaseBackend::Sqlite,
                "SELECT id,name,is_mt5,server,login FROM environments ORDER BY id",
            ))
            .await?;
        rows.into_iter()
            .map(|row| {
                Ok(Environment {
                    id: row.try_get_by_index(0)?,
                    name: row.try_get_by_index(1)?,
                    is_mt5: row.try_get_by_index::<i64>(2)? != 0,
                    server: row.try_get_by_index(3)?,
                    login: row.try_get_by_index(4)?,
                })
            })
            .collect()
    }

    pub async fn create_environment(&self, input: &CreateEnvironment) -> Result<i64, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO environments",
                "(name,is_mt5,server,login,password,created_at) ",
                "VALUES (?,?,?,?,?,datetime('now'))",
            ),
            [
                input.name.clone().into(),
                (input.is_mt5 as i32).into(),
                input.server.clone().into(),
                input.login.clone().into(),
                input.password.clone().into(),
            ],
        );
        match self.orm().await?.execute(statement).await {
            Ok(result) => Ok(result.last_insert_id() as i64),
            Err(error) if unique_constraint(&error) => {
                Err(ApiError::Conflict("environment name already exists".into()))
            }
            Err(error) => Err(error.into()),
        }
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

    pub async fn environment_rules(
        &self,
        environment_id: i64,
    ) -> Result<Option<Vec<EnvironmentRule>>, ApiError> {
        if !self.environment_exists(environment_id).await? {
            return Ok(None);
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "SELECT id,rule_type,value FROM environment_rules ",
                "WHERE environment_id=? ORDER BY id",
            ),
            [environment_id.into()],
        );
        let rows = self.orm().await?.query_all(statement).await?;
        let rules = rows
            .into_iter()
            .map(|row| {
                Ok(EnvironmentRule {
                    id: row.try_get_by_index(0)?,
                    rule_type: row.try_get_by_index(1)?,
                    value: row.try_get_by_index(2)?,
                })
            })
            .collect::<Result<_, ApiError>>()?;
        Ok(Some(rules))
    }

    pub async fn environment_costs(
        &self,
        environment_id: Option<i64>,
    ) -> Result<EnvironmentCosts, ApiError> {
        let Some(environment_id) = environment_id else {
            return Ok(EnvironmentCosts::default());
        };
        if !self.environment_exists(environment_id).await? {
            return Err(ApiError::NotFound("environment not found".into()));
        }
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT rule_type,value FROM environment_rules WHERE environment_id=?",
            [environment_id.into()],
        );
        let mut costs = EnvironmentCosts::default();
        for row in self.orm().await?.query_all(statement).await? {
            let kind = row.try_get_by_index::<String>(0)?;
            let value = row.try_get_by_index::<f64>(1)?;
            match kind.as_str() {
                "spread" => costs.spread = value,
                "slippage" => costs.slippage = value,
                "commission" => costs.commission = value,
                _ => {}
            }
        }
        Ok(costs)
    }

    pub async fn create_environment_rule(
        &self,
        environment_id: i64,
        rule_type: &str,
        value: f64,
    ) -> Result<(), ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            concat!(
                "INSERT INTO environment_rules",
                "(environment_id,rule_type,value,created_at) ",
                "VALUES (?,?,?,datetime('now'))",
            ),
            [
                environment_id.into(),
                rule_type.to_owned().into(),
                value.into(),
            ],
        );
        match self.orm().await?.execute(statement).await {
            Ok(_) => Ok(()),
            Err(error) if error.to_string().contains("constraint") => Err(ApiError::Conflict(
                "this environment already has that rule".into(),
            )),
            Err(error) => Err(error.into()),
        }
    }

    pub async fn update_environment_rule(
        &self,
        environment_id: i64,
        rule_type: &str,
        value: f64,
    ) -> Result<(), ApiError> {
        self.execute_environment_rule(
            "UPDATE environment_rules SET value=? WHERE environment_id=? AND rule_type=?",
            value,
            environment_id,
            rule_type,
        )
        .await
    }

    pub async fn delete_environment_rule(
        &self,
        environment_id: i64,
        rule_type: &str,
    ) -> Result<(), ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "DELETE FROM environment_rules WHERE environment_id=? AND rule_type=?",
            [environment_id.into(), rule_type.to_owned().into()],
        );
        self.orm().await?.execute(statement).await?;
        Ok(())
    }

    async fn environment_exists(&self, id: i64) -> Result<bool, ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            "SELECT 1 FROM environments WHERE id=?",
            [id.into()],
        );
        Ok(self.orm().await?.query_one(statement).await?.is_some())
    }

    async fn execute_environment_rule(
        &self,
        sql: &str,
        value: f64,
        environment_id: i64,
        rule_type: &str,
    ) -> Result<(), ApiError> {
        let statement = Statement::from_sql_and_values(
            DatabaseBackend::Sqlite,
            sql,
            [
                value.into(),
                environment_id.into(),
                rule_type.to_owned().into(),
            ],
        );
        self.orm().await?.execute(statement).await?;
        Ok(())
    }
}

fn unique_constraint(error: &sea_orm::DbErr) -> bool {
    let message = error.to_string();
    message.contains("UNIQUE constraint") || message.contains("unique constraint")
}
