pub mod idk;

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
}

const IDK_STRATEGIES: &[StrategyDefinition] = &[
    StrategyDefinition {
        id: "night_drift",
        name: "Night Drift",
    },
    StrategyDefinition {
        id: "noise_momentum",
        name: "Noise Momentum",
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
}
