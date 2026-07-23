pub mod idk;

use idk::PreferredData;
use serde::Serialize;

#[derive(Clone, Copy, Default)]
pub enum StrategyEnvironment {
    #[default]
    Idk,
}

impl StrategyEnvironment {
    pub fn from_name(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "idk" => Some(Self::Idk),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Serialize)]
pub struct StrategyDefinition {
    pub id: &'static str,
    pub name: &'static str,
    pub preferred_data: PreferredData,
}

const IDK_STRATEGIES: &[StrategyDefinition] = &[
    StrategyDefinition {
        id: "hourly_delta_reversal_nq",
        name: "Hourly Delta Reversal NQ",
        preferred_data: PreferredData::OrderFlow,
    },
    StrategyDefinition {
        id: "hourly_delta_reversal_es",
        name: "Hourly Delta Reversal ES",
        preferred_data: PreferredData::OrderFlow,
    },
    StrategyDefinition {
        id: "night_drift",
        name: "Night Drift",
        preferred_data: PreferredData::Ohlcv,
    },
    StrategyDefinition {
        id: "night_drift_2",
        name: "Night Drift 2",
        preferred_data: PreferredData::Ohlcv,
    },
    StrategyDefinition {
        id: "noise_momentum",
        name: "Noise Momentum",
        preferred_data: PreferredData::Ohlcv,
    },
    StrategyDefinition {
        id: "noise_momentum_2",
        name: "Noise Momentum 2",
        preferred_data: PreferredData::Ohlcv,
    },
];

/// Returns the backtest strategies registered for an environment name.
///
/// Strategy directories are Rust modules, not runtime-discovered folders, so
/// each environment must be registered here before the API can expose it.
pub fn for_environment(name: &str) -> &'static [StrategyDefinition] {
    match StrategyEnvironment::from_name(name) {
        Some(StrategyEnvironment::Idk) => IDK_STRATEGIES,
        None => &[],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn environment_lookup_is_case_insensitive() {
        assert!(matches!(
            StrategyEnvironment::from_name(" IDK "),
            Some(StrategyEnvironment::Idk)
        ));
    }

    #[test]
    fn idk_exposes_registered_strategies() {
        let strategies = for_environment("idk");
        assert_eq!(strategies.len(), 6);
        assert_eq!(strategies[0].id, "hourly_delta_reversal_nq");
        assert_eq!(strategies[0].name, "Hourly Delta Reversal NQ");
        assert_eq!(strategies[1].id, "hourly_delta_reversal_es");
        assert_eq!(strategies[1].name, "Hourly Delta Reversal ES");
        assert_eq!(strategies[2].id, "night_drift");
        assert_eq!(strategies[3].id, "night_drift_2");
        assert_eq!(strategies[4].id, "noise_momentum");
        assert_eq!(strategies[5].id, "noise_momentum_2");
    }
}
