use crate::{
    backtest::{tuning::TuneStore, warm_live_night_drift},
    database::Database,
    error::ApiError,
    execution::ExecutionClient,
    live::state::MarchStore,
    questdb::QuestDb,
};

#[derive(Clone)]
pub struct AppState {
    pub db: Database,
    pub questdb: QuestDb,
    pub executor: ExecutionClient,
    pub march: MarchStore,
    pub tune: TuneStore,
}

impl AppState {
    pub fn new(db: Database, questdb: QuestDb) -> Self {
        Self {
            db,
            questdb,
            executor: ExecutionClient::from_env(),
            march: MarchStore::default(),
            tune: TuneStore::default(),
        }
    }

    pub async fn initialize_march(&self) -> Result<(), ApiError> {
        let active = self
            .db
            .strategies()
            .await?
            .into_iter()
            .any(|strategy| strategy.name == "night_drift" && strategy.active);
        if active {
            let strategy = warm_live_night_drift(&self.questdb).await?;
            self.march.activate(strategy);
        }
        Ok(())
    }
}
