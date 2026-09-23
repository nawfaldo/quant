use super::{
    drawdown::DrawdownTracker,
    engine::{EngineConfig, RunResult},
    report::{montecarlo, report},
};
use serde_json::Value;

/// Merges already-realized trade logs into one equity curve. The Combine
/// command no longer uses this — it runs strategies live in a shared account
/// (see `backtest::run_combined`); this remains the reporting path for FX
/// repricing, which turns a single re-priced trade log back into a report.
pub fn combine_realized(
    sources: &[crate::database::CombineSource],
    initial: f64,
    from: Option<i64>,
    to: Option<i64>,
) -> RunResult {
    let mut trades = Vec::new();
    for source in sources {
        trades.extend(
            source
                .trades
                .iter()
                .filter(|t| {
                    from.is_none_or(|d| t.exit_timestamp.div_euclid(86400) >= d)
                        && to.is_none_or(|d| t.entry_timestamp.div_euclid(86400) <= d)
                })
                .cloned(),
        );
    }
    trades.sort_by_key(|t| t.exit_timestamp);
    let first = trades.first().map(|t| t.entry_timestamp).unwrap_or(0);
    let last = trades.last().map(|t| t.exit_timestamp).unwrap_or(0);
    let mut equity = initial;
    let mut drawdowns = DrawdownTracker::new(initial, first.div_euclid(86_400));
    for trade in &trades {
        let day = trade.exit_timestamp.div_euclid(86400);
        equity += trade.pnl;
        drawdowns.observe(equity, day);
    }
    let symbol = unique_label(sources.iter().map(|s| s.symbol.as_str()), "combined");
    let instrument = unique_label(sources.iter().map(|s| s.instrument.as_str()), "combined");
    let strategy = sources
        .iter()
        .map(|s| format!("{} (#{})", strategy_display(&s.strategy), s.id))
        .collect::<Vec<_>>()
        .join(" + ");
    let cfg = EngineConfig {
        initial,
        symbol: symbol.clone(),
        start_day: first.div_euclid(86400),
        // Report-only, as with the quote rates below: this path re-reads stored
        // trades and opens nothing, so there is no exposure to cap.
        gross_cap: None,
    };
    let mut body = report(&trades, &cfg, first, last, equity, drawdowns.finish());
    body["instrument"] = Value::String(instrument);
    body["strategy"] = Value::String(strategy);
    if sources.len() > 1 {
        let realized = sources
            .iter()
            .map(|source| source.trades.iter().map(|trade| trade.pnl).sum::<f64>())
            .collect::<Vec<_>>();
        let gross = realized.iter().map(|pnl| pnl.abs()).sum::<f64>();
        let mut contribution = sources
            .iter()
            .zip(realized)
            .map(|(source, pnl)| {
                let mut strategy_equity = initial;
                let mut tracker = DrawdownTracker::new(initial, first.div_euclid(86_400));
                let mut strategy_trades = source.trades.iter().collect::<Vec<_>>();
                strategy_trades.sort_by_key(|trade| trade.exit_timestamp);
                for trade in &strategy_trades {
                    strategy_equity += trade.pnl;
                    tracker.observe(strategy_equity, trade.exit_timestamp.div_euclid(86_400));
                }
                let drawdown = tracker.finish();
                serde_json::json!({
                    "strategy": strategy_display(&source.strategy),
                    "pnl": round2(pnl),
                    "trades": source.trades.len(),
                    "avg_drawdown": round2(drawdown.avg_dd),
                    "max_drawdown": round2(drawdown.max_dd),
                    "share_pct": if gross > 0.0 {
                        round2(100.0 * pnl / gross)
                    } else {
                        0.0
                    },
                })
            })
            .collect::<Vec<_>>();
        contribution.sort_by(|a, b| {
            b["pnl"]
                .as_f64()
                .unwrap_or_default()
                .total_cmp(&a["pnl"].as_f64().unwrap_or_default())
        });
        body["contribution"] = serde_json::json!(contribution);
        body["contribution_net"] = serde_json::json!(round2(equity - initial));
    }
    body["montecarlo"] = montecarlo(&trades, initial);
    RunResult { body, trades }
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn unique_label<'a>(items: impl Iterator<Item = &'a str>, fallback: &str) -> String {
    let mut out = Vec::new();
    for item in items {
        if !out.contains(&item) {
            out.push(item);
        }
    }
    if out.is_empty() {
        fallback.into()
    } else {
        out.join(" + ")
    }
}
fn strategy_display(value: &str) -> &str {
    match value {
        "NIGHT_DRIFT" | "EU_OPEN" => "NQ Night Drift",
        "NIGHT_DRIFT_2" => "NQ Night Drift 2",
        "MINUTE_CYCLE" => "NQ Minute Cycle",
        "NOISE_MOMENTUM" => "NQ Noise Momentum",
        "NOISE_MOMENTUM_2" => "NQ Noise Momentum 2",
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backtest::{Side, Trade};
    use crate::database::CombineSource;

    fn trade(strategy: &str, timestamp: i64, pnl: f64) -> Trade {
        Trade {
            strategy: strategy.to_owned(),
            side: Side::Long,
            entry_timestamp: timestamp,
            exit_timestamp: timestamp + 60,
            entry_price: 100.0,
            exit_price: 100.0 + pnl,
            pnl,
            quantity: 1.0,
            entry_raw: 100.0,
            exit_raw: 100.0 + pnl,
        }
    }

    #[test]
    fn realized_combination_reports_each_strategy_contribution() {
        let sources = [
            CombineSource {
                id: 0,
                strategy: "Microprice".into(),
                symbol: "NQ".into(),
                instrument: "forex".into(),
                initial_balance: 10_000.0,
                trades: vec![trade("Microprice", 1_000, 25.0)],
            },
            CombineSource {
                id: 1,
                strategy: "OFI".into(),
                symbol: "NQ".into(),
                instrument: "forex".into(),
                initial_balance: 10_000.0,
                trades: vec![trade("OFI", 2_000, -10.0)],
            },
        ];

        let result = combine_realized(&sources, 10_000.0, None, None);
        let contribution = result.body["contribution"]
            .as_array()
            .expect("contribution rows");
        assert_eq!(contribution.len(), 2);
        assert_eq!(contribution[0]["strategy"], "Microprice");
        assert_eq!(contribution[0]["trades"], 1);
        assert_eq!(contribution[0]["pnl"], 25.0);
        assert_eq!(contribution[1]["strategy"], "OFI");
        assert_eq!(contribution[1]["pnl"], -10.0);
        assert_eq!(result.body["contribution_net"], 15.0);
    }
}
