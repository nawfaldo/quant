use crate::{
    database::Database, live::portfolio::PortfolioStatusStore, parquet_store::ParquetStore,
};

/// Everything a request handler or a live runtime shares.
///
/// THE ONLY LIVE RUNTIME IS `live::portfolio`. This used to carry two more --
/// a `MarchStore` holding a `LiveNightDrift` and a `MinuteCycleStore` -- plus an
/// `ExecutionClient` that turned their signal transitions into MT5 commands.
/// All three belonged to strategies that no longer exist, and the book runs
/// through `portfolio`, which enqueues its own commands.
#[derive(Clone)]
pub struct AppState {
    pub db: Database,
    pub store: ParquetStore,
    pub portfolio_status: PortfolioStatusStore,
}

impl AppState {
    pub fn new(db: Database, store: ParquetStore) -> Self {
        Self {
            db,
            store,
            portfolio_status: PortfolioStatusStore::default(),
        }
    }
}
