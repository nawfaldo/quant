pub mod night_drift;
pub mod noise_momentum;

use serde::Serialize;

/// Market data a strategy wants the backtest loader to use.  Strategies keep
/// this close to their implementation rather than inheriting whatever source
/// the chart happens to display.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PreferredData {
    Ohlcv,
    Bookmap,
    Combined,
}

/// Returns the preferred source for an IDK strategy's historical backtest
/// bars. Existing strategies deliberately stay on the established OHLCV data.
pub fn preferred_data(strategy: &str) -> Option<PreferredData> {
    match strategy {
        "Night Drift" | "Noise Momentum" => Some(PreferredData::Ohlcv),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn existing_strategies_explicitly_prefer_ohlcv() {
        assert_eq!(preferred_data("Night Drift"), Some(PreferredData::Ohlcv));
        assert_eq!(preferred_data("Noise Momentum"), Some(PreferredData::Ohlcv));
    }
}
