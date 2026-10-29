//! The FundedNext book's challenge Monte Carlo grid -- the research's default
//! report (`fundednext_combined_strategies._grid_table`) -- from the Rust
//! engine, on 2024-2025 (where the book was chosen) and 2026, checked cell for
//! cell against the seal's test in `sandbox/results/fundednext_challenge_tests.json`.
//!
//! ```text
//! cargo run --release --bin fundednext_challenge
//! ```
//!
//! ONE RUN, 2024-01-01..2026-10-12, as the research's pool logs are one run:
//! each window's log is every trade closed before its end, replayed from its
//! start (`backtest::challenge::Book`).

use live_trade::backtest::{FUNDEDNEXT_MODEL_ID, RunRequest, run_combined};
use live_trade::parquet_store::ParquetStore;
use live_trade::strategies::fundednext::oct_21_2026::{BOOK, INITIAL_BALANCE, SEAL};
use serde_json::{Value, json};

const TESTS: &str = "../sandbox/results/fundednext_challenge_tests.json";
const WINDOWS: [(&str, &str, &str); 2] = [
    ("2024-2025", "2024-01-01", "2026-01-01"),
    ("2026", "2026-01-01", "2026-10-13"),
];
const ALIVE: [f64; 4] = [1.0, 0.75, 0.5, 0.25];
const KEEP: [f64; 4] = [1.0, 0.75, 0.5, 0.25];

fn label(alive: f64, keep: f64) -> String {
    if alive == 1.0 && keep == 1.0 {
        "full backtest edge".into()
    } else if keep == 1.0 {
        format!("sym alive {:.0}%", alive * 100.0)
    } else {
        format!("edge kept {:.0}%", keep * 100.0)
    }
}

fn cut(value: &Value) -> String {
    match value.as_f64() {
        Some(v) => format!("{v}"),
        None => "-".into(),
    }
}

fn line(name: &str, c: &Value) -> String {
    let within = &c["within"];
    format!(
        "{name:<21}{:>6.1}%{:>5.1}%{:>5.1}%{:>8}{:>5}{:>6.1}%{:>6.1}%{:>6.1}%{:>10}{:>11}{:>10}",
        c["pass"].as_f64().unwrap_or(f64::NAN),
        c["max"].as_f64().unwrap_or(f64::NAN),
        c["daily"].as_f64().unwrap_or(f64::NAN),
        cut(&c["median_trading_days"]),
        cut(&c["p90_trading_days"]),
        within["20"].as_f64().unwrap_or(f64::NAN),
        within["40"].as_f64().unwrap_or(f64::NAN),
        within["60"].as_f64().unwrap_or(f64::NAN),
        cut(&c["expected_days"]),
        format!("{}/{}", cut(&c["worst_day_50"]), cut(&c["worst_day_95"])),
        format!("{}/{}", cut(&c["deepest_50"]), cut(&c["deepest_95"])),
    )
}

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let store = ParquetStore::from_env()?;
    let strategies: Vec<String> = BOOK.iter().map(|sleeve| sleeve.display().to_owned()).collect();
    let request: RunRequest = serde_json::from_value(json!({
        "model": FUNDEDNEXT_MODEL_ID,
        "strategy": strategies[0],
        "symbol": BOOK[0].market(),
        "instrument": "forex",
        "initialBalance": format!("{INITIAL_BALANCE}"),
        "fromDate": "2024-01-01",
        "toDate": "2026-10-12",
        "dataEnd": "2026-10-13",
        "canonReplay": true,
        "challengeWindows": WINDOWS.iter().map(|(_, lo, hi)| [lo, hi]).collect::<Vec<_>>(),
    }))?;
    let result = run_combined(&store, &request, &strategies).await?;
    let tests: Value = serde_json::from_str(&std::fs::read_to_string(TESTS)?)?;
    let reference = tests
        .as_array()
        .and_then(|runs| runs.iter().rev().find(|run| run["seal"] == SEAL))
        .map(|run| run["grid"].clone());
    let mut clean = true;
    for (index, (name, lo, hi)) in WINDOWS.iter().enumerate() {
        let cells = &result.body["challenge"][index]["cells"];
        println!("\n{name}  ({lo}..{hi}), {} paths a cell", result.body["challenge"][index]["paths"]);
        println!(
            "{:<21}{:>7}{:>6}{:>6}{:>8}{:>5}{:>7}{:>7}{:>7}{:>10}{:>11}{:>10}",
            "", "pass", "max", "daily", "median", "p90", "<=20d", "<=40d", "<=60d", "w/ retry", "worst day", "deepest"
        );
        let mut rows: Vec<(f64, f64)> = vec![(1.0, 1.0)];
        rows.extend(ALIVE[1..].iter().map(|a| (*a, 1.0)));
        rows.extend(KEEP[1..].iter().map(|k| (1.0, *k)));
        for (alive, keep) in rows {
            println!("{}", line(&label(alive, keep), &cells[format!("{alive}/{keep}")]));
        }
        for (q, title) in [(50, "p50 challenge: pass / days / deepest"), (75, "p75 challenge: pass / days / deepest"), (95, "p95 challenge: pass / days / deepest")] {
            println!("
  {title:<48}{}", KEEP.iter().map(|k| format!("{:>22}", format!("edge kept {:.0}%", k * 100.0))).collect::<String>());
            for alive in ALIVE {
                let row: String = KEEP
                    .iter()
                    .map(|keep| {
                        let c = &cells[format!("{alive}/{keep}")];
                        let days = if q == 50 { &c["median_trading_days"] } else { &c[format!("p{q}_trading_days")] };
                        format!("{:>22}", format!("{:.1}% / {}d / ${}", c["pass"].as_f64().unwrap_or(f64::NAN), cut(days), cut(&c[format!("deepest_{q}")])))
                    })
                    .collect();
                println!("  {:<48}{row}", format!("sym alive {:.0}%", alive * 100.0));
            }
        }
        let Some(reference) = reference.as_ref().map(|grid| &grid[*name]) else {
            println!("  no research test of seal {SEAL} to compare against");
            continue;
        };
        let mut differing = 0;
        for alive in ALIVE {
            for keep in KEEP {
                let key = format!("{alive}/{keep}");
                let (mine, theirs) = (&cells[&key], &reference[&key]);
                for field in ["pass", "max", "daily", "stuck", "median_trading_days", "p75_trading_days", "p90_trading_days", "p95_trading_days", "expected_days", "worst_day_50", "worst_day_75", "worst_day_95", "deepest_50", "deepest_75", "deepest_95"] {
                    if mine[field].as_f64() != theirs[field].as_f64() {
                        differing += 1;
                        println!("    DIFF {key} {field}: rust {} research {}", mine[field], theirs[field]);
                    }
                }
            }
        }
        println!("  vs research: {}", if differing == 0 { "IDENTICAL".to_owned() } else { format!("{differing} differing fields") });
        clean &= differing == 0;
    }
    if !clean {
        anyhow::bail!("the challenge grid differs from the research");
    }
    Ok(())
}
