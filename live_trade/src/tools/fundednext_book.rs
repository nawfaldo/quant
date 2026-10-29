//! Runs the FundedNext book through the real backtest engine on the canon
//! windows and diffs every trade against Python's
//! `sandbox/results/fundednext_canon_trades.json`
//! (`py -m sandbox.research._fundednext_parity canon`).
//!
//! The parity fixtures prove each sleeve's SIGNAL on idealised fills; this
//! proves the whole book: FundedNext fills off `fundednext_<broker>_1m`, the
//! commission, the shared $6,000 balance at risk 0.03, the broker-minimum
//! rounding and the market interleaving -- through `run_combined`, the path
//! `/api/combine` takes under the `fundednext` model.
//!
//! ```text
//! cargo run --release --bin fundednext_book                  # all three windows
//! cargo run --release --bin fundednext_book -- "2026 OOS"    # one
//! ```
//!
//! THE WINDOWS ARE PYTHON'S. Its end is exclusive and the engine's `toDate` is
//! inclusive, so 2026-01-01 is spelled 2025-12-31. The 2026 window replays the
//! 2024-2026 trade logs, so each sleeve's admission account has compounded
//! since 2024-01-01 -- `admissionFrom` asks the engine for exactly that.

use std::collections::HashMap;

use live_trade::backtest::{FUNDEDNEXT_MODEL_ID, RunRequest, run_combined};
use live_trade::parquet_store::ParquetStore;
use live_trade::strategies::cfd::Sleeve;
use live_trade::strategies::fundednext::oct_21_2026::{BOOK, INITIAL_BALANCE};
use serde_json::{Value, json};

const CANON: &str = "../sandbox/results/fundednext_canon_trades.json";

/// `(label, from, inclusive to, admission from)`.
const WINDOWS: [(&str, &str, &str, Option<&str>); 3] = [
    ("2024-2025 IS", "2024-01-01", "2025-12-31", None),
    ("2026 OOS", "2026-01-01", "2026-10-12", Some("2024-01-01")),
    ("2024-2026", "2024-01-01", "2026-10-12", None),
];

/// One settled trade, keyed the way Python's log keys it.
#[derive(Clone, Debug)]
struct Row {
    sleeve: String,
    /// Entry and exit on the sleeve's OWN (shifted) clock, as Python records them.
    entry: i64,
    exit: i64,
    side: i64,
    lots: f64,
    pnl: f64,
}

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let wanted: Vec<String> = std::env::args().skip(1).collect();
    // Canon replay by default (Python's own rules); `FN_LIVE_RULES=1` runs the
    // live rules instead, to measure what live costs against the canon.
    let canon_rules = std::env::var("FN_LIVE_RULES").is_err();
    eprintln!("rules: {}", if canon_rules { "canon replay" } else { "LIVE" });
    let canon: Value = serde_json::from_str(&std::fs::read_to_string(CANON)?)?;
    let store = ParquetStore::from_env()?;
    let strategies: Vec<String> = BOOK.iter().map(|sleeve| sleeve.display().to_owned()).collect();
    let mut clean = true;
    for (label, from, to, admission) in WINDOWS {
        if !wanted.is_empty() && !wanted.iter().any(|name| name == label) {
            continue;
        }
        let window = &canon["windows"][label];
        let request: RunRequest = serde_json::from_value(json!({
            "model": FUNDEDNEXT_MODEL_ID,
            "strategy": strategies[0],
            "symbol": BOOK[0].market(),
            "instrument": "forex",
            "initialBalance": format!("{INITIAL_BALANCE}"),
            "fromDate": from,
            "toDate": to,
            "admissionFrom": admission,
            // `CFD_OOS_END`: Python's series stops here, so a position still
            // open then was never closed in its log.
            "dataEnd": "2026-10-13",
            "canonReplay": canon_rules,
        }))?;
        let started = std::time::Instant::now();
        let result = run_combined(&store, &request, &strategies)
            .await
            .map_err(|error| anyhow::anyhow!("{error:?}"))?;
        eprintln!("{label}: ran in {:.1}s", started.elapsed().as_secs_f64());

        let rust: Vec<Row> = result
            .trades
            .iter()
            .map(|trade| {
                let sleeve = Sleeve::from_display(trade.strategy_name()).expect("a book sleeve");
                let shift = sleeve.shift_hours() * 3_600;
                Row {
                    sleeve: sleeve.python_key().to_owned(),
                    entry: trade.entry_timestamp + shift,
                    exit: trade.exit_timestamp + shift,
                    side: match trade.side {
                        live_trade::backtest::Side::Long => 1,
                        live_trade::backtest::Side::Short => -1,
                    },
                    lots: trade.quantity,
                    pnl: trade.pnl,
                }
            })
            .collect();
        let python: Vec<Row> = window["trades"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or_default()
            .iter()
            .map(|row| Row {
                sleeve: row["s"].as_str().unwrap_or_default().to_owned(),
                entry: row["e"].as_i64().unwrap_or_default(),
                exit: row["x"].as_i64().unwrap_or_default(),
                side: row["side"].as_i64().unwrap_or_default(),
                lots: row["lots"].as_f64().unwrap_or(f64::NAN),
                pnl: row["pnl"].as_f64().unwrap_or(f64::NAN),
            })
            .collect();
        clean &= report(label, &result.body, window, &rust, &python);
    }
    if !clean {
        anyhow::bail!("the book differs from the Python canon");
    }
    Ok(())
}

/// Prints the window and returns whether it matches the Python to the cent.
fn report(label: &str, body: &Value, window: &Value, rust: &[Row], python: &[Row]) -> bool {
    let summary = &window["summary"];
    let final_rust = body["final_bal"].as_f64().unwrap_or(f64::NAN);
    let final_python = summary["final"].as_f64().unwrap_or(f64::NAN);
    println!("\n=== {label} ===");
    println!("                    rust        python");
    println!("  trades       {:>10}    {:>10}", rust.len(), python.len());
    println!("  final        {final_rust:>10.2}    {final_python:>10.2}");
    println!(
        "  return %     {:>10.2}    {:>10.2}",
        100.0 * (final_rust / INITIAL_BALANCE - 1.0),
        summary["return_pct"].as_f64().unwrap_or(f64::NAN)
    );
    println!(
        "  MTM dd %     {:>10.2}    {:>10.2}",
        body["max_drawdown"].as_f64().unwrap_or(f64::NAN),
        summary["mtm_dd_pct"].as_f64().unwrap_or(f64::NAN)
    );

    // PER SLEEVE, every time.
    let mut by_sleeve: HashMap<&str, (usize, f64, usize, f64)> = HashMap::new();
    for row in rust {
        let entry = by_sleeve.entry(&row.sleeve).or_default();
        entry.0 += 1;
        entry.1 += row.pnl;
    }
    for row in python {
        let entry = by_sleeve.entry(&row.sleeve).or_default();
        entry.2 += 1;
        entry.3 += row.pnl;
    }
    let mut names: Vec<&str> = by_sleeve.keys().copied().collect();
    names.sort_by(|a, b| by_sleeve[b].3.total_cmp(&by_sleeve[a].3));
    println!("\n  {:40} {:>7} {:>7} {:>11} {:>11}", "sleeve", "rust n", "py n", "rust P&L", "py P&L");
    for name in names {
        let (rn, rp, pn, pp) = by_sleeve[name];
        let flag = if rn != pn || (rp - pp).abs() > 0.005 { "  <--" } else { "" };
        println!("  {name:40} {rn:>7} {pn:>7} {rp:>11.2} {pp:>11.2}{flag}");
    }

    // TRADE BY TRADE: sleeve, entry, side, exit, lots and P&L.
    let key = |row: &Row| (row.sleeve.clone(), row.entry, row.side);
    let mut theirs: HashMap<(String, i64, i64), &Row> = HashMap::new();
    for row in python {
        theirs.insert(key(row), row);
    }
    let mut mine: HashMap<(String, i64, i64), &Row> = HashMap::new();
    for row in rust {
        mine.insert(key(row), row);
    }
    let mut only_rust: Vec<&Row> = rust.iter().filter(|row| !theirs.contains_key(&key(row))).collect();
    let mut only_python: Vec<&Row> = python.iter().filter(|row| !mine.contains_key(&key(row))).collect();
    let mut different: Vec<(&Row, &Row)> = rust
        .iter()
        .filter_map(|row| theirs.get(&key(row)).map(|other| (row, *other)))
        .filter(|(a, b)| {
            a.exit != b.exit || (a.lots - b.lots).abs() > 1e-9 || (a.pnl - b.pnl).abs() > 0.005
        })
        .collect();
    // The first trades whose P&L differs at all, same lots: where a balance
    // drift that later flips a lot at a boundary comes from.
    let mut drift: Vec<(&Row, &Row)> = rust
        .iter()
        .filter_map(|row| theirs.get(&key(row)).map(|other| (row, *other)))
        .filter(|(a, b)| (a.lots - b.lots).abs() <= 1e-9 && (a.pnl - b.pnl).abs() > 1e-6)
        .collect();
    drift.sort_by_key(|(row, _)| row.exit);
    for (a, b) in drift.iter().take(5) {
        println!("    P&L DRIFT {:+.6} {:?}", a.pnl - b.pnl, a);
    }
    only_rust.sort_by_key(|row| row.entry);
    only_python.sort_by_key(|row| row.entry);
    different.sort_by_key(|(row, _)| row.entry);
    println!(
        "\n  only in rust {}   only in python {}   different exit/lots/P&L {}",
        only_rust.len(),
        only_python.len(),
        different.len()
    );
    for row in only_rust.iter().take(10) {
        println!("    RUST ONLY {row:?}");
    }
    for row in only_python.iter().take(10) {
        println!("    PY ONLY   {row:?}");
    }
    for (a, b) in different.iter().take(10) {
        println!("    DIFF rust {a:?}\n         py   {b:?}");
    }
    // `FN_DIFF_OUT=dir`: every difference of this window as JSON, for analysis.
    if let Ok(dir) = std::env::var("FN_DIFF_OUT") {
        let row = |r: &Row| json!({"s": r.sleeve, "e": r.entry, "x": r.exit, "side": r.side, "lots": r.lots, "pnl": r.pnl});
        let dump = json!({
            "only_rust": only_rust.iter().map(|r| row(r)).collect::<Vec<_>>(),
            "only_python": only_python.iter().map(|r| row(r)).collect::<Vec<_>>(),
            "different": different.iter().map(|(a, b)| json!([row(a), row(b)])).collect::<Vec<_>>(),
        });
        let path = std::path::Path::new(&dir).join(format!("{}.json", label.replace(' ', "_")));
        let _ = std::fs::write(path, dump.to_string());
    }
    only_rust.is_empty() && only_python.is_empty() && different.is_empty()
}
