//! Steps ONE sleeve over raw stored minutes and reports what it did.
//!
//! A diagnostic, not a result. `exness_book` runs the whole book through
//! `prepare` and the engine; when that produces a number nobody believes, this
//! narrows down which of the three layers is responsible by removing two of
//! them: it reads the market's own `<symbol>_1m` table, feeds the minutes
//! straight into `ExnessCombined`, and counts the actions that come back.
//!
//! ```text
//! cargo run --release --bin exness_sleeve_probe -- jp225_swing_break 2024-06-01 2026-08-21
//! ```

use live_trade::backtest::data::iso_day;
use live_trade::backtest::types_for_probe::{Action, Bar};
use live_trade::parquet_store::{self, NANOS_PER_SECOND, ParquetStore};
use live_trade::strategies::idk::exness_combined::{ExnessCombined, Sleeve};

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let id = args
        .first()
        .cloned()
        .unwrap_or("jp225_swing_break".to_owned());
    let from = args.get(1).cloned().unwrap_or("2024-06-01".to_owned());
    let to = args.get(2).cloned().unwrap_or("2026-08-21".to_owned());
    let equity: f64 = args
        .get(3)
        .and_then(|value| value.parse().ok())
        .unwrap_or(1e9);

    let sleeve = Sleeve::from_id(&id).ok_or_else(|| anyhow::anyhow!("unknown sleeve {id}"))?;
    let market = sleeve.market();
    let store = ParquetStore::from_env()?;

    // `[from, to)`, matching the half-open range the SQL asked for. Note this
    // one does NOT include the `to` day, unlike the backtest loader.
    let table = format!("{market}_1m");
    let batches = store
        .scan(
            &table,
            &["open", "high", "low", "close", "volume"],
            iso_day(&from).map(|day| day * 86_400 * NANOS_PER_SECOND),
            iso_day(&to).map(|day| day * 86_400 * NANOS_PER_SECOND),
        )
        .map_err(|error| anyhow::anyhow!("{error:?}"))?;

    let mut strategy = ExnessCombined::new(sleeve, 0.01);
    let (mut entries, mut closes) = (0usize, 0usize);
    let (mut first, mut last) = (None::<i64>, None::<i64>);
    let mut minutes = 0usize;
    for batch in &batches {
        let stamps =
            parquet_store::timestamp_column(batch).map_err(|error| anyhow::anyhow!("{error:?}"))?;
        let column = |name: &str| -> anyhow::Result<Vec<f64>> {
            parquet_store::floats(batch, name).map_err(|error| anyhow::anyhow!("{error:?}"))
        };
        let (open, high, low) = (column("open")?, column("high")?, column("low")?);
        let (close, volume) = (column("close")?, column("volume")?);
        minutes += stamps.len();
        for index in 0..stamps.len() {
            let bar = Bar {
                ts: stamps[index] / NANOS_PER_SECOND,
                open: open[index],
                high: high[index],
                low: low[index],
                close: close[index],
                volume: volume[index],
                volume_delta: 0.0,
                depth_events: 0,
                level_two: false,
                order_flow: Default::default(),
                benchmark: None,
            };
            for action in strategy.step(bar, equity) {
                match action {
                    Action::Enter { .. } | Action::EnterPosition { .. } => {
                        entries += 1;
                        first.get_or_insert(bar.ts);
                        last = Some(bar.ts);
                    }
                    Action::Close { .. } | Action::ClosePosition { .. } => closes += 1,
                    Action::Hold => {}
                }
            }
        }
    }
    eprintln!("{table}: {minutes} minutes {from}..{to}");
    println!("{id}: {entries} entries, {closes} closes");
    if let (Some(first), Some(last)) = (first, last) {
        println!(
            "  first {}  last {}",
            live_trade::backtest::format_ts(first),
            live_trade::backtest::format_ts(last)
        );
    }
    Ok(())
}
