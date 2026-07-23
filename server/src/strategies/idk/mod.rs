pub mod hourly_delta_reversal_es;
pub mod hourly_delta_reversal_nq;
pub mod night_drift;
pub mod night_drift_2;
pub mod noise_momentum;
pub mod noise_momentum_2;

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
    OrderFlow,
}

/// Returns the preferred source for an IDK strategy's historical backtest
/// bars. Existing strategies deliberately stay on the established OHLCV data.
pub fn preferred_data(strategy: &str) -> Option<PreferredData> {
    match strategy {
        "Night Drift" => Some(PreferredData::Ohlcv),
        "Night Drift 2" => Some(PreferredData::Ohlcv),
        "Noise Momentum" => Some(PreferredData::Ohlcv),
        "Noise Momentum 2" => Some(PreferredData::Ohlcv),
        "Hourly Delta Reversal NQ" | "Hourly Delta Reversal ES" => Some(PreferredData::OrderFlow),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_momentum_explicitly_prefers_ohlcv() {
        assert_eq!(preferred_data("Night Drift"), Some(PreferredData::Ohlcv));
        assert_eq!(preferred_data("Noise Momentum"), Some(PreferredData::Ohlcv));
    }
}
