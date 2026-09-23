//! Prints the live rows the `idk` account must hold for the 2026-09-07 book.
//!
//! WHY A TOOL AND NOT A LIST IN THE ACTIVATION SCRIPT. The activation writes
//! `mt5_account_strategies`, whose `symbol` column is what the bridge sends the
//! order to. Transcribing twenty-two ids and twenty-two broker symbols by hand
//! into a second language is exactly the failure the routing module warns
//! about: a sleeve configured with another instrument's symbol is stepped with
//! its own market's bars and sends its real orders somewhere else, which cost
//! 800 dollars at an 88% drawdown while the standalone backtest looked fine.
//!
//! So the registry answers instead. `LIVE_STRATEGIES` is the id list and
//! `required_symbol` resolves each to the broker's own spelling -- the same
//! function `symbol_matches_strategy` validates a stored row against -- and the
//! script that touches the database only copies what this prints.
//!
//! ```text
//! cargo run --release --bin exness_live_book
//! ```

use live_trade::live::portfolio::{LIVE_STRATEGIES, required_symbol};
use live_trade::strategies::idk::exness_combined::{BOOK, BOOK_ID, BOOK_NAME};

fn main() -> anyhow::Result<()> {
    // The live list and the book must be the same twenty-two. They are
    // separate declarations -- one is a `const` the runtime reads, the other is
    // the sealed selection -- and a sleeve added to one and not the other is a
    // sleeve that backtests and never trades, or trades and was never measured.
    anyhow::ensure!(
        LIVE_STRATEGIES.len() == BOOK.len(),
        "live list holds {} strategies against {} in the book",
        LIVE_STRATEGIES.len(),
        BOOK.len()
    );
    let mut rows = Vec::new();
    for id in LIVE_STRATEGIES {
        let sleeve = BOOK
            .into_iter()
            .find(|sleeve| sleeve.id() == *id)
            .ok_or_else(|| anyhow::anyhow!("{id} is live but not a member of the book"))?;
        let symbol = required_symbol(id)
            .ok_or_else(|| anyhow::anyhow!("{id} resolves to no broker symbol"))?;
        rows.push(serde_json::json!({
            "strategy": id,
            "symbol": symbol,
            "market": sleeve.market(),
            "display": sleeve.display(),
        }));
    }
    // EVERY MARKET THAT MUST BE FED, which is not the same list as the markets
    // the book TRADES. A sleeve that reads a second market needs that market's
    // bars too, and the failure is silent -- the cell does not error, it simply
    // never fires, so the live account runs one sleeve fewer than the backtest
    // with nothing to say so.
    //
    // `tools/idk_market_live_data_feeds.py` cross-checks what it feeds against
    // this, for the same reason the activation script copies `strategies`
    // rather than transcribing them: a market-data daemon that quietly stops
    // feeding a canon table is a sleeve deciding on stale prices.
    let mut markets: Vec<&str> = live_trade::strategies::known_markets();
    for (_, benchmark) in live_trade::live::portfolio::benchmark_pairs() {
        if !markets.contains(&benchmark) {
            markets.push(benchmark);
        }
    }
    markets.sort_unstable();
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "book_id": BOOK_ID,
            "book_name": BOOK_NAME,
            "markets": markets,
            "strategies": rows,
        }))?
    );
    Ok(())
}
