pub mod combine;
pub mod data;
mod drawdown;
pub mod engine;
mod exposure;
pub mod fx;
pub mod monte_carlo;
pub(crate) mod prepare;
mod report;
mod request;
pub(crate) mod types;

pub use combine::combine_realized;
pub use data::{format_ts, iso_day};
pub use engine::costs::{CostModel, cost_model};
pub use engine::{RunResult, execute};
pub use prepare::{PreparedRun, prepare, run, run_combined};
pub use request::RunRequest;
pub use types::{Side, Trade};

/// `Action` and `Bar` for the diagnostic binaries.
///
/// The engine's types are crate-private on purpose -- a caller outside it has no
/// business constructing a fill -- but `exness_sleeve_probe` has to step a
/// strategy by hand to find out which layer a wrong number came from, and that
/// means building bars and reading actions.
pub mod types_for_probe {
    pub use super::types::{Action, Bar, OrderFlowFeatures};
}
