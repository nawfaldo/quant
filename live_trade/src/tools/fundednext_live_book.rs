//! Prints the live rows the FundedNext account must hold, the way
//! `exness_live_book` does for the Exness one: every member of the FundedNext
//! book with the broker symbol `required_symbol` gives it, plus the markets the
//! feed daemon must keep current. `tools/go_live_fundednext.py` copies these.
//!
//! ```text
//! cargo run --release --bin fundednext_live_book
//! ```

use live_trade::live::portfolio::required_symbol;
use live_trade::strategies::fundednext::oct_21_2026::{BOOK, BOOK_ID, BOOK_NAME};

fn main() -> anyhow::Result<()> {
    let mut rows = Vec::new();
    let mut markets: Vec<&str> = Vec::new();
    for sleeve in BOOK {
        let symbol = required_symbol(sleeve.id())
            .ok_or_else(|| anyhow::anyhow!("{} resolves to no broker symbol", sleeve.id()))?;
        rows.push(serde_json::json!({
            "strategy": sleeve.id(),
            "symbol": symbol,
            "market": sleeve.market(),
            "display": sleeve.display(),
        }));
        if !markets.contains(&sleeve.market()) {
            markets.push(sleeve.market());
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
