//! Runs the 2026-09-07 Exness book through the real backtest engine and prints
//! the result, so it can be compared against
//! `py_sandbox/research/exness_combined_strategies.py`.
//!
//! WHY A BINARY AND NOT A TEST. The parity fixtures in `exness_combined` prove
//! each sleeve reproduces its Python cell bar for bar, but they replay one cell
//! against a frozen bar list. This runs all twenty-two through `run_combined`
//! against the parquet store — the same path `/api/backtests` takes — so what is
//! measured is the whole book on one compounding balance, including the market
//! interleaving, the per-market warm-up and the broker-minimum refusals. Those
//! are exactly the parts a per-cell fixture cannot reach.
//!
//! It takes no database. `RunRequest` carries everything the engine needs, and
//! the cost model is Exness Pro with nothing to configure.
//!
//! ```text
//! cargo run --release --bin exness_book -- 2025-01-01 2026-08-20 500
//! ```
//!
//! THE END DATE IS INCLUSIVE HERE AND EXCLUSIVE IN THE PYTHON, so the canon
//! window ending `CANON_DATA_END = 2026-08-21` is spelled `2026-08-20` above.
//! `load_ohlcv_bars` asks for `timestamp < dateadd('d', 1, to)` while
//! `ef.backtest` breaks on `ts >= hi`. Passing 2026-08-21 hands the book one
//! extra session, which is worth about a trade on each of half the sleeves and
//! made a matching run read 690.00% against Python's 654.27%.
//!
//! READ THE MARK-TO-MARKET DRAWDOWN, not the closed-trade one. On an intraday
//! book the worst moment is usually inside a position rather than after it: a
//! closed-trade replay once read 21% where this engine read 37%.
//!
//! AND IT IS THE ONE FIGURE THAT DOES NOT MATCH THE PYTHON, ON PURPOSE. On the
//! canon window this reads 19.12% against `_canon_book`'s 18.54%, while the
//! trades agree to the cent -- all 3,318 of them, in quantity as well as in
//! P&L -- and the closed drawdown agrees at 18.01%. The gap is entirely in HOW
//! an open position is marked:
//!
//! `replay` marks a position ONLY at stamps where its own symbol printed
//! (`price_at[symbol].get(stamp)`, `continue` on a miss), so at every other
//! instant that position contributes ZERO unrealised -- the book is marked as
//! though it were flat in every market that did not print. This engine sums
//! every open position at its own market's last known price on every instant,
//! which is what an account actually experiences.
//!
//! MEASURED RATHER THAN ASSERTED. Making this engine skip a slot whose market
//! did not print on the instant reproduces 18.53% against Python's 18.54%;
//! coarsening the marks to 30-minute boundaries alone moves it only to 18.96%.
//! So the divergence is the skip rule and not the sampling rate, and the Python
//! is the model that understates.

use live_trade::backtest::{RunRequest, run_combined};
use live_trade::parquet_store::ParquetStore;
use live_trade::strategies::idk::exness_combined::{
    BOOK, CANON_GROSS_CAP, CANON_RISK_SCALE, Sleeve,
};
use serde_json::{Value, json};

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let from = args.first().cloned().unwrap_or("2025-01-01".to_owned());
    let to = args.get(1).cloned().unwrap_or("2026-08-21".to_owned());
    let balance = args.get(2).cloned().unwrap_or("500".to_owned());

    // An optional comma-separated list of sleeve ids, so one market's sleeves
    // can be run without loading the other seven. The full book pulls about
    // eight minutes of QuestDB before it decides anything, which makes it a poor
    // instrument for finding out WHY a number is wrong.
    let only: Vec<String> = args
        .get(3)
        .map(|value| value.split(',').map(str::to_owned).collect())
        .unwrap_or_default();
    let strategies: Vec<String> = BOOK
        .iter()
        .filter(|sleeve| only.is_empty() || only.iter().any(|id| id == sleeve.id()))
        .map(|sleeve| sleeve.display().to_owned())
        .collect();
    anyhow::ensure!(
        strategies.len() >= 2,
        "a combined run needs at least two sleeves; got {strategies:?}"
    );

    // `symbol` names the run's PRIMARY market. A MIXED run splits per market
    // from `market_symbol` and ignores this, but a run whose sleeves all trade
    // one market is pinned to it and `prepare` refuses a mismatch — which is
    // what a filtered run down to a single market produces.
    let mut markets: Vec<&str> = BOOK
        .iter()
        .filter(|sleeve| only.is_empty() || only.iter().any(|id| id == sleeve.id()))
        .map(|sleeve| sleeve.market())
        .collect();
    markets.sort_unstable();
    markets.dedup();
    // A MIXED run splits per market and ignores this, but `prepare` still checks
    // it against `known_markets`, so it has to be a market the book trades --
    // which is why it is read off `markets` rather than hard-coded. It was "nq"
    // until 2026-09-03, when the symbol was barred and the default stopped
    // naming anything the loader serves.
    let symbol = markets[0];

    let request: RunRequest = serde_json::from_value(json!({
        "strategy": strategies[0],
        "symbol": symbol,
        "instrument": "forex",
        "initialBalance": balance,
        "fromDate": from,
        "toDate": to,
    }))?;

    let store = ParquetStore::from_env()?;
    let started = std::time::Instant::now();
    eprintln!(
        "loading {} sleeves, {from}..{to}, ${balance}",
        strategies.len()
    );

    let result = run_combined(&store, &request, &strategies)
        .await
        .map_err(|error| anyhow::anyhow!("{error:?}"))?;

    eprintln!("done in {:.1}s", started.elapsed().as_secs_f64());
    // A per-trade dump, for comparing lot sizes against Python's replay when a
    // book total disagrees while the trade COUNTS match.
    if let Ok(path) = std::env::var("DUMP_TRADES") {
        let rows: Vec<Value> = result
            .trades
            .iter()
            .map(|trade| {
                json!({
                    "s": trade.strategy_name(),
                    "et": trade.entry_timestamp,
                    "qty": trade.quantity,
                    "ep": trade.entry_price,
                    "raw": trade.entry_raw,
                    "xp": trade.exit_price,
                    "xraw": trade.exit_raw,
                    "xt": trade.exit_timestamp,
                    "pnl": trade.pnl,
                })
            })
            .collect();
        std::fs::write(&path, serde_json::to_string(&rows)?)?;
        eprintln!("wrote {} trades to {path}", rows.len());
    }
    print(&result.body, &balance);
    Ok(())
}

fn number(body: &Value, key: &str) -> f64 {
    body.get(key).and_then(Value::as_f64).unwrap_or(f64::NAN)
}

/// The CLOSED-TRADE drawdown, `replay`'s `max_dd_pct`, in per cent.
///
/// Computed here rather than read off the body because the engine reports the
/// marked one and nothing else -- which is the right default, since on an
/// intraday book the worst moment is usually inside a position rather than after
/// it ([[engine-drawdown-is-mark-to-market]]). This exists ONLY so the gap
/// between the two can be compared against the Python, and that gap IS the
/// book's open risk.
///
/// Trades are walked in EXIT order because that is the order the balance
/// actually moves in: a position opened first but closed last pays into the
/// account after one opened later.
///
/// ON THE SHIFTED CLOCK, which is the order `replay` settles in. Python loads
/// JP225 with `SHIFT_HOURS` already added, so its trade log carries shifted
/// stamps and the settlement walk puts a JP225 exit six hours later than real
/// time relative to every other market. The engine records REAL stamps -- a
/// shifted one would be wrong for anything but this comparison -- so the shift
/// is re-applied here and nowhere else. Without it the same trades and the same
/// P&L read 11.53% against Python's 11.37%, purely from settlement order.
fn closed_drawdown(body: &Value, initial: f64) -> f64 {
    let Some(trades) = body.get("trades").and_then(Value::as_array) else {
        return f64::NAN;
    };
    // TIES ARE BROKEN THE WAY `replay` BREAKS THEM. Several trades can close at
    // one stamp, and Python settles them in the order they were OPENED --
    // `pending` is sorted by `(entry_ts, sleeve)` and `open_positions` keeps
    // that order -- so the same trades and the same P&L read 11.41% in exit
    // order alone against 11.37% in Python's.
    let mut settled: Vec<(i64, i64, String, f64)> = trades
        .iter()
        .map(|trade| {
            let sleeve = trade["s"].as_str().and_then(Sleeve::from_display);
            let shift = sleeve.map_or(0, |sleeve| sleeve.shift_hours() * 3_600);
            (
                trade["xt"].as_i64().unwrap_or_default() + shift,
                trade["et"].as_i64().unwrap_or_default() + shift,
                sleeve.map_or(String::new(), |sleeve| sleeve.python_key().to_owned()),
                trade["pnl"].as_f64().unwrap_or_default(),
            )
        })
        .collect();
    settled.sort_by(|left, right| (left.0, left.1, &left.2).cmp(&(right.0, right.1, &right.2)));
    let (mut equity, mut peak, mut worst) = (initial, initial, 0.0f64);
    for (_, _, _, pnl) in settled {
        equity += pnl;
        peak = peak.max(equity);
        if peak > 0.0 {
            worst = worst.max((peak - equity) / peak);
        }
    }
    100.0 * worst
}

fn print(body: &Value, balance: &str) {
    let initial: f64 = balance.parse().unwrap_or(f64::NAN);
    let final_balance = number(body, "final_bal");
    // THE THREE SETTINGS THAT DECIDE WHAT THIS RUN IS, printed rather than
    // assumed. A book read at another risk scale, with the cap on, or dropping
    // orders under the broker minimum is a different strategy, and the reader
    // should not have to go and check which one they got.
    println!();
    println!("SETTINGS");
    println!("  risk scale        {CANON_RISK_SCALE:>12.3}");
    println!(
        "  gross cap         {:>12}",
        CANON_GROSS_CAP.map_or("off".to_owned(), |cap| format!("{cap:.1}x"))
    );
    println!();
    println!("BOOK");
    println!("  final balance     {final_balance:>12.2}");
    println!(
        "  return            {:>12.2}%",
        100.0 * (final_balance / initial - 1.0)
    );
    println!(
        "  max drawdown MTM  {:>12.2}%",
        number(body, "max_drawdown")
    );
    println!(
        "  max drawdown clsd {:>12.2}%   (optimistic)",
        closed_drawdown(body, initial)
    );
    println!(
        "  profit factor     {:>12.3}",
        number(body, "profit_factor")
    );
    let trades = body["trades"].as_array().map(Vec::len).unwrap_or(0);
    println!("  trades            {trades:>12}");
    println!(
        "  net profit        {:>12.2}",
        number(body, "contribution_net")
    );
    println!("  win rate          {:>12.2}%", number(body, "win_rate"));

    // PER-SLEEVE DETAIL IS NOT OPTIONAL. The book total hides the two failures
    // that matter: a book that merely tracks its best member, and a sleeve that
    // earns standalone and INVERTS inside the combination.
    if let Some(rows) = body.get("contribution").and_then(Value::as_array) {
        println!();
        println!("PER SLEEVE");
        println!(
            "  {:<28}{:>10}{:>8}{:>9}{:>12}",
            "sleeve", "pnl", "share", "trades", "own maxDD"
        );
        for row in rows {
            println!(
                "  {:<28}{:>10.2}{:>7.1}%{:>9}{:>11.2}%",
                row["strategy"].as_str().unwrap_or("?"),
                row["pnl"].as_f64().unwrap_or_default(),
                row["share_pct"].as_f64().unwrap_or_default(),
                row["trades"].as_u64().unwrap_or_default(),
                row["max_drawdown"].as_f64().unwrap_or_default(),
            );
        }
    }

    // A sleeve whose orders the balance could not place is not a smaller version
    // of itself: the backtest and a real account are running different
    // strategies, and a high count here says so.
    if let Some(rejected) = body.get("below_broker_minimum").and_then(Value::as_object) {
        let total: u64 = rejected
            .values()
            .map(|count| count.as_u64().unwrap_or_default())
            .sum();
        println!();
        println!("REFUSED BELOW BROKER MINIMUM  {total}");
        for (name, count) in rejected {
            println!("  {name:<28}{:>10}", count.as_u64().unwrap_or_default());
        }
    }

    // THE EXNESS FILL MODEL'S REACH. A fill the broker's minute table does not
    // cover falls back to the vendor open, the constant spread and the idealised
    // exit, so anything under 100% here is that much kinder than the account.
    if let Some(coverage) = body.get("fill_coverage").and_then(Value::as_object) {
        let count = |row: &Value, key: &str| row.get(key).and_then(Value::as_u64).unwrap_or(0);
        let share = |priced: u64, total: u64| {
            if total == 0 {
                "-".to_owned()
            } else {
                format!("{:.1}%", 100.0 * priced as f64 / total as f64)
            }
        };
        let (mut entries, mut entries_priced, mut exits, mut exits_priced) = (0, 0, 0, 0);
        for row in coverage.values() {
            entries += count(row, "entries");
            entries_priced += count(row, "entries_priced");
            exits += count(row, "exits");
            exits_priced += count(row, "exits_priced");
        }
        println!();
        println!(
            "EXNESS LIVE FILLS  entries {}  exits {}",
            share(entries_priced, entries),
            share(exits_priced, exits)
        );
        for (name, row) in coverage {
            let (a, b) = (count(row, "entries_priced"), count(row, "entries"));
            let (c, d) = (count(row, "exits_priced"), count(row, "exits"));
            if a < b || c < d {
                println!("  {name:<28} entries {a}/{b}  exits {c}/{d}");
            }
        }
    }

    // The cap is first-come-first-served, so a sleeve that shows up here was
    // crowded out by whoever was already holding the exposure -- not by its own
    // signals drying up.
    if let Some(refused) = body.get("refused_by_gross_cap").and_then(Value::as_object) {
        let total: u64 = refused
            .values()
            .map(|count| count.as_u64().unwrap_or_default())
            .sum();
        println!();
        println!("REFUSED BY GROSS CAP  {total}");
        for (name, count) in refused {
            println!("  {name:<28}{:>10}", count.as_u64().unwrap_or_default());
        }
    }

    if let Some(monthly) = body.get("equity_monthly").and_then(Value::as_array) {
        let positive = monthly
            .iter()
            .filter(|row| row["pnl"].as_f64().unwrap_or_default() > 0.0)
            .count();
        println!();
        println!("MONTHS  {positive}/{} positive", monthly.len());
    }
}
