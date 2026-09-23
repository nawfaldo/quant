//! The `idk` environment, which is one book and nothing else.
//!
//! Nineteen strategies were deleted on 2026-08-23 and replaced by the book in
//! `exness_combined_07-09-2026/`, which reached twenty-five sleeves on
//! 2026-08-29, rebuilt to twenty-three on 2026-09-04 when NQ was barred as a
//! symbol and a decay screen took seven more, and cut to twenty-two on
//! 2026-09-07 when `hk50:level_confluence` was dropped on data quality.
//!
//! `nq_ofi_momentum.rs` and `nq_drift_vwap.rs` were carried INTO that directory
//! rather than retired with the other seventeen, and then left with the symbol.
//! Every member is now an `exness_families` cell, so the directory holds one
//! engine rather than three.
//!
//! THE DIRECTORY NAME IS NOT A VALID MODULE PATH, which is why `#[path]` is
//! here. The date is part of the name on purpose: the book is a sealed selection
//! made on one day against one data snapshot, and a rebuild is a different book
//! rather than a new version of this one.

use serde::Serialize;

#[path = "exness_combined_07-09-2026/mod.rs"]
pub mod exness_combined;

/// Market data a strategy wants the backtest loader to use. Strategies keep this
/// close to their implementation rather than inheriting whatever source the
/// chart happens to display.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PreferredData {
    Ohlcv,
    /// The HYBRID feed, handed over at the FIRST LEVEL-TWO BAR.
    ///
    /// `exness_combined_strategies.external_trades` splits
    /// `nq:volatility_breakout` at exactly that timestamp: it runs
    /// `ef.backtest` over `lower..min(boundary, upper)` and then
    /// `max(boundary, lower)..upper`, and `backtest` filters BAR BY BAR.
    Combined,
    /// The same hybrid feed, handed over at the FOLLOWING MIDNIGHT.
    ///
    /// `drift_vwap_pullback.backtest` groups its bars `by_day` and skips a day
    /// wholesale with `if day * 86_400 < from_ts: continue`, so the day
    /// CONTAINING the first level-two minute belongs to the native segment in
    /// full and level two starts the next midnight.
    ///
    /// TWO VARIANTS BECAUSE THE TWO PYTHONS DISAGREE, not because the feed
    /// does. Running both sleeves off one stream forces one of them onto the
    /// other's boundary: the exact split cost `nq:drift_vwap` a session, and
    /// the daily split moved `nq:volatility_breakout` from 201 trades to 203.
    CombinedDaily,
    LevelTwo,
    /// Plain OHLCV with a SECOND market's 30-minute closes joined on.
    ///
    /// `ethusd:idio_break` asks whether ETHUSD broke its own channel while BTC
    /// stayed inside its own -- a joint statement about two markets that neither
    /// price series can make alone. A strategy is stepped only with its own
    /// market's bars, so the benchmark rides along in `Bar::benchmark`.
    ///
    /// IT IS ITS OWN SOURCE KEY, not a flag on `Ohlcv`, because the key decides
    /// which stream a strategy is assigned to. ETHUSD carries five sleeves and
    /// only this one wants the join; sharing one stream would either deny it the
    /// benchmark or make the other four pay for building it.
    OhlcvWithBenchmark,
}

/// Returns the preferred source for an IDK strategy's historical backtest bars.
///
/// PLAIN OHLCV FOR EVERY MEMBER SINCE 2026-09-04, and the function is kept
/// rather than folded into a constant because the QUESTION is per sleeve. NQ was
/// barred as a symbol on 2026-09-03 and took the level-two feed with it; the
/// decay screen took `ethusd:idio_break`, the one cell that read a second
/// market. So the four exotic sources below are all unreached, and the loader
/// still knows how to serve them.
///
/// A sleeve that wanted one again would answer here, and nothing else would
/// have to change -- which is why the enum keeps its arms.
pub fn preferred_data(strategy: &str) -> Option<PreferredData> {
    exness_combined::Sleeve::from_display(strategy).map(|_| PreferredData::Ohlcv)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The whole book reads one source, and the test is here so that stops
    /// being true loudly rather than quietly.
    #[test]
    fn every_sleeve_reads_plain_ohlcv() {
        for sleeve in exness_combined::BOOK {
            assert_eq!(
                preferred_data(sleeve.display()),
                Some(PreferredData::Ohlcv),
                "{} must stay on OHLCV",
                sleeve.display()
            );
        }
    }

    /// A name the book does not carry must not resolve, or the API would
    /// advertise a strategy the engine cannot build.
    #[test]
    fn a_retired_strategy_name_is_unknown() {
        assert_eq!(preferred_data("NQ OFI"), None);
        assert_eq!(preferred_data("NQ Drift VWAP"), None);
        assert_eq!(preferred_data("ETHUSD Idio Break"), None);
        assert_eq!(preferred_data("BTC Maroy Ladder"), None);
    }
}
