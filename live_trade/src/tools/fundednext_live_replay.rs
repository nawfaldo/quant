//! Does live trade what the backtest trades? The FundedNext book down the live
//! path (`live::portfolio::replay`) against the live-rules backtest of the same
//! days, trade by trade.
//!
//! ```text
//! cargo run --release --bin fundednext_live_replay -- FIRST LAST [BALANCE] [SERVER_START]
//! ```
//!
//! Both sides size at a constant BALANCE (default 6000) so lot differences are
//! the sleeve's own. Prints every one-sided trade, every entry/exit stamp that
//! differs, and every distance/multiplier that is not bitwise equal.

use live_trade::backtest::{FUNDEDNEXT_MODEL_ID, RunRequest, Side, run_combined};
use live_trade::live::portfolio::replay::{ReplayEvent, replay_fundednext};
use live_trade::parquet_store::ParquetStore;
use live_trade::strategies::cfd::Sleeve;
use live_trade::strategies::fundednext::oct_21_2026::BOOK;
use serde_json::json;

struct LiveTrade {
    enter: ReplayEvent,
    exit: Option<ReplayEvent>,
}

fn label(ts: i64) -> String {
    live_trade::backtest::format_ts(ts)
}

#[actix_web::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let first = args.first().cloned().unwrap_or("2026-10-12".into());
    let last = args.get(1).cloned().unwrap_or("2026-10-16".into());
    let balance: f64 = args.get(2).map_or(Ok(6000.0), |b| b.parse())?;
    let server_start = args.get(3).cloned().unwrap_or(first.clone());
    let store = ParquetStore::from_env()?;

    let events = replay_fundednext(&store, &first, &last, balance, &server_start)
        .await
        .map_err(|e| anyhow::anyhow!("{e:?}"))?;
    let mut live: Vec<LiveTrade> = Vec::new();
    for event in events {
        match event.kind {
            "enter" => live.push(LiveTrade { enter: event, exit: None }),
            _ => {
                if let Some(open) = live
                    .iter_mut()
                    .rev()
                    .find(|t| t.enter.strategy == event.strategy && t.exit.is_none())
                {
                    open.exit = Some(event);
                }
            }
        }
    }

    let strategies: Vec<String> = BOOK.iter().map(|s| s.display().to_owned()).collect();
    let request: RunRequest = serde_json::from_value(json!({
        "model": FUNDEDNEXT_MODEL_ID,
        "strategy": strategies[0],
        "symbol": BOOK[0].market(),
        "instrument": "forex",
        "initialBalance": format!("{balance}"),
        "fromDate": first,
        "toDate": last,
        "constantSizing": true,
        "challengePaths": 0,
    }))?;
    let result = run_combined(&store, &request, &strategies)
        .await
        .map_err(|e| anyhow::anyhow!("{e:?}"))?;

    // THE FIRST DAY IS THE BOUNDARY, NOT THE TEST: the engine resets its
    // strategies at the window's start after a candle the replay has already
    // stepped, so trades the first day's opening candles signal can differ.
    // Live never compares that day (`compare_live_backtest.py` backtests from
    // the activation day and reports from the day after).
    let from_ts = live_trade::backtest::iso_day(&first).map(|d| (d + 1) * 86_400).unwrap_or(0);
    live.retain(|t| t.enter.ts >= from_ts);
    // A position still open when the data ends is not a trade yet: the
    // backtest books none for it either.
    let still_open = live.iter().filter(|t| t.exit.is_none()).count();
    live.retain(|t| t.exit.is_some());
    let bt_trades: Vec<_> = result.trades.iter().filter(|t| t.entry_timestamp >= from_ts).collect();
    let mut used = vec![false; live.len()];
    let (mut matched, mut only_bt, mut exit_diff, mut size_diff) = (0, 0, 0, 0);
    let mut lines = Vec::new();
    for trade in bt_trades.iter().copied() {
        let Some(sleeve) = Sleeve::from_display(trade.strategy_name()) else { continue };
        let id = sleeve.id();
        let long = trade.side == Side::Long;
        let width = sleeve.candle_seconds();
        let found = live.iter().enumerate().find(|(i, t)| {
            !used[*i]
                && t.enter.strategy == id
                && t.enter.long == long
                // The same fill candle, or (an entry the vendor's late bars
                // sent after its candle opened) an order within one candle.
                && ((t.enter.candle - trade.entry_timestamp).abs() < 60
                    || (t.enter.ts - trade.entry_timestamp).abs() < width)
        });
        let Some((index, mine)) = found else {
            only_bt += 1;
            lines.push(format!(
                "BACKTEST ONLY  {id:<44} {} {}",
                if long { "long " } else { "short" },
                label(trade.entry_timestamp)
            ));
            continue;
        };
        used[index] = true;
        matched += 1;
        let exit_ts = mine.exit.as_ref().map(|e| e.ts);
        if exit_ts.is_none_or(|ts| (ts - trade.exit_timestamp).abs() >= 60) {
            exit_diff += 1;
            lines.push(format!(
                "EXIT DIFFERS   {id:<44} entry {}  bt exit {}  live exit {} ({})",
                label(trade.entry_timestamp),
                label(trade.exit_timestamp),
                exit_ts.map_or("open".into(), label),
                mine.exit.as_ref().map_or("-", |e| e.kind),
            ));
        }
        if mine.enter.mult.to_bits() != trade.vol_mult.to_bits()
            || mine.enter.distance.to_bits() != trade.distance.to_bits()
            || (mine.enter.quantity - trade.quantity).abs() > 1e-9
        {
            size_diff += 1;
            lines.push(format!(
                "SIZE DIFFERS   {id:<44} entry {}  mult {} vs {}  dist {} vs {}  lots {} vs {}",
                label(trade.entry_timestamp),
                mine.enter.mult,
                trade.vol_mult,
                mine.enter.distance,
                trade.distance,
                mine.enter.quantity,
                trade.quantity
            ));
        }
    }
    let mut only_live = 0;
    for (index, trade) in live.iter().enumerate() {
        if !used[index] {
            only_live += 1;
            lines.push(format!(
                "LIVE ONLY      {:<44} {} {}",
                trade.enter.strategy,
                if trade.enter.long { "long " } else { "short" },
                label(trade.enter.ts)
            ));
        }
    }
    lines.sort();
    for line in &lines {
        println!("{line}");
    }
    println!(
        "\n{first}..{last} (server start {server_start}): {} backtest trades, {} live; matched {matched}, \
         backtest-only {only_bt}, live-only {only_live}, exits differ {exit_diff}, sizes differ {size_diff}          ({still_open} live still open at the end, not compared)",
        bt_trades.len(),
        live.len()
    );
    Ok(())
}
