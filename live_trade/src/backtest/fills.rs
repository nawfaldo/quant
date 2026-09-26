//! THE EXNESS LIVE-FILL MODEL, and the only fill model the backtest has.
//!
//! A port of `sandbox/research/fill_models/exness.py` (`spread_by_bar_1m`,
//! `price_by_bar_1m`, `exit_delay_seconds`) as `cfd_families.backtest` spends
//! it. The sealed fill -- vendor candle open in, stop or target to the digit
//! out, one spread constant per market -- is what no account ever gets. This is
//! what this account does get, read off the broker's own minute bars
//! (`exness_<broker>_1m`):
//!
//!   spread  the broker's quote in the minute the entry opens in. MT5's M1
//!           `spread` column IS the median of that minute's ticks (ratio 1.00x,
//!           correlation 0.956-1.000 over 105,000 matched minutes), stored in
//!           POINTS and converted here against that minute's own close.
//!   entry   the vendor candle open moved by the RATIO the broker's price
//!           travelled over the feed lag plus the bridge queue. A ratio and never
//!           the broker's absolute price: the sleeve DECIDES on a vendor table
//!           and is FILLED at Exness, and pasting the broker's level in put the
//!           entry at one price level while the stop and target stayed at
//!           another (`ukoil:xma_cross` +69.6% against a sealed +28.6%).
//!   exit    the same ratio one WHOLE BAR plus that lag later, for every exit
//!           reason. The MT5 payload carries no stop or target, so the runtime
//!           watches the level, acts when its candle rolls, and sends a market
//!           order down the same path an entry takes
//!           ([[no-broker-side-stops-exits-are-late-market-orders]]).
//!
//! NEVER LOOKS AHEAD. Both ends of a ratio read the open of the minute
//! CONTAINING the moment, which rounds the delay down to a whole minute: the 2s
//! entry delay resolves to the entry minute itself (a ratio of exactly 1.0) and
//! the 1,802s exit delay to the minute 30 minutes on.
//!
//! A MINUTE THE BROKER TABLE DOES NOT HOLD FALLS BACK, exactly as the Python's
//! `.get(ts, default)` does: the vendor open and the constant spread. That is the
//! idealised fill, so the fall-back is counted per sleeve and reported
//! (`fill_coverage`) rather than left silent -- a window the broker table does
//! not reach is still being priced the kind way.
//!
//! BACKTEST ONLY. The live runtime never installs one of these: live is priced
//! by the broker itself, and a live strategy has no future minute to read.

use crate::api::parquet_store::{self, NANOS_PER_SECOND, ParquetStore};
use crate::error::ApiError;

/// Everything between the runtime deciding and MT5 executing, in seconds.
/// `fill_models.exness.BRIDGE_QUEUE_SECONDS`: 0.25s store poll, 0.13s bridge
/// poll, ~0.6s of HTTP and `order_send`.
const BRIDGE_QUEUE_SECONDS: f64 = 1.0;

/// The candle every family sleeve trades, and so the exit's first term.
/// `bar_seconds` in the Python reads it off the context bars; every sleeve this
/// model is installed on aggregates 30-minute candles.
const BAR_SECONDS: f64 = 1_800.0;

/// A quote this far from the moment it stands for is the previous session's
/// last print, not the market at that moment. `at - stamp >= 60` is refused in
/// both `spread_by_bar_1m` and `price_by_bar_1m`.
const FRESH_SECONDS: i64 = 60;

/// `exness_latency.MEASURED_LAG_SECONDS[FEED[market]]`: seconds between a
/// vendor bar closing and the runtime holding it. Per VENDOR, because the
/// deciding table is Dukascopy for FX and the index/oil CFDs, Binance for the
/// crypto, and Exness's own feed for aluminium; an unlisted market is on the
/// Dukascopy path, as it is in the Python.
fn feed_lag_seconds(market: &str) -> f64 {
    match market {
        "ethusd" | "btc" => 2.0,
        "xalusd" => 5.0,
        "nq" => 1.0,
        _ => 0.6,
    }
}

/// The broker's own name for a market, which is what its table is keyed on.
/// `cfd_families.BROKER_ALIAS` / `exness_import_1m.CANON`.
fn broker_symbol(market: &str) -> Option<&'static str> {
    Some(match market {
        "usdjpy" => "usdjpy",
        "audusd" => "audusd",
        "eurjpy" => "eurjpy",
        "gbpjpy" => "gbpjpy",
        "gbpusd" => "gbpusd",
        "ukoil" => "ukoil",
        "ethusd" => "ethusd",
        "jp225" => "jp225",
        "uk100" => "uk100",
        "xalusd" => "xalusd",
        "xniusd" => "xniusd",
        "btc" => "btcusd",
        "nq" => "ustec",
        _ => return None,
    })
}

/// The broker's point for a market -- the unit the M1 `spread` column counts
/// in. Frozen from `sandbox/results/exness_pro_specs.json`, where `_point_size`
/// reads it first.
fn point_size(market: &str) -> Option<f64> {
    Some(match market {
        "usdjpy" | "eurjpy" | "gbpjpy" | "ukoil" => 0.001,
        "audusd" | "gbpusd" => 0.000_01,
        "ethusd" | "uk100" | "xalusd" | "btc" | "xniusd" | "nq" => 0.01,
        "jp225" => 0.1,
        _ => return None,
    })
}

/// `exness_<broker>_1m`, the table `cfd_families.broker_minute_table` names.
pub fn broker_table(market: &str) -> Option<String> {
    broker_symbol(market).map(|broker| format!("exness_{broker}_1m"))
}

/// Python's `int(round(x))` on the delays, which are never exactly half a
/// second, so round-half-even and round-half-away agree on every one of them.
fn whole_seconds(value: f64) -> i64 {
    value.round() as i64
}

/// How many of one sleeve's fills the broker's minute table could price.
///
/// A fill it could not reach falls back to the vendor open, the constant spread
/// and the idealised exit -- the fill no account gets -- so the share that did
/// is reported with every run rather than assumed. Counted from the reported
/// window's start (`reset_trading_state`), not through the preroll, and counted
/// where the STRATEGY fills, so an entry the engine then refuses is included.
#[derive(Clone, Copy, Debug, Default, PartialEq, serde::Serialize)]
pub struct FillCoverage {
    pub entries: u64,
    pub entries_priced: u64,
    pub exits: u64,
    pub exits_priced: u64,
}

/// One market's broker minutes and the delays it is filled at.
pub struct MarketFills {
    /// Broker minute stamps, New York wall clock as epoch seconds, ascending and
    /// unique. Only minutes with a positive open, close and spread are kept --
    /// `_read_minutes`' `good` mask -- so a zeroed row is a hole, not a price.
    stamps: Vec<i64>,
    opens: Vec<f64>,
    /// `1e4 * spread_points * point / close`, basis points of the minute's own
    /// close.
    spread_bp: Vec<f64>,
    entry_delay: i64,
    exit_delay: i64,
    /// Seconds to SUBTRACT from a candle stamp to reach the broker's clock.
    /// A shifted market's candles run `shift_hours` ahead of New York and its
    /// broker table does not (`fill_models.exness.clock_offset`).
    offset: i64,
}

impl MarketFills {
    /// Builds a market's fills from already-decoded minutes. `rows` is
    /// `(stamp, open, close, spread_points)` in any order.
    pub fn from_rows(market: &str, shift_hours: i64, mut rows: Vec<(i64, f64, f64, f64)>) -> Self {
        let point = point_size(market).unwrap_or(0.0);
        // STABLE, so a stamp stored twice keeps its LAST row -- the one
        // `np.searchsorted(side="right") - 1` lands on after the Python's stable
        // argsort.
        rows.sort_by_key(|row| row.0);
        let mut stamps = Vec::with_capacity(rows.len());
        let mut opens = Vec::with_capacity(rows.len());
        let mut spread_bp = Vec::with_capacity(rows.len());
        for (ts, open, close, spread) in rows {
            if !(open > 0.0 && close > 0.0 && spread > 0.0) {
                continue;
            }
            let bp = 1e4 * spread * point / close;
            if stamps.last() == Some(&ts) {
                let last = stamps.len() - 1;
                opens[last] = open;
                spread_bp[last] = bp;
                continue;
            }
            stamps.push(ts);
            opens.push(open);
            spread_bp.push(bp);
        }
        let lag = feed_lag_seconds(market);
        Self {
            stamps,
            opens,
            spread_bp,
            entry_delay: whole_seconds(lag + BRIDGE_QUEUE_SECONDS),
            exit_delay: whole_seconds(BAR_SECONDS + lag + BRIDGE_QUEUE_SECONDS),
            offset: shift_hours * 3_600,
        }
    }

    /// Loads `market`'s broker minutes over `[from, to)` epoch nanoseconds.
    /// `None` when the market has no broker table at all -- every bar of it then
    /// fills the idealised way, which `fill_coverage` reports.
    pub fn load(
        store: &ParquetStore,
        market: &str,
        shift_hours: i64,
        from: Option<i64>,
        to: Option<i64>,
    ) -> Result<Option<Self>, ApiError> {
        let Some(table) = broker_table(market) else {
            return Ok(None);
        };
        if !store.has_table(&table) {
            return Ok(None);
        }
        let mut rows = Vec::new();
        for batch in store.scan(&table, &["open", "close", "spread"], from, to)? {
            let stamps = parquet_store::timestamp_column(&batch)?;
            let open = parquet_store::floats(&batch, "open")?;
            let close = parquet_store::floats(&batch, "close")?;
            let spread = parquet_store::floats(&batch, "spread")?;
            for index in 0..stamps.len() {
                rows.push((
                    stamps[index].div_euclid(NANOS_PER_SECOND),
                    open[index],
                    close[index],
                    spread[index],
                ));
            }
        }
        Ok(Some(Self::from_rows(market, shift_hours, rows)))
    }

    /// The last kept minute at or before `at`, if it is that minute itself.
    fn minute_at(&self, at: i64) -> Option<usize> {
        let index = self
            .stamps
            .partition_point(|stamp| *stamp <= at)
            .checked_sub(1)?;
        (at - self.stamps[index] < FRESH_SECONDS).then_some(index)
    }

    /// `spread_by_bar_1m`: the broker's spread, in bp, in the minute a candle
    /// stamped `ts` (on the sleeve's own clock) opens in.
    pub fn spread_bp(&self, ts: i64) -> Option<f64> {
        self.minute_at(ts - self.offset)
            .map(|index| self.spread_bp[index])
    }

    /// `price_by_bar_1m` at the entry delay.
    pub fn entry(&self, ts: i64, open: f64) -> Option<f64> {
        self.moved(ts, open, self.entry_delay)
    }

    /// `price_by_bar_1m` at the exit delay, keyed on the candle the exit fired
    /// on and applied whatever the reason -- stop, target, session or clock.
    pub fn exit(&self, ts: i64, open: f64) -> Option<f64> {
        self.moved(ts, open, self.exit_delay)
    }

    fn moved(&self, ts: i64, open: f64, delay: i64) -> Option<f64> {
        if open == 0.0 || !open.is_finite() {
            return None;
        }
        let at = ts - self.offset;
        let anchor = self.minute_at(at)?;
        let moved = self.minute_at(at + delay)?;
        let base = self.opens[anchor];
        (base > 0.0).then(|| open * self.opens[moved] / base)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: i64 = 1_790_000_040 - 1_790_000_040 % 1_800;

    fn usdjpy(rows: Vec<(i64, f64, f64, f64)>) -> MarketFills {
        MarketFills::from_rows("usdjpy", 0, rows)
    }

    /// The entry delay is two seconds, which lands in the entry minute itself:
    /// a ratio of exactly 1.0, so the vendor open comes back untouched. This is
    /// the control the Python calls "+0s reproduces the sealed return".
    #[test]
    fn the_entry_delay_resolves_to_the_entry_minute() {
        let fills = usdjpy(vec![(T, 158.0, 158.1, 7.0), (T + 60, 159.0, 159.1, 7.0)]);
        assert_eq!(fills.entry(T, 157.5), Some(157.5));
    }

    /// The exit is priced by what the BROKER did over one bar plus the lag:
    /// the vendor open scaled by broker(T+30m) / broker(T).
    #[test]
    fn the_exit_moves_by_the_broker_ratio_one_bar_later() {
        let fills = usdjpy(vec![
            (T, 158.0, 158.0, 7.0),
            (T + 1_800, 158.79, 158.8, 7.0),
        ]);
        let exit = fills.exit(T, 157.5).unwrap();
        assert!((exit - 157.5 * 158.79 / 158.0).abs() < 1e-12);
    }

    /// A stale minute is a gap, not a price: both ends must be the minute itself.
    #[test]
    fn a_stale_minute_falls_back() {
        let fills = usdjpy(vec![
            (T - 60, 158.0, 158.0, 7.0),
            (T + 1_800, 158.5, 158.5, 7.0),
        ]);
        assert_eq!(fills.exit(T, 157.5), None);
        assert_eq!(fills.spread_bp(T), None);
        let fills = usdjpy(vec![(T, 158.0, 158.0, 7.0), (T + 1_740, 158.5, 158.5, 7.0)]);
        assert_eq!(fills.exit(T, 157.5), None);
    }

    /// Points to basis points against the minute's own close; a zero-spread row
    /// is dropped entirely, as `_read_minutes`' mask drops it.
    #[test]
    fn spread_is_points_over_the_close() {
        let fills = usdjpy(vec![(T, 158.0, 160.0, 8.0)]);
        assert!((fills.spread_bp(T).unwrap() - 1e4 * 8.0 * 0.001 / 160.0).abs() < 1e-12);
        let fills = usdjpy(vec![(T, 158.0, 160.0, 0.0)]);
        assert_eq!(fills.spread_bp(T), None);
    }

    /// A stamp stored twice keeps its last row.
    #[test]
    fn a_duplicated_stamp_keeps_its_last_row() {
        let fills = usdjpy(vec![(T, 158.0, 160.0, 8.0), (T, 158.0, 160.0, 16.0)]);
        assert!((fills.spread_bp(T).unwrap() - 1e4 * 16.0 * 0.001 / 160.0).abs() < 1e-12);
    }

    /// A shifted market's candle stamps run ahead of the broker's clock.
    #[test]
    fn a_shifted_market_reads_the_broker_clock() {
        let fills = MarketFills::from_rows("jp225", 6, vec![(T, 100.0, 100.0, 20.0)]);
        assert!(fills.spread_bp(T + 6 * 3_600).is_some());
        assert!(fills.spread_bp(T).is_none());
    }

    #[test]
    fn delays_match_the_python() {
        let fx = usdjpy(Vec::new());
        assert_eq!((fx.entry_delay, fx.exit_delay), (2, 1_802));
        let eth = MarketFills::from_rows("ethusd", 0, Vec::new());
        assert_eq!((eth.entry_delay, eth.exit_delay), (3, 1_803));
    }
}
