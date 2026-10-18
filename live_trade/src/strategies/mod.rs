pub mod cfd;
pub mod exness;
pub mod fundednext;

use exness::PreferredData;
use cfd::Sleeve;
use exness::oct_12_2026;
use serde::Serialize;

#[derive(Clone, Copy, Serialize)]
pub struct StrategyDefinition {
    pub id: &'static str,
    pub name: &'static str,
    pub preferred_data: PreferredData,
}

/// The registered strategies: the book itself, then every sleeve of it.
///
/// Built from `BOOK` rather than transcribed, because the failure this guards
/// against is a name listed here that `build_strategy` cannot construct: that
/// makes the strategy selectable in the UI and then fails the run with "unknown
/// strategy". Deriving the list from the same enum the factory matches on means
/// the two cannot drift apart.
///
/// NOT SCOPED TO ANYTHING. This used to be looked up by environment name, and
/// exactly one environment ever answered; the registry is the book.
pub fn registered() -> Vec<StrategyDefinition> {
    // THE BOOK ITSELF, FIRST, and it is not a twenty-third sleeve. Picking it
    // runs all twenty-two on one balance -- `book_request` hands it to the
    // combined path -- so it exists here only to save a person ticking
    // twenty-two boxes to ask for the one selection the book actually is.
    //
    // `preferred_data` is nominal for it: a combined run resolves a source per
    // sleeve and never reads this field.
    let book = StrategyDefinition {
        id: oct_12_2026::BOOK_ID,
        name: oct_12_2026::BOOK_NAME,
        preferred_data: PreferredData::Ohlcv,
    };
    // The FundedNext book, offered for a BACKTEST only: as a whole book under
    // the `fundednext` model, never as sleeves, so none of it can become a live
    // row (`registered_sleeves` filters book entries out).
    let fundednext = StrategyDefinition {
        id: fundednext::oct_18_2026::BOOK_ID,
        name: fundednext::oct_18_2026::BOOK_NAME,
        preferred_data: PreferredData::Ohlcv,
    };
    [book, fundednext]
        .into_iter()
        .chain(
            oct_12_2026::registry()
                .iter()
                .map(|sleeve| StrategyDefinition {
                    id: sleeve.id(),
                    name: sleeve.display(),
                    preferred_data: exness::preferred_data(sleeve.display())
                        .unwrap_or(PreferredData::Ohlcv),
                }),
        )
        .collect()
}

/// The market a strategy trades, lower-cased to match `RunRequest::symbol`.
///
/// A combined run feeds each strategy only its own market's bars, so this is
/// what keeps a JP225 sleeve from being stepped with NQ prices. Mirrors
/// `live::portfolio::strategy_symbol`, which does the same job for the live
/// runtime; both must agree or a backtest and its live twin trade differently.
///
/// AN UNREGISTERED NAME FALLS THROUGH TO "nq". That fall-through is what once
/// routed `BTC Donchian` to the NQ feed and lost 800 dollars at an 88% drawdown
/// inside a combined run while its standalone backtest was fine, so
/// `market_routing_tests` asserts every registered sleeve is routed to the
/// market its display name declares -- a new sleeve without a routing arm fails
/// the test rather than silently inheriting NQ prices.
pub fn market_symbol(strategy: &str) -> &'static str {
    Sleeve::from_display(strategy)
        .map(Sleeve::market)
        .unwrap_or("nq")
}

/// Whether every name in `strategies` is a sleeve of THIS book, and there is at
/// least one.
///
/// It decides whether non-NQ markets get the deep warm-up preroll their `calm`
/// gates need, and whether the book's own gross-exposure cap applies. Both are
/// properties of the sealed selection, so a run that mixes in anything else --
/// or that runs nothing at all -- is not the configuration they were chosen for.
///
/// MEMBERSHIP, NOT EMPTINESS. An earlier version asked whether every member of a
/// named list was present; that list was emptied and the predicate became
/// vacuously true for any run, including one with no sleeves in it.
pub fn is_book_run<'a>(strategies: impl Iterator<Item = &'a str>) -> bool {
    let mut seen = false;
    for name in strategies {
        if Sleeve::from_display(name).is_none() {
            return false;
        }
        seen = true;
    }
    seen
}

/// Whether this registry entry is the BOOK rather than one of its sleeves.
///
/// `registered` lists the book alongside its sleeves, and only the sleeves are
/// strategies. The book is a selection, so it has no market, no contract, no
/// spread and no lot floor -- anything that walks the registry asking a sleeve
/// question has to step over it, and a caller that forgets gets `None` from a
/// lookup that has never returned `None` before.
pub fn is_book_entry(name: &str) -> bool {
    name == oct_12_2026::BOOK_NAME
        || name == oct_12_2026::BOOK_ID
        || name == fundednext::oct_18_2026::BOOK_NAME
        || name == fundednext::oct_18_2026::BOOK_ID
}

/// The registry entries that ARE sleeves, which is everything but the book.
pub fn registered_sleeves() -> Vec<StrategyDefinition> {
    registered()
        .into_iter()
        .filter(|entry| !is_book_entry(entry.name))
        .collect()
}

/// Calendar days of history `strategy` must be replayed over before its first
/// traded bar, or `None` for a name the book does not carry.
///
/// ONE SOURCE FOR BOTH RUNTIMES. The backtest and the live warm-up each used to
/// carry a constant: a flat 400 calendar days for every market in a book run,
/// and a flat 100 sessions per live strategy. They disagreed with each other and
/// neither was derived from the sleeve it was warming, so a cell whose deepest
/// window changed silently kept whichever number happened to be written down.
/// `Sleeve::warmup_calendar_days` answers from the sleeve's own parameters and
/// its own market calendar, and both callers now ask it.
pub fn warmup_calendar_days(strategy: &str) -> Option<i64> {
    Sleeve::from_display(strategy)
        .or_else(|| Sleeve::from_id(strategy))
        .map(Sleeve::warmup_calendar_days)
}

/// The markets a backtest priced under `book`'s model can ask for.
pub fn book_markets(book: cfd::Book) -> Vec<&'static str> {
    let members: &[Sleeve] = match book {
        cfd::Book::Exness => oct_12_2026::registry(),
        cfd::Book::FundedNext => &fundednext::oct_18_2026::BOOK,
    };
    let mut markets: Vec<&'static str> = members.iter().map(|sleeve| sleeve.market()).collect();
    markets.sort_unstable();
    markets.dedup();
    markets
}

/// The markets any registered strategy can ask for.
pub fn known_markets() -> Vec<&'static str> {
    let mut markets: Vec<&'static str> = oct_12_2026::registry()
        .iter()
        .map(|sleeve| sleeve.market())
        .collect();
    markets.sort_unstable();
    markets.dedup();
    markets
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategies::exness::oct_12_2026::BOOK;

    /// THE BOOK IS NOT A LIVE ROW, and `create_account_strategy` has to check
    /// membership against `registered_sleeves` rather than `registered` to keep
    /// it that way.
    ///
    /// The symbol check cannot catch it: `required_symbol` returns `None` for
    /// anything the live runtime does not support, and `symbol_matches_strategy`
    /// reads a missing requirement as "nothing to violate" and lets it through.
    /// A row naming the book would then sit active, build nothing, and look
    /// enabled -- which is worse than one that refuses to start.
    #[test]
    fn the_book_is_offered_for_a_backtest_and_never_as_a_live_row() {
        let listed: Vec<&str> = registered().iter().map(|e| e.id).collect();
        assert!(listed.contains(&oct_12_2026::BOOK_ID));

        let live: Vec<&str> = registered_sleeves().iter().map(|e| e.id).collect();
        assert!(!live.contains(&oct_12_2026::BOOK_ID));
        assert_eq!(live.len(), BOOK.len());

        // The permissive arm this guards against, asserted so a change to
        // `symbol_matches_strategy` cannot quietly make the guard redundant --
        // or quietly make it the only one.
        assert!(crate::live::portfolio::required_symbol(oct_12_2026::BOOK_ID).is_none());
        assert!(crate::live::portfolio::symbol_matches_strategy(
            oct_12_2026::BOOK_ID,
            "JP225"
        ));
    }

    #[test]
    fn the_registry_is_the_book_then_its_sleeves() {
        assert_eq!(registered_sleeves().len(), BOOK.len());
        // The book itself rides alongside its sleeves.
        assert_eq!(registered().len(), BOOK.len() + 2);
        let strategies = registered();
        let ids: Vec<&str> = strategies.iter().map(|entry| entry.id).collect();
        let mut expected = vec![
            // The BOOKS first, then the Exness sleeves.
            "exness_combined_07-09-2026",
            "fundednext_combined_18-10-2026",
        ];
        expected.extend(BOOK.into_iter().map(Sleeve::id));
        assert_eq!(ids, expected);
    }

    /// The market a display name declares by its first word.
    ///
    /// An unknown prefix panics rather than defaulting, so adding a strategy for
    /// a market this table does not know fails the test instead of quietly
    /// inheriting whatever `market_symbol` falls through to.
    fn declared_market(name: &str) -> &'static str {
        match name.split_whitespace().next().unwrap_or_default() {
            "NQ" => "nq",
            "USDJPY" => "usdjpy",
            "AUDUSD" => "audusd",
            "JP225" => "jp225",
            "ETHUSD" => "ethusd",
            "GBPJPY" => "gbpjpy",
            "GBPUSD" => "gbpusd",
            "UKOIL" => "ukoil",
            "EURJPY" => "eurjpy",
            "USTEC" => "ustec",
            "US500" => "us500",
            "DE40" => "de40",
            "UK100" => "uk100",
            "USOIL" => "usoil",
            "BTC" => "btc",
            other => panic!("{other} declares no market; add it here and to market_symbol"),
        }
    }

    /// `market_symbol` still falls through to "nq" for an unregistered name, so
    /// a sleeve added without a routing arm would be stepped with NQ prices --
    /// the exact failure `BTC Donchian` hit, which cost 800 dollars at an 88%
    /// drawdown in a combined run while its standalone backtest looked fine.
    #[test]
    fn every_strategy_is_routed_to_the_market_its_name_declares() {
        for definition in registered_sleeves() {
            assert_eq!(
                market_symbol(definition.name),
                declared_market(definition.name),
                "{} is routed to the wrong feed",
                definition.name
            );
        }
    }

    /// The six markets the loader has to be able to serve. JP225 left with
    /// every one of its sleeves on 2026-09-23.
    ///
    /// FIVE FEWER THAN THE 29-08 BOOK, and two of the departures are the ones to
    /// remember: `xniusd` was the only market with no one-minute table, so
    /// `live_ohlcv_source` still carries a special case for it, and `hk50` was
    /// the only one that stopped printing for an hour inside its own session
    /// ([[hk50-stops-for-lunch-inside-its-session]]). A new market added blindly
    /// inherits neither guard.
    #[test]
    fn the_book_spans_eleven_markets() {
        assert_eq!(
            known_markets(),
            vec![
                "audusd", "btc", "ethusd", "eurjpy", "gbpjpy", "gbpusd", "uk100", "ukoil",
                "usdjpy", "usoil", "ustec",
            ]
        );
    }
}
