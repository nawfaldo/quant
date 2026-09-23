use serde::Serialize;

#[derive(Clone, Copy, Default)]
pub struct OrderFlowFeatures {
    // ONLY WHAT IS READ. The derived feature table has twenty-six columns and
    // exactly these EIGHT are consumed anywhere in the engine -- the four
    // add/cancel legs `nq:ofi` nets into deep OFI, plus the three gates it
    // vetoes on and the validity flag it fails closed against.
    //
    // THE OTHER EIGHTEEN COST 12.7 GB. `Bar` is `Copy` and carries this inline,
    // so every bar of every market pays for the whole struct whether or not its
    // series has order flow at all -- and only ONE of the twelve series does.
    // At 288 bytes a bar, seven years across twelve series asked the allocator
    // for 14.3 GB and the 2020-2026 run died before it read a price. Dropping
    // the dead columns takes a bar to 136 bytes.
    //
    // Re-adding a column is a one-line change; leaving one here because it
    // might be wanted later is what made the long window unrunnable.
    pub spread: f64,
    pub top5_imbalance: f64,
    pub bid_add_volume: f64,
    pub bid_cancel_volume: f64,
    pub ask_add_volume: f64,
    pub ask_cancel_volume: f64,
    pub trade_delta: f64,
    pub book_valid: bool,
}

#[derive(Clone, Copy)]
pub struct Bar {
    pub ts: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub volume_delta: f64,
    pub depth_events: u64,
    /// True when this bar was built from the level-two feed rather than the
    /// OHLCV table. `PreferredData::Combined` unions the two, and level-two
    /// strategies must ignore the OHLCV half — see `idk::from_level_two_feed`.
    pub level_two: bool,
    pub order_flow: OrderFlowFeatures,
    /// The BENCHMARK market's close for this bar's own 30-minute slot, carried
    /// by `PreferredData::OhlcvWithBenchmark` and `None` on every other source.
    ///
    /// One sleeve reads it. `ethusd:idio_break` asks whether ETHUSD broke its
    /// own channel while BTC stayed inside its own, which is a joint statement
    /// about two markets that neither price series can make alone -- and a
    /// strategy is stepped only with its OWN market's bars, so the second series
    /// has to arrive attached to the first. `ind.align`'s rule is reproduced in
    /// the loader: the last benchmark close at or before this bar's slot, never
    /// interpolated and never a future one.
    pub benchmark: Option<f64>,
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Long,
    Short,
}
#[derive(Clone, Serialize)]
pub struct Trade {
    /// Ownership for combined-run attribution, emitted as `s`. Single-strategy
    /// runs leave this empty and the key is omitted, so their wire format is
    /// unchanged; a combined run tags every row with the slot that opened it,
    /// which is the only way a caller can split the merged log by sleeve.
    #[serde(rename = "s", skip_serializing_if = "String::is_empty")]
    pub(crate) strategy: String,
    pub side: Side,
    #[serde(rename = "et")]
    pub entry_timestamp: i64,
    #[serde(rename = "xt")]
    pub exit_timestamp: i64,
    #[serde(rename = "ep", serialize_with = "serialize_four_decimals")]
    pub entry_price: f64,
    #[serde(rename = "xp", serialize_with = "serialize_four_decimals")]
    pub exit_price: f64,
    #[serde(serialize_with = "serialize_four_decimals")]
    pub pnl: f64,
    #[serde(rename = "qty", serialize_with = "serialize_four_decimals")]
    pub quantity: f64,
    #[serde(skip_serializing)]
    pub entry_raw: f64,
    #[serde(skip_serializing)]
    pub exit_raw: f64,
}

impl Trade {
    /// The owning sleeve, for a combined run. Empty for a single-strategy run.
    pub fn strategy_name(&self) -> &str {
        &self.strategy
    }
}

fn serialize_four_decimals<S>(value: &f64, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    serializer.serialize_f64((value * 10_000.0).round() / 10_000.0)
}
/// The MT5 lot step every market in this book quotes in.
///
/// There used to be an `Instrument` enum here with `Forex`, `Mini` and `Micro`
/// arms carrying futures contract multipliers. Only `Forex` was ever reachable:
/// `prepare` refuses anything else and the web pins the field, so the futures
/// arms were a second, wrong set of point values one bad request away from the
/// money path. Per-market contract specs live in `engine::costs` instead.
pub(crate) const LOT_STEP: f64 = 0.01;

/// A requested quantity rounded to the lot step, never below one step.
pub(crate) fn lot_size(raw: f64) -> f64 {
    (raw / LOT_STEP).round().max(1.0) * LOT_STEP
}

/// A RISK-limited quantity: floored to the lot step, refused below one.
///
/// Floored rather than rounded, because rounding up would take more risk than
/// the budget allows.
pub(crate) fn risk_lot_size(raw: f64) -> Option<f64> {
    let size = (raw / LOT_STEP).floor() * LOT_STEP;
    (size >= LOT_STEP).then_some(size)
}

#[derive(Clone, Copy)]
pub enum Action {
    Hold,
    Enter {
        side: Side,
        price: f64,
        quantity: f64,
    },
    Close {
        price: f64,
        fraction: f64,
    },
    EnterPosition {
        id: u64,
        side: Side,
        price: f64,
        quantity: f64,
    },
    ClosePosition {
        id: u64,
        price: f64,
    },
}

pub(crate) trait Strategy {
    fn update(&mut self, bar: Bar, equity: f64) -> Action;
    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        vec![self.update(bar, equity)]
    }
    fn discard(&mut self, _action: Action) {}
    fn discard_all(&mut self, actions: Vec<Action>) {
        for action in actions {
            self.discard(action);
        }
    }
    /// Research/event timestamp represented by the most recently emitted
    /// action. Execution happens on the bar the action arrives on; the parity
    /// fixtures use this to attribute an action from a completed aggregate
    /// candle back to that candle.
    #[cfg_attr(not(test), allow(dead_code))]
    fn action_timestamp(&self, default: i64) -> i64 {
        default
    }
    /// The same question for an EXIT leg, which is not always the same answer.
    ///
    /// `FamilyEngine` decides a candle's exit and its fill on one candle, so one
    /// stamp serves both. `DriftVwap` does not: Python resolves five-minute
    /// candle `k`'s bracket while iterating bar `k` and fills a pending signal
    /// at bar `k + 1`'s open, so the exit belongs to `k` and the entry to
    /// `k + 1` -- and the port sees both when the first minute of `k + 1`
    /// arrives. Stamping the exit with the entry's bar booked 1,111 of its 1,229
    /// exits five minutes late, which reorders settlement against every other
    /// sleeve sharing the balance.
    #[cfg_attr(not(test), allow(dead_code))]
    fn exit_timestamp(&self, default: i64) -> i64 {
        self.action_timestamp(default)
    }
    /// Clear pending/open execution state at the requested backtest boundary
    /// while retaining indicators warmed from preroll bars.
    fn reset_trading_state(&mut self) {}

    /// Forget an open position the live book never actually took, keeping
    /// everything else -- indicators, the day guard, the shadow balance.
    ///
    /// SKIP THE TRADE, TAKE THE NEXT SIGNAL. The live warm-up replays this
    /// strategy over history and takes every entry unconditionally, but the
    /// runtime may not have been running when one of them fired -- starting a
    /// session after the market opened is the ordinary case, not a mistake. The
    /// replay then believes it holds something the broker has never heard of,
    /// and the slot used to answer that by blocking the sleeve for the rest of
    /// the session. This is the narrower answer: drop the trade live missed and
    /// let the strategy look for its next one.
    ///
    /// NOT `reset_trading_state`, WHICH IS TOO BLUNT FOR THIS. That one also
    /// clears `traded_day` and rewinds the shadow balance to `ADMISSION_BALANCE`
    /// -- so a once-a-day sleeve would be free to trade AGAIN the same day, and
    /// the admission question would be answered against an equity the backtest
    /// never had. Both would make live take trades the study does not.
    fn abandon_open_position(&mut self) {}
    fn session_end_minute(&self) -> Option<usize> {
        None
    }
    /// First minute of the New York day at which this strategy's market is
    /// expected to be printing, paired with `session_end_minute`.
    ///
    /// The BACKTEST has no use for it -- a strategy decides for itself which
    /// bars it acts on. The LIVE runtime does: its feed watchdog flattens open
    /// positions when a stream stops advancing, and without a session window it
    /// cannot tell a dead feed from a market that closed on schedule. JP225
    /// prints nearly around the clock, breaks for two hours a day and stops for
    /// the weekend, and `jp225:swing_donchian` is the one sleeve that holds
    /// through the close -- so a watchdog with no session would have
    /// emergency-flattened its multi-day position at every scheduled break.
    ///
    /// Reported in REAL New York minutes, like `session_end_minute`, and it may
    /// be GREATER than the end: JP225 trades 19:00-02:00 and so reports
    /// `(1140, 120)`. Callers must treat that as a wrapping window.
    fn session_start_minute(&self) -> Option<usize> {
        None
    }
    /// Whether the strategy emits its own session-close exit.
    ///
    /// The engine's generic flattener closes on the LAST in-session bar, at that
    /// bar's close. The frozen py_sandbox cells exit on the FIRST bar at or after
    /// the session close, at that bar's OPEN, and `commodity_book` / `index_book`
    /// already emit exactly that themselves. Running both means the engine's
    /// flatten fires first and the strategy's own exit never happens, which is
    /// what made `XALUSD PDR` return -2.08% against Python's +31.17%: its 2R
    /// targets were being cut short one bar early at the wrong price.
    ///
    /// Returning `true` suppresses the engine's flatten only. `session_end_minute`
    /// is deliberately still reported, because the LIVE runtime drives its own
    /// session flatten from it and must keep doing so ([[live-vs-backtest-parity]]).
    fn flattens_itself(&self) -> bool {
        false
    }
    /// Whether this strategy deliberately sizes below the market's `volume_min`.
    ///
    /// The engine refuses an order under the broker's minimum, which is right
    /// for anything that sizes itself against a contract spec. The two sleeves
    /// imported into `exness_combined` do not: their Python path floors to the
    /// 0.01 lot STEP and never consults `volume_min`, so refusing them here
    /// would make the Rust book stricter than the record it is measured against.
    fn sizes_below_broker_minimum(&self) -> bool {
        false
    }
    fn entry_risk_fraction(&self) -> Option<f64> {
        None
    }
    fn entry_stop_price(&self) -> Option<f64> {
        None
    }
    fn max_drawdown_dollars(&self) -> Option<f64> {
        None
    }
    fn monte_carlo_drawdown_ruin_dollars(&self) -> Option<f64> {
        None
    }

    /// Re-sizes an entry against the equity the account HOLDS WHEN THE FILL IS
    /// APPLIED, rather than the equity the strategy was shown when it decided.
    ///
    /// The two differ whenever something closes at the same instant, and in a
    /// combined run that is often. `replay` settles every position whose exit
    /// has arrived and only THEN opens anything, so an entry is sized off the
    /// balance the closes left behind. The engine has to step a strategy to
    /// learn it wants to close, so it cannot hand out the post-close balance in
    /// advance -- it asks for the size again once the closes are booked.
    ///
    /// It cost 4.9 points of return to find: `jp225:swing_donchian` entering
    /// 2025-07-23 sized 6.18 lots against $637.97 where Python sized 6.16
    /// against $635.40, because `uk100:gated_fade` closed for -$2.57 at the same
    /// stamp and the port had not booked it yet.
    ///
    /// The default keeps the decided quantity, which is right for any strategy
    /// whose size does not scale with the balance.
    fn resize_entry(&self, _price: f64, quantity: f64, _shown: f64, _actual: f64) -> f64 {
        quantity
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trade_uses_the_existing_wire_field_names() {
        let trade = Trade {
            strategy: String::new(),
            side: Side::Long,
            entry_timestamp: 1,
            exit_timestamp: 2,
            entry_price: 100.0,
            exit_price: 101.0,
            pnl: 1.0,
            quantity: 0.5,
            entry_raw: 100.0,
            exit_raw: 101.0,
        };

        let value = serde_json::to_value(trade).unwrap();
        assert_eq!(value["et"], 1);
        assert_eq!(value["xt"], 2);
        assert_eq!(value["ep"], 100.0);
        assert_eq!(value["xp"], 101.0);
        assert_eq!(value["qty"], 0.5);
        assert!(value.get("entry_timestamp").is_none());
    }

    #[test]
    fn risk_size_supports_centilots_without_rounding_up() {
        assert_eq!(risk_lot_size(0.509), Some(0.5));
        assert_eq!(risk_lot_size(0.009), None);
    }
}
